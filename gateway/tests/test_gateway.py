from gateway.catalog import load_catalog
from gateway.schemas import RunSettings
from gateway.service import fit_params
from tests import plugins
from tests.conftest import run_chat


def text_of(events):
    return "".join(d["text"] for e, d in events if e == "text")


def test_catalog_marks_unconfigured_providers(client):
    cat = client.get("/api/catalog").json()
    by_name = {p["name"]: p for p in cat["providers"]}
    assert by_name["mock"]["configured"] is True
    assert by_name["anthropic"]["configured"] is False
    assert cat["cache_enabled"] is False and cat["router_enabled"] is False


def test_default_settings_fall_back_to_available_model(client):
    s = client.get("/api/settings").json()
    assert (s["provider"], s["model"]) == ("mock", "echo")
    s["temperature"] = 0.3
    assert client.put("/api/settings", json=s).status_code == 200
    assert client.get("/api/settings").json()["temperature"] == 0.3


def test_put_settings_rejects_unconfigured_provider(client):
    r = client.put("/api/settings", json={"provider": "anthropic", "model": "claude-opus-5-5"})
    assert r.status_code == 400
    assert "ANTHROPIC_API_KEY" in r.json()["detail"]


def test_chat_streams_and_persists(client):
    events = run_chat(client, "hello there", routing="manual")
    names = [e for e, _ in events]
    assert names[0] == "meta" and names[-1] == "done"
    meta, done = events[0][1], events[-1][1]
    assert meta["routed_by"] == "user"
    assert "hello there" in text_of(events)
    assert done["status"] == "ok" and done["cache_status"] == "bypass"

    cid = meta["conversation_id"]
    conv = client.get(f"/api/conversations/{cid}").json()
    assert [m["role"] for m in conv["messages"]] == ["user", "assistant"]
    assert conv["messages"][1]["meta"]["model"] == "echo"
    assert conv["settings"]["routing"] == "manual"

    # second turn sees the history
    events = run_chat(client, "again", conversation_id=cid)
    assert "turn 2" in text_of(events)
    assert len(client.get(f"/api/conversations/{cid}").json()["messages"]) == 4
    assert client.get("/api/conversations").json()[0]["id"] == cid


def test_unknown_model_is_400(client):
    r = client.post("/api/chat", json={"message": "x", "settings": {"provider": "mock", "model": "nope"}})
    assert r.status_code == 400


def test_unconfigured_provider_is_400(client):
    r = client.post(
        "/api/chat", json={"message": "x", "settings": {"provider": "anthropic", "model": "claude-opus-5-5"}}
    )
    assert r.status_code == 400


def test_cache_hit_on_identical_request(make_client):
    with make_client(cache_class="gateway.plugins.cache:MemoryCache") as client:
        first = run_chat(client, "cache me")
        second = run_chat(client, "cache me")  # new conversation, same prompt + params
        assert first[-1][1]["cache_status"] == "miss"
        assert second[-1][1]["cache_status"] == "hit"
        assert text_of(first) == text_of(second)
        # different params -> different key
        third = run_chat(client, "cache me", temperature=0.1)
        assert third[-1][1]["cache_status"] == "miss"
        # opting out
        fourth = run_chat(client, "cache me", use_cache=False)
        assert fourth[-1][1]["cache_status"] == "bypass"
        stats = client.get("/api/stats").json()
        assert stats["total_requests"] == 4 and stats["cache_hit_rate"] == round(1 / 3, 4)


def test_router_overrides_and_fallbacks(make_client):
    with make_client(router_class="tests.plugins:PickEchoHigh") as client:
        ev = run_chat(client, "route me", level="low")
        assert ev[0][1]["routed_by"] == "router"
        assert ev[0][1]["params"]["level"] == "high"
        assert ev[0][1]["route_reason"] == "test rule"
        assert plugins.CALLS[-1].requested.model == "echo"

        for prompt, reason in [
            ("keep", "router kept your selection"),
            ("bogus", "router picked unavailable mock/does-not-exist, used your selection"),
            ("boom", "router failed (RuntimeError), used your selection"),
            ("slow", "router timed out, used your selection"),
        ]:
            meta = run_chat(client, prompt, level="low")[0][1]
            assert meta["routed_by"] == "user", prompt
            assert meta["route_reason"] == reason
            assert meta["params"]["level"] == "low"

        # manual mode skips the router entirely
        n = len(plugins.CALLS)
        meta = run_chat(client, "route me", routing="manual")[0][1]
        assert meta["routed_by"] == "user" and len(plugins.CALLS) == n


def test_fit_params_respects_model_capabilities():
    cat = load_catalog("catalog.yaml")
    opus = cat.get("anthropic", "claude-opus-5-5")
    s = RunSettings(provider="anthropic", model=opus.id, temperature=0.9, top_p=0.5, level="minimal",
                    max_tokens=10**9, system_prompt="  ")
    p = fit_params(opus, s)
    assert p.temperature is None and p.top_p is None  # sampling not accepted
    assert p.level == "medium"  # invalid level -> model default
    assert p.max_tokens == opus.max_output_tokens
    assert p.system_prompt is None

    llama = cat.get("ollama", "llama3.2")
    p = fit_params(llama, s)
    assert p.level is None and p.temperature == 0.9


def test_delete_conversation(client):
    cid = run_chat(client, "bye")[0][1]["conversation_id"]
    assert client.delete(f"/api/conversations/{cid}").status_code == 204
    assert client.get(f"/api/conversations/{cid}").status_code == 404
    assert client.get("/api/requests").json()[0]["conversation_id"] is None

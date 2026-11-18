from fakeredis import FakeAsyncRedis

from tests.api.conftest import chat


def test_healthz_and_metrics(client):
    assert client.get("/healthz").json() == {"status": "ok"}
    body = client.get("/metrics").text
    assert "gateway_request_duration_seconds" in body
    assert "gateway_cache_hits_total" in body


def test_route_endpoint_returns_decision(client):
    r = client.post("/route", json={"prompt": "why does my go program deadlock"})
    assert r.status_code == 200
    assert r.json()["route"] == "code" and r.json()["target"]["model"] == "coder"


def test_chat_miss_then_exact_hit(client, upstream):
    r1 = chat(client, "fix this python TypeError please")
    assert r1.status_code == 200
    assert (r1.headers["x-cache"], r1.headers["x-route"], r1.headers["x-model"]) == ("miss", "code", "coder")
    assert r1.json()["choices"][0]["message"]["role"] == "assistant"

    r2 = chat(client, "fix this python TypeError please")
    assert r2.headers["x-cache"] == "exact"
    assert r2.json() == r1.json()
    assert upstream.calls == 1                    # second answer came from Redis


def test_semantic_hit_on_near_duplicate(client, upstream):
    chat(client, "why does my go program deadlock")
    r = chat(client, "Why does my Go program deadlock?")
    assert r.headers["x-cache"] == "semantic"
    assert float(r.headers["x-cache-similarity"]) >= 0.95
    assert upstream.calls == 1


def test_semantic_cache_respects_route(client, upstream):
    # Nearly identical text, different routes: must not share a cache entry.
    first = chat(client, "write a function for the integral of x")          # math
    r = chat(client, "write a python function for the integral of x")       # code (tie -> first route)
    assert (first.headers["x-route"], r.headers["x-route"]) == ("math", "code")
    assert r.headers["x-cache"] == "miss"
    assert upstream.calls == 2


def test_semantic_disabled(make_client, upstream):
    c = make_client(FakeAsyncRedis(), semantic_enabled=False)
    chat(c, "why does my go program deadlock")
    assert chat(c, "Why does my Go program deadlock?").headers["x-cache"] == "miss"


def test_fail_open_when_redis_down(make_client, dead_redis, upstream):
    c = make_client(dead_redis)
    r1, r2 = chat(c, "python TypeError"), chat(c, "python TypeError")
    assert r1.status_code == r2.status_code == 200    # still serving
    assert r2.headers["x-cache"] == "miss"            # ...just without cache
    assert upstream.calls == 2
    assert "gateway_cache_errors_total" in c.get("/metrics").text


def test_rate_limit_429_with_retry_after(make_client):
    c = make_client(FakeAsyncRedis(), rate=0.5, burst=2)
    codes = [chat(c, f"python q{i}").status_code for i in range(3)]
    assert codes == [200, 200, 429]
    r = chat(c, "python again")
    assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1
    assert chat(c, "python other client", key="someone-else").status_code == 200   # per-client buckets


def test_per_client_override(make_client):
    c = make_client(FakeAsyncRedis(), rate=0.1, burst=1, rate_clients="gold=100:100")
    assert [chat(c, f"q{i}", key="gold").status_code for i in range(5)] == [200] * 5
    assert [chat(c, f"q{i}", key="free").status_code for i in range(2)] == [200, 429]


def test_rate_limit_fail_open_and_fail_closed(make_client, dead_redis):
    assert chat(make_client(dead_redis, rate_fail_mode="open"), "hi").status_code == 200
    r = chat(make_client(dead_redis, rate_fail_mode="closed"), "hi")
    assert r.status_code == 503


def test_upstream_failure_falls_back_and_is_not_cached(client, upstream, monkeypatch):
    monkeypatch.setenv("MOCK_ERROR_RATE", "1")
    monkeypatch.setenv("MOCK_ERROR_MODELS", "coder")       # only the primary model fails
    r = chat(client, "python TypeError fallback")
    assert r.status_code == 200 and r.headers["x-model"] == "small-chat"
    monkeypatch.setenv("MOCK_ERROR_RATE", "0")
    assert chat(client, "python TypeError fallback").headers["x-cache"] == "miss"   # degraded answer not cached


def test_upstream_total_failure_is_502(client, monkeypatch):
    monkeypatch.setenv("MOCK_ERROR_RATE", "1")
    assert chat(client, "python TypeError").status_code == 502


def test_validation_errors(client):
    assert client.post("/v1/chat/completions", json={"messages": []}).status_code == 422
    bad_role = client.post("/v1/chat/completions",
                           json={"messages": [{"role": "assistant", "content": "x"}]})
    assert bad_role.status_code == 400
    assert chat(client, "hi", stream=True).status_code == 400


def test_healthz_fail_drill(make_client):
    assert make_client(FakeAsyncRedis(), healthz_fail=True).get("/healthz").status_code == 500


def test_mock_llm_metrics_endpoint():
    from fastapi.testclient import TestClient

    from mock_llm.main import app
    assert "mock_llm_calls_total" in TestClient(app).get("/metrics").text
    assert TestClient(app).get("/healthz").status_code == 200

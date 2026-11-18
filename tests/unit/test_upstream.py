import asyncio

import httpx
import pytest

from app.upstream import UpstreamClient, UpstreamError

OK = {"id": "x", "object": "chat.completion", "created": 0, "model": "m",
      "choices": [{"index": 0, "message": {"role": "assistant", "content": "hi"}, "finish_reason": "stop"}]}


def scripted(responses: dict[str, list]):
    """MockTransport that pops the next status (or exception) per model; records calls."""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        model = __import__("json").loads(request.content)["model"]
        calls.append(model)
        nxt = responses[model].pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return httpx.Response(nxt, json={**OK, "model": model} if nxt == 200 else {"error": "x"})
    return httpx.MockTransport(handler), calls


def run(coro):
    return asyncio.run(coro)


def client(transport, retries=2):
    return UpstreamClient("http://up/v1", retries=retries, backoff_s=0.001, transport=transport)


def test_success_first_try():
    t, calls = scripted({"coder": [200]})
    res = run(client(t).chat({"messages": []}, "coder", "small-chat"))
    assert (res.model, res.attempts, res.fell_back, calls) == ("coder", 1, False, ["coder"])


def test_retries_on_5xx_and_429_then_succeeds():
    t, calls = scripted({"coder": [503, 429, 200]})
    res = run(client(t).chat({"messages": []}, "coder"))
    assert res.attempts == 3 and calls == ["coder"] * 3


def test_retries_on_timeout():
    t, calls = scripted({"coder": [httpx.ReadTimeout("slow"), 200]})
    assert run(client(t).chat({"messages": []}, "coder")).attempts == 2


def test_falls_back_after_retries_exhausted():
    t, calls = scripted({"coder": [500, 500, 500], "small-chat": [200]})
    res = run(client(t).chat({"messages": []}, "coder", "small-chat"))
    assert res.fell_back and res.model == "small-chat"
    assert calls == ["coder", "coder", "coder", "small-chat"]


def test_4xx_is_not_retried_but_falls_back():
    t, calls = scripted({"coder": [400], "small-chat": [200]})
    res = run(client(t).chat({"messages": []}, "coder", "small-chat"))
    assert calls == ["coder", "small-chat"] and res.fell_back


def test_everything_fails_raises():
    t, _ = scripted({"coder": [500] * 3, "small-chat": [500] * 3})
    with pytest.raises(UpstreamError):
        run(client(t).chat({"messages": []}, "coder", "small-chat"))


def test_no_fallback_when_same_model():
    t, calls = scripted({"small-chat": [500] * 3})
    with pytest.raises(UpstreamError):
        run(client(t).chat({"messages": []}, "small-chat", "small-chat"))
    assert calls == ["small-chat"] * 3

import httpx
import pytest
from fakeredis import FakeAsyncRedis
from fastapi.testclient import TestClient

from app.main import Settings, create_app, make_redis
from mock_llm.main import app as mock_llm_app


class CountingTransport(httpx.AsyncBaseTransport):
    """Forwards to the in-process mock-llm and counts upstream calls."""

    def __init__(self):
        self.inner = httpx.ASGITransport(app=mock_llm_app)
        self.calls = 0

    async def handle_async_request(self, request):
        self.calls += 1
        return await self.inner.handle_async_request(request)


@pytest.fixture(autouse=True)
def fast_mock_llm(monkeypatch):
    monkeypatch.setenv("MOCK_LATENCY_MS", "0")
    monkeypatch.setenv("MOCK_LATENCY_JITTER_MS", "0")
    monkeypatch.setenv("MOCK_ERROR_RATE", "0")


def make_settings(**kw) -> Settings:
    base = dict(routes_path="routes.yaml", redis_url=None, rate=100, burst=100,
                semantic_threshold=0.95, upstream_url="http://mock/v1", upstream_backoff_s=0.001)
    return Settings(**{**base, **kw})


@pytest.fixture
def upstream():
    return CountingTransport()


@pytest.fixture
def make_client(upstream):
    clients = []

    def _make(redis_client=None, **settings):
        c = TestClient(create_app(make_settings(**settings), redis_client=redis_client, upstream_transport=upstream))
        c.__enter__()                       # runs lifespan
        clients.append(c)
        return c
    yield _make
    for c in clients:
        c.__exit__(None, None, None)


@pytest.fixture
def client(make_client):
    return make_client(FakeAsyncRedis())


@pytest.fixture
def dead_redis():
    # Nothing listens on port 1: every command raises ConnectionError (like a killed Redis pod).
    return make_redis("redis://127.0.0.1:1/0", 0.05)


def chat(client, text, key="test", **extra):
    return client.post("/v1/chat/completions", headers={"X-API-Key": key},
                       json={"messages": [{"role": "user", "content": text}], **extra})

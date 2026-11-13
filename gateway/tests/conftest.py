import json

import pytest
from fastapi.testclient import TestClient

from gateway.config import Settings
from gateway.main import create_app
from gateway.providers.mock_provider import MockProvider


@pytest.fixture(autouse=True)
def fast_mock(monkeypatch):
    monkeypatch.setattr(MockProvider, "delay_s", 0)
    for env in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(env, raising=False)


@pytest.fixture
def make_client(tmp_path):
    def _make(**overrides) -> TestClient:
        settings = Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path}/test.db",
            catalog_path="catalog.yaml",
            static_dir=str(tmp_path / "no-ui"),
            router_timeout_s=0.2,
            **overrides,
        )
        return TestClient(create_app(settings))

    return _make


@pytest.fixture
def client(make_client):
    with make_client() as c:
        yield c


def parse_sse(body: str) -> list[tuple[str, dict]]:
    out = []
    for block in body.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        out.append((lines["event"], json.loads(lines["data"])))
    return out


def run_chat(client: TestClient, message: str, conversation_id=None, **settings) -> list[tuple[str, dict]]:
    body = {
        "conversation_id": conversation_id,
        "message": message,
        "settings": {"provider": "mock", "model": "echo", **settings},
    }
    r = client.post("/api/chat", json=body)
    assert r.status_code == 200, r.text
    return parse_sse(r.text)

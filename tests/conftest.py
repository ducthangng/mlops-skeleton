import pytest

from app.config import load_routes

TINY_ROUTES = """
min_score: 1
threshold: 0.4
default:
  target: { model: small-chat, temperature: 0.7, max_tokens: 64 }
routes:
  - name: code
    target: { model: coder, fallback: small-chat }
    keywords: [python, stack trace]
    patterns: ['\\b\\w+Error\\b']
    utterances: [a, b, c, d, e]
  - name: math
    target: { model: reasoner }
    keywords: [integral, python]        # 'python' on both routes: tie-break test
    patterns: ['\\d+\\s*[+*/-]\\s*\\d+']
    utterances: [a, b, c, d, e]
"""


@pytest.fixture
def tiny_routes_path(tmp_path):
    p = tmp_path / "routes.yaml"
    p.write_text(TINY_ROUTES)
    return str(p)


@pytest.fixture
def tiny_cfg(tiny_routes_path):
    return load_routes(tiny_routes_path)

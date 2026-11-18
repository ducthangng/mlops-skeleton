import numpy as np
import pytest

from app.cache import _tag, context_hash, exact_key
from app.embedder import HashingEmbedder, make_embedder
from app.ratelimit import parse_clients


@pytest.fixture
def emb():
    return HashingEmbedder(dim=512)


def test_embedding_is_unit_length_and_deterministic(emb):
    a, b = emb.embed("why does my go program deadlock"), emb.embed("why does my go program deadlock")
    assert a.shape == (512,) and a.dtype == np.float32
    assert np.isclose(np.linalg.norm(a), 1.0, atol=1e-5)
    assert np.array_equal(a, b)


def test_near_duplicate_more_similar_than_unrelated(emb):
    q = emb.embed("why does my go program deadlock")
    near = emb.embed("Why does my Go program deadlock?")
    far = emb.embed("tell me a joke about cats")
    assert float(q @ near) > 0.95
    assert float(q @ far) < 0.3


def test_empty_text_does_not_crash(emb):
    v = emb.embed("")
    assert np.isfinite(v).all()


def test_make_embedder_default():
    assert isinstance(make_embedder("hashing"), HashingEmbedder)
    with pytest.raises(ValueError):
        make_embedder("nope")


MSGS = [{"role": "user", "content": "hi"}]


def test_exact_key_contains_version_route_model():
    k = exact_key("abc123", "code", "coder", MSGS)
    assert k.startswith("exact:abc123:code:coder:")


def test_exact_key_changes_with_any_input():
    base = exact_key("v1", "code", "coder", MSGS)
    assert base != exact_key("v2", "code", "coder", MSGS)          # config edit invalidates
    assert base != exact_key("v1", "math", "coder", MSGS)
    assert base != exact_key("v1", "code", "reasoner", MSGS)
    assert base != exact_key("v1", "code", "coder", [{"role": "user", "content": "hi!"}])


def test_exact_key_ignores_extra_message_fields():
    with_name = [{"role": "user", "content": "hi", "name": "bob"}]
    assert exact_key("v", "r", "m", MSGS) == exact_key("v", "r", "m", with_name)


def test_context_hash_ignores_last_message_only():
    a = [{"role": "system", "content": "be brief"}, {"role": "user", "content": "q1"}]
    b = [{"role": "system", "content": "be brief"}, {"role": "user", "content": "q2"}]
    c = [{"role": "system", "content": "be verbose"}, {"role": "user", "content": "q1"}]
    assert context_hash(a) == context_hash(b)
    assert context_hash(a) != context_hash(c)


def test_tag_escaping():
    assert _tag("small-chat") == r"small\-chat"
    assert _tag("coder") == "coder"


def test_parse_clients():
    assert parse_clients("") == {}
    assert parse_clients("gold=50:100, bronze=1:2") == {"gold": (50.0, 100), "bronze": (1.0, 2)}

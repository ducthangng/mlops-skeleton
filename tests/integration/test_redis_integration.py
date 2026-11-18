"""Needs a real Redis (>= 8 for the vector-search tests). In Jenkins it is a sidecar container
in the agent pod; locally: make test-integration (Redis 8 on :6390)

  REDIS_URL=redis://localhost:6390/0 uv run pytest -m integration tests/integration
"""

import asyncio
import os
import uuid

import pytest
import redis.asyncio as aioredis

from app.cache import ExactCache, SemanticCache
from app.embedder import HashingEmbedder
from app.ratelimit import RateLimiter

pytestmark = pytest.mark.integration
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
EMB = HashingEmbedder(dim=256)


def run(coro):
    return asyncio.run(coro)


async def fresh_redis() -> aioredis.Redis:
    r = aioredis.Redis.from_url(REDIS_URL)
    await r.flushdb()
    return r


async def has_search(r: aioredis.Redis) -> bool:
    try:
        await r.execute_command("FT._LIST")
        return True
    except Exception:
        return False


def test_token_bucket_is_atomic_under_concurrency():
    async def go():
        r = await fresh_redis()
        lim = RateLimiter(r, rate=0.001, burst=10)       # practically no refill during the test
        verdicts = await asyncio.gather(*[lim.check("burst-client") for _ in range(50)])
        await r.aclose()
        return verdicts
    verdicts = run(go())
    assert sum(v.allowed for v in verdicts) == 10          # exactly the bucket size, no race
    assert all(v.retry_after_s >= 1 for v in verdicts if not v.allowed)


def test_token_bucket_refills():
    async def go():
        r = await fresh_redis()
        lim = RateLimiter(r, rate=20, burst=1)
        first, second = await lim.check("c"), await lim.check("c")
        await asyncio.sleep(0.1)                           # 20 tokens/s -> 2 tokens in 100 ms
        third = await lim.check("c")
        ttl = await r.pttl("rl:c")
        await r.aclose()
        return first, second, third, ttl
    first, second, third, ttl = run(go())
    assert (first.allowed, second.allowed, third.allowed) == (True, False, True)
    assert 0 < ttl <= 1100                                 # idle buckets expire


def test_exact_cache_roundtrip_and_ttl():
    async def go():
        r = await fresh_redis()
        c = ExactCache(r, ttl_s=60)
        await c.set("exact:k", {"a": 1})
        got, ttl = await c.get("exact:k"), await r.ttl("exact:k")
        await r.aclose()
        return got, ttl
    got, ttl = run(go())
    assert got == {"a": 1} and 0 < ttl <= 60


@pytest.mark.parametrize("backend", ["numpy", "redisearch"])
def test_semantic_cache_backends(backend):
    async def go():
        r = await fresh_redis()
        if backend == "redisearch" and not await has_search(r):
            pytest.skip("Redis without the search module")
        sc = SemanticCache(r, dim=256, threshold=0.9, ttl_s=60, backend=backend)
        await sc.startup()
        where = {"cfg": "v1", "route": "code", "model": "small-chat", "ctx": uuid.uuid4().hex[:16]}
        await sc.store(EMB.embed("why does my go program deadlock"), {"answer": 42}, **where)
        await asyncio.sleep(0.05)                          # let the index pick up the new hash
        near = await sc.lookup(EMB.embed("Why does my Go program deadlock?"), **where)
        far = await sc.lookup(EMB.embed("tell me a joke about cats"), **where)
        other_model = await sc.lookup(EMB.embed("why does my go program deadlock"),
                                      **{**where, "model": "coder"})
        other_cfg = await sc.lookup(EMB.embed("why does my go program deadlock"), **{**where, "cfg": "v2"})
        await r.aclose()
        return sc.backend, near, far, other_model, other_cfg
    used, near, far, other_model, other_cfg = run(go())
    assert used == backend
    assert near is not None and near.response == {"answer": 42} and near.similarity >= 0.9
    assert far is None
    assert other_model is None and other_cfg is None       # hit needs same route+model+cfg


def test_numpy_bucket_is_bounded():
    async def go():
        r = await fresh_redis()
        sc = SemanticCache(r, dim=256, threshold=0.9, ttl_s=60, max_entries=5, backend="numpy")
        where = {"cfg": "v1", "route": "code", "model": "coder", "ctx": "c"}
        for i in range(12):
            await sc.store(EMB.embed(f"prompt number {i}"), {"i": i}, **where)
        n = await r.llen(sc._bucket(**where))
        await r.aclose()
        return n
    assert run(go()) == 5

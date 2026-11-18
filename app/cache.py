"""Two cache layers in front of the LLM, both fail-open (Redis error = cache miss).

1. ExactCache:    key = cfg_version : route : model : sha256(messages)
2. SemanticCache: nearest neighbour of the last user message's embedding, among
   entries with the same route + model + cfg_version + conversation context.
   Hit only if cosine similarity >= tau.

Semantic backends:
  redisearch  Redis Query Engine (redis >= 8 ships it): FLAT vector index, KNN 1
              with TAG pre-filter.
  numpy       fallback when FT.* is missing (redis:7, fakeredis): brute-force
              cosine over the newest `max_entries` keys of the bucket.
"""

import hashlib
import json
import logging
import re
import uuid
from dataclasses import dataclass

import numpy as np
import redis.asyncio as aioredis
from redis.commands.search.field import TagField, VectorField
from redis.commands.search.index_definition import IndexDefinition, IndexType
from redis.commands.search.query import Query
from redis.exceptions import RedisError, ResponseError

from app.metrics import CACHE_ERRORS

log = logging.getLogger(__name__)
CACHE_EXCEPTIONS = (RedisError, OSError, TimeoutError)


def canonical(messages: list[dict]) -> str:
    return json.dumps([{"role": m["role"], "content": m["content"]} for m in messages],
                      sort_keys=True, separators=(",", ":"))


def exact_key(cfg_version: str, route: str, model: str, messages: list[dict]) -> str:
    h = hashlib.sha256(canonical(messages).encode()).hexdigest()
    return f"exact:{cfg_version}:{route}:{model}:{h}"


def context_hash(messages: list[dict]) -> str:
    """Hash of everything except the last user message: two prompts only share a
    semantic cache entry if they arrive in the same conversation context."""
    return hashlib.sha256(canonical(messages[:-1]).encode()).hexdigest()[:16]


class ExactCache:
    def __init__(self, r: aioredis.Redis | None, ttl_s: int):
        self.r, self.ttl = r, ttl_s

    async def get(self, key: str) -> dict | None:
        if self.r is None:
            return None
        try:
            raw = await self.r.get(key)
        except CACHE_EXCEPTIONS:
            CACHE_ERRORS.labels("exact_get").inc()
            return None
        return json.loads(raw) if raw else None

    async def set(self, key: str, value: dict) -> None:
        if self.r is None:
            return
        try:
            await self.r.set(key, json.dumps(value), ex=self.ttl)
        except CACHE_EXCEPTIONS:
            CACHE_ERRORS.labels("exact_set").inc()


@dataclass
class SemanticHit:
    response: dict
    similarity: float


_TAG_SPECIAL = re.compile(r"([^A-Za-z0-9_])")


def _tag(value: str) -> str:
    return _TAG_SPECIAL.sub(r"\\\1", value)      # 'small-chat' -> 'small\-chat' for TAG queries


class SemanticCache:
    def __init__(self, r: aioredis.Redis | None, dim: int, threshold: float, ttl_s: int,
                 max_entries: int = 500, backend: str = "auto"):
        self.r, self.dim, self.threshold, self.ttl = r, dim, threshold, ttl_s
        self.max_entries = max_entries
        self.requested_backend = backend
        self.backend = "numpy"
        self.index = f"semidx:d{dim}"
        self.prefix = f"sem:d{dim}:"

    async def startup(self) -> None:
        """Pick the backend once. Called at app startup; a failure keeps numpy."""
        if self.r is None or self.requested_backend == "numpy":
            return
        try:
            await self.r.ft(self.index).info()
            self.backend = "redisearch"
        except ResponseError as e:
            if "unknown command" in str(e).lower():
                self._require_numpy_allowed(e)
                return
            await self._create_index()          # module present, index not yet created
            self.backend = "redisearch"
        except CACHE_EXCEPTIONS as e:
            self._require_numpy_allowed(e)
        log.info("semantic cache backend: %s", self.backend)

    def _require_numpy_allowed(self, err: Exception) -> None:
        if self.requested_backend == "redisearch":
            raise RuntimeError(f"SEMANTIC_BACKEND=redisearch but Redis has no FT.*: {err}")

    async def _create_index(self) -> None:
        schema = (
            TagField("route"), TagField("model"), TagField("cfg"), TagField("ctx"),
            VectorField("emb", "FLAT", {"TYPE": "FLOAT32", "DIM": self.dim, "DISTANCE_METRIC": "COSINE"}),
        )
        try:
            await self.r.ft(self.index).create_index(
                schema, definition=IndexDefinition(prefix=[self.prefix], index_type=IndexType.HASH))
        except ResponseError as e:
            if "already exists" not in str(e):  # another pod won the race: fine
                raise

    def _bucket(self, cfg: str, route: str, model: str, ctx: str) -> str:
        return f"semlist:d{self.dim}:{cfg}:{route}:{model}:{ctx}"

    async def lookup(self, emb: np.ndarray, *, cfg: str, route: str, model: str, ctx: str) -> SemanticHit | None:
        if self.r is None:
            return None
        try:
            if self.backend == "redisearch":
                return await self._lookup_redisearch(emb, cfg, route, model, ctx)
            return await self._lookup_numpy(emb, cfg, route, model, ctx)
        except CACHE_EXCEPTIONS:
            CACHE_ERRORS.labels("semantic_get").inc()
            return None

    async def _lookup_redisearch(self, emb, cfg, route, model, ctx) -> SemanticHit | None:
        flt = f"@route:{{{_tag(route)}}} @model:{{{_tag(model)}}} @cfg:{{{_tag(cfg)}}} @ctx:{{{_tag(ctx)}}}"
        q = (Query(f"({flt})=>[KNN 1 @emb $vec AS dist]")
             .sort_by("dist").return_fields("dist", "response").paging(0, 1).dialect(2))
        res = await self.r.ft(self.index).search(q, query_params={"vec": emb.astype(np.float32).tobytes()})
        if not res.docs:
            return None
        doc = res.docs[0]
        sim = 1.0 - float(doc.dist)                  # COSINE distance = 1 - cosine similarity
        return SemanticHit(json.loads(doc.response), sim) if sim >= self.threshold else None

    async def _lookup_numpy(self, emb, cfg, route, model, ctx) -> SemanticHit | None:
        ids = await self.r.lrange(self._bucket(cfg, route, model, ctx), 0, self.max_entries - 1)
        if not ids:
            return None
        pipe = self.r.pipeline(transaction=False)
        for i in ids:
            pipe.hmget(self.prefix + i.decode(), "emb", "response")
        rows = [row for row in await pipe.execute() if row[0] is not None]   # expired keys -> None
        if not rows:
            return None
        matrix = np.frombuffer(b"".join(r[0] for r in rows), dtype=np.float32).reshape(len(rows), self.dim)
        sims = matrix @ emb.astype(np.float32)       # vectors are unit length -> dot = cosine
        best = int(sims.argmax())
        sim = float(sims[best])
        return SemanticHit(json.loads(rows[best][1]), sim) if sim >= self.threshold else None

    async def store(self, emb: np.ndarray, response: dict, *, cfg: str, route: str, model: str, ctx: str) -> None:
        if self.r is None:
            return
        entry_id = uuid.uuid4().hex
        key = self.prefix + entry_id
        try:
            pipe = self.r.pipeline(transaction=False)
            pipe.hset(key, mapping={"emb": emb.astype(np.float32).tobytes(), "response": json.dumps(response),
                                    "route": route, "model": model, "cfg": cfg, "ctx": ctx})
            pipe.expire(key, self.ttl)
            if self.backend == "numpy":
                bucket = self._bucket(cfg, route, model, ctx)
                pipe.lpush(bucket, entry_id)
                pipe.ltrim(bucket, 0, self.max_entries - 1)   # bounded key set
                pipe.expire(bucket, self.ttl)
            await pipe.execute()
        except CACHE_EXCEPTIONS:
            CACHE_ERRORS.labels("semantic_set").inc()

"""Gateway: rate limit -> classify -> exact cache -> semantic cache -> LLM backend."""

import logging
import os
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

import httpx
import redis.asyncio as aioredis
from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel, ConfigDict, Field
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff

from app.cache import ExactCache, SemanticCache, context_hash, exact_key
from app.classifier import make_classifier
from app.config import load_routes
from app.embedder import make_embedder
from app.metrics import CACHE_HITS, CACHE_MISSES, RATE_LIMITED, REQUEST_LATENCY
from app.ratelimit import RateLimiter, parse_clients
from app.upstream import UpstreamClient, UpstreamError

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(levelname)s %(name)s %(message)s")


def make_redis(url: str, timeout_s: float) -> aioredis.Redis:
    """No client-side retries: redis-py's default retry+backoff turned 'fail-open' into
    seconds of extra latency per request when Redis was down. One short timeout, then miss."""
    return aioredis.Redis.from_url(url, socket_timeout=timeout_s, socket_connect_timeout=timeout_s,
                                   retry=Retry(NoBackoff(), 0))


def _env_bool(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).lower() in ("1", "true", "yes")


@dataclass
class Settings:
    routes_path: str = field(default_factory=lambda: os.getenv("ROUTES_PATH", "routes.yaml"))
    redis_url: str | None = field(default_factory=lambda: os.getenv("REDIS_URL") or None)
    redis_timeout_s: float = field(default_factory=lambda: float(os.getenv("REDIS_TIMEOUT_S", "0.05")))
    cache_ttl_s: int = field(default_factory=lambda: int(os.getenv("CACHE_TTL", "3600")))
    semantic_enabled: bool = field(default_factory=lambda: _env_bool("SEMANTIC_CACHE_ENABLED", "true"))
    semantic_threshold: float = field(default_factory=lambda: float(os.getenv("SEMANTIC_CACHE_THRESHOLD", "0.95")))
    semantic_backend: str = field(default_factory=lambda: os.getenv("SEMANTIC_BACKEND", "auto"))
    semantic_max_entries: int = field(default_factory=lambda: int(os.getenv("SEMANTIC_MAX_ENTRIES", "500")))
    rate: float = field(default_factory=lambda: float(os.getenv("RATE_LIMIT_RATE", "5")))
    burst: int = field(default_factory=lambda: int(os.getenv("RATE_LIMIT_BURST", "10")))
    rate_clients: str = field(default_factory=lambda: os.getenv("RATE_LIMIT_CLIENTS", ""))
    rate_fail_mode: str = field(default_factory=lambda: os.getenv("RATE_LIMIT_FAIL_MODE", "open"))
    upstream_url: str = field(default_factory=lambda: os.getenv("UPSTREAM_BASE_URL", "http://localhost:8001/v1"))
    upstream_key: str = field(default_factory=lambda: os.getenv("UPSTREAM_API_KEY", ""))
    upstream_timeout_s: float = field(default_factory=lambda: float(os.getenv("UPSTREAM_TIMEOUT_S", "10")))
    upstream_retries: int = field(default_factory=lambda: int(os.getenv("UPSTREAM_RETRIES", "2")))
    upstream_backoff_s: float = field(default_factory=lambda: float(os.getenv("UPSTREAM_BACKOFF_S", "0.1")))
    fallback_model: str = field(default_factory=lambda: os.getenv("FALLBACK_MODEL", "small-chat"))
    healthz_fail: bool = field(default_factory=lambda: _env_bool("HEALTHZ_FAIL"))   # drill only


class Message(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    model: str = "auto"                       # ignored: the gateway picks the model
    messages: list[Message] = Field(min_length=1)
    stream: bool = False


class Choice(BaseModel):
    index: int
    message: Message
    finish_reason: str | None = None


class Usage(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class ChatResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: list[Choice]
    usage: Usage | None = None


class RouteRequest(BaseModel):
    prompt: str


def create_app(settings: Settings | None = None, redis_client: aioredis.Redis | None = None,
               upstream_transport: httpx.AsyncBaseTransport | None = None) -> FastAPI:
    s = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        cfg = load_routes(s.routes_path)
        r = redis_client
        if r is None and s.redis_url:
            r = make_redis(s.redis_url, s.redis_timeout_s)
        embedder = make_embedder()
        semantic = SemanticCache(r if s.semantic_enabled else None, embedder.dim, s.semantic_threshold,
                                 s.cache_ttl_s, s.semantic_max_entries, s.semantic_backend)
        await semantic.startup()
        app.state.cfg = cfg
        app.state.classifier = make_classifier(cfg)
        app.state.embedder = embedder
        app.state.exact = ExactCache(r, s.cache_ttl_s)
        app.state.semantic = semantic
        app.state.limiter = RateLimiter(r, s.rate, s.burst, parse_clients(s.rate_clients), s.rate_fail_mode)
        app.state.upstream = UpstreamClient(s.upstream_url, s.upstream_key, s.upstream_timeout_s,
                                            s.upstream_retries, s.upstream_backoff_s, upstream_transport)
        yield
        await app.state.upstream.aclose()
        if r is not None and redis_client is None:
            await r.aclose()

    app = FastAPI(title="AI gateway", version="2.0", lifespan=lifespan)

    async def enforce_rate_limit(request: Request, api_key: str | None) -> None:
        verdict = await request.app.state.limiter.check(api_key or "anonymous")
        if verdict.allowed:
            return
        if verdict.degraded:                  # fail-closed and Redis is down
            raise HTTPException(503, "rate limiter unavailable", headers={"Retry-After": "1"})
        RATE_LIMITED.inc()
        raise HTTPException(429, "rate limit exceeded", headers={"Retry-After": str(verdict.retry_after_s)})

    @app.get("/healthz")
    def healthz():
        if s.healthz_fail:
            return JSONResponse({"status": "failing (HEALTHZ_FAIL=true)"}, status_code=500)
        return {"status": "ok"}

    @app.get("/metrics")
    def metrics():
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.post("/route")
    async def route(req: RouteRequest, request: Request, x_api_key: str | None = Header(None)):
        """Routing decision only (no LLM call). Useful for debugging routes.yaml."""
        await enforce_rate_limit(request, x_api_key)
        st = request.app.state
        return {**st.classifier.classify(req.prompt).as_dict(), "cfg_version": st.cfg.version}

    @app.post("/v1/chat/completions", response_model=ChatResponse)
    async def chat(req: ChatRequest, request: Request, response: Response,
                   x_api_key: str | None = Header(None)):
        await enforce_rate_limit(request, x_api_key)
        if req.stream:
            raise HTTPException(400, "stream=true is not supported")
        if req.messages[-1].role != "user":
            raise HTTPException(400, "last message must have role 'user'")

        st, t0 = request.app.state, time.perf_counter()
        messages = [m.model_dump() for m in req.messages]
        prompt = messages[-1]["content"]
        decision = st.classifier.classify(prompt)
        model = decision.target["model"]
        cache_type = "miss"
        response.headers.update({"x-route": decision.route, "x-cfg-version": st.cfg.version})
        try:
            key = exact_key(st.cfg.version, decision.route, model, messages)
            if (body := await st.exact.get(key)) is not None:
                cache_type = "exact"
                response.headers.update({"x-cache": "exact", "x-model": body.get("model", model)})
                return body

            emb = st.embedder.embed(prompt)             # computed once, used for lookup and store
            where = {"cfg": st.cfg.version, "route": decision.route, "model": model,
                     "ctx": context_hash(messages)}
            if (hit := await st.semantic.lookup(emb, **where)) is not None:
                cache_type = "semantic"
                response.headers.update({"x-cache": "semantic", "x-model": hit.response.get("model", model),
                                         "x-cache-similarity": f"{hit.similarity:.4f}"})
                return hit.response

            payload = {"messages": messages, "temperature": decision.target.get("temperature"),
                       "max_tokens": decision.target.get("max_tokens")}
            fallback = decision.target.get("fallback", s.fallback_model)
            try:
                result = await st.upstream.chat(payload, model, fallback)
            except UpstreamError as e:
                raise HTTPException(502, f"upstream failed: {e}") from e
            if not result.fell_back:                    # never cache a degraded (fallback) answer
                await st.exact.set(key, result.body)
                await st.semantic.store(emb, result.body, **where)
            response.headers.update({"x-cache": "miss", "x-model": result.model,
                                     "x-upstream-attempts": str(result.attempts)})
            return result.body
        finally:
            if cache_type == "miss":
                CACHE_MISSES.inc()
            else:
                CACHE_HITS.labels(cache_type).inc()
            REQUEST_LATENCY.labels(decision.route, cache_type).observe(time.perf_counter() - t0)

    return app


app = create_app()

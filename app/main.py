import hashlib
import json
import os
import time
from contextlib import asynccontextmanager

import redis
from fastapi import FastAPI
from pydantic import BaseModel

from app.router import SemanticRouter

MODEL_NAME = os.getenv("MODEL_NAME", "sentence-transformers/all-MiniLM-L6-v2")
ROUTES_PATH = os.getenv("ROUTES_PATH", "routes.yaml")
STRATEGY = os.getenv("STRATEGY", "max")
REDIS_URL = os.getenv("REDIS_URL")           
CACHE_TTL = int(os.getenv("CACHE_TTL", "3600"))
state: dict = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    state["router"] = SemanticRouter(MODEL_NAME, ROUTES_PATH)
    with open(ROUTES_PATH, "rb") as f:
        state["cfg_ver"] = hashlib.sha256(f.read()).hexdigest()[:12] # set key for routes.yaml. Different key will invalide cache.
    state["cache"] = (
        redis.Redis.from_url(REDIS_URL, socket_timeout=0.05, socket_connect_timeout=0.05)
        if REDIS_URL else None
    )
    yield


app = FastAPI(lifespan=lifespan)


class RouteRequest(BaseModel):
    prompt: str


def cache_key(prompt: str) -> str:
    h = hashlib.sha256(prompt.encode()).hexdigest()
    return f"route:{state['cfg_ver']}:{STRATEGY}:{h}"


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.post("/route")
def route(req: RouteRequest):
    t0 = time.perf_counter()
    cache, key = state["cache"], cache_key(req.prompt)

    if cache:
        try:
            hit = cache.get(key)
            if hit:
                result = json.loads(hit)
                result["cache"] = "hit"
                result["latency_ms"] = round((time.perf_counter() - t0) * 1000, 2)
                return result
        except redis.RedisError:
            pass                                       # Fail-open: sổ hỏng thì tự tính

    result = state["router"].route(req.prompt, strategy=STRATEGY)
    if cache:
        try:
            cache.set(key, json.dumps(result), ex=CACHE_TTL)
        except redis.RedisError:
            pass
    result["cache"] = "miss"
    result["latency_ms"] = round((time.perf_counter() - t0) * 1000, 2)
    return result
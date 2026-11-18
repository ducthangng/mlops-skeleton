"""Mock OpenAI-compatible LLM. Runs offline at zero cost.

Env:
  MOCK_LATENCY_MS        base latency per call (default 200)
  MOCK_LATENCY_JITTER_MS uniform jitter added on top (default 50)
  MOCK_ERROR_RATE        probability of an error response, 0..1 (default 0)
  MOCK_ERROR_STATUS      status code used for errors (default 500)
  MOCK_ERROR_MODELS      comma list: only these models fail (default: all)
  MOCK_COMPLETION_TOKENS tokens in each answer (default 64)
"""

import asyncio
import os
import random
import time
import uuid

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from prometheus_client import Counter, generate_latest

app = FastAPI(title="mock-llm")
CALLS = Counter("mock_llm_calls_total", "Calls received", ["model", "status"])


def cfg(name: str, default: str) -> str:
    return os.getenv(name, default)


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type="text/plain")


@app.post("/v1/chat/completions")
async def chat(request: Request):
    body = await request.json()
    model = body.get("model", "unknown")
    latency = float(cfg("MOCK_LATENCY_MS", "200")) + random.uniform(0, float(cfg("MOCK_LATENCY_JITTER_MS", "50")))
    await asyncio.sleep(latency / 1000)

    failing = [m for m in cfg("MOCK_ERROR_MODELS", "").split(",") if m]
    if (not failing or model in failing) and random.random() < float(cfg("MOCK_ERROR_RATE", "0")):
        status = int(cfg("MOCK_ERROR_STATUS", "500"))
        CALLS.labels(model, str(status)).inc()
        return JSONResponse({"error": {"message": "mock failure", "type": "server_error"}}, status_code=status)

    prompt = body["messages"][-1]["content"]
    completion_tokens = int(cfg("MOCK_COMPLETION_TOKENS", "64"))
    prompt_tokens = max(1, len(prompt) // 4)
    CALLS.labels(model, "200").inc()
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": f"[{model}] mock answer to: {prompt[:80]}"},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
                  "total_tokens": prompt_tokens + completion_tokens},
    }

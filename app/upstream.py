"""Client for any OpenAI-compatible backend: timeout, retry with backoff, fallback model."""

import asyncio
import logging
import random
from dataclasses import dataclass

import httpx

from app.metrics import UPSTREAM_CALLS, UPSTREAM_ERRORS, UPSTREAM_FALLBACKS

log = logging.getLogger(__name__)
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class UpstreamError(Exception):
    pass


@dataclass
class UpstreamResult:
    body: dict
    model: str                 # the model that actually answered
    attempts: int
    fell_back: bool


class UpstreamClient:
    def __init__(self, base_url: str, api_key: str = "", timeout_s: float = 10.0,
                 retries: int = 2, backoff_s: float = 0.1, transport: httpx.AsyncBaseTransport | None = None):
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self.http = httpx.AsyncClient(base_url=base_url, timeout=timeout_s, headers=headers, transport=transport)
        self.retries, self.backoff_s = retries, backoff_s

    async def aclose(self) -> None:
        await self.http.aclose()

    async def _call_model(self, model: str, payload: dict) -> tuple[dict, int]:
        """Try one model up to 1 + retries times. Returns (body, attempts)."""
        last = ""
        for attempt in range(1, self.retries + 2):
            UPSTREAM_CALLS.labels(model).inc()
            try:
                resp = await self.http.post("/chat/completions", json={**payload, "model": model})
            except httpx.TimeoutException:
                reason, last = "timeout", "timeout"
            except httpx.TransportError as e:
                reason, last = "connect", f"connect: {e}"
            else:
                if resp.status_code == 200:
                    return resp.json(), attempt
                if resp.status_code not in RETRYABLE_STATUS:      # 4xx = our bug, retrying won't help
                    UPSTREAM_ERRORS.labels(model, f"status_{resp.status_code}").inc()
                    raise UpstreamError(f"{model}: HTTP {resp.status_code}: {resp.text[:200]}")
                reason, last = f"status_{resp.status_code}", f"HTTP {resp.status_code}"
            UPSTREAM_ERRORS.labels(model, reason).inc()
            if attempt <= self.retries:
                delay = self.backoff_s * 2 ** (attempt - 1)
                await asyncio.sleep(delay + random.uniform(0, delay))   # full jitter avoids retry storms
        raise UpstreamError(f"{model}: gave up after {self.retries + 1} attempts ({last})")

    async def chat(self, payload: dict, model: str, fallback: str | None = None) -> UpstreamResult:
        try:
            body, attempts = await self._call_model(model, payload)
            return UpstreamResult(body, model, attempts, fell_back=False)
        except UpstreamError as e:
            if not fallback or fallback == model:
                raise
            log.warning("primary failed, falling back to %s: %s", fallback, e)
        UPSTREAM_FALLBACKS.inc()
        body, attempts = await self._call_model(fallback, payload)
        return UpstreamResult(body, fallback, attempts, fell_back=True)

"""The gateway pipeline: route -> cache lookup -> provider stream -> persist -> cache store."""

import asyncio
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from gateway.catalog import Catalog, ModelSpec
from gateway.config import Settings
from gateway.db import Conversation, Message, RequestLog
from gateway.plugins.cache import Cache, cache_key
from gateway.plugins.router import Router, RouteRequest, Target
from gateway.providers import ChatMessage, GenParams, Provider, ProviderError
from gateway.schemas import ChatRequest, RunSettings

log = logging.getLogger("gateway")

OVERRIDABLE = ("temperature", "top_p", "max_tokens", "level", "system_prompt", "show_thinking")


class GatewayError(Exception):
    """A request the gateway refuses before streaming starts (maps to HTTP 400/404)."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


def fit_params(model: ModelSpec, s: RunSettings, overrides: dict | None = None) -> GenParams:
    """Fit the user's settings (plus router overrides) to what `model` accepts."""
    vals: dict[str, Any] = {k: getattr(s, k) for k in OVERRIDABLE}
    for k, v in (overrides or {}).items():
        if k in OVERRIDABLE:
            vals[k] = v
    if not model.sampling:
        vals["temperature"] = vals["top_p"] = None
    if not model.levels:
        vals["level"] = None
    elif vals["level"] not in model.levels:
        vals["level"] = model.default_level
    vals["max_tokens"] = min(int(vals["max_tokens"] or model.default_max_tokens), model.max_output_tokens)
    vals["system_prompt"] = (vals["system_prompt"] or "").strip() or None
    return GenParams(**vals)


@dataclass
class Prepared:
    conversation_id: uuid.UUID
    settings: RunSettings
    prompt: str
    history: list[ChatMessage]


@dataclass
class Routed:
    model: ModelSpec
    params: GenParams
    routed_by: str  # user | router
    reason: str | None
    score: float | None


def sse_event(event: str, data: dict) -> dict:
    return {"event": event, "data": data}


class Gateway:
    def __init__(
        self,
        settings: Settings,
        catalog: Catalog,
        providers: dict[str, Provider],
        sessionmaker: async_sessionmaker,
        cache: Cache,
        router: Router,
    ):
        self.settings = settings
        self.catalog = catalog
        self.providers = providers
        self.sm = sessionmaker
        self.cache = cache
        self.router = router

    # ---------- capabilities ----------

    @property
    def cache_enabled(self) -> bool:
        return getattr(self.cache, "enabled", True)

    @property
    def router_enabled(self) -> bool:
        return getattr(self.router, "enabled", True)

    def available_providers(self) -> set[str]:
        return {name for name, p in self.providers.items() if p.available}

    def check_model(self, provider: str, model: str) -> ModelSpec:
        spec = self.catalog.get(provider, model)
        if spec is None:
            raise GatewayError(f"Unknown model {provider}/{model}. Add it to catalog.yaml.")
        if not self.providers[provider].available:
            env = self.catalog.providers[provider].api_key_env
            raise GatewayError(f"Provider '{provider}' has no API key. Set {env} and restart the gateway.")
        return spec

    # ---------- step 1: validate + store the user turn (before the stream opens) ----------

    async def prepare(self, req: ChatRequest) -> Prepared:
        self.check_model(req.settings.provider, req.settings.model)
        async with self.sm() as db:
            if req.conversation_id:
                conv = await db.get(Conversation, req.conversation_id)
                if conv is None:
                    raise GatewayError("Conversation not found", status=404)
            else:
                title = " ".join(req.message.split())[:60] or "New chat"
                conv = Conversation(title=title, settings={})
                db.add(conv)
                await db.flush()

            rows = (
                await db.execute(
                    select(Message.role, Message.content)
                    .where(Message.conversation_id == conv.id)
                    .order_by(Message.created_at.desc())
                    .limit(self.settings.history_limit)
                )
            ).all()
            history = [ChatMessage(r.role, r.content) for r in reversed(rows) if r.content.strip()]
            while history and history[0].role != "user":  # models expect the first turn to be the user's
                history.pop(0)

            db.add(Message(conversation_id=conv.id, role="user", content=req.message))
            conv.settings = req.settings.model_dump()
            await db.commit()
            return Prepared(conv.id, req.settings, req.message, history)

    # ---------- step 2: routing ----------

    async def route(self, prep: Prepared) -> Routed:
        s = prep.settings
        requested = self.check_model(s.provider, s.model)

        def keep(reason: str | None) -> Routed:
            return Routed(requested, fit_params(requested, s), "user", reason, None)

        if s.routing == "manual":
            return keep("manual selection")
        if not self.router_enabled:
            return keep("no router configured, used your selection")

        rreq = RouteRequest(
            prompt=prep.prompt,
            history=prep.history,
            requested=Target(s.provider, s.model),
            params=s.model_dump(include=set(OVERRIDABLE)),
            catalog=self.catalog,
            available_providers=self.available_providers(),
        )
        try:
            decision = await asyncio.wait_for(self.router.route(rreq), self.settings.router_timeout_s)
        except TimeoutError:
            log.warning("router timed out after %.1fs", self.settings.router_timeout_s)
            return keep("router timed out, used your selection")
        except Exception as e:
            log.exception("router failed")
            return keep(f"router failed ({type(e).__name__}), used your selection")

        if decision is None:
            return keep("router kept your selection")
        spec = self.catalog.get(decision.provider, decision.model)
        if spec is None or decision.provider not in self.available_providers():
            log.warning("router picked unusable model %s/%s", decision.provider, decision.model)
            return keep(f"router picked unavailable {decision.provider}/{decision.model}, used your selection")
        return Routed(spec, fit_params(spec, s, decision.params), "router", decision.reason, decision.score)

    # ---------- step 3: run ----------

    async def run(self, prep: Prepared) -> AsyncIterator[dict]:
        t0 = time.perf_counter()
        request_id = uuid.uuid4()
        routed = await self.route(prep)
        model, params = routed.model, routed.params
        messages = [*prep.history, ChatMessage("user", prep.prompt)]

        yield sse_event(
            "meta",
            {
                "request_id": str(request_id),
                "conversation_id": str(prep.conversation_id),
                "provider": model.provider,
                "model": model.id,
                "model_label": model.label,
                "routed_by": routed.routed_by,
                "route_reason": routed.reason,
                "params": params.model_dump(),
            },
        )

        rec: dict[str, Any] = {
            "id": request_id,
            "conversation_id": prep.conversation_id,
            "requested_provider": prep.settings.provider,
            "requested_model": prep.settings.model,
            "provider": model.provider,
            "model": model.id,
            "routed_by": routed.routed_by,
            "route_reason": routed.reason,
            "route_score": routed.score,
            "params": params.model_dump(),
            "cache_status": "bypass",
        }

        # --- cache lookup (fail-open) ---
        key = None
        cached = None
        if prep.settings.use_cache and self.cache_enabled:
            key = cache_key(model.provider, model.id, params.model_dump(), [m.__dict__ for m in messages])
            try:
                hit = await asyncio.wait_for(self.cache.get(key), self.settings.cache_timeout_s)
                cached = json.loads(hit) if hit else None
                rec["cache_status"] = "hit" if cached else "miss"
            except Exception as e:  # noqa: BLE001 - timeout, connection error, bad JSON: all a miss
                log.warning("cache get failed: %s", e)
                rec["cache_status"] = "error"
                key = None  # don't write to a cache that just failed

        if cached:
            if cached.get("thinking"):
                yield sse_event("thinking", {"text": cached["thinking"]})
            yield sse_event("text", {"text": cached["text"]})
            elapsed = round((time.perf_counter() - t0) * 1000, 2)
            rec.update(
                status="ok",
                stop_reason=cached.get("stop_reason"),
                served_model=cached.get("served_model"),
                input_tokens=0,
                output_tokens=0,
                ttft_ms=elapsed,
                latency_ms=elapsed,
            )
            msg_id = await self._save(rec, cached["text"], cached.get("thinking"), prep.settings)
            yield sse_event("done", self._done(rec, msg_id))
            return

        # --- provider stream ---
        text: list[str] = []
        thinking: list[str] = []
        final = None
        provider = self.providers[model.provider]
        try:
            async for ev in provider.stream(model, messages, params):
                if ev.type == "final":
                    final = ev
                    continue
                if rec.get("ttft_ms") is None:
                    rec["ttft_ms"] = round((time.perf_counter() - t0) * 1000, 2)
                (text if ev.type == "text" else thinking).append(ev.text)
                yield sse_event(ev.type, {"text": ev.text})
            rec["status"] = "refused" if final and final.stop_reason == "refusal" else "ok"
        except ProviderError as e:
            rec.update(status="error", error=e.message)
        except asyncio.CancelledError:
            # Client closed the tab / pressed Stop: keep what we have.
            rec.update(status="cancelled", latency_ms=round((time.perf_counter() - t0) * 1000, 2))
            await asyncio.shield(self._save(rec, "".join(text), "".join(thinking) or None, prep.settings))
            raise
        except Exception as e:
            log.exception("provider %s crashed", model.provider)
            rec.update(status="error", error=f"Gateway error: {type(e).__name__}: {e}")

        if final:
            rec.update(
                stop_reason=final.stop_reason,
                served_model=final.served_model,
                input_tokens=final.input_tokens,
                output_tokens=final.output_tokens,
            )
        rec["latency_ms"] = round((time.perf_counter() - t0) * 1000, 2)
        full_text, full_thinking = "".join(text), "".join(thinking) or None
        msg_id = await self._save(rec, full_text, full_thinking, prep.settings)

        if rec["status"] == "error":
            yield sse_event("error", {"message": rec["error"], "message_id": str(msg_id)})
            return

        # --- cache store (fail-open; only complete, non-refused answers) ---
        if key and rec["status"] == "ok" and rec.get("stop_reason") not in ("max_tokens", "length"):
            value = json.dumps(
                {
                    "text": full_text,
                    "thinking": full_thinking,
                    "stop_reason": rec.get("stop_reason"),
                    "served_model": rec.get("served_model"),
                }
            )
            try:
                await asyncio.wait_for(
                    self.cache.set(key, value, self.settings.cache_ttl_s), self.settings.cache_timeout_s
                )
            except Exception as e:  # noqa: BLE001 - fail-open
                log.warning("cache set failed: %s", e)

        yield sse_event("done", self._done(rec, msg_id))

    @staticmethod
    def _done(rec: dict, msg_id: uuid.UUID) -> dict:
        return {
            "message_id": str(msg_id),
            "status": rec["status"],
            "cache_status": rec["cache_status"],
            "stop_reason": rec.get("stop_reason"),
            "served_model": rec.get("served_model"),
            "input_tokens": rec.get("input_tokens"),
            "output_tokens": rec.get("output_tokens"),
            "ttft_ms": rec.get("ttft_ms"),
            "latency_ms": rec.get("latency_ms"),
        }

    async def _save(self, rec: dict, text: str, thinking: str | None, settings: RunSettings) -> uuid.UUID:
        async with self.sm() as db:
            db.add(RequestLog(**rec))
            msg = Message(
                conversation_id=rec["conversation_id"],
                role="assistant",
                content=text,
                thinking=thinking,
                provider=rec["provider"],
                model=rec["model"],
                request_id=rec["id"],
                error=rec.get("error"),
            )
            db.add(msg)
            conv = await db.get(Conversation, rec["conversation_id"])
            if conv is not None:
                conv.settings = settings.model_dump()
                conv.updated_at = func.now()
            await db.commit()
            return msg.id

"""The gateway pipeline: cache lookup -> route -> fit params -> provider stream -> cache store -> persist."""

import asyncio
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from gateway.catalog import Catalog, ModelSpec
from gateway.config import Settings
from gateway.db import Conversation, Message, RequestLog
from gateway.pipeline import Pipeline
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
    outcome: str  # manual | disabled | decided | kept | failed
    reason: str | None
    score: float | None
    overrides: dict = field(default_factory=dict)


def param_sources(model: ModelSpec, s: RunSettings, overrides: dict, p: GenParams) -> dict[str, str]:
    """Who decided each value: the router, the user, or the model default."""
    out = {}
    if not model.levels:
        out["level"] = "unsupported"
    elif "level" in overrides and overrides["level"] == p.level:
        out["level"] = "router"
    else:
        out["level"] = "you" if s.level == p.level else "model default"
    if "max_tokens" in overrides:
        out["max_tokens"] = "router"
    else:
        out["max_tokens"] = "you" if s.max_tokens else "model default"
    if not model.sampling:
        out["sampling"] = "unsupported"
    elif {"temperature", "top_p"} & overrides.keys():
        out["sampling"] = "router"
    else:
        out["sampling"] = "you" if (s.temperature is not None or s.top_p is not None) else "model default"
    return out


def sampling_text(p: dict) -> str:
    t, tp = p.get("temperature"), p.get("top_p")
    return f"temp {'auto' if t is None else t} · top P {'auto' if tp is None else tp}"


def ms_since(t: float) -> float:
    return round((time.perf_counter() - t) * 1000, 2)


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

    @property
    def router_name(self) -> str:
        return getattr(self.router, "display_name", None) or self.settings.router_name

    # ---------- routing ----------

    async def route(self, prep: Prepared) -> Routed:
        s = prep.settings
        requested = self.check_model(s.provider, s.model)
        name = self.router_name

        def keep(outcome: str, reason: str) -> Routed:
            return Routed(requested, fit_params(requested, s), "user", outcome, reason, None)

        if s.routing == "manual":
            return keep("manual", "manual mode: your settings")
        if not self.router_enabled:
            return keep("disabled", f"{name} is not connected: your settings")

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
            return keep("failed", f"{name} timed out: your settings")
        except Exception as e:
            log.exception("router failed")
            return keep("failed", f"{name} failed ({type(e).__name__}): your settings")

        if decision is None:
            return keep("kept", f"{name} kept your settings")
        spec = self.catalog.get(decision.provider, decision.model)
        if spec is None or decision.provider not in self.available_providers():
            log.warning("router picked unusable model %s/%s", decision.provider, decision.model)
            return keep("failed", f"{name} picked unavailable {decision.provider}/{decision.model}: your settings")
        overrides = {k: v for k, v in decision.params.items() if k in OVERRIDABLE}
        return Routed(
            spec, fit_params(spec, s, overrides), "router", "decided", decision.reason, decision.score, overrides
        )

    # ---------- run ----------

    async def run(self, prep: Prepared) -> AsyncIterator[dict]:
        t0 = time.perf_counter()
        s = prep.settings
        request_id = uuid.uuid4()
        pipe = Pipeline(self.router_name)
        requested = self.check_model(s.provider, s.model)
        base_params = fit_params(requested, s)
        messages = [*prep.history, ChatMessage("user", prep.prompt)]

        yield sse_event(
            "meta",
            {"request_id": str(request_id), "conversation_id": str(prep.conversation_id), "steps": pipe.snapshot()},
        )

        rec: dict[str, Any] = {
            "id": request_id,
            "conversation_id": prep.conversation_id,
            "requested_provider": s.provider,
            "requested_model": s.model,
            "provider": requested.provider,
            "model": requested.id,
            "routed_by": "user",
            "params": base_params.model_dump(),
            "cache_status": "bypass",
        }

        # --- 1. cache (fail-open). Checked before the router so a hit skips the router call. ---
        key = None
        cached = None
        tc = time.perf_counter()
        if not s.use_cache:
            yield pipe.update("cache", status="stopped", value="Off", detail="Turned off in Run settings")
        elif not self.cache_enabled:
            yield pipe.update("cache", status="stopped", value="Not connected", detail="No cache plugin configured")
        else:
            yield pipe.update("cache", status="in-progress")
            # Auto and manual requests never share entries: in auto mode the router may change the answer.
            key = cache_key(
                requested.provider, requested.id, {**base_params.model_dump(), "routing": s.routing},
                [m.__dict__ for m in messages],
            )
            try:
                hit = await asyncio.wait_for(self.cache.get(key), self.settings.cache_timeout_s)
                cached = json.loads(hit) if hit else None
                rec["cache_status"] = "hit" if cached else "miss"
                yield pipe.update(
                    "cache", status="success", value="Hit" if cached else "Miss",
                    detail="Answer found" if cached else "Not cached yet", ms=ms_since(tc),
                )
            except Exception as e:  # noqa: BLE001 - timeout, connection error, bad JSON: all a miss
                log.warning("cache get failed: %s", e)
                rec["cache_status"] = "error"
                key = None  # don't write to a cache that just failed
                yield pipe.update("cache", status="warning", value="Unavailable", detail="Continuing without cache",
                                  ms=ms_since(tc))

        if cached:
            async for ev in self._serve_cached(cached, rec, pipe, prep, t0):
                yield ev
            return

        # --- 2. router ---
        tr = time.perf_counter()
        if s.routing == "auto" and self.router_enabled:
            yield pipe.update("router", status="in-progress", detail="Choosing model and settings")
        routed = await self.route(prep)
        model, params = routed.model, routed.params
        router_step = {
            "manual": {"status": "stopped", "value": "Skipped", "detail": "Manual mode: you chose the settings"},
            "disabled": {"status": "stopped", "value": "Not connected", "detail": "Using your settings"},
            "decided": {"status": "success", "value": model.label, "detail": routed.reason or "Chose the model"},
            "kept": {"status": "success", "value": "Kept your choice", "detail": "Using your settings"},
            "failed": {"status": "warning", "value": "Fell back", "detail": routed.reason},
        }[routed.outcome]
        ran_router = routed.outcome in ("decided", "kept", "failed")
        yield pipe.update("router", **router_step, ms=ms_since(tr) if ran_router else None)
        rec.update(
            provider=model.provider, model=model.id, routed_by=routed.routed_by, route_reason=routed.reason,
            route_score=routed.score, params=params.model_dump(),
        )

        # --- 3. resolved settings ---
        src = param_sources(model, s, routed.overrides, params)
        yield pipe.update("model", status="success", value=model.label,
                          source="router" if routed.routed_by == "router" else "you")
        if src["level"] == "unsupported":
            yield pipe.update("level", status="stopped", value="Not supported", detail=f"{model.label} has no levels")
        else:
            yield pipe.update("level", status="success", value=params.level, source=src["level"])
        yield pipe.update("max_tokens", status="success", value=f"{params.max_tokens:,}", source=src["max_tokens"])
        if src["sampling"] == "unsupported":
            yield pipe.update("sampling", status="stopped", value="Not supported",
                              detail=f"{model.label} doesn't accept them")
        else:
            yield pipe.update("sampling", status="success", value=sampling_text(params.model_dump()),
                              source=src["sampling"])
        yield sse_event("model", self._model_event(rec, model.label))

        # --- 4. provider stream ---
        text: list[str] = []
        thinking: list[str] = []
        final = None
        tg = time.perf_counter()
        yield pipe.update("generate", status="in-progress", detail=f"Calling {self.catalog.providers[model.provider].label}")
        try:
            async for ev in self.providers[model.provider].stream(model, messages, params):
                if ev.type == "final":
                    final = ev
                    continue
                if rec.get("ttft_ms") is None:
                    rec["ttft_ms"] = ms_since(t0)
                    yield pipe.update("generate", detail=f"First token after {ms_since(tg):.0f} ms")
                (text if ev.type == "text" else thinking).append(ev.text)
                yield sse_event(ev.type, {"text": ev.text})
            rec["status"] = "refused" if final and final.stop_reason == "refusal" else "ok"
        except ProviderError as e:
            rec.update(status="error", error=e.message)
        except asyncio.CancelledError:
            # Client closed the tab / pressed Stop: keep what we have.
            rec.update(status="cancelled", latency_ms=ms_since(t0))
            pipe.update("generate", status="stopped", value="Stopped", detail="Stopped by you", ms=ms_since(tg))
            pipe.update("save", status="success", value="Postgres", detail="Partial answer saved")
            rec["steps"] = pipe.snapshot()
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
        rec["latency_ms"] = ms_since(t0)
        gen_ms = ms_since(tg)
        if rec["status"] == "error":
            yield pipe.update("generate", status="error", value="Failed", detail=rec["error"], ms=gen_ms)
        elif rec["status"] == "refused":
            yield pipe.update("generate", status="warning", value="Declined", detail="The model declined", ms=gen_ms)
        else:
            out = rec.get("output_tokens")
            detail = f"Served by {rec['served_model']}" if rec.get("served_model") not in (None, model.id) else None
            if rec.get("stop_reason") in ("max_tokens", "length"):
                detail = "Hit the max output tokens limit"
            yield pipe.update("generate", status="success", value=f"{out:,} tokens" if out else "Done",
                              detail=detail, ms=gen_ms)
        full_text, full_thinking = "".join(text), "".join(thinking) or None

        # --- 5. cache store (fail-open; only complete, non-refused answers) ---
        stored = False
        if key and rec["status"] == "ok" and rec.get("stop_reason") not in ("max_tokens", "length"):
            value = json.dumps({
                "text": full_text, "thinking": full_thinking, "stop_reason": rec.get("stop_reason"),
                "served_model": rec.get("served_model"), "provider": model.provider, "model": model.id,
                "model_label": model.label, "params": params.model_dump(), "routed_by": rec["routed_by"],
                "route_reason": rec.get("route_reason"),
            })
            try:
                await asyncio.wait_for(
                    self.cache.set(key, value, self.settings.cache_ttl_s), self.settings.cache_timeout_s
                )
                stored = True
            except Exception as e:  # noqa: BLE001 - fail-open
                log.warning("cache set failed: %s", e)

        # --- 6. persist ---
        save_event = pipe.update("save", status="success", value="Postgres + cache" if stored else "Postgres",
                                 detail="Conversation and request log written")
        rec["steps"] = pipe.snapshot()
        msg_id = await self._save(rec, full_text, full_thinking, prep.settings)
        yield save_event

        if rec["status"] == "error":
            yield sse_event("error", {"message": rec["error"], "message_id": str(msg_id)})
            return
        yield sse_event("done", self._done(rec, msg_id))

    async def _serve_cached(
        self, cached: dict, rec: dict, pipe: Pipeline, prep: Prepared, t0: float
    ) -> AsyncIterator[dict]:
        p = cached.get("params") or {}
        label = cached.get("model_label") or cached.get("model") or rec["model"]
        rec.update(
            provider=cached.get("provider", rec["provider"]), model=cached.get("model", rec["model"]),
            routed_by=cached.get("routed_by", "user"), route_reason=cached.get("route_reason"),
            params=p or rec["params"],
        )
        yield pipe.update("router", status="stopped", value="Skipped", detail="Answered from cache")
        yield pipe.update("model", status="success", value=label, source="cache")
        yield pipe.update("level", status="success" if p.get("level") else "stopped",
                          value=p.get("level") or "Not supported", source="cache" if p.get("level") else None)
        yield pipe.update("max_tokens", status="success", value=f"{p.get('max_tokens', 0):,}", source="cache")
        yield pipe.update("sampling", status="success", value=sampling_text(p), source="cache")
        yield sse_event("model", self._model_event(rec, label))
        if cached.get("thinking"):
            yield sse_event("thinking", {"text": cached["thinking"]})
        yield sse_event("text", {"text": cached["text"]})
        elapsed = ms_since(t0)
        rec.update(
            status="ok", stop_reason=cached.get("stop_reason"), served_model=cached.get("served_model"),
            input_tokens=0, output_tokens=0, ttft_ms=elapsed, latency_ms=elapsed,
        )
        yield pipe.update("generate", status="stopped", value="Skipped", detail="Served from cache, no model call")
        save_event = pipe.update("save", status="success", value="Postgres", detail="Conversation and request log written")
        rec["steps"] = pipe.snapshot()
        msg_id = await self._save(rec, cached["text"], cached.get("thinking"), prep.settings)
        yield save_event
        yield sse_event("done", self._done(rec, msg_id))

    @staticmethod
    def _model_event(rec: dict, label: str) -> dict:
        return {
            "provider": rec["provider"],
            "model": rec["model"],
            "model_label": label,
            "routed_by": rec["routed_by"],
            "route_reason": rec.get("route_reason"),
            "params": rec["params"],
        }

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

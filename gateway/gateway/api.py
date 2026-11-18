import json
import uuid

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import delete, func, select, update

from gateway.db import AppSetting, Conversation, Message, RequestLog
from gateway.schemas import (
    CatalogOut,
    ChatRequest,
    ConversationOut,
    ConversationPatch,
    ConversationSummary,
    MessageMeta,
    MessageOut,
    ModelOut,
    ProviderOut,
    RequestLogOut,
    RunSettings,
)
from gateway.service import Gateway, GatewayError

api = APIRouter(prefix="/api")

DEFAULT_KEY = "default_run"
PREFERRED_DEFAULTS = [("anthropic", "claude-opus-5-5"), ("mock", "echo")]


def gw(request: Request) -> Gateway:
    return request.app.state.gateway


@api.get("/healthz")
async def healthz():
    return {"status": "ok"}


@api.get("/catalog", response_model=CatalogOut)
async def catalog(request: Request):
    g = gw(request)
    avail = g.available_providers()
    return CatalogOut(
        providers=[
            ProviderOut(name=n, label=c.label, type=c.type, configured=n in avail, api_key_env=c.api_key_env)
            for n, c in g.catalog.providers.items()
        ],
        models=[
            ModelOut(**m.model_dump(exclude={"level_style", "refusal_fallback"}), key=m.key,
                     available=m.provider in avail)
            for m in g.catalog.models
        ],
        cache_enabled=g.cache_enabled,
        router_enabled=g.router_enabled,
        router_name=g.router_name,
    )


# ---------- default run settings (the user's default model) ----------

def _fallback_default(g: Gateway) -> RunSettings:
    avail = g.available_providers()
    for p, m in PREFERRED_DEFAULTS:
        if p in avail and g.catalog.get(p, m):
            spec = g.catalog.get(p, m)
            return RunSettings(provider=p, model=m, level=spec.default_level)
    spec = next(m for m in g.catalog.models if m.provider in avail)
    return RunSettings(provider=spec.provider, model=spec.id, level=spec.default_level)


@api.get("/settings", response_model=RunSettings)
async def get_settings(request: Request):
    g = gw(request)
    async with g.sm() as db:
        row = await db.get(AppSetting, DEFAULT_KEY)
    if row:
        s = RunSettings.model_validate(row.value)
        if g.catalog.get(s.provider, s.model) and s.provider in g.available_providers():
            return s
    return _fallback_default(g)


@api.put("/settings", response_model=RunSettings)
async def put_settings(body: RunSettings, request: Request):
    g = gw(request)
    try:
        g.check_model(body.provider, body.model)
    except GatewayError as e:
        raise HTTPException(e.status, e.message) from e
    async with g.sm() as db:
        row = await db.get(AppSetting, DEFAULT_KEY)
        if row:
            row.value = body.model_dump()
        else:
            db.add(AppSetting(key=DEFAULT_KEY, value=body.model_dump()))
        await db.commit()
    return body


# ---------- chat ----------

@api.post("/chat")
async def chat(body: ChatRequest, request: Request):
    """Streams Server-Sent Events: meta, thinking*, text*, then done or error."""
    g = gw(request)
    try:
        prep = await g.prepare(body)
    except GatewayError as e:
        raise HTTPException(e.status, e.message) from e

    async def events():
        async for ev in g.run(prep):
            yield f"event: {ev['event']}\ndata: {json.dumps(ev['data'], ensure_ascii=False)}\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------- conversations ----------

@api.get("/conversations", response_model=list[ConversationSummary])
async def list_conversations(request: Request, limit: int = Query(100, le=500)):
    async with gw(request).sm() as db:
        rows = (
            await db.execute(select(Conversation).order_by(Conversation.updated_at.desc()).limit(limit))
        ).scalars()
        return [
            ConversationSummary(id=c.id, title=c.title, updated_at=c.updated_at, model=(c.settings or {}).get("model"))
            for c in rows
        ]


@api.get("/conversations/{cid}", response_model=ConversationOut)
async def get_conversation(cid: uuid.UUID, request: Request):
    async with gw(request).sm() as db:
        conv = await db.get(Conversation, cid)
        if conv is None:
            raise HTTPException(404, "Conversation not found")
        msgs = (
            await db.execute(select(Message).where(Message.conversation_id == cid).order_by(Message.created_at))
        ).scalars().all()
        req_ids = [m.request_id for m in msgs if m.request_id]
        reqs = {}
        if req_ids:
            reqs = {r.id: r for r in (await db.execute(select(RequestLog).where(RequestLog.id.in_(req_ids)))).scalars()}

        def meta(m: Message) -> MessageMeta | None:
            r = reqs.get(m.request_id)
            return MessageMeta.model_validate(r, from_attributes=True) if r else None

        return ConversationOut(
            id=conv.id,
            title=conv.title,
            settings=conv.settings or {},
            created_at=conv.created_at,
            updated_at=conv.updated_at,
            messages=[
                MessageOut(id=m.id, role=m.role, content=m.content, thinking=m.thinking, error=m.error,
                           created_at=m.created_at, meta=meta(m))
                for m in msgs
            ],
        )


@api.patch("/conversations/{cid}", response_model=ConversationSummary)
async def rename_conversation(cid: uuid.UUID, body: ConversationPatch, request: Request):
    async with gw(request).sm() as db:
        conv = await db.get(Conversation, cid)
        if conv is None:
            raise HTTPException(404, "Conversation not found")
        conv.title = body.title
        await db.commit()
        await db.refresh(conv)
        return ConversationSummary(id=conv.id, title=conv.title, updated_at=conv.updated_at,
                                   model=(conv.settings or {}).get("model"))


@api.delete("/conversations/{cid}", status_code=204)
async def delete_conversation(cid: uuid.UUID, request: Request):
    async with gw(request).sm() as db:
        # Keep the request log for stats, but detach it from the deleted chat.
        await db.execute(update(RequestLog).where(RequestLog.conversation_id == cid).values(conversation_id=None))
        await db.execute(delete(Message).where(Message.conversation_id == cid))
        res = await db.execute(delete(Conversation).where(Conversation.id == cid))
        await db.commit()
    if res.rowcount == 0:
        raise HTTPException(404, "Conversation not found")


# ---------- observability ----------

@api.get("/requests", response_model=list[RequestLogOut])
async def list_requests(request: Request, limit: int = Query(200, le=1000)):
    async with gw(request).sm() as db:
        rows = (await db.execute(select(RequestLog).order_by(RequestLog.created_at.desc()).limit(limit))).scalars()
        return [RequestLogOut.model_validate(r, from_attributes=True) for r in rows]


def _pct(values: list[float], p: float) -> float | None:
    if not values:
        return None
    v = sorted(values)
    return round(v[min(len(v) - 1, int(p * len(v)))], 2)


@api.get("/stats")
async def stats(request: Request, window: int = Query(1000, le=10000)):
    """Summary over the last `window` requests."""
    async with gw(request).sm() as db:
        total = (await db.execute(select(func.count()).select_from(RequestLog))).scalar_one()
        rows = (
            await db.execute(select(RequestLog).order_by(RequestLog.created_at.desc()).limit(window))
        ).scalars().all()

    looked_up = [r for r in rows if r.cache_status in ("hit", "miss")]
    hits = [r for r in looked_up if r.cache_status == "hit"]
    by_model: dict[str, dict] = {}
    for r in rows:
        m = by_model.setdefault(f"{r.provider}/{r.model}", {"requests": 0, "errors": 0, "lat": [], "out": 0})
        m["requests"] += 1
        m["errors"] += r.status == "error"
        if r.latency_ms is not None and r.cache_status != "hit":
            m["lat"].append(r.latency_ms)
        m["out"] += r.output_tokens or 0
    return {
        "total_requests": total,
        "window": len(rows),
        "errors": sum(r.status == "error" for r in rows),
        "routed_by_router": sum(r.routed_by == "router" for r in rows),
        "cache_hit_rate": round(len(hits) / len(looked_up), 4) if looked_up else None,
        "latency_ms_p50": _pct([r.latency_ms for r in rows if r.latency_ms and r.cache_status != "hit"], 0.5),
        "latency_ms_p95": _pct([r.latency_ms for r in rows if r.latency_ms and r.cache_status != "hit"], 0.95),
        "ttft_ms_p50": _pct([r.ttft_ms for r in rows if r.ttft_ms and r.cache_status != "hit"], 0.5),
        "by_model": [
            {"model": k, "requests": v["requests"], "errors": v["errors"], "output_tokens": v["out"],
             "latency_ms_p50": _pct(v["lat"], 0.5)}
            for k, v in sorted(by_model.items(), key=lambda kv: -kv[1]["requests"])
        ],
    }

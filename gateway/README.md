# AI Gateway

A middleman between a chat UI and LLM providers. For every prompt it:

1. **Routes**: asks the router plugin which model should answer. If there's no router, or it returns nothing, fails, or is too slow, it uses the model you picked in the UI.
2. **Checks the cache**: asks the KV cache plugin for an exact-match answer. On a hit, the answer comes back without calling a provider.
3. **Calls the provider** (Anthropic, OpenAI or any OpenAI-compatible server, Gemini, or an offline mock) and streams the answer to the UI.
4. **Records** the conversation and one request-log row (routing decision, cache result, latency, tokens) in **Postgres**.

```
UI (web/) ──SSE──▶ POST /api/chat
                     │
                     ├─ router plugin  ──▶ RouteDecision | None   (None = use the user's pick)
                     ├─ cache plugin   ──▶ hit? stream it, done
                     ├─ provider SDK   ──▶ stream text + thinking
                     ├─ Postgres       ◀── conversation, messages, request log
                     └─ cache plugin   ◀── store complete answers
```

The router and the cache are both **fail-open**: an error, a timeout or a bad answer from either one never breaks a chat.

## Run it

```bash
# 1. Postgres
cd gateway && docker compose up -d postgres

# 2. Gateway (API on :8090; serves the built UI at / when web/dist exists)
cp .env.example .env           # add the API keys you have
uv sync
uv run uvicorn gateway.main:app --port 8090 --reload

# 3. UI dev server with hot reload (proxies /api to :8090)
cd ../web && npm install && npm run dev      # http://localhost:5173
```

You don't need any API key to try it: pick **Echo (mock)** in Run settings. Providers without a key show as disabled in the model picker.

The whole stack in containers: `docker compose up --build`, then open http://localhost:8090.

## Plug in your cache and router

Both are plain Python classes that the gateway loads from a dotted path at startup. You don't need to edit gateway code.

```bash
GATEWAY_CACHE_CLASS=my_cache.redis_kv:RedisKV
GATEWAY_ROUTER_CLASS=my_router.semantic:SemanticRouter
```

**Cache.** The interface is in [gateway/plugins/cache.py](gateway/plugins/cache.py). The gateway builds the key (a hash of provider, model, params, system prompt and full history) and the value (a JSON string). Your class only stores strings:

```python
class RedisKV:
    async def startup(self): ...                       # optional
    async def get(self, key: str) -> str | None: ...
    async def set(self, key: str, value: str, ttl_s: int | None = None) -> None: ...
    async def shutdown(self): ...                      # optional
```

`gateway.plugins.cache:MemoryCache` is an in-process LRU you can use until yours is ready. Lookups time out after `GATEWAY_CACHE_TIMEOUT_S` (50 ms) and count as a miss. Truncated (`max_tokens`), refused and failed answers are never cached.

**Router.** The interface is in [gateway/plugins/router.py](gateway/plugins/router.py):

```python
class SemanticRouter:
    async def route(self, req: RouteRequest) -> RouteDecision | None:
        # req.prompt, req.history, req.requested (user's pick), req.params,
        # req.catalog (all models + capabilities), req.available_providers
        return RouteDecision(provider="anthropic", model="claude-haiku-5-5",
                             params={"level": "low"}, reason="small talk", score=0.82)
```

Return `None` to keep the user's model. The gateway ignores a decision whose model isn't in `catalog.yaml` or whose provider has no key. Param overrides are fitted to the chosen model: unsupported levels fall back to the model default, temperature is dropped for models that don't accept it, and `max_tokens` is capped. [examples/semantic_router_http.py](examples/semantic_router_http.py) shows how to call the existing semantic router in `../app` over HTTP.

In the UI, **Routing: Manual** skips the router for that chat.

## Models

[catalog.yaml](catalog.yaml) is the source of truth for providers and models: ids, labels, thinking levels, whether temperature/top P are accepted, and output limits. The UI builds its model picker and settings from it. Restart the gateway after editing it.

- **Anthropic**: thinking is adaptive, and the "Thinking level" sets `output_config.effort`. The current API rejects temperature/top P, so those controls are hidden for Claude models. Opus 5.5, Sonnet 5.5 and Fable 5.2 have server-side refusal fallback turned on (`refusal_fallback: default`): if the model declines, the API retries on a fallback model in the same call. The request log's `served_model` shows which model answered. Remove the line to turn it off.
- **OpenAI / OpenAI-compatible**: the level maps to `reasoning_effort`. Ollama is preconfigured at `localhost:11434`.
- **Gemini 2.5**: the level maps to a thinking token budget.

## API

| Method | Path | |
|---|---|---|
| POST | `/api/chat` | Server-Sent Events: `meta`, `thinking`*, `text`*, then `done` or `error` |
| GET | `/api/catalog` | providers (with `configured`) and models |
| GET/PUT | `/api/settings` | the user's default run settings (model, level, routing, ...) |
| GET | `/api/conversations` · `/api/conversations/{id}` | chat history |
| PATCH/DELETE | `/api/conversations/{id}` | rename / delete |
| GET | `/api/requests` · `/api/stats` | request log and summary (cache hit rate, latency p50/p95) |

Interactive docs are at `/docs`.

## Data (Postgres)

| Table | What |
|---|---|
| `conversations` | title, last-used run settings |
| `messages` | user and assistant turns, with thinking text and errors |
| `requests` | one row per call: requested vs. actual model, routed_by, cache status, status, tokens, TTFT, latency |
| `app_settings` | the default run settings |

Tables are created on startup (`create_all`). Add Alembic before you change the schema on a database that holds data you want to keep.

## Tests

```bash
uv run pytest -q          # SQLite + mock provider, no keys or Postgres needed
uv run ruff check .
```

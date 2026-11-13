"""Example router plugin: ask the existing semantic router service (app/ in this repo).

The semantic router returns abstract targets such as {"model": "coder"}; ROUTE_MAP turns
those into real catalog models. Run the gateway with:

    GATEWAY_ROUTER_CLASS=examples.semantic_router_http:SemanticRouterHTTP \
    SEMANTIC_ROUTER_URL=http://localhost:8000 uv run uvicorn gateway.main:app --port 8090

This file is a template, replace it with your own router.
"""

import os

import httpx

from gateway.plugins.router import RouteDecision, RouteRequest

ROUTE_MAP = {
    "coder": ("anthropic", "claude-opus-5-5"),
    "reasoner": ("anthropic", "claude-opus-5-5"),
    "small-chat": ("anthropic", "claude-haiku-5-5"),
}


class SemanticRouterHTTP:
    def __init__(self) -> None:
        self.url = os.getenv("SEMANTIC_ROUTER_URL", "http://localhost:8000")
        self.client: httpx.AsyncClient | None = None

    async def startup(self) -> None:
        self.client = httpx.AsyncClient(base_url=self.url, timeout=2.0)

    async def shutdown(self) -> None:
        if self.client:
            await self.client.aclose()

    async def route(self, req: RouteRequest) -> RouteDecision | None:
        assert self.client is not None
        r = await self.client.post("/route", json={"prompt": req.prompt})
        r.raise_for_status()
        body = r.json()
        target = ROUTE_MAP.get(body["target"]["model"])
        if target is None or target[0] not in req.available_providers:
            return None  # keep the user's choice
        overrides = {}
        if "temperature" in body["target"]:
            overrides["temperature"] = body["target"]["temperature"]
        return RouteDecision(
            provider=target[0],
            model=target[1],
            params=overrides,
            reason=f"semantic route '{body['route']}'",
            score=body.get("score"),
        )

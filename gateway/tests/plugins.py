"""Fake plugins used by the tests."""

import asyncio

from gateway.plugins.router import RouteDecision, RouteRequest

CALLS: list[RouteRequest] = []


class PickEchoHigh:
    async def route(self, req: RouteRequest) -> RouteDecision | None:
        CALLS.append(req)
        if "keep" in req.prompt:
            return None
        if "bogus" in req.prompt:
            return RouteDecision(provider="mock", model="does-not-exist")
        if "boom" in req.prompt:
            raise RuntimeError("router exploded")
        if "slow" in req.prompt:
            await asyncio.sleep(5)
        return RouteDecision(provider="mock", model="echo", params={"level": "high"}, reason="test rule", score=0.9)

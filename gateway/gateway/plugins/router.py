"""Router plugin slot: decides which provider/model a prompt goes to.

Write your own class with this shape and point GATEWAY_ROUTER_CLASS at it, e.g.
    GATEWAY_ROUTER_CLASS=my_router.semantic:SemanticRouterPlugin

Return a RouteDecision to override the user's pick, or None to keep it.
The gateway falls back to the user's choice when the router returns None, raises,
times out, or names a model that isn't in catalog.yaml or whose provider has no API key.
"""

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from gateway.catalog import Catalog
from gateway.providers.base import ChatMessage


@dataclass(frozen=True)
class Target:
    provider: str
    model: str


@dataclass
class RouteRequest:
    prompt: str  # the new user message
    history: list[ChatMessage]  # earlier turns, oldest first (excludes `prompt`)
    requested: Target  # what the user picked in the UI
    params: dict  # the user's generation params (temperature, level, max_tokens, ...)
    catalog: Catalog  # every model the gateway knows, with capabilities
    available_providers: set[str]  # providers that have credentials configured


@dataclass
class RouteDecision:
    provider: str
    model: str
    # Overrides merged on top of the user's params, e.g. {"level": "high", "temperature": 0.2}.
    # Unknown keys are ignored; values are fitted to the model (levels, max tokens, sampling).
    params: dict = field(default_factory=dict)
    reason: str | None = None  # shown in the UI next to the answer
    score: float | None = None  # e.g. a similarity score, stored in the request log


@runtime_checkable
class Router(Protocol):
    async def route(self, req: RouteRequest) -> RouteDecision | None: ...

    # Optional lifecycle hooks; the gateway calls them if they exist:
    #   async def startup(self) -> None
    #   async def shutdown(self) -> None


class NullRouter:
    """Used when no router is configured: always keeps the user's choice."""

    enabled = False

    async def route(self, req: RouteRequest) -> RouteDecision | None:
        return None

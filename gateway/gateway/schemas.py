import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class RunSettings(BaseModel):
    """What the user picked in the Run settings panel. Also stored as their default."""

    provider: str
    model: str
    system_prompt: str = ""
    temperature: float | None = Field(None, ge=0, le=2)
    top_p: float | None = Field(None, ge=0, le=1)
    max_tokens: int | None = Field(None, ge=1)
    level: str | None = None
    show_thinking: bool = True
    routing: Literal["auto", "manual"] = "auto"  # auto = let the router override the model
    use_cache: bool = True


class ChatRequest(BaseModel):
    conversation_id: uuid.UUID | None = None
    message: str = Field(min_length=1, max_length=200_000)
    settings: RunSettings


class ProviderOut(BaseModel):
    name: str
    label: str
    type: str
    configured: bool
    api_key_env: str | None


class ModelOut(BaseModel):
    key: str
    id: str
    provider: str
    label: str
    description: str
    levels: list[str]
    default_level: str | None
    sampling: bool
    max_output_tokens: int
    default_max_tokens: int
    context_window: int | None
    available: bool


class CatalogOut(BaseModel):
    providers: list[ProviderOut]
    models: list[ModelOut]
    cache_enabled: bool
    router_enabled: bool
    router_name: str


class ConversationSummary(BaseModel):
    id: uuid.UUID
    title: str
    updated_at: datetime
    model: str | None = None


class MessageMeta(BaseModel):
    provider: str
    model: str
    served_model: str | None
    routed_by: str
    route_reason: str | None
    cache_status: str
    status: str
    stop_reason: str | None
    input_tokens: int | None
    output_tokens: int | None
    ttft_ms: float | None
    latency_ms: float | None
    steps: list[dict] | None = None


class MessageOut(BaseModel):
    id: uuid.UUID
    role: str
    content: str
    thinking: str | None
    error: str | None
    created_at: datetime
    meta: MessageMeta | None = None


class ConversationOut(BaseModel):
    id: uuid.UUID
    title: str
    settings: dict
    created_at: datetime
    updated_at: datetime
    messages: list[MessageOut]


class ConversationPatch(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class RequestLogOut(MessageMeta):
    id: uuid.UUID
    conversation_id: uuid.UUID | None
    requested_provider: str
    requested_model: str
    route_score: float | None
    params: dict
    error: str | None
    created_at: datetime

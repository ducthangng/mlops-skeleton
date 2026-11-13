"""Provider + model catalog, loaded from catalog.yaml (Git is the source of truth)."""

import os
from typing import Literal

import yaml
from pydantic import BaseModel, Field

ProviderType = Literal["anthropic", "openai", "gemini", "mock"]


class ProviderConfig(BaseModel):
    type: ProviderType
    label: str
    api_key_env: str | None = None
    api_key: str | None = None  # literal key, only for keyless local servers (e.g. Ollama)
    base_url: str | None = None
    # openai-compatible servers that only understand the old `max_tokens` field
    legacy_max_tokens: bool = False

    def resolve_api_key(self) -> str | None:
        if self.api_key:
            return self.api_key
        return os.getenv(self.api_key_env) if self.api_key_env else None


class ModelSpec(BaseModel):
    id: str  # the provider's model id
    provider: str
    label: str
    description: str = ""
    levels: list[str] = Field(default_factory=list)  # reasoning / effort levels, empty = none
    default_level: str | None = None
    level_style: Literal["effort", "budget"] = "effort"  # gemini 2.5 uses token budgets
    sampling: bool = True  # accepts temperature / top_p
    max_output_tokens: int = 8192
    default_max_tokens: int = 8192
    context_window: int | None = None
    refusal_fallback: str | None = None  # anthropic server-side fallback ("default")

    @property
    def key(self) -> str:
        return f"{self.provider}/{self.id}"


class Catalog(BaseModel):
    providers: dict[str, ProviderConfig]
    models: list[ModelSpec]

    def get(self, provider: str, model: str) -> ModelSpec | None:
        return next((m for m in self.models if m.provider == provider and m.id == model), None)


def load_catalog(path: str) -> Catalog:
    with open(path) as f:
        cat = Catalog.model_validate(yaml.safe_load(f))
    for m in cat.models:
        if m.provider not in cat.providers:
            raise ValueError(f"model {m.id} references unknown provider {m.provider}")
        if m.default_level and m.default_level not in m.levels:
            raise ValueError(f"model {m.key}: default_level {m.default_level} not in levels")
    return cat

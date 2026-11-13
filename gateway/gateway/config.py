from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All settings come from env vars prefixed GATEWAY_ (or a .env file).

    Provider API keys are NOT here: each provider in catalog.yaml names the env var
    it reads (ANTHROPIC_API_KEY, OPENAI_API_KEY, GEMINI_API_KEY, ...).
    """

    model_config = SettingsConfigDict(env_prefix="GATEWAY_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://gateway:gateway@localhost:5432/gateway"
    catalog_path: str = "catalog.yaml"

    # Plugins, as "package.module:ClassName". Unset = built-in no-op.
    cache_class: str | None = None
    router_class: str | None = None

    cache_ttl_s: int = 3600
    cache_timeout_s: float = 0.05  # fail-open: a slow cache is treated as a miss
    router_timeout_s: float = 2.0  # fail-open: a slow router falls back to the user's choice

    history_limit: int = 50  # max previous messages sent to the model
    static_dir: str = "../web/dist"  # built UI; served at / when present
    cors_origins: list[str] = ["http://localhost:5173"]


@lru_cache
def get_settings() -> Settings:
    return Settings()

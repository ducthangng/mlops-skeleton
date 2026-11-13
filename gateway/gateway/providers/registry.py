from gateway.catalog import Catalog
from gateway.providers.base import Provider


def build_providers(catalog: Catalog) -> dict[str, Provider]:
    # Imported lazily so a missing optional SDK only disables that one provider type.
    out: dict[str, Provider] = {}
    for name, cfg in catalog.providers.items():
        if cfg.type == "anthropic":
            from gateway.providers.anthropic_provider import AnthropicProvider as cls
        elif cfg.type == "openai":
            from gateway.providers.openai_provider import OpenAIProvider as cls
        elif cfg.type == "gemini":
            from gateway.providers.gemini_provider import GeminiProvider as cls
        else:
            from gateway.providers.mock_provider import MockProvider as cls
        out[name] = cls(name, cfg)
    return out

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from gateway.api import api
from gateway.catalog import load_catalog
from gateway.config import Settings, get_settings
from gateway.db import create_schema, make_engine, make_sessionmaker
from gateway.plugins.cache import NullCache
from gateway.plugins.loader import call_hook, load_plugin
from gateway.plugins.router import NullRouter
from gateway.providers import build_providers
from gateway.service import Gateway

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("gateway")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine = make_engine(settings.database_url)
        await create_schema(engine)
        catalog = load_catalog(settings.catalog_path)
        providers = build_providers(catalog)
        cache = load_plugin(settings.cache_class) if settings.cache_class else NullCache()
        router = load_plugin(settings.router_class) if settings.router_class else NullRouter()
        for plugin in (cache, router):
            await call_hook(plugin, "startup")
        app.state.gateway = Gateway(settings, catalog, providers, make_sessionmaker(engine), cache, router)
        log.info(
            "gateway ready: providers=%s cache=%s router=%s",
            sorted(app.state.gateway.available_providers()),
            type(cache).__name__,
            type(router).__name__,
        )
        yield
        for plugin in (cache, router):
            await call_hook(plugin, "shutdown")
        for p in providers.values():
            await p.aclose()
        await engine.dispose()

    app = FastAPI(title="AI Gateway", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["*"], allow_headers=["*"]
    )
    app.include_router(api)

    static = Path(settings.static_dir)
    if static.is_dir():
        app.mount("/", StaticFiles(directory=static, html=True), name="ui")
    return app


app = create_app()

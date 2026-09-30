"""FastAPI app: Telegram webhook and health check. Run with `uvicorn dropship.api:app`."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

from .cache import make_redis
from .config import get_settings
from .db import make_engine, make_sessionmaker
from .telegram.client import TelegramClient
from .telegram.webhook import WebhookDeps, make_router
from .telemetry import instrument_fastapi, setup_tracing
from .temporal import connect


@asynccontextmanager
async def _lifespan(app: FastAPI):
    if getattr(app.state, "deps", None) is not None:
        yield  # tests inject deps
        return
    settings = get_settings()
    setup_tracing(f"{settings.service_name}-api")
    redis = make_redis(settings.redis_url)
    engine = make_engine(settings.database_url)
    app.state.deps = WebhookDeps(
        secret=settings.telegram_webhook_secret.get_secret_value(),
        owner_chat_id=settings.telegram_owner_chat_id,
        telegram=TelegramClient(settings.telegram_bot_token.get_secret_value()),
        temporal=await connect(settings),
        redis=redis,
        sessions=make_sessionmaker(engine),
        task_queue=settings.temporal_task_queue,
    )
    try:
        yield
    finally:
        await redis.aclose()
        await engine.dispose()


def create_app(deps: WebhookDeps | None = None) -> FastAPI:
    app = FastAPI(title="Drop-Shipping-Agent", lifespan=_lifespan, docs_url=None, redoc_url=None)
    app.state.deps = deps
    app.include_router(make_router(lambda request: request.app.state.deps))

    @app.get("/health")
    async def health(_: Request) -> dict[str, str]:
        return {"status": "ok"}

    instrument_fastapi(app)
    return app


app = create_app()

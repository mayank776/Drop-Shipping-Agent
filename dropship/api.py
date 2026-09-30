"""FastAPI app: Telegram webhook and health check. Run with `uvicorn dropship.api:app`."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

from .cache import make_redis
from .config import get_settings
from .telegram.client import TelegramClient
from .telegram.webhook import WebhookDeps, make_router
from .telemetry import instrument_fastapi, setup_tracing
from .temporal import connect


@asynccontextmanager
async def _lifespan(app: FastAPI):
    if getattr(app.state, "deps", None) is None:
        settings = get_settings()
        setup_tracing(f"{settings.service_name}-api")
        redis = make_redis(settings.redis_url)
        app.state.deps = WebhookDeps(
            secret=settings.telegram_webhook_secret.get_secret_value(),
            owner_chat_id=settings.telegram_owner_chat_id,
            telegram=TelegramClient(settings.telegram_bot_token.get_secret_value()),
            temporal=await connect(settings),
            redis=redis,
        )
        yield
        await redis.aclose()
    else:
        yield  # tests inject deps


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

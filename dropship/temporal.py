from __future__ import annotations

from temporalio.client import Client

from .config import Settings
from .telemetry import temporal_interceptors


async def connect(settings: Settings) -> Client:
    return await Client.connect(
        settings.temporal_address,
        namespace=settings.temporal_namespace,
        interceptors=temporal_interceptors(),
    )

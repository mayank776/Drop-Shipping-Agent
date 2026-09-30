"""Settings from the environment.

In production, ECS injects AWS Secrets Manager values into the task's environment; locally they
come from `.env`. Secrets are `SecretStr`, so they don't show up in reprs or logs.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    redis_url: str

    temporal_address: str = "localhost:7233"
    temporal_namespace: str = "default"
    temporal_task_queue: str = "dropship"

    telegram_bot_token: SecretStr
    telegram_webhook_secret: SecretStr
    telegram_owner_chat_id: int

    openai_api_key: SecretStr | None = None
    openai_model: str | None = None
    openai_embedding_model: str = "text-embedding-3-small"
    ollama_base_url: str | None = None
    ollama_model: str | None = None

    service_name: str = "dropship"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]

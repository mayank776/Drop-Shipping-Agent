"""Settings loaded from the environment.

In Azure, secret values are Key Vault references in the Function App's settings
(``@Microsoft.KeyVault(SecretUri=...)``), which the platform resolves into plain
environment variables. Locally they come from ``local.settings.json``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Mapping

DEFAULT_OPENAI_API_VERSION = "2024-10-21"


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str = field(repr=False)
    telegram_webhook_secret: str = field(repr=False)
    telegram_owner_chat_id: int
    tables_endpoint: str | None = None
    tables_connection_string: str | None = field(default=None, repr=False)
    openai_endpoint: str | None = None
    openai_deployment: str | None = None
    openai_api_version: str = DEFAULT_OPENAI_API_VERSION
    openai_api_key: str | None = field(default=None, repr=False)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if env is None else env

        def get(name: str) -> str | None:
            value = env.get(name, "").strip()
            return value or None

        missing = [
            name
            for name in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_WEBHOOK_SECRET", "TELEGRAM_OWNER_CHAT_ID")
            if not get(name)
        ]
        tables_endpoint = get("DATA_TABLES_ENDPOINT")
        tables_conn = get("DATA_TABLES_CONNECTION_STRING") or get("AzureWebJobsStorage")
        if not tables_endpoint and not tables_conn:
            missing.append("DATA_TABLES_ENDPOINT or DATA_TABLES_CONNECTION_STRING")
        if missing:
            raise ConfigError("Missing settings: " + ", ".join(missing))

        try:
            owner_chat_id = int(get("TELEGRAM_OWNER_CHAT_ID"))  # type: ignore[arg-type]
        except ValueError:
            raise ConfigError("TELEGRAM_OWNER_CHAT_ID must be an integer") from None

        return cls(
            telegram_bot_token=get("TELEGRAM_BOT_TOKEN"),  # type: ignore[arg-type]
            telegram_webhook_secret=get("TELEGRAM_WEBHOOK_SECRET"),  # type: ignore[arg-type]
            telegram_owner_chat_id=owner_chat_id,
            tables_endpoint=tables_endpoint,
            tables_connection_string=None if tables_endpoint else tables_conn,
            openai_endpoint=get("AZURE_OPENAI_ENDPOINT"),
            openai_deployment=get("AZURE_OPENAI_DEPLOYMENT"),
            openai_api_version=get("AZURE_OPENAI_API_VERSION") or DEFAULT_OPENAI_API_VERSION,
            openai_api_key=get("AZURE_OPENAI_API_KEY"),
        )

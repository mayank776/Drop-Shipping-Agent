"""Builds the shared services once per worker from settings."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from .approvals import ApprovalService
from .audit import AuditLog
from .config import ConfigError, Settings
from .llm import LLMJudge
from .store import Store, TableStore
from .telegram import TelegramClient
from .webhook import TelegramWebhook


@dataclass(frozen=True)
class Services:
    settings: Settings
    store: Store
    audit: AuditLog
    telegram: TelegramClient
    approvals: ApprovalService
    webhook: TelegramWebhook


@lru_cache(maxsize=1)
def services() -> Services:
    settings = Settings.from_env()
    store = TableStore(endpoint=settings.tables_endpoint, connection_string=settings.tables_connection_string)
    audit = AuditLog(store)
    telegram = TelegramClient(settings.telegram_bot_token)
    approvals = ApprovalService(store, audit, telegram, settings.telegram_owner_chat_id)
    webhook = TelegramWebhook(settings.telegram_webhook_secret, settings.telegram_owner_chat_id, telegram, approvals)
    return Services(settings, store, audit, telegram, approvals, webhook)


@lru_cache(maxsize=1)
def llm_judge() -> LLMJudge:
    svc = services()
    s = svc.settings
    if not s.openai_endpoint or not s.openai_deployment:
        raise ConfigError("AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_DEPLOYMENT are required for LLM calls")

    from openai import AzureOpenAI

    if s.openai_api_key:
        client = AzureOpenAI(azure_endpoint=s.openai_endpoint, api_version=s.openai_api_version, api_key=s.openai_api_key)
    else:
        from azure.identity import DefaultAzureCredential, get_bearer_token_provider

        token_provider = get_bearer_token_provider(
            DefaultAzureCredential(), "https://cognitiveservices.azure.com/.default"
        )
        client = AzureOpenAI(
            azure_endpoint=s.openai_endpoint, api_version=s.openai_api_version, azure_ad_token_provider=token_provider
        )
    return LLMJudge(client, s.openai_deployment, svc.audit)

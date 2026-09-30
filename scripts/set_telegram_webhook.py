"""Point the Telegram bot at the deployed webhook.

Usage: python scripts/set_telegram_webhook.py https://<host>/telegram/webhook
(reads TELEGRAM_BOT_TOKEN and TELEGRAM_WEBHOOK_SECRET from the environment or .env)
"""

import asyncio
import sys

from dropship.config import get_settings
from dropship.telegram.client import TelegramClient


async def main(url: str) -> None:
    settings = get_settings()
    client = TelegramClient(settings.telegram_bot_token.get_secret_value())
    await client.set_webhook(url, settings.telegram_webhook_secret.get_secret_value())
    print("Webhook set.")


if __name__ == "__main__":
    if len(sys.argv) != 2 or not sys.argv[1].startswith("https://"):
        sys.exit("usage: set_telegram_webhook.py https://<host>/telegram/webhook")
    asyncio.run(main(sys.argv[1]))

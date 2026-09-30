"""Point the Telegram bot at the deployed webhook.

Usage: TELEGRAM_BOT_TOKEN=... TELEGRAM_WEBHOOK_SECRET=... \
    python scripts/set_telegram_webhook.py https://<app>.azurewebsites.net/api/telegram/webhook
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from foundation.telegram import TelegramClient  # noqa: E402

if __name__ == "__main__":
    if len(sys.argv) != 2 or not sys.argv[1].startswith("https://"):
        sys.exit("usage: set_telegram_webhook.py https://<host>/api/telegram/webhook")
    TelegramClient(os.environ["TELEGRAM_BOT_TOKEN"]).set_webhook(sys.argv[1], os.environ["TELEGRAM_WEBHOOK_SECRET"])
    print("Webhook set.")

# Drop-Shipping-Agent

An Amazon India reseller store run by a team of six AI agents, sourcing from one local
wholesaler. The goal, decision ledger and build order are in
[`Goal, Decision Ledger & Spec Plan By CLAUDE.md`](<Goal, Decision Ledger & Spec Plan By CLAUDE.md>);
each spec has its own document in `docs/specs/`.

## Status

| Spec | State |
|---|---|
| S2 Foundation | Built: [docs/specs/S2-foundation.md](docs/specs/S2-foundation.md) |
| S3 Ops Lead | Built: [docs/specs/S3-ops-lead.md](docs/specs/S3-ops-lead.md) |
| S4 Wholesaler Interface | Blocked on D10 (wholesaler conversation) |

## Stack

Python + FastAPI, Temporal (self-hosted) for business processes, LangGraph for agent reasoning,
OpenAI with an Ollama open-source fallback, PostgreSQL + pgvector, Redis, OpenTelemetry +
LangSmith, Docker on AWS ECS Fargate, secrets from AWS Secrets Manager.

## Layout

```
dropship/            application package
  api.py             FastAPI app (Telegram webhook, health)
  worker.py          Temporal worker
  approvals.py       approval workflow and activities
  ops_lead/          S3: daily brief, daily plan, alert routing, brief schedule
  llm/               LLM client with fallback, and the shared output validator
  telegram/          Bot API client and webhook
  audit.py knowledge.py cache.py db.py models.py config.py telemetry.py temporal.py
migrations/          Alembic migrations
tests/               pytest suite
docs/specs/          one document per spec
```

## Local development

```bash
cp .env.example .env            # fill in the Telegram and OpenAI values
docker compose up --build       # Postgres, Redis, Temporal (UI on :8233), migrations, API, worker
docker compose --profile llm up ollama   # optional: the fallback model
```

Tests:

```bash
python3.11 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
docker compose up -d postgres redis
docker compose exec postgres createdb -U dropship dropship_test
pytest
```

Integration tests skip themselves when PostgreSQL, Redis or the Temporal dev server aren't
available; see `tests/conftest.py` for the variables that point them elsewhere.

## Telegram bot setup

1. Create a bot with @BotFather and put the token in `TELEGRAM_BOT_TOKEN`.
2. Find your numeric chat ID (e.g. message @userinfobot) and set `TELEGRAM_OWNER_CHAT_ID`.
3. Pick a random `TELEGRAM_WEBHOOK_SECRET` (letters, digits, `_` and `-`).
4. Once the API is reachable over HTTPS, register the webhook:
   `python scripts/set_telegram_webhook.py https://<host>/telegram/webhook`

The bot acts only on updates from that chat ID, and the webhook rejects any request without
the secret token.

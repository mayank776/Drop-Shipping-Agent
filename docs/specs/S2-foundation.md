# S2 Foundation

Shared plumbing every agent runs on. No agent logic lives here; agents (S3+) build on it.

Serves: D17 (Telegram channel), D18 (Azure Functions + Durable Functions + Azure OpenAI,
LLM only at judgment points, every LLM output validated). Depends on nothing; blocked by nothing.

## Scope

| Part | Module | What it does |
|---|---|---|
| Data store | `foundation/store.py` | Key/record store on Azure Table Storage with optimistic concurrency (ETags). In-memory twin for tests. |
| Audit log | `foundation/audit.py` | Append-only log of every agent action and every approval decision. No update or delete API. |
| Secrets | `foundation/config.py` | Settings read from environment. In Azure, secrets are Key Vault references in app settings; locally, `local.settings.json` (git-ignored). Secret values never appear in `repr` or logs. |
| LLM validator | `foundation/llm.py` | Every LLM call goes through `LLMJudge`: JSON-schema response format, Pydantic validation, business-rule checks, one retry with the errors fed back, then a hard failure the calling agent must escalate. Every attempt is audited. |
| Telegram bot | `foundation/telegram.py`, `foundation/approvals.py`, `function_app.py` | Sends approval requests with Approve/Reject buttons; webhook records the decision and raises a Durable Functions event for the waiting orchestration. |

## Decisions made in this spec

- **Language:** Python 3.11, Azure Functions v2 programming model.
- **Data store:** Azure Table Storage. Volume is tiny (< 50 products, < 500 orders/month) and
  Durable Functions already needs a storage account. Records are stored as a JSON `data` column
  so schemas can evolve without migrations. Access goes through the `Store` protocol, so it can
  be swapped later (e.g. for Finance reporting) without touching agents.
- **Audit partitioning:** partition = UTC date, row key = timestamp + random suffix, so a day's
  log reads in order.
- **Telegram security (D17):**
  1. Webhook rejects any request without Telegram's secret-token header (constant-time compare).
  2. Updates are acted on only if both the chat ID and the sender ID equal the owner's chat ID
     (a private chat). Everything else is ignored.
  3. A decision on an approval that is no longer pending is a no-op ("already approved").
  4. Decisions are written with an ETag check, so a double tap can't flip a decision.
- **Approval → orchestration hand-off:** an approval can carry a Durable Functions instance ID.
  On a decision the webhook raises the `ApprovalDecision` event on that instance with
  `{"approval_id", "approved"}`. S3 builds the waiting orchestrations and the daily batching (D16).
- **LLM failure mode:** after the retry, `LLMValidationError` is raised. Agents must never act on
  unvalidated output; they escalate to Ops Lead instead.
- **Azure OpenAI auth:** API key if `AZURE_OPENAI_API_KEY` is set, otherwise managed identity
  (Entra ID).

## Settings

| Name | Required | Notes |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | yes | Secret (Key Vault) |
| `TELEGRAM_WEBHOOK_SECRET` | yes | Secret (Key Vault); 1–256 chars of `A-Z a-z 0-9 _ -` |
| `TELEGRAM_OWNER_CHAT_ID` | yes | Mayank's private chat ID |
| `DATA_TABLES_ENDPOINT` | one of | e.g. `https://<account>.table.core.windows.net`, managed identity |
| `DATA_TABLES_CONNECTION_STRING` | one of | Secret; falls back to `AzureWebJobsStorage` |
| `AZURE_OPENAI_ENDPOINT` | for LLM | |
| `AZURE_OPENAI_DEPLOYMENT` | for LLM | |
| `AZURE_OPENAI_API_VERSION` | no | default `2024-10-21` |
| `AZURE_OPENAI_API_KEY` | no | Secret; omit to use managed identity |

## Out of scope (later specs)

- Infrastructure as code (Bicep) for the Function App, Key Vault, storage and Azure OpenAI.
- Approval batching, reminders and expiry (S3).
- Commands to the bot beyond a liveness reply (S3).

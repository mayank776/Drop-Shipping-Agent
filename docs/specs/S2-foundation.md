# S2 Foundation

Shared plumbing every agent runs on. No agent logic lives here; agents (S3+) build on it.

Serves D17 (Telegram channel) and D18 (Python + FastAPI on AWS, LangGraph for agent reasoning,
Temporal for business processes, every LLM output validated). Depends on nothing; blocked by nothing.

## Scope

| Part | Where | What it does |
|---|---|---|
| PostgreSQL + pgvector | `dropship/db.py`, `dropship/models.py`, `migrations/` | Async SQLAlchemy on asyncpg; Alembic migrations; `vector` extension. |
| Knowledge store | `dropship/knowledge.py` | pgvector table for policies, product and wholesaler docs; cosine search. |
| Redis | `dropship/cache.py` | Client, distributed lock, "do once" markers (used to drop Telegram retries). |
| Temporal | `dropship/temporal.py`, `dropship/worker.py` | Client factory, worker entry point, the approval workflow. |
| Secrets | `dropship/config.py` | Settings from environment. In production, ECS injects them from AWS Secrets Manager; locally they come from `.env`. Secret values are `SecretStr` and never logged. |
| Observability | `dropship/telemetry.py` | OpenTelemetry traces (FastAPI + Temporal interceptor) exported over OTLP when configured; LangSmith traces every LLM call when enabled. |
| Audit log | `dropship/audit.py` | Append-only table. A database trigger rejects UPDATE, DELETE and TRUNCATE, so no code path can rewrite history. |
| LLM validator | `dropship/llm/` | Every LLM call goes through `LLM.decide`: JSON-schema output, Pydantic validation, business rules, retry with the errors fed back, fallback to the Ollama model, then a hard failure the agent must escalate. Every attempt is audited. |
| Telegram bot | `dropship/telegram/`, `dropship/approvals.py`, `dropship/api.py` | Sends approval requests with Approve/Reject buttons; the webhook turns a tap into a Temporal signal. |

## Decisions made in this spec

- **Temporal: self-hosted, not Temporal Cloud.** Temporal Cloud's monthly minimum would count against
  the ₹50k operating-loss stop-loss (D23) at a volume of a few hundred workflows a month. Production
  runs the Temporal server as one small ECS Fargate service using the same PostgreSQL instance
  (separate databases). Local dev uses `temporal server start-dev`. Application code only sees an
  address and namespace, so moving to Temporal Cloud later is a config change.
- **LLM fallback: an open-source model on Ollama.** OpenAI is primary. Provider errors (outage,
  rate limit, timeout) move to the Ollama model through its OpenAI-compatible API. Model names are
  settings (`OPENAI_MODEL`, `OLLAMA_MODEL`). Embeddings use OpenAI only, because stored vectors
  must come from one model (`text-embedding-3-small`, 1536 dimensions).
- **Validation before fallback.** A schema or rule failure is retried once on the same provider
  with the errors fed back; if it still fails, the next provider gets a fresh try. After the last
  provider, `LLMValidationError` is raised. Agents never act on unvalidated output.
- **Approvals are Temporal workflows.** `ApprovalWorkflow` stores the request, sends the Telegram
  message, waits for a `decide` signal, records the decision exactly once (later signals are
  ignored), audits it and edits the message to show the outcome. Workflow ID = `approval-<id>`.
  S3 adds the daily batching (D16), reminders and routing on top.
- **Telegram security (D17).** The webhook rejects requests without Telegram's secret-token header
  (constant-time compare) and acts only when both the chat and the sender are the owner's chat ID.
  Retried updates are dropped using Redis.
- **Secrets through ECS, not application code.** ECS task definitions inject Secrets Manager values
  as environment variables, so the app has one settings path everywhere and no AWS SDK dependency.
- **Knowledge scope.** One `knowledge_chunks` table with a `source` column (e.g. `g1-policy`,
  `amazon-policy`, `product`, `wholesaler`) and an HNSW cosine index.

## Settings

| Name | Required | Notes |
|---|---|---|
| `DATABASE_URL` | yes | `postgresql+asyncpg://…` |
| `REDIS_URL` | yes | `redis://…` |
| `TEMPORAL_ADDRESS` | no | default `localhost:7233` |
| `TEMPORAL_NAMESPACE` | no | default `default` |
| `TEMPORAL_TASK_QUEUE` | no | default `dropship` |
| `TELEGRAM_BOT_TOKEN` | yes | secret |
| `TELEGRAM_WEBHOOK_SECRET` | yes | secret; 1–256 chars of `A-Z a-z 0-9 _ -` |
| `TELEGRAM_OWNER_CHAT_ID` | yes | Mayank's private chat ID |
| `OPENAI_API_KEY`, `OPENAI_MODEL` | for LLM | key is secret |
| `OPENAI_EMBEDDING_MODEL` | no | default `text-embedding-3-small` |
| `OLLAMA_BASE_URL`, `OLLAMA_MODEL` | for fallback | e.g. `http://ollama:11434`, `qwen2.5:7b-instruct` |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | no | tracing is off when unset |
| `LANGSMITH_TRACING`, `LANGSMITH_API_KEY` | no | LangSmith's own variables |

## Cost notes (D23)

- Hosting the Ollama fallback in production needs a machine that can run the model; a CPU-only
  Fargate task will be slow for 7B-class models. Until go-live traffic justifies it, the fallback
  can run on a small instance started only when needed, or be left unset (primary only).
- One PostgreSQL instance serves the app, pgvector and Temporal.

## Out of scope (later specs)

- Infrastructure as code for ECS, RDS, ElastiCache and Secrets Manager.
- Approval batching, reminders, expiry and bot commands (S3).
- LangGraph agent graphs (each agent's own spec); they call `LLM.decide` for every judgment.

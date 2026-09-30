# S3 Ops Lead

The only agent that talks to Mayank. It turns the other agents' requests and alerts into one
daily Telegram brief, so the approval queue is cleared once a day (D16) within the 3–8 hrs/week
budget (D12).

Serves D13 (Catalog proposes prices, Ops Lead batches), D16 (daily queue), D17 (Telegram),
D22 (instant kill-switch alerts). Depends on S2; blocked by nothing.

## Scope

| Part | Where | What it does |
|---|---|---|
| Approval queue | `dropship/approvals.py` | Approvals now wait in the queue until the daily brief releases them. Urgent ones (e.g. the D22b resume veto) go out immediately. |
| Daily brief | `dropship/ops_lead/brief.py` | A Temporal Schedule runs `DailyBriefWorkflow` every day at 09:00 IST: build the daily plan, send one summary message, then release each queued approval as its own Approve/Reject message. |
| Daily plan | `dropship/ops_lead/plan.py` | Each agent contributes plan items through a provider; Ops Lead assembles and stores one plan per day. Deterministic, no LLM. |
| Alert routing | `dropship/ops_lead/alerts.py` | `critical` → Telegram now; `warning` → next daily brief; `info` → audit only. Repeated critical alerts with the same key are suppressed for an hour. |
| Bot commands | `dropship/telegram/webhook.py` | `/queue` releases the queue now (runs the brief on demand); `/plan` shows today's plan. |

## Decisions made in this spec

- **Brief time: 09:00 IST**, configurable with `DAILY_BRIEF_TIME` (`HH:MM`, Asia/Kolkata). The
  worker creates or updates the Temporal Schedule on startup, so a change takes effect on restart.
- **One message per approval.** Each approval keeps its own buttons so a tap maps to exactly one
  decision; the summary message groups them by kind (D13's price batches show as "price change ×N").
- **What is urgent.** Only requests that lose their meaning if held for a day: `resume` (the 12-hour
  veto window in D22b) and anything a caller marks `urgent=True`. Refunds and discounts wait for the
  brief; Support's holding reply (D20) covers the buyer meanwhile.
- **Undecided approvals are not re-sent.** The brief counts them under "still waiting" with their
  references, and `/queue` can be used at any time.
- **Alert severities.**
  - `critical`: kill switch (D22), stop-loss at 100% (D23), anything that stops orders shipping.
  - `warning`: stop-loss at 50%/75%, stock-outs, escalations an agent couldn't resolve.
  - `info`: everything else worth an audit entry.
  Every alert is stored and audited; suppressed duplicates are audited too.
- **Plan providers.** An agent adds a provider when its spec is built. S3 ships the Ops Lead's own
  provider (approvals waiting on Mayank). If a provider fails, the plan says so instead of failing
  the brief.
- **No LLM in S3.** Everything here is deterministic (D18: LLMs only at judgment points).

## Data

Migration `0002` adds:
- `approvals.urgent` (bool) and `approvals.released_at` (when it was sent to Telegram).
- `alerts`: severity, source, title, body, dedupe key, created/delivered times.
- `daily_plans`: one row per IST date with the plan items as JSON.

## Out of scope

- Routing work to other agents' workflows: each agent spec adds its task queue and workflows; Ops
  Lead starts them from the brief or from alerts once they exist.
- Kill-switch detection and auto-resume logic (S9); S3 only delivers its alerts and approvals.

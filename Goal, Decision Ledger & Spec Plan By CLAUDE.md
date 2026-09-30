# AI-Run Amazon India Store — Goal, Decision Ledger & Spec Plan

Sep 29, 2026 · @Mayank Attri

## Real goal

Build a low-touch Amazon India reseller business, run by an AI agent team, sourcing from one local wholesaler I trust. It should reach ₹25k–50k/month net profit within 6 months of going live, with no more than 8 hours/week of my time.

My role is to approve spend, refunds, discounts, price changes and wholesaler payments. Everything else belongs to the agents. Every spec below must serve this goal; anything that doesn't gets cut.

## Decision ledger

Every decision is locked except D10, which stays open until the wholesaler conversation.

| # | Area | Decision | Status |
| --- | --- | --- | --- |
| D1 | Goal | Real, profit-making store; agents are the operators, not the product | Locked |
| D2 | Market | India | Locked |
| D3 | Autonomy | Support fully autonomous; refunds and discounts need approval; spend runs on an approved monthly cap | Locked |
| D4 / D7 | Channel | Amazon India only at launch; Flipkart and Meesho are separate later specs | Locked |
| D5 | Sourcing | One local wholesaler Mayank deals with directly | Locked |
| D6 | Fulfillment | Wholesaler packs and ships from their premises under Mayank's seller account, on his invoices and labels | Locked |
| D8 | Legal | Entity and GST registration in progress; build runs in parallel; nothing goes live before GST | Locked |
| D9 | Scale | 1 wholesaler, under 50 products at launch | Locked |
| D10 | Wholesaler interface | To be chosen after talking to the wholesaler | Open |
| D11 | Target | ₹25k–50k/month net profit, 6 months after going live (roughly 250–500 orders/month at about ₹100/order) | Locked |
| D12 | Time | 3–8 hrs/week, including approvals | Locked |
| D13 | Pricing | Every price change needs approval; Catalog proposes, Ops Lead batches | Locked |
| D14 | Payables | Weekly wholesaler settlement; Finance prepares it, Mayank approves and pays; separate from the ad cap | Locked |
| D15 | Roster | Six agents: Ops Lead, Catalog, Fulfillment, Support, Ads, Finance | Locked |
| D16 | Cadence | Approval queue cleared once a day | Locked |
| D17 | Channel to Mayank | Telegram bot with Approve/Reject buttons, restricted to Mayank's chat ID | Locked |
| D18 | Stack | Python + FastAPI on AWS; LangGraph for agent reasoning, Temporal for business processes (see Tech stack) | Locked |
| D19 | Amazon integration | Direct SP-API, private app on own seller account | Locked |
| D20 | Support channel | Amazon buyer–seller email relay via a dedicated mailbox; immediate holding reply for anything awaiting approval | Locked |
| D21 | Ad cap | ₹10,000/month hard ceiling; raised only via approval queue | Locked |
| D22a | Kill switch trigger | Fires at 75% of Amazon's limit on any account-health metric | Locked |
| D22 | Kill switch action | Auto-pause Ads and all listings, alert Mayank instantly; existing orders keep shipping and Support keeps running | Locked |
| D22b | Un-pause | Ops Lead auto-resumes after metrics recover for 7 days, only with a logged root cause and a 12-hour Telegram veto window | Locked |
| D23 | Stop-loss | ₹50,000 cumulative operating loss (setup excluded) | Locked |
| D24 | Checkpoint | Month 2 after go-live: is fully loaded per-order profit positive? If not, stop or pivot | Locked |
| G1 | Guardrails | Support guardrail spec: policy-checked replies plus escalation triggers | In scope |

## Definitions and open items

**Operating loss (D23).** Running costs only: ads, Amazon fees, shipping and packaging, return and RTO losses, wholesaler cost of goods, and monthly cloud and LLM bills. One-time setup is excluded: registrations, the build itself, and initial listing content. Finance tracks this against ₹50,000 and alerts at 50%, 75% and 100%.

**Fully loaded per-order profit (D24).** Sale price minus Amazon fees, wholesaler cost, shipping, packaging, return and RTO losses, and ad spend spread across orders. Finance reports it per product and overall at the month-2 checkpoint.

**D10: wholesaler interface (open).** Questions to ask the wholesaler:

1. Do they use a smartphone daily, and which apps (WhatsApp, a browser, Google Sheets)?
2. Who handles orders at their end, and when do they dispatch each day?
3. Can they print your GST invoice and Amazon shipping label from PDFs you send?
4. How often does stock change, and can they report stock-outs?
5. Are they fine with weekly settlement (D14)?

Candidate interfaces: a simple mobile web page (orders to pack, mark shipped, update stock), a WhatsApp bot, or a shared Google Sheet. Choose whichever they'll actually use every day.

## Agent roster

The business runs on six agents. Only Ops Lead talks to Mayank, and each agent is one self-contained spec.

| Agent | Owns | Needs Mayank's approval for |
| --- | --- | --- |
| Ops Lead | Daily plan, routing work between agents, the daily Telegram approval queue, kill-switch alerts and auto-resume | Nothing itself; it collects approvals |
| Catalog | Listings, images, content, stock sync from the wholesaler, product proposals | Adding or removing products; every price change |
| Fulfillment | Pulling Amazon orders, sending orders, invoices and labels to the wholesaler, dispatch deadlines, tracking, RTO | Nothing routine |
| Support | Buyer messages via the email relay, under G1 guardrails; immediate holding replies | Refunds, discounts |
| Ads | Sponsored Products campaigns, keyword and bid allocation | The monthly cap (₹10,000) and any raise |
| Finance | Settlement reconciliation, per-order profit, stop-loss tracker, weekly wholesaler settlement, GST records for the CA | Every payment out |

During a kill-switch pause, Fulfillment and Support keep running so existing orders ship and buyers get answers.

## Tech stack

The core rule: **LangGraph controls agent reasoning; Temporal controls business processes.** Don't make LangGraph responsible for everything. Orders, dispatch deadlines, settlements, approvals and the kill switch run as Temporal workflows. LangGraph is called only where an agent has to reason: support replies, listing copy, ad decisions and anomaly triage.

| Layer | Technology | Role |
| --- | --- | --- |
| Agent orchestration | LangGraph | Stateful, long-running agent reasoning |
| Backend | Python + FastAPI | Agent services and business APIs |
| Business automation | Temporal | Durable workflows, retries, scheduled jobs, approval waits |
| LLM | OpenAI models + fallback model | Reasoning, planning, classification; every output passes the shared validator |
| Structured data | PostgreSQL | Orders, products, wholesaler, buyers, finances |
| Vector search | pgvector | Product knowledge, wholesaler docs, Amazon and G1 policies |
| Cache / queues | Redis | Caching, locks, short-lived state |
| Marketplace | Amazon SP-API (D19) | Listings, orders, inventory, reports, ads, settlements; Amazon handles storefront, checkout and payment collection |
| Wholesaler integration | Adapter behind the D10 interface | Order handoff, stock updates, dispatch confirmation |
| Browser automation | Playwright | Seller Central pages without usable API coverage |
| Analytics | Postgres → ClickHouse later | Business and event analytics |
| Observability | OpenTelemetry + LangSmith | Agent traces and workflow debugging |
| Deployment | Docker + AWS ECS on Fargate | Production infrastructure |
| Secrets | AWS Secrets Manager | SP-API credentials, API keys, mailbox and Telegram tokens |
| Notifications | Telegram (D17) + email | Approvals and alerts via Telegram; buyer support via the Amazon email relay (D20) |

Shopify, Shopify Payments and Stripe were removed: Amazon India is the only channel (D4) and Amazon collects payment.

Cloud and LLM bills count toward the ₹50k operating-loss stop-loss (D23), so keep infrastructure minimal at launch.

## Spec breakdown (build order)

There are ten small specs, ordered so that everything protecting account health goes live before anything that buys traffic.

| Spec | Scope | Depends on | Blocked by |
| --- | --- | --- | --- |
| S1 Prerequisites | GST (with wholesaler premises), seller account, SP-API developer registration, restricted-data access, dedicated seller mailbox | D8 | GST approval |
| S2 Foundation | PostgreSQL + pgvector, Redis, Temporal, secrets, OpenTelemetry + LangSmith, audit log, shared LLM output validator, Telegram bot | — | — |
| S3 Ops Lead | Daily plan, approval queue as Temporal workflows waiting for Telegram signals, alert routing | S2 | — |
| S4 Wholesaler Interface | Order handoff, stock updates, dispatch confirmation | S2 | D10 |
| S5 Fulfillment | Order pull, handoff, dispatch deadlines, tracking, RTO handling | S1, S3, S4 | SP-API access |
| S6 Catalog | Listings, stock sync, price and product proposals | S1, S3, S4 | SP-API access |
| S7 Support + G1 | Email-relay handling, policy-checked replies, escalation triggers, holding replies | S1, S3 | Mailbox live |
| S8 Finance | Reconciliation, per-order profit, stop-loss tracker, month-2 checkpoint report, weekly settlement, GST records | S1, S3 | SP-API access |
| S9 Kill Switch | Health-metric watcher, 75% trigger, pause and resume logic with root cause and veto | S3, S5, S6 | — |
| S10 Ads | Sponsored Products within ₹10,000/month cap | S8, S9 | Go-live stable |

S2 and S3 can be built now, while GST is in progress.

## Verify first

These assumptions came from the interview, not from checked sources. Confirm each one before its spec is written.

- [ ] CA: the wholesaler's premises must be added as an additional place of business on the GSTIN (S1)
- [ ] Seller Central: current Amazon India dropshipping and seller-of-record rules for invoices and packing slips (S4, S5)
- [ ] Seller Central: current India account-health metrics and their limits, to set the 75% triggers (S9)
- [ ] SP-API docs: the Messaging API's limits; Support is designed around the email relay regardless (S7)
- [ ] SP-API docs: the restricted-data approval process and timeline for buyer PII (S1, S5)
- [ ] Seller Central: which returns and refunds Amazon processes automatically, outside the D3 approval step (S7, S8)
- [ ] Wholesaler: the five D10 questions above (S4)

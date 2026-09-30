# S4 Wholesaler Interface — DRAFT

> **Status: draft.** Blocked on D10 (the wholesaler conversation) and one "Verify first" item.
> Everything marked **Open** below must be settled before this spec is built. The parts that
> don't depend on D10 are written as final.

The channel between the business and the one wholesaler (D5) who packs and ships from their own
premises under Mayank's seller account, on his invoices and labels (D6). It carries three things:
**order handoff**, **stock updates** and **dispatch confirmation**.

Serves D5, D6, D9 (under 50 products), D10, D22 (orders keep shipping during a kill-switch pause).
Depends on S2. Blocked by D10.

## Boundary with other specs

| Spec | Owns | Uses S4 for |
|---|---|---|
| S4 (this) | The channel: getting orders, invoices and labels to the wholesaler and their replies back | — |
| S5 Fulfillment | The order lifecycle: pulling orders from Amazon, dispatch deadlines, tracking, RTO | Sending each order; hearing "packed" and "dispatched" |
| S6 Catalog | Listings and stock on Amazon | Receiving stock levels and stock-outs |
| S8 Finance | Weekly settlement (D14) | Optionally showing the wholesaler their weekly statement |
| S3 Ops Lead | Alerts to Mayank | S4 raises alerts when the wholesaler is unresponsive or reports a problem |

S4 never talks to Amazon and never decides deadlines; it reports what the wholesaler did, and
S5 decides what that means.

## What stays the same whatever D10 decides

### Adapter interface

Every candidate interface implements one adapter, so S5 and S6 never know which one was chosen
(tech stack: "Adapter behind the D10 interface").

```python
class WholesalerAdapter(Protocol):
    async def send_order(self, handoff: OrderHandoff) -> None: ...
    async def cancel_order(self, amazon_order_id: str, reason: str) -> None: ...
    async def notify(self, text: str) -> None: ...  # reminders, e.g. "2 orders due by 14:00"
```

Replies from the wholesaler arrive as events, whatever the channel:

| Event | Fields | Goes to |
|---|---|---|
| `OrderAcknowledged` | order id, at | S5 (signal on the order workflow) |
| `OrderPacked` | order id, at | S5 |
| `OrderDispatched` | order id, at, tracking/AWB if self-ship | S5 |
| `OrderProblem` | order id, kind (`out_of_stock`, `damaged`, `other`), note | S5 + `warning` alert |
| `StockUpdated` | SKU → quantity (full or partial list), at | S6 |
| `StockOut` | SKU, at | S6 + `warning` alert |

### Data

- `wholesaler_skus`: our SKU ↔ wholesaler's item code, wholesaler cost price, pack size. Under 50
  rows (D9); changes go through Catalog's product proposals (approval required).
- `stock_snapshots`: last reported quantity per SKU with timestamp. S6 treats a snapshot older
  than **Open (Q4)** as stale and lowers listed quantity to a safety buffer.
- `handoffs`: one row per order sent, with document references and each event's time. Every
  event is also written to the audit log.

### Documents

Each handoff carries the GST invoice and the Amazon shipping label as PDFs (D6). They're stored in
one private AWS S3 bucket (new in this spec; a few MB a month at 500 orders), and the wholesaler
only ever gets short-lived links (24 hours), never the bucket.

### Buyer data (restricted PII)

Labels show buyer names and addresses, which is restricted data under SP-API (S1). Rules:
- The wholesaler sees only what's needed to pack and ship one order: items, quantities, the
  label and the invoice. No phone numbers or emails.
- Links expire; every document open is audited.
- Proposed: documents are deleted 30 days after delivery, or sooner if Amazon's data
  protection policy requires (checked with the restricted-data approval in S1).

### Reliability

- Handoffs are idempotent: resending an order never creates a second one at the wholesaler.
- If the wholesaler hasn't acknowledged an order within **Open (Q2)** of sending, S4 sends one
  reminder; if still nothing, it raises a `warning`, and a `critical` if the order's ship-by is at
  risk (thresholds owned by S5).
- A stock report that contradicts an order (ordered 3, stock says 0) raises `OrderProblem` before
  the order is lost.

## Open: which interface (D10)

Three candidates from the goals file, plus a hybrid worth considering:

| | A. Mobile web page | B. WhatsApp bot | C. Shared Google Sheet | D. WhatsApp + web page |
|---|---|---|---|---|
| How it works | Page served by our FastAPI app: "to pack" list, print buttons, mark packed/dispatched, edit stock | WhatsApp Business messages per order with PDFs attached; buttons to acknowledge/dispatch; stock by structured reply | Our service writes one row per order with PDF links; wholesaler edits a status column; a stock tab | WhatsApp message "3 new orders, due 14:00" with a link to page A |
| Wholesaler needs | A phone browser, a printer | WhatsApp, a printer | Google Sheets, a printer | WhatsApp + browser, a printer |
| Running cost | None beyond hosting | Meta's WhatsApp Business messaging fees (check current India pricing; expected small at this volume) | None | Same as B, fewer messages |
| Setup lead time | Days | Meta Business verification and a dedicated number (lead time varies; check before choosing) | Days | Same as B |
| Input quality | Controlled: buttons and validated fields | Medium: buttons fine, free text for stock is error-prone | Low: free edits, typos, moved rows | Controlled |
| Printing labels | Direct from page | Open PDF in WhatsApp, print | Open link, print | Direct from page |
| Notification | None unless they check the page | Push, instant | None | Push, instant |
| Build effort | Medium | Medium-high | Low-medium | High (both) |

### How the answers decide it

| Question (D10) | Answer that points to |
|---|---|
| Q1: daily smartphone use and apps | WhatsApp only → B or D. Browser comfortable → A or D. Already runs on Sheets → C |
| Q2: who handles orders, dispatch time | One person at a fixed time → A can work without push. Several people or ad-hoc → B or D (push notifications) |
| Q3: can they print our PDFs | No → blocker for every option; needs a fix in S1/S5 (e.g. Amazon-managed shipping with pickup labels) |
| Q4: how often stock changes, can they report stock-outs | Daily or faster → structured stock entry (A or D), not free-text WhatsApp |
| Q5: weekly settlement OK | Doesn't change the interface; if they want a statement, A/D can show S8's weekly summary |

**Recommendation to confirm after the conversation:** D (WhatsApp notification + mobile web page)
if they live on WhatsApp, which is common; A alone if they're comfortable checking a page at a
fixed time; C only if they already run their business on Google Sheets. B alone is the weakest
for stock updates.

## Other open items

1. **Verify first: seller-of-record rules.** Amazon India's current dropshipping and
   seller-of-record rules for invoices and packing slips (goals file, S4/S5). If Amazon forbids
   anything identifying the wholesaler in the package, the page must say "no wholesaler
   branding, invoices or slips in the box".
2. **Shipping method.** Easy Ship (Amazon's carrier picks up from the wholesaler with an
   Amazon-generated label) versus self-ship (wholesaler's courier, AWB entered in S4). This
   decides whether `OrderDispatched` needs a tracking number and changes S5. Needs checking in
   Seller Central alongside item 1.
3. **Pickup address.** Easy Ship pickups need the wholesaler's premises as the pickup address on
   the seller account; ties to the GST additional-place-of-business item in S1.
4. **Thresholds from Q2 and Q4:** acknowledgement reminder time and stock staleness limit.

## Acceptance (for the built spec)

- An order handed to S4 reaches the wholesaler with both PDFs, exactly once, even if sent twice.
- Acknowledged, packed, dispatched and problem events reach S5 within a minute of the wholesaler
  acting, and each is in the audit log.
- A stock update reaches S6; a stock-out raises a `warning` in the next brief.
- An unacknowledged order triggers one reminder, then an alert.
- Document links stop working after 24 hours, and documents are gone 30 days after delivery.
- During a kill-switch pause, handoffs keep flowing (D22).
- Swapping the adapter (e.g. A → D) needs no change in S5 or S6.

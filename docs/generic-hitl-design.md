# Generic Human-in-the-Loop (HITL) — Design Document

## 1. The Problem With the Current Approach

The current dispatch agent embeds the human review loop **inside the order domain**.
The agent calls `recommend_dispatch` → the orders table gets a `pending_review` status →
the dashboard has a dedicated "Pending Review" section with approve/reject buttons wired
to `/orders/{id}/approve`.

This works for one agent. It breaks as soon as you add a second:

| Agent | Custom table status | Custom UI section | Custom approve endpoint |
|---|---|---|---|
| order-dispatch | `pending_review` on `orders` | Pending Review table | `POST /orders/{id}/approve` |
| pharma-outreach | `pending_send` on `prospects`? | Email Review table? | `POST /prospects/{id}/send`? |
| pricing-agent | `pending_update` on `products`? | Pricing Review panel? | `POST /products/{id}/update`? |

Every new agent requires:
- A new status in a domain table
- A new UI section with custom columns
- A new API endpoint
- New JavaScript for that section

This is expensive, inconsistent, and couples the platform layer to every domain feature.

---

## 2. The Core Insight

The human review loop is **always the same shape**, regardless of the domain:

```
Agent makes a decision
  → "I want to do X"
  → Store the intent with enough context for a human to evaluate it
  → Human sees the intent, decides: Yes or No
  → If Yes: execute the action that was planned
  → If No: discard, optionally return to agent
```

The **only things that vary** between agents are:
1. What the human needs to see to make a decision (display data)
2. What API call to execute if they approve (the action)

Everything else — the inbox UI, the approve/reject flow, the lifecycle, the audit trail —
is identical and can be owned by the platform.

---

## 3. The Solution: Action Inbox

Introduce a first-class platform concept: **`AgentAction`**.

An `AgentAction` is a **stored intent with self-contained execution instructions**.
The agent writes one. The human reviews it. The platform executes it on approval.
No domain-specific code is needed anywhere in this loop.

```
                     ┌─────────────────────────────────┐
                     │       agent_actions table        │
                     │                                  │
                     │  title: "Dispatch ORD-001 AIR"  │
                     │  display_data: [{label, value}]  │
                     │  approval_action: {method, url,  │
                     │                    body}         │
                     │  status: pending_review          │
                     └──────────────┬──────────────────┘
                                    │
           ┌────────────────────────┼────────────────────────┐
           │                        │                        │
    Agent writes             Human reviews          Platform executes
    propose_action()         /approvals UI          approval_action
    (tool call)              (generic page)         (HTTP call, server-side)
```

---

## 4. Data Model: `agent_actions`

```
agent_actions
  id                UUID PK

  ── Provenance ──────────────────────────────
  agent_name        str               "order-dispatch-review"
  agent_run_id      UUID FK           links to agent_runs for trace/audit

  ── What to show the human ──────────────────
  title             str               "Dispatch ORD-001 via AIR"
  summary           str               "MedCorp · $14.2k · due in 2 days"
  reasoning         text              AI's full justification
  confidence        str               high | medium | low
  display_data      JSONB             [{"label": "Order Ref", "value": "ORD-001"}, ...]
  tags              JSONB             ["dispatch", "urgent", "pharma"]

  ── What to execute on approval ─────────────
  approval_action   JSONB             {method, url, body}
  rejection_action  JSONB (optional)  {method, url, body}  — e.g. log, notify

  ── Lifecycle ───────────────────────────────
  status            str               pending_review | approved | rejected
                                      | approval_failed | expired
  decided_by        str | null        analyst username / "system"
  decided_at        datetime | null
  decision_note     str | null        human's optional comment
  override_body     JSONB | null      human overrides some fields of approval_action.body
  expires_at        datetime | null   stale actions auto-expire

  created_at        datetime
  updated_at        datetime
```

### The `approval_action` schema

```json
{
  "method": "PATCH",
  "url": "/api/v1/orders/{order_id}/dispatch",
  "url_params": { "order_id": "3fa85f64-..." },
  "body": { "mode": "air", "decided_by": "ai" }
}
```

The platform resolves `url_params` into the URL and merges `override_body` (if provided
by the human) on top of `body` before executing the call. No custom code needed.

---

## 5. The Platform Tool: `propose_action`

A **single generic tool** replaces all domain-specific recommend tools
(`recommend_dispatch`, `recommend_email_send`, etc.).

The agent calls it with everything needed to display and execute the action:

```python
propose_action(
    title="Dispatch ORD-001 via AIR",
    summary="MedCorp · $14,200 · due in 2 days",
    reasoning="urgency_days=2, amount=$14,200 → Rule 1: air. Margin 28% confirms upgrade.",
    confidence="high",
    display_data=[
        {"label": "Order Ref",        "value": "ORD-001"},
        {"label": "Retailer",         "value": "MedCorp Pharmaceuticals"},
        {"label": "Amount",           "value": "$14,200"},
        {"label": "Recommended Mode", "value": "AIR ✈"},
        {"label": "Due In",           "value": "2 days (CRITICAL)"},
        {"label": "Margin",           "value": "28% — High"},
    ],
    approval_action={
        "method": "PATCH",
        "url": "/api/v1/orders/{order_id}/dispatch",
        "url_params": {"order_id": "3fa85f64-..."},
        "body": {"mode": "air", "decided_by": "ai"}
    }
)
```

For the email outreach agent, the same tool, different data:

```python
propose_action(
    title="Send partnership email to Apollo Pharma (Bangalore)",
    summary="₹480k revenue · Distributor · Matched campaign: Fundly Partnership Outreach",
    reasoning="Revenue exceeds ₹400k threshold. Distributor type qualifies. Region matches.",
    confidence="medium",
    display_data=[
        {"label": "Company",   "value": "Apollo Pharma Pvt Ltd"},
        {"label": "Revenue",   "value": "₹480k"},
        {"label": "Type",      "value": "Distributor"},
        {"label": "Region",    "value": "Bangalore"},
        {"label": "Email",     "value": "procurement@apollo-pharma.in"},
        {"label": "Template",  "value": "Partnership Outreach v2"},
    ],
    approval_action={
        "method": "POST",
        "url": "/api/v1/outreach/send-email",
        "body": {
            "prospect_id": "uuid-here",
            "template": "partnership_v2",
            "sender": "The Fundly Team"
        }
    }
)
```

The platform does not know or care what domain this action belongs to. It stores it,
displays it, and executes it. Same code path for every agent.

---

## 6. The Generic UI: `/approvals`

A single page that renders all `AgentAction` records in `pending_review` status
across all agents. The UI is driven entirely by the `display_data` field — no
agent-specific templates or JavaScript.

```
┌─────────────────────────────────────────────────────────────────────────┐
│  Action Inbox              7 pending · [All agents ▾] [Confidence ▾]   │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  ┌───────────────────────────────────────────────────────────────────┐  │
│  │  🤖 order-dispatch-review  ·  HIGH  ·  2 min ago                 │  │
│  │                                                                   │  │
│  │  Dispatch ORD-001 via AIR                                         │  │
│  │  MedCorp · $14,200 · due in 2 days                               │  │
│  │                                                                   │  │
│  │  ┌─────────────────────────────────────────────────────────────┐  │  │
│  │  │  Order Ref       │ ORD-001                                  │  │  │
│  │  │  Retailer        │ MedCorp Pharmaceuticals                  │  │  │
│  │  │  Amount          │ $14,200                                  │  │  │
│  │  │  Recommended     │ AIR ✈                                    │  │  │
│  │  │  Due In          │ 2 days (CRITICAL)                        │  │  │
│  │  │  Margin          │ 28% — High                               │  │  │
│  │  └─────────────────────────────────────────────────────────────┘  │  │
│  │                                                                   │  │
│  │  Reasoning: urgency_days=2, amount=$14,200 → Rule 1: air.        │  │
│  │  Margin 28% confirms upgrade.                                    │  │
│  │                                                                   │  │
│  │  [✓ Approve]  [✗ Reject]  [override body ▾]  [📝 note]          │  │
│  └───────────────────────────────────────────────────────────────────┘  │
│                                                                         │
│  ┌───────────────────────────────────────────────────────────────────┐  │
│  │  🤖 pharma-outreach  ·  MEDIUM  ·  5 min ago                     │  │
│  │  Send partnership email to Apollo Pharma (Bangalore)             │  │
│  │  ₹480k revenue · Distributor · Region: Bangalore                 │  │
│  │  ...                                                             │  │
│  └───────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────┘
```

The UI loops over `pending_review` actions and renders each with:
- Header: agent name, confidence badge, time ago
- Title + summary line
- `display_data` as a two-column key-value table
- Reasoning paragraph
- Approve / Reject / (optional override) / Note

**The Approve button executes `POST /api/v1/actions/{id}/approve`.**

The platform endpoint:
1. Reads `approval_action` from the record
2. Resolves URL params, merges override_body if provided
3. Calls the stored API (e.g. `PATCH /orders/{id}/dispatch`) — server-side HTTP
4. If the call succeeds: marks action `approved`
5. If the call fails: marks action `approval_failed`, returns error to UI

No JavaScript per domain. No custom template per agent. The action executes itself.

---

## 7. How This Changes Agent Design

### Before (domain-coupled)

```
Agent YAML tools:
  - recommend_dispatch      ← domain-specific
  - dispatch_order          ← domain-specific

Agent logic:
  human_in_the_loop=true  → call recommend_dispatch(order_id, mode, confidence, reasoning)
  human_in_the_loop=false → call dispatch_order(order_id, mode, reasoning)
```

Domain table carries the state. UI is purpose-built per agent.

### After (platform-generic)

```
Agent YAML tools:
  - propose_action          ← platform tool, same for all agents
  - dispatch_order          ← domain tool, for direct execution only

Agent logic:
  human_in_the_loop=true  → call propose_action(title, summary, display_data, approval_action)
  human_in_the_loop=false → call dispatch_order(order_id, mode, reasoning)
```

The domain API (`dispatch_order`'s underlying endpoint) is called the same way in both
cases — directly in Mode 3, via platform execution on approval in Mode 2.

**The "same API" principle is preserved.** The approval path and the direct path both
call `PATCH /api/v1/orders/{id}/dispatch`. The only difference is whether a human
reviewed the intent first.

---

## 8. Override Support

The `/approvals` UI supports an optional override before approving. This is handled
generically via `override_body` — the human can modify any field of `approval_action.body`
without agent-specific code.

For the dispatch case:
- AI proposed: `mode: "air"`
- Human disagrees, selects "train" from a rendered dropdown (built from the body fields)
- `POST /api/v1/actions/{id}/approve` with `override_body: {"mode": "train"}`
- Platform merges override into body: `{"mode": "train", "decided_by": "ai"}`
- Calls `PATCH /orders/{id}/dispatch` with the merged body

The UI can render override controls automatically from the `approval_action.body` schema —
string fields become text inputs, enum fields (if annotated) become dropdowns. This
requires a small `body_schema` optional field in `approval_action`:

```json
"body_schema": {
  "mode": {"type": "enum", "options": ["air", "train", "road"], "label": "Override mode"}
}
```

---

## 9. Lifecycle and State Machine

```
                     Agent calls propose_action()
                              │
                              ▼
                       pending_review  ◄──── default, shown in inbox
                              │
                ┌─────────────┴──────────────┐
                │                            │
         Human approves                Human rejects
                │                            │
                ▼                            ▼
       Platform calls                   rejected
       approval_action                       │
          API (HTTP)               (optional) rejection_action
                │                       API called
          ┌─────┴──────┐
          │            │
       success       failure
          │            │
       approved   approval_failed
                  (shown in inbox
                   with error msg,
                   human can retry)

       expires_at passes without decision
                   → expired
```

---

## 10. API: `/api/v1/actions`

| Method | Path | What it does |
|---|---|---|
| `GET` | `/actions` | List actions. Filter by `status`, `agent_name`, `confidence`, `agent_run_id` |
| `GET` | `/actions/{id}` | Get single action with full detail |
| `POST` | `/actions/{id}/approve` | Approve: execute `approval_action`, mark `approved` |
| `POST` | `/actions/{id}/reject` | Reject: optionally execute `rejection_action`, mark `rejected` |
| `POST` | `/actions/{id}/retry` | Retry a failed `approval_action` execution |
| `GET` | `/actions/counts` | Summary counts by agent and status — for dashboard badges |

The `POST /actions/{id}/approve` body:
```json
{
  "override_body": { "mode": "train" },
  "note": "Changed to train — air cost not justified at this margin"
}
```

---

## 11. Impact on Existing Agents

### Order Dispatch Agent

- Replace `recommend_dispatch` tool with `propose_action` tool
- Update system prompt: instead of calling `recommend_dispatch(order_id, mode, ...)`,
  call `propose_action(title, display_data, approval_action={PATCH /orders/{id}/dispatch})`
- Remove the `pending_review` status from `orders` table (no longer needed — the action
  inbox owns the review state)
- The dashboard's "Pending Review" section is removed; human reviews from `/approvals`
- The `POST /orders/{id}/approve` and `POST /orders/{id}/recommend` endpoints can be
  deprecated over time

### Pharma Outreach Email Agent

- The current `send_email` tool sends directly
- With HITL: add `propose_action` with `approval_action = POST /outreach/send-email`
- No outreach-specific UI needed
- The same `/approvals` inbox handles email approvals alongside dispatch approvals

### Any Future Agent

- Agent developer only writes: the direct-action tool (for automation mode) and calls
  `propose_action` (for review mode) with the correct `approval_action`
- Zero platform UI work needed

---

## 12. What Stays Domain-Specific

The goal is not to eliminate domain code — it is to contain it to the right layer:

| Layer | What stays domain-specific | What becomes generic |
|---|---|---|
| Domain APIs | Business logic, validation, state transitions | Nothing — APIs are unchanged |
| Agent tools | Direct action tools (dispatch_order, send_email) | propose_action replaces all recommend_* tools |
| Agent YAML | System prompt, tool list, guardrails | human_in_the_loop flag and how it flows |
| Platform | Action execution engine | Replaces per-agent approve/reject endpoints |
| UI | Domain dashboards (orders list, etc.) | /approvals inbox replaces all review sections |

The domain table (orders, prospects) no longer carries review state. The `agent_actions`
table owns the lifecycle of every pending human decision, regardless of domain.

---

## 13. Implementation Sequence

When this is built (in a follow-up), the sequence is:

```
Step 1 — Data layer
  AgentAction model + Alembic migration
  /api/v1/actions CRUD endpoints
  Platform-side execution engine (approve → call approval_action HTTP)

Step 2 — Platform tool
  propose_action() tool in tools/platform.py
  Register in _TOOL_REGISTRY
  Unit test: propose_action creates the right record

Step 3 — Generic UI (/approvals)
  Jinja2 template driven by display_data (no domain logic)
  Approve/reject with optional override_body
  Filter by agent_name, confidence, date

Step 4 — Migrate dispatch agent
  Replace recommend_dispatch with propose_action in system prompt + tools
  Remove pending_review from orders table (migration)
  Remove /orders/recommend and /orders/approve endpoints
  Remove "Pending Review" section from dashboard.html

Step 5 — Validate with email agent
  Add human_in_the_loop path to pharma-outreach using propose_action
  Confirm /approvals renders email review without any new UI code
```

---

## 14. Summary

The current approach is **domain-coupled** — each agent brings its own review UI.
The proposed approach is **platform-owned** — agents write self-describing intents,
the platform owns the review lifecycle, and one generic UI serves all agents.

The key move is from:
> *"Agent recommends X; domain endpoint stores it; custom UI shows it; custom endpoint approves it"*

to:

> *"Agent proposes an action; action stores its own execution instructions; platform
> executes them on approval; generic UI renders any action without custom code"*

The "same API" principle from the dispatch demo extends naturally: the AI agent in
full-automation mode calls `PATCH /orders/{id}/dispatch` directly. The AI agent in
review mode calls `propose_action` with `approval_action = PATCH /orders/{id}/dispatch`.
A human approves. The platform calls `PATCH /orders/{id}/dispatch`.
**Same API. Same result. Same audit trail. Zero extra UI work.**

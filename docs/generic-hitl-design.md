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

## 14. Stale Action Problem — Analysis and Solution Design

### 14.1 The Problem

An `AgentAction` is created at a point in time based on the agent's observation of the
world. Between that moment and the moment a human reviews it, the real-world state may
have changed. The action can become **stale, invalid, or actively harmful** if executed.

This is not an edge case — in any busy system with concurrent users and automated agents,
the window between action creation and human review will routinely see state changes.

---

### 14.2 Complete Failure Case Taxonomy

#### Case 1 — Direct Resource Conflict (Same Action Already Performed)

The most common case. A staff member performs the exact action the agent proposed, before
the human reviewer approves it.

```
Agent run at 09:00: proposes "Dispatch Order #1 via AIR"
09:30: warehouse staff dispatches Order #1 via ROAD manually from the Orders page
10:00: reviewer opens inbox, sees the agent card, clicks Approve
10:00: platform calls PATCH /orders/1/dispatch → Order is already dispatched
Result: API error (409), or worse — order gets dispatched twice in some systems
```

**Risk level: HIGH.** Causes immediate operational errors or double-execution.

---

#### Case 2 — Reasoning Invalidated (State Drifted, Action Still Executes)

The resource changes in a way that makes the agent's reasoning wrong, but the action
itself can still technically execute. This is more dangerous than Case 1 because there
is no error — the system accepts the action, but the business decision is wrong.

```
Agent run at 09:00: "Dispatch Order #1 via AIR — urgency_days=2, critical"
09:15: customer requests due date extension → Order #1 due_date updated, urgency now 9 days
10:00: reviewer reads "due in 2 days, CRITICAL", trusts agent, clicks Approve
10:00: platform dispatches via AIR — succeeds, no error
Result: unnecessary $800 air freight instead of $120 road, based on stale urgency
```

**Risk level: HIGH.** No error is raised. The human makes a wrong decision based on
information the agent believed to be true but is no longer accurate.

---

#### Case 3 — Resource Deleted or Cancelled

The resource the action targets no longer exists or has moved to a terminal state.

```
Agent run at 09:00: proposes "Dispatch Order #1 via AIR"
09:20: customer cancels Order #1 → order status = cancelled
10:00: reviewer approves
10:00: platform calls PATCH /orders/1/dispatch → 404 or 422
Result: action goes to approval_failed with a confusing error
The reviewer has no context to understand why it failed
```

**Risk level: MEDIUM.** Action fails harmlessly, but the reviewer experience is poor
and the failed action pollutes the audit trail without clear explanation.

---

#### Case 4 — Duplicate Agent Actions (Re-Run Before Review)

The agent is re-triggered (manually or on schedule) before the previous run's actions
are reviewed. Multiple pending actions now target the same resource.

```
Run 1 at 09:00: proposes "Dispatch Order #1 via AIR" (pending_review)
Staff manually re-runs the agent at 09:30 (Order #1 still pending in DB)
Run 2 at 09:30: proposes "Dispatch Order #1 via TRAIN" (pending_review)
Reviewer sees two cards for Order #1 in the inbox
Reviewer approves AIR card at 10:00 → dispatched via AIR
Reviewer approves TRAIN card at 10:05 → conflict or re-dispatch
```

**Risk level: HIGH.** Creates inbox clutter and conflicting approvals. The second
approval either errors or silently overrides the first decision.

---

#### Case 5 — Race Condition (Concurrent Human Approvals)

Two reviewers see the same pending action simultaneously and both click Approve.

```
Reviewer A opens inbox — sees "Dispatch Order #1 via AIR" card
Reviewer B opens inbox — same card (both in pending_review)
Both click Approve within 500ms of each other
Both requests hit the platform approval engine concurrently
Both call PATCH /orders/1/dispatch
```

**Risk level: MEDIUM** (lower in practice with small review teams, but rises with scale).
Without optimistic locking, both can succeed — first write wins but no error raised for
the second, depending on how idempotent the domain endpoint is.

> **Out of scope for this deployment.** This system assumes a single human reviewer
> at a time. Case 5 is documented for completeness and future reference when the
> platform scales to multi-reviewer teams.

---

#### Case 6 — Temporal Expiry Without Contextual Awareness

The action sits in the inbox past the point where it is meaningful, even if the resource
has not been touched.

```
Agent proposes "Dispatch Order #1 via AIR — due in 1 day"
Inbox is not reviewed for 2 days (reviewer on leave, notifications missed)
Reviewer returns, sees the card, approves it
Order is 1 day overdue — dispatching now may violate SLA
The system has no knowledge that this action has become nonsensical
```

**Risk level: MEDIUM.** Action executes correctly but at the wrong time, potentially
breaking SLA commitments or downstream logistics planning.

---

#### Case 7 — Approval Failure Followed by Stale Retry

The first approval attempt fails (network, API down, transient error). The resource
state changes during the window before the human retries.

```
10:00: Reviewer approves "Dispatch Order #1 via AIR"
10:00: HTTP call fails (API timeout) → status = approval_failed
10:05: Staff manually dispatches Order #1 via ROAD
10:10: Reviewer sees "approval_failed" card, clicks Retry
10:10: Platform calls PATCH /orders/1/dispatch → conflict
```

**Risk level: MEDIUM.** The retry path has no awareness that the world changed between
the first attempt and the retry.

---

#### Case 8 — Cross-Agent Conflict (Two Agents, Same Resource)

Two different agents independently propose conflicting actions on the same resource.

```
Order dispatch agent: proposes "Dispatch Order #1 via AIR" (pending_review)
Credit check agent: proposes "Hold Order #1 — credit limit exceeded" (pending_review)
Reviewer A approves dispatch → Order #1 dispatched
Reviewer B approves hold → tries to hold an already-dispatched order → error
```

**Risk level: HIGH** in complex systems. Agent actions have no awareness of each other.
Both appear in the inbox as independent cards with no indication they target the same
resource.

---

#### Case 9 — Silent Wrong Outcome (No Error, Wrong Business Effect)

The approval executes without any error, but produces a wrong business outcome because
the action was not idempotent and the side effect compounds with something already done.

```
Pharma outreach agent: proposes "Send email to Kohinoor Pharma"
Sales rep manually emails Kohinoor Pharma from their email client
Reviewer approves agent action → email sent to Kohinoor Pharma again
No API error — two emails delivered, customer receives duplicates
Result: damaged relationship, zero technical indication anything went wrong
```

**Risk level: HIGH** for non-idempotent actions (emails, SMS, payment triggers).
No failure signal of any kind — the harm is purely at the business layer.

---

### 14.3 Design Principle: Action Dependency Classification

Not all `AgentAction` records carry the same staleness risk. Before applying any
protection mechanism, classify each action by its dependency on external state:

| Category | Description | Drift risk | Examples |
|---|---|---|---|
| **Resource-independent** | Action has no dependency on current resource state | None | Send a pre-generated report, trigger a webhook with static payload |
| **Resource-dependent** | Action targets a specific resource whose state may change | HIGH | Dispatch an order, update a pricing record, hold an account |

**Resource-dependent actions are the only category that needs staleness protection.**
An action that sends a pre-composed email has no state to drift. An action that dispatches
an order depends on the order still being in `pending` status — that can change at any time.

The agent knows which category applies because the feature flags in its YAML are explicit
about it. When neither flag is set, the action is implicitly treated as resource-independent.

---

### 14.4 Solution: Two Feature Flags

Rather than building infrastructure that touches every domain endpoint, the solution is
**per-agent YAML configuration**. Each agent declares its own staleness contract. The
platform enforces it at approval time. No domain code is modified.

Two flags are introduced under `feature_flags` in each agent's YAML:

---

#### Flag 1 — `stale_after`

**Purpose:** Automatically retire actions that have sat in the inbox longer than makes
business sense, before a reviewer ever sees or acts on them.

**Format:** A duration string — `"30m"`, `"2h"`, `"1d"`. Omit the flag entirely if
the action has no time-based expiry requirement.

**Behavior — auto-mark at inbox load (API-level):**

The enforcement happens server-side when the inbox is fetched (`GET /api/v1/actions`),
not at approval click. This means stale actions are retired silently and automatically —
no user dialog, no manual step.

```
Reviewer opens inbox → GET /api/v1/actions?status=pending_review
  Platform runs before returning results:
    SELECT * FROM agent_actions
    WHERE status = 'pending_review'
      AND stale_after_seconds IS NOT NULL
      AND created_at + stale_after_seconds < NOW()
    → Found 2 actions past their window
    → UPDATE status = 'stale', stale_marked_at = NOW()
  Response: { "actions": [...], "auto_staled_count": 2 }
```

The active inbox never shows the stale actions — they were already transitioned before
the page rendered. The UI shows a dismissible notice if any were auto-staled:

```
ℹ 2 actions were automatically marked stale (past their review window)
  and moved to History.  [View History]
```

Stale records are available under the History filter with reason:
`"Automatically marked stale — exceeded 4h review window (age: 6h 14m)"`

**Age indicator chips (proactive warning while the action is still active):**

Since auto-marking only fires on inbox load, active cards show an age indicator so the
reviewer knows an action is approaching its window before it disappears on the next load:

- ≤ 50% of `stale_after` window elapsed → no indicator
- 50–90% elapsed → amber `⏱ Xh Ym remaining`
- > 90% elapsed → red `⚠ Expires soon`

This gives reviewers advance notice and encourages timely review rather than silent
disappearance as a surprise.

---

#### Flag 2 — `track_resource_state`

**Purpose:** Detect state drift between when the agent made its proposal and when the
human tries to approve it. Prevents the human from approving a decision based on
information that is no longer true.

**Format:** A list of field names to snapshot, or `true` to use a default set.
Set alongside `resource_check_url` — the endpoint the platform will call to re-fetch
the resource at approval time.

```yaml
feature_flags:
  human_in_the_loop: true
  track_resource_state:
    fields: ["status", "shipment_mode", "due_date", "urgency_days"]
    check_url: "/api/v1/orders/{resource_id}"
```

**How it works:**

*At propose time (agent side):*
The system prompt (written for this flag) instructs the agent to capture the current
values of the tracked fields from the resource it just fetched and include them as
`expected_state` in the `propose_action` call:

```python
propose_action(
    title="Dispatch ORD-001 via AIR",
    ...
    expected_state={
        "resource_id": "uuid-of-order",
        "status": "pending",
        "shipment_mode": None,
        "due_date": "2026-07-05",
        "urgency_days": 2
    }
)
```

*At approval time (platform side):*
1. Platform reads `expected_state` from the `AgentAction` record
2. Platform calls `check_url` (e.g. `GET /api/v1/orders/{resource_id}`) to fetch current state
3. Compares current values of the tracked fields against `expected_state`
4. Decision:

**No drift** → proceed to execute the approval action as normal.

**Drift detected** → block the approval and show a drift panel:

```
⚠ Resource state has changed since this action was proposed

  Field          When Proposed        Now
  ─────────────────────────────────────────────────────
  status         pending              dispatched ← changed
  shipment_mode  —                    road ← changed
  due_date       2026-07-05           2026-07-05
  urgency_days   2                    2

  The order was dispatched via ROAD while this action was awaiting review.
  Proceeding may cause a conflict.

  [Mark as Drifted]   [Approve Anyway]   [Cancel]
```

- **Mark as Drifted**: transitions record to `drifted` status, removes from active inbox,
  stores the diff in `drift_details`. History filter shows it with the full diff.
- **Approve Anyway**: human explicitly overrides the drift warning and proceeds with approval.
  The override is recorded in the audit trail (`drift_override: true`, `drift_details` preserved).
- **Cancel**: nothing happens, card stays in inbox.

The `Approve Anyway` path is intentional — there are cases where drift is cosmetic
(e.g. urgency_days changed from 2 to 1 — even more urgent, air is still correct) and
the human is the right person to make that call.

---

### 14.5 YAML Configuration

**order-dispatch-review agent** (resource-dependent, time-sensitive):

```yaml
feature_flags:
  human_in_the_loop: true
  stale_after: "4h"                      # orders are time-critical; 4h is the safety window
  track_resource_state:
    fields: ["status", "shipment_mode", "due_date", "urgency_days"]
    check_url: "/api/v1/orders/{resource_id}"
```

**pharma-outreach agent** (resource-independent if email is pre-composed):

```yaml
feature_flags:
  human_in_the_loop: true
  stale_after: "2d"                      # outreach window is wider; 2 days is reasonable
  # track_resource_state not set — email content doesn't depend on mutable resource state
```

**A future payment-hold agent** (high-stakes, tight window):

```yaml
feature_flags:
  human_in_the_loop: true
  stale_after: "1h"
  track_resource_state:
    fields: ["status", "balance_usd", "credit_limit_usd"]
    check_url: "/api/v1/accounts/{resource_id}"
```

---

### 14.6 System Prompt Requirements

When `track_resource_state` is set, the system prompt must instruct the agent to:

1. Capture the tracked fields from the resource it already fetched (no extra API call needed)
2. Include them as `expected_state` in the `propose_action` call

Example system prompt section added when the flag is on:

```
[Feature flags]
...
track_resource_state:
  fields: ["status", "shipment_mode", "due_date", "urgency_days"]
  check_url: "/api/v1/orders/{resource_id}"

When track_resource_state is configured:
- After fetching order details and before calling propose_action, capture the current
  values of the tracked fields from the order you just read.
- Pass these as expected_state in the propose_action call. Example:
    expected_state={
        "resource_id": "<order UUID>",
        "status": "pending",
        "shipment_mode": null,
        "due_date": "2026-07-05",
        "urgency_days": 2
    }
- This snapshot is stored with the action so the platform can detect state drift
  before a human approves it. Do not skip this — it protects the reviewer.
```

The agent does not need to understand why it is providing this data — it just follows
the instruction in the feature flags block. The platform owns the validation logic.

---

### 14.7 Data Model Changes

New and changed fields on `agent_actions`:

```
  ── Resource state tracking ─────────────────────────────
  expected_state      JSONB | None      Snapshot captured by agent at propose time.
                                        {resource_id, field: value, ...}
                                        Populated when track_resource_state is configured.

  ── Staleness / drift outcomes ──────────────────────────
  status              str               Extended with two new terminal states:
                                          "stale"   — exceeded stale_after window,
                                                       auto-marked at inbox load
                                          "drifted" — resource drift detected,
                                                       human acknowledged and dismissed
  stale_after_seconds int | None        Parsed from YAML stale_after at propose time.
                                        Stored so enforcement is self-contained in the
                                        record — no YAML lookup needed at query time.
  stale_marked_at     datetime | None   When the platform auto-marked the action stale
                                        (set by GET /actions inbox-load enforcement).
  drift_detected_at   datetime | None   When drift was first detected on an approval attempt
  drift_details       JSONB | None      Full diff: {field: {expected, actual}, ...}
  drift_override      bool              True if human clicked "Approve Anyway" despite drift
```

**Updated lifecycle:**

```
                     Agent calls propose_action()
                              │
                              ▼
                       pending_review  ◄──── shown in active inbox
                              │
          ┌───────────────────┼───────────────────────┐
          │                   │                       │
   Human approves      Human rejects       Inbox loads + stale_after exceeded
          │                   │            (automatic, no human action needed)
          ▼                   ▼                       │
  Platform executes       rejected                  stale
  approval_action     (human decision)        (platform auto-marks,
     API (HTTP)                                hidden from inbox,
     ┌────┴────┐                               shown in History)
  success    failure
     │          │             Human clicks "Mark as Drifted"
  approved  approval_failed   (after seeing drift panel at approval click)
            (retry available)         │
                                   drifted
                              (human decision,
                               diff stored,
                               hidden from inbox,
                               shown in History)
```

All non-active statuses (`stale`, `drifted`, `rejected`, `approved`, `approval_failed`)
are hidden from the default inbox view and available under a **History** filter.

---

### 14.8 Coverage Against Failure Cases

| Case | `stale_after` | `track_resource_state` | Notes |
|---|:---:|:---:|---|
| 1 — Action already performed (conflict) | ✅ Reduces window | ✅ Detects at approval | |
| 2 — Reasoning stale (state drifted, no error) | | ✅ Primary | |
| 3 — Resource deleted / cancelled | | ✅ Detects missing resource | |
| 4 — Duplicate actions from re-run | | | Accepted gap — manageable at current scale |
| 5 — Concurrent reviewers | — | — | Out of scope — single-reviewer deployment |
| 6 — Temporal expiry | ✅ Auto-marks at inbox load | | |
| 7 — Failed retry + state changed | | ✅ Re-checks on retry | |
| 8 — Cross-agent conflict | | ✅ Second approver blocked | |
| 9 — Silent non-idempotent repeat (e.g. email) | ✅ Reduces window | | Accepted gap — partial coverage |

**8 of the 8 applicable cases addressed** (Case 5 is out of scope for single-reviewer
deployments). One accepted gap remains:
- **Case 4** (duplicate inbox cards from re-runs) — manageable manually at current team
  size; can be addressed with a dedup-on-create policy when the need arises

---

### 14.9 Implementation Sequence

```
Phase 1 — Data and enforcement
  Add new fields to agent_actions:
    expected_state, stale_after_seconds, stale_marked_at,
    drift_detected_at, drift_details, drift_override
  Add new status values: "stale", "drifted"
  Alembic migration
  Update propose_action tool: accept expected_state and stale_after_seconds params,
    store them on the AgentAction record
  Update GET /actions (inbox load):
    before returning results, find all pending_review records where
    stale_after_seconds IS NOT NULL AND created_at + stale_after_seconds < NOW()
    → mark them stale (status='stale', stale_marked_at=NOW()) in a single UPDATE
    → include auto_staled_count in the response body
  Add drift check to POST /actions/{id}/approve:
    if expected_state is set → call check_url, compare tracked fields,
    if any field differs → return structured drift response (not a hard failure)
    human sees diff panel and decides: Mark as Drifted / Approve Anyway / Cancel
  New endpoint: POST /actions/{id}/mark-drifted
    accepts drift_details body, transitions to drifted, stores diff

Phase 2 — Agent YAML and system prompts
  Add stale_after and track_resource_state flags to order-dispatch-review.yaml
  Update system prompt: instruct agent to capture expected_state when flag is set
  Add stale_after to pharma-outreach.yaml (no track_resource_state — email is static)
  Validate: run agent, propose an action, manually change the order, try to approve →
    drift panel should appear with expected vs current diff

Phase 3 — Inbox UI
  Add age indicator chips to action cards (amber / red based on stale_after progress)
  On inbox load: if auto_staled_count > 0, show dismissible notice banner
    "N actions were automatically marked stale and moved to History"
  Handle drift response from approval endpoint:
    show diff panel with three choices — Mark as Drifted / Approve Anyway / Cancel
  History filter: show stale + drifted records with reason/diff
  Active inbox: stale + drifted records already excluded server-side; no client filtering needed
```

---

## 15. Summary

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

### Staleness Protection Design

The stale action problem is solved by two optional per-agent feature flags:

| Flag | What it solves | Enforcement point | Human action required |
|---|---|---|---|
| `stale_after` | Actions past their useful review window | **Inbox load (automatic)** — platform auto-marks before page renders | None — action silently moves to History; dismissible notice shown |
| `track_resource_state` | Resource changed while action awaited review | **Approval click** — platform re-checks state before executing | Human sees diff, chooses: Mark as Drifted / Approve Anyway / Cancel |

Both flags are declared in agent YAML. The agent includes the snapshot in `propose_action`
when instructed by its system prompt (driven by the flag).

`stale_after` requires no human interaction — expired actions retire themselves. The
reviewer only sees active, within-window actions when they open the inbox.

`track_resource_state` keeps the human in the loop — the platform detects drift but
the human decides whether it matters. An override is explicit and recorded in the audit trail.

**No domain endpoint changes. No background jobs. No infrastructure beyond what the
HITL system already provides.** Applicable to 8 of the 8 in-scope failure cases,
with one known gap (Case 4 — duplicate cards from agent re-runs) accepted for now.

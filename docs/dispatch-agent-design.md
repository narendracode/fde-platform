# Order Dispatch Agent — Design & Implementation Plan

## 1. What We Are Building and Why

A pharma distributor's analyst manually reviews pending orders every day and
decides which shipment mode (air / train / road) to use for each one. The
decision is based on three variables: **order amount**, **margin**, and
**due date**. This is mechanical, rule-based work that takes time and scales
poorly as order volume grows.

The goal of this POC is to demonstrate to a customer that:

> *The same API that a human calls from a UI can be called by an AI agent — and
> the transition from human → AI can be controlled incrementally, with full
> audit trail at every step.*

---

## 2. The Three Operating Modes

This is the central concept of the demo. The system supports three modes,
controlled by two feature flags. Showing all three modes live is the demo itself.

```
MODE 1 — Manual (baseline)
  Human analyst opens UI
  Reviews each order manually
  Selects mode from dropdown, clicks Dispatch
  → POST /api/v1/orders/{id}/dispatch
  Order marked ready_to_dispatch

MODE 2 — AI Assisted with Human Review  (human_in_the_loop = true)
  Analyst clicks "Run AI Agent"
  Agent analyses all pending orders
  Recommends a mode for each with reasoning
  → Recommendations stored, orders move to "pending_review"
  Analyst sees recommendations in UI, reviews and approves/rejects
  → POST /api/v1/orders/{id}/approve  (or reject → back to pending)
  Order marked ready_to_dispatch

MODE 3 — Full Automation  (human_in_the_loop = false)
  Analyst clicks "Run AI Agent"
  Agent analyses all pending orders
  Dispatches each order directly
  → POST /api/v1/orders/{id}/dispatch  (same endpoint as Mode 1)
  Order marked ready_to_dispatch — no human step needed
```

The `ai_automation_enabled` flag in the UI controls whether the "Run AI Agent"
button is visible. The `human_in_the_loop` flag in the agent YAML controls what
the agent does once it runs.

---

## 3. The "Same API" Principle — The Core of the Demo

This is the architectural insight that makes the demo compelling:

```
                    ┌─────────────────────────────────────────────┐
                    │         Orders API  /api/v1/orders          │
                    │                                             │
                    │   PATCH /{id}/dispatch                      │
                    │   PATCH /{id}/recommend                     │
                    │   POST  /{id}/approve                       │
                    └──────────────┬──────────────────────────────┘
                                   │
                  ┌────────────────┴─────────────────┐
                  │                                   │
          ┌───────▼──────────┐             ┌──────────▼───────────┐
          │   Human UI       │             │   AI Agent Tools     │
          │                  │             │                      │
          │  Analyst fills   │             │  dispatch_order()    │
          │  form, clicks    │             │  recommend_dispatch()│
          │  "Dispatch"      │             │  approve_order()     │
          └──────────────────┘             └──────────────────────┘
```

The AI and human are interchangeable at the API layer. The API does not know
or care which path called it — it just enforces business rules and writes to
the database. The audit trail captures `decided_by: human` or `decided_by: ai`
so every dispatch decision is traceable regardless of source.

---

## 4. Data Model

### New table: `orders`

```
orders
  id                  UUID PK
  order_ref           string (e.g. "ORD-2026-0042") — human-readable
  retailer_name       string
  medicine_name       string
  quantity            integer
  unit_price_usd      float
  order_amount_usd    float  (quantity × unit_price)
  margin_percent      float  (distributor's margin on this order)
  due_date            date   (when retailer needs delivery by)
  urgency_days        integer (computed: due_date - today)

  status              enum: pending | pending_review | ready_to_dispatch | dispatched
  shipment_mode       enum: air | train | road | null
  decided_by          enum: human | ai | null
  ai_confidence       string: high | medium | low | null
  ai_reasoning        text   (AI's explanation — shown to human in Mode 2)
  ai_recommended_mode enum: air | train | road | null  (stored before human approves)

  agent_run_id        UUID FK → agent_runs (which agent run made this decision)
  dispatched_at       timestamp
  created_at          timestamp
  updated_at          timestamp
```

### Status machine

```
pending
  │
  ├─── human selects mode ──────────────────────► ready_to_dispatch
  │
  ├─── AI (human_in_the_loop=false) dispatches ► ready_to_dispatch
  │
  └─── AI (human_in_the_loop=true) recommends ─► pending_review
                                                       │
                                  human approves ──────►  ready_to_dispatch
                                  human rejects  ──────►  pending  (back to start)

ready_to_dispatch
  └─── logistics picks up ──────────────────────► dispatched
```

### No new tables needed for agents or runs

The existing `agents` and `agent_runs` tables capture everything about the AI
execution. The `orders.agent_run_id` FK links a dispatch decision back to the
full LangSmith trace.

---

## 5. API Design

### New router: `/api/v1/orders`

| Method | Path | Who calls it | What it does |
|---|---|---|---|
| `GET` | `/orders` | UI, tools | List orders. Filter by `status`, `retailer`, `mode` |
| `GET` | `/orders/{id}` | UI, tools | Get single order with full detail |
| `POST` | `/orders/seed` | Makefile / CI | Load seed data (idempotent) |
| `PATCH` | `/orders/{id}/dispatch` | Human UI **and** AI tool | Set mode, mark ready_to_dispatch |
| `PATCH` | `/orders/{id}/recommend` | AI tool (Mode 2 only) | Store AI recommendation, move to pending_review |
| `POST` | `/orders/{id}/approve` | Human UI (Mode 2) | Accept AI recommendation, dispatch |
| `POST` | `/orders/{id}/reject` | Human UI (Mode 2) | Reject AI recommendation, back to pending |

The `PATCH /dispatch` body:
```json
{
  "mode": "air",
  "decided_by": "human",
  "reasoning": "Optional free-text note"
}
```

The `PATCH /recommend` body:
```json
{
  "mode": "air",
  "confidence": "high",
  "reasoning": "Due in 1 day, amount $12,400 — urgency overrides cost",
  "agent_run_id": "uuid-of-the-agent-run"
}
```

Both endpoints write to the same `orders` table and emit the same structure in
the response — the calling code does not need to know which path was taken.

---

## 6. UI Design

Served as a new route `/dashboard` on the existing FastAPI server (:8000).
Technology: **FastAPI + Jinja2 templates + HTMX** — no separate frontend stack,
no build pipeline, works inside the existing container.

### Dashboard layout

```
┌───────────────────────────────────────────────────────────────────────────┐
│  Fundly  Order Dispatch Dashboard                    [⚙ Settings]         │
├───────────────────────────────────────────────────────────────────────────┤
│                                                                           │
│  ┌─────────────────────────────────────────────────────────────────────┐  │
│  │  AI Automation    [ ○ OFF │ ● ON ]       Mode: [AI + Review ▾]     │  │
│  │                                                                     │  │
│  │  ON + Review  →  AI recommends, you approve                        │  │
│  │  ON + Auto    →  AI dispatches directly (no review step)           │  │
│  └─────────────────────────────────────────────────────────────────────┘  │
│                                                                           │
│  Pending Orders (12)              [ Run AI Agent ]  [ Refresh ]          │
│                                                                           │
│  ┌──────────────────────────────────────────────────────────────────────┐ │
│  │ Ref        │ Retailer    │ Medicine    │ Amount  │ Margin │ Due  │ Act│ │
│  ├────────────┼─────────────┼─────────────┼─────────┼────────┼──────┼───┤ │
│  │ ORD-0042   │ MedPlus     │ Amoxicillin │ $14,200 │ 18%   │ 1d   │[▾]│ │
│  │ ORD-0043   │ Apollo      │ Metformin   │  $3,100 │ 22%   │ 8d   │[▾]│ │
│  │ ORD-0044   │ Sai Medical │ Paracetamol │  $8,800 │ 15%   │ 3d   │[▾]│ │
│  │ ...        │             │             │         │       │      │   │ │
│  └──────────────────────────────────────────────────────────────────────┘ │
│                                                                           │
│  Pending Review (AI Recommendations) (4)                                 │
│                                                                           │
│  ┌──────────────────────────────────────────────────────────────────────┐ │
│  │ Ref       │ AI Recommends │ Confidence │ Reasoning           │ Act  │ │
│  ├───────────┼───────────────┼────────────┼─────────────────────┼──────┤ │
│  │ ORD-0038  │  ✈ Air        │  HIGH      │ Due in 1d, $12k     │ ✓ ✗ │ │
│  │ ORD-0039  │  🚂 Train     │  MEDIUM    │ Due in 4d, margin   │ ✓ ✗ │ │
│  └──────────────────────────────────────────────────────────────────────┘ │
└───────────────────────────────────────────────────────────────────────────┘
```

The **Action** column `[▾]` dropdown in Manual mode shows: `Air / Train / Road /
Dispatch`.

The `[ Run AI Agent ]` button is only visible when `ai_automation_enabled = true`.

The **Pending Review** section only appears when `human_in_the_loop = true` and
there are recommendations waiting.

### Settings panel (`/dashboard/settings`)

Controls the two feature flags:
- AI Automation toggle (ON/OFF) — stored in browser localStorage or a simple
  `platform_settings` table with a single row
- Agent mode dropdown (AI + Review / Full Auto) — controls which agent YAML is
  loaded, or sets `human_in_the_loop` flag

---

## 7. Tools Design

New file: `src/fde_agent/agent/tools/dispatch.py`

Five tools, all making HTTP calls to the Orders API:

### `get_pending_orders()`
```
Input:  none
Output: JSON list of all orders with status=pending
        Each order has: id, ref, retailer, medicine, quantity,
        amount, margin_percent, urgency_days
Purpose: Agent's starting point — find what needs to be processed
```

### `get_order_details(order_id)`
```
Input:  order_id (string UUID)
Output: Full order record including calculated urgency score
Purpose: Agent uses this when it needs more detail before deciding
```

### `get_dispatch_rules()`
```
Input:  none
Output: Plain text explanation of the business rules:
        - urgency_days <= 2 AND amount > $5000 → air
        - urgency_days <= 2 AND amount <= $5000 → train
        - urgency_days 3–5 AND amount > $10000 → train
        - urgency_days 3–5 AND amount <= $10000 → road
        - urgency_days > 5 → road (unless high margin justifies train)
        - margin > 25% always upgrades one tier (road→train, train→air)
Purpose: Provides the decision rules the agent should follow.
         Keeping rules in a tool (not system prompt) allows rules to
         evolve without changing the agent YAML.
```

### `dispatch_order(order_id, mode, reasoning)`
```
Input:  order_id, mode (air|train|road), reasoning (free text)
Output: Updated order record
Calls:  PATCH /api/v1/orders/{id}/dispatch
        with decided_by="ai", mode=mode, reasoning=reasoning
Purpose: Used in Mode 3 (full automation) — actually dispatches the order
```

### `recommend_dispatch(order_id, mode, confidence, reasoning)`
```
Input:  order_id, mode, confidence (high|medium|low), reasoning
Output: Updated order record (status=pending_review)
Calls:  PATCH /api/v1/orders/{id}/recommend
Purpose: Used in Mode 2 (human-in-the-loop) — stores recommendation
         for human review without actually dispatching
```

The agent decides which of the last two tools to call based on the
`human_in_the_loop` feature flag injected into its runtime context.

---

## 8. Feature Flags Design

Two flags, two different homes — each matched to the decision authority:

### Flag 1: `ai_automation_enabled` — lives in the UI

**What it controls:** Whether the "Run AI Agent" button is shown on the dashboard.
When OFF, the UI only shows manual dispatch forms. When ON, the AI run button appears.

**Where it lives:** A single-row `platform_settings` table in PostgreSQL, readable
and writable via `GET/PATCH /api/v1/settings`. The UI reads it on load.

**Why here:** This is an operational toggle — ops/team lead turns it on when the
team is ready to try AI assistance. It has nothing to do with the agent's internal
behaviour.

```
platform_settings table
  key   TEXT PK
  value JSONB
  updated_at TIMESTAMP

Row: { key: "ai_automation_enabled", value: false }
```

### Flag 2: `human_in_the_loop` — lives in the agent YAML

**What it controls:** Whether the agent calls `dispatch_order` (Mode 3) or
`recommend_dispatch` (Mode 2).

**Where it lives:** A new `feature_flags` section in `AgentConfig`:

```yaml
agent:
  name: order-dispatch-agent
  feature_flags:
    human_in_the_loop: true   # true = AI recommends, human approves
                               # false = AI dispatches directly
```

**Why here:** This is a deployment-level decision about the agent's trust level.
It is reviewed in a PR just like system prompts and guardrails. Changing it
requires a deliberate YAML edit and redeploy — appropriate for a control that
determines whether a human is in the loop.

**How it flows to the tool:**
The `react_agent.py` already injects `extra_context` into the agent's message.
The `feature_flags` from `AgentConfig` will be injected the same way — as part
of a `[Platform context]` block the agent reads before deciding which tool to call.

The system prompt instructs the agent:
> "If `human_in_the_loop` is `true`, use `recommend_dispatch`. If `false`, use
> `dispatch_order`. Never mix these two."

---

## 9. Agent YAML Design

Two YAML files — one per operating mode. This makes the modes explicit and
independently auditable.

### `agents/configs/order-dispatch-review.yaml`
Human-in-the-loop mode. The conservative choice for initial rollout.

```yaml
agent:
  name: order-dispatch-review
  description: "AI recommends shipment mode for pending orders; human approves each."
  
  feature_flags:
    human_in_the_loop: true
  
  inputs:
    batch_size:
      type: integer
      required: false
      default: 20
      description: "Max orders to process per run"
  
  tools:
    - name: get_pending_orders
    - name: get_order_details
    - name: get_dispatch_rules
    - name: recommend_dispatch     # ← note: recommend, not dispatch
  
  guardrails:
    max_iterations: 60   # up to 20 orders × 3 tool calls per order = 60
```

### `agents/configs/order-dispatch-auto.yaml`
Full automation mode. The target end state.

```yaml
agent:
  name: order-dispatch-auto
  description: "AI dispatches shipment mode for pending orders automatically."
  
  feature_flags:
    human_in_the_loop: false
  
  tools:
    - name: get_pending_orders
    - name: get_order_details
    - name: get_dispatch_rules
    - name: dispatch_order         # ← direct dispatch
  
  guardrails:
    max_iterations: 60
```

Keeping them as separate YAMLs means:
- They activate independently (`is_active` flag per agent)
- They have independent audit trails in `agent_runs`
- Switching between modes is as simple as activating one and deactivating the other

---

## 10. Seed Data

A script `scripts/seed_orders.py` creates ~20 realistic orders covering all
decision scenarios the agent will face:

| Scenario | Urgency | Amount | Margin | Expected mode |
|---|---|---|---|---|
| Very urgent, high value | 1 day | $15,000 | 18% | air |
| Very urgent, low value | 1 day | $2,200 | 12% | train |
| Near deadline, high value | 3 days | $12,000 | 20% | train |
| Near deadline, high margin | 4 days | $5,000 | 28% | train (margin upgrade) |
| Comfortable timeline | 8 days | $8,000 | 15% | road |
| Comfortable, high margin | 7 days | $6,000 | 30% | train (margin upgrade) |

This ensures the demo shows the agent making varied, reasoned decisions — not
just always picking the same mode.

---

## 11. Implementation Sequence

The work is ordered so each step produces something demonstrable on its own.

```
Step 1 — Data foundation
  ├── orders DB model + Alembic migration
  ├── seed_orders.py script
  └── GET /api/v1/orders  (read-only, no UI yet)
  Deliverable: can see seed data in Adminer

Step 2 — Manual workflow (Mode 1)
  ├── Full Orders API (dispatch, recommend, approve, reject)
  ├── platform_settings table + GET/PATCH /api/v1/settings
  ├── Dashboard UI (list + manual dispatch form)
  └── ai_automation_enabled toggle in UI (only toggles visibility for now)
  Deliverable: analyst can do their job entirely from the UI

Step 3 — Tools
  ├── dispatch.py tool implementations
  ├── Register all 5 tools in _TOOL_REGISTRY
  └── Unit test each tool independently
  Deliverable: tools work standalone via curl against the API

Step 4 — Agent YAML + feature_flags in loader
  ├── Add feature_flags to AgentConfig in loader.py
  ├── Inject feature_flags into agent context in react_agent.py
  ├── Write order-dispatch-review.yaml
  ├── Write order-dispatch-auto.yaml
  └── Use Launcher to review/refine the system prompts
  Deliverable: agent runs in both modes via POST /run

Step 5 — Connect UI to agent (Mode 2 + Mode 3)
  ├── "Run AI Agent" button calls POST /api/v1/agents/{name}/run/async
  ├── UI polls for completion and refreshes the order list
  ├── Pending Review section shows AI recommendations with approve/reject
  └── Mode selector in settings switches between the two agent YAMLs
  Deliverable: full end-to-end demo of all three modes
```

---

## 12. What the Demo Shows

Run the demo in this order to tell the story progressively:

**Scene 1 — The Problem (ai_automation_enabled = false)**
"Here are 20 orders. This is what the analyst does every morning."
Show the manual form. Fill in a couple of orders by hand. Make the point: this
is repetitive, time-consuming, error-prone at scale.

**Scene 2 — AI Assistance with Human Control (human_in_the_loop = true)**
Toggle `ai_automation_enabled = true`. Keep mode on `AI + Review`.
Click "Run AI Agent". Watch the Pending Review table populate.
Show the reasoning column — AI explains why it chose each mode.
Approve a few, reject one (change the mode). Dispatch.
Point: AI does the analysis, human stays in control. Trust is built incrementally.

**Scene 3 — Full Automation (human_in_the_loop = false)**
Seed a fresh batch of orders. Switch mode to `Full Auto`.
Click "Run AI Agent". Orders move directly to ready_to_dispatch.
Open LangSmith — show the full trace of every tool call and decision.
Open the `agent_runs` table in Adminer — show the audit trail.
Point: same business rules, zero human time, fully auditable.

**The punchline**
"The AI and the human are calling the exact same API endpoint. The only
difference is who pressed the button."

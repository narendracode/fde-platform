# Fundly Agent Platform — System Design

## Overview

The platform is a centralised infrastructure layer that standardises how AI agents are
**defined, deployed, executed, observed, and audited** across Fundly. Any team
can ship a new agent by committing a YAML file — without inventing their own tooling,
choosing their own models, or bypassing compliance guardrails.

A first-class **Human-in-the-Loop (HITL)** system is built into the platform. Agents
that require human review before executing actions use a single generic `propose_action`
tool. A shared Action Inbox (`/approvals`) handles review for all agents — no agent
needs its own review UI, approve/reject endpoints, or domain-specific lifecycle state.

---

## Architecture Diagram

```
                           ┌──────────────────────────────────────────────────┐
                           │                  Client Layer                     │
                           │                                                  │
                           │   REST Clients                  Browser / CLI    │
                           │   (curl / SDK / Swagger)        /approvals UI    │
                           └──────┬───────────────────┬────────────────┬──────┘
                                  │                   │                │
                           ┌──────▼───────────────────▼────────────────▼──────┐
                           │                  API Layer                        │
                           │                                                  │
                           │               FastAPI  :8000                     │
                           │                                                  │
                           │   /api/v1/agents/{name}/run        (sync)        │
                           │   /api/v1/agents/{name}/run/async  (queued)      │
                           │   /api/v1/agents                   (CRUD)        │
                           │   /api/v1/actions                  (HITL inbox)  │
                           │   /api/v1/orders                   (domain)      │
                           │   /api/v1/outreach                 (domain)      │
                           │   /api/v1/runs                     (audit)       │
                           │   /approvals                       (UI)          │
                           │   /dashboard                       (UI)          │
                           │   /health                          (ops)         │
                           └──────┬────────────────────────────┬──────────────┘
                                  │                            │
                    ┌─────────────▼──────────┐   ┌────────────▼───────────────┐
                    │    Agent Engine         │   │     Task Queue              │
                    │                        │   │                             │
                    │  YAML Config Loader    │   │   Celery + Redis            │
                    │  LangGraph ReAct Agent │   │   rate limit: 10 runs/min  │
                    │  Tool Registry         │   │   acks_late + retry x2     │
                    │  Guardrail Checks      │   │   worker concurrency: 4    │
                    └─────────────┬──────────┘   └────────────┬───────────────┘
                                  │                            │
                    ┌─────────────▼────────────────────────────▼───────────────┐
                    │                   Persistence Layer                       │
                    │                                                           │
                    │   PostgreSQL                          Redis               │
                    │   ├── agents           (registry)    ├── Celery broker   │
                    │   ├── agent_runs       (audit trail) └── Celery results  │
                    │   ├── agent_actions    (HITL inbox)                      │
                    │   ├── orders           (domain data)                     │
                    │   └── platform_settings(config KV)                       │
                    └───────────────────────────────────────────────────────────┘
                                  │
                    ┌─────────────▼──────────────────────────────────────────────┐
                    │                  Observability Layer                        │
                    │                                                             │
                    │   LangSmith  :smith.langchain.com                          │
                    │     └── LLM traces: prompts, completions, token counts     │
                    │         cost per run (server-side pricing), latency        │
                    │                                                             │
                    │   OpenTelemetry → Jaeger  :16686                          │
                    │     └── Distributed traces: HTTP spans, DB queries,        │
                    │         Redis ops, Celery tasks, agent.run span            │
                    │                                                             │
                    │   agent_runs table (PostgreSQL)                            │
                    │     └── Every run: input/output/tokens/cost +              │
                    │         langsmith_trace_url + otel_trace_url               │
                    └─────────────────────────────────────────────────────────────┘
```

---

## Components

### 1. YAML Agent Config (`agents/configs/*.yaml`)

**What it is:** The declarative definition of an agent. Every agent in the platform
starts as a YAML file committed to the Git repository.

**What it controls:**
- Which LLM to use (provider, model name, temperature, max tokens)
- Cost cap per run (`max_cost_usd`)
- Which tools are enabled
- The system prompt
- Guardrails: max iterations, timeout, blocked input patterns
- Feature flags: `human_in_the_loop`, `stale_after`, `track_resource_state`
- Observability switches: whether to trace to LangSmith, what to log

**Important:** Agent configs are stored in the DB **without** the outer `agent:` key wrapper.
The DB stores the content of the `agent:` YAML node directly. All config lookups must go to
`agent.config["feature_flags"]`, not `agent.config["agent"]["feature_flags"]`.

**Feature flags (all keys accepted in `feature_flags:` block):**

| Key | Type | Purpose |
|---|---|---|
| `human_in_the_loop` | bool | Instructs agent to call `propose_action` rather than executing directly |
| `stale_after` | string | Duration string (e.g. `"4h"`, `"2d"`) — action auto-stales after this window |
| `track_resource_state` | object | Fields and check URL for drift detection at approval time |
| `enable_refinement` | bool | Shows "Refine with AI" canvas button on this agent's HITL actions |
| `refinement_agent` | string | Name of the agent invoked per chat turn in the refinement canvas |
| `refinement_preview` | string | Name of the Jinja preview partial (e.g. `"sandhar-plan"`) for the live preview pane; falls back to JSON viewer if absent |

**Currently registered agents:**

| Agent | Purpose | HITL | Stale | Refinement |
|---|---|---|---|---|
| `order-dispatch-review` | Recommends shipment mode for pharma orders | Yes | 4h | No |
| `pharma-outreach` | Email marketing outreach to pharma retailers | Yes | 2d | No |
| `sandhar-planning-supervisor` | Orchestrates full daily production planning pipeline (supervisor agent) | No | — | No |
| `sandhar-attendance-analyst` | Analyses shift-wise attendance; maps operators to skills; raises certification alerts | No | — | No |
| `sandhar-wo-prioritisation` | Imports and ranks open work orders; identifies quality holds | No | — | No |
| `sandhar-constraint-validator` | Validates machine, material, and quality constraints; creates alerts | No | — | No |
| `sandhar-resource-allocator` | Allocates operators to lines/machines; detects and alerts on skill/manpower gaps | No | — | No |
| `sandhar-plan-generator` | Assembles final shift plan, calculates quantities, proposes plan to HITL inbox | Yes | — | **Yes** — uses `sandhar-plan-refiner` |
| `sandhar-plan-refiner` | Conversational refinement agent; applies targeted plan edits via domain tools | No | — | — (is the refiner) |

**Example (order-dispatch-review):**
```yaml
agent:
  name: order-dispatch-review
  feature_flags:
    human_in_the_loop: true
    stale_after: "4h"
    track_resource_state:
      fields: ["status", "shipment_mode", "due_date", "urgency_days"]
      check_url: "/api/v1/orders/{resource_id}"
  model:
    provider: anthropic
    name: claude-sonnet-4-6
    max_cost_usd: 2.00
  tools:
    - name: get_dispatch_rules
      enabled: true
    - name: propose_action
      enabled: true
```

---

### 2. Config Loader (`src/agri_agent/config/loader.py`)

**What it is:** A Python module that reads YAML files and validates them into typed
Pydantic models (`AgentConfig`, `ModelConfig`, `ToolConfig`, etc.).

**How it works:**
1. `load_agent_config("pharma-outreach")` searches `agents/configs/` for a matching file
2. Parses YAML with PyYAML
3. Validates and coerces all fields via Pydantic v2
4. Returns a fully typed `AgentConfig` object consumed by the agent engine

**Key guarantee:** Invalid configs fail fast with clear error messages before any LLM call.

---

### 3. Pydantic Settings (`src/agri_agent/config/settings.py`)

**What it is:** A `pydantic-settings` class that reads all runtime secrets and
environment variables from `.env` or the container environment.

**What it manages:**
- Database URL (async + sync variants for SQLAlchemy and Celery)
- Redis and Celery broker URLs
- LLM API keys (Anthropic, OpenAI)
- LangSmith API key and project name
- Platform API key for request authentication (`api_key`)
- Platform base URL (`api_base_url`) — used by the HITL drift check to call internal endpoints
- Log level
- OpenTelemetry settings: `OTEL_ENABLED`, `OTEL_SERVICE_NAME`, `OTEL_EXPORTER_OTLP_ENDPOINT`, `JAEGER_UI_URL`

---

### 4. LangGraph ReAct Agent (`src/agri_agent/agent/react_agent.py`)

**What it is:** The execution core. Takes a loaded `AgentConfig` and a user message,
builds a LangGraph compiled graph, and runs it.

**How it works:**

```
User message
     │
     ▼
Guardrail check  ──── blocked? ──→  return blocked response immediately
     │
     ▼
build_agent(config)
  ├── _build_model()     →  ChatAnthropic or ChatOpenAI instance
  ├── get_tools_for_config()  →  list of enabled LangChain tool objects
  └── create_react_agent(model, tools, prompt=system_prompt)
         └── returns a compiled LangGraph StateGraph
     │
     ▼
agent.invoke({"messages": [HumanMessage]}, config=RunnableConfig)
  └── LangGraph runs the ReAct loop:
        think → pick tool → call tool → observe result → think → ... → final answer
     │
     ▼
Extract output, token counts, tool call log
     │
     ▼
Return structured result dict
```

**Feature flags injected into system prompt:**
At run time, the agent's `feature_flags` block is injected as a `[Feature flags]` section
in the system prompt. This allows agents to branch behaviour (e.g. `human_in_the_loop: true`
→ call `propose_action`; false → call `dispatch_order`) without hardcoding decisions in YAML.

**Guardrails applied here:**
- `blocked_patterns` — regex match on input before any LLM call
- `max_iterations` — passed as `recursion_limit` to LangGraph, hard-stops runaway loops

---

### 5. Tool Registry (`src/agri_agent/agent/tools/`)

**What it is:** A dictionary mapping tool names (as used in YAML) to LangChain
`@tool`-decorated Python functions.

**All registered tools:**

| Tool name | File | What it does |
|---|---|---|
| `calculator` | `calculator.py` | Safe AST-based math — no `eval()`, no code injection risk |
| `web_search` | `search.py` | Tavily search if API key set; graceful mock fallback otherwise |
| `list_retailers` | `outreach.py` | Mock: returns pharma retailers for a given region |
| `filter_prospects` | `outreach.py` | Filters retailer list by yearly revenue threshold |
| `send_email` | `outreach.py` | Mock: records email, returns sent status |
| `get_pending_orders` | `orders.py` | Returns pending pharma orders from the platform DB |
| `get_order_details` | `orders.py` | Returns full detail for a single order by ID |
| `get_dispatch_rules` | `orders.py` | Returns business rules for dispatch mode selection |
| `dispatch_order` | `orders.py` | Directly dispatches an order (used when `human_in_the_loop=false`) |
| `recommend_dispatch` | `orders.py` | Legacy — kept for backward compatibility; use `propose_action` instead |
| `propose_action` | `platform.py` | **Platform tool** — creates an AgentAction record for human review |
| `sandhar_get_attendance_summary` | `sandhar/attendance.py` | Shift-wise present/absent/late counts by designation |
| `sandhar_get_present_operators` | `sandhar/attendance.py` | Full list of present operators for a shift |
| `sandhar_get_operator_skills` | `sandhar/attendance.py` | Line and machine skills for one employee |
| `sandhar_find_qualified_operators` | `sandhar/attendance.py` | Present operators qualified for a line/machine at a minimum skill level |
| `sandhar_check_certification_expiry` | `sandhar/attendance.py` | Employees with certifications expiring within 30 days |
| `sandhar_get_open_work_orders` | `sandhar/workorders.py` | Open WOs sorted by priority rank |
| `sandhar_get_work_order_detail` | `sandhar/workorders.py` | Full WO detail including operations |
| `sandhar_rank_work_orders` | `sandhar/workorders.py` | Re-orders WOs by due date proximity, customer priority, WO priority |
| `sandhar_get_machine_status` | `sandhar/constraints.py` | Current status of all machines |
| `sandhar_check_material_availability` | `sandhar/constraints.py` | Products with material shortfall for a planning date |
| `sandhar_get_quality_holds` | `sandhar/constraints.py` | Active quality holds on WOs or products |
| `sandhar_get_constraint_summary` | `sandhar/constraints.py` | Consolidated constraint summary: affected WOs, blocked qty, by type |
| `sandhar_calculate_planned_qty` | `sandhar/planning.py` | Qty = cycle time × manpower × shift hours for a line+product+manpower combo |
| `sandhar_allocate_line` | `sandhar/planning.py` | Creates `sandhar_resource_allocation` + `sandhar_plan_detail` rows |
| `sandhar_get_crossskill_candidates` | `sandhar/planning.py` | Operators from lower-priority lines who are qualified for this line |
| `sandhar_save_plan_header` | `sandhar/planning.py` | Creates a `sandhar_plan_header` row; returns `plan_header_id` |
| `sandhar_create_alert` | `sandhar/planning.py` | Creates a `sandhar_alert` row; used by any Sandhar agent |
| `sandhar_propose_plan_for_review` | `sandhar/planning.py` | Wrapper around `propose_action` formatted for production plan display |
| `sandhar_refine_get_plan` | `sandhar/plan_refiner.py` | Read-only: returns current plan details for a header |
| `sandhar_refine_update_qty` | `sandhar/plan_refiner.py` | Updates `planned_qty` and/or `planned_manpower` on a plan detail row |
| `sandhar_refine_move_wo` | `sandhar/plan_refiner.py` | Reassigns a WO to a different production line |
| `sandhar_refine_add_wo` | `sandhar/plan_refiner.py` | Adds an open WO as a new plan detail row |
| `sandhar_refine_remove_wo` | `sandhar/plan_refiner.py` | Removes a plan detail row (WO returns to unplanned) |
| `sandhar_refine_explain_constraint` | `sandhar/plan_refiner.py` | Explains a manpower gap or constraint in plain language |

**`propose_action` is the key platform tool** that enables the generic HITL system.
See the HITL section for full details.

**How new tools are added:**
1. Write a `@tool`-decorated function in `src/agri_agent/agent/tools/`
2. Register it in `_TOOL_REGISTRY` in `tools/__init__.py`
3. Reference it by name in any agent's YAML config

---

### 6. FastAPI Service (`src/agri_agent/api/`)

**What it is:** The HTTP interface to the platform.

#### Agent Management & Execution

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/health` | None | Liveness probe |
| `GET` | `/health/db` | None | DB connectivity check |
| `GET` | `/api/v1/agents` | API key | List all agents with `is_active` status |
| `GET` | `/api/v1/agents/configs` | API key | List YAML configs from disk |
| `GET` | `/api/v1/agents/tools` | API key | List all available tools (name + description) |
| `POST` | `/api/v1/agents/register` | API key | Load YAML config into DB (upsert, starts inactive) |
| `GET` | `/api/v1/agents/{name}` | API key | Get agent config from DB |
| `GET` | `/api/v1/agents/{name}/yaml` | API key | Return raw YAML config file as plain text |
| `PATCH` | `/api/v1/agents/{name}/activate` | API key | Activate — allows run requests |
| `PATCH` | `/api/v1/agents/{name}/deactivate` | API key | Deactivate — blocks run requests |
| `POST` | `/api/v1/agents/{name}/run` | API key | **Sync run** — waits for result |
| `POST` | `/api/v1/agents/{name}/run/async` | API key | **Async run** — returns task ID immediately |
| `GET` | `/api/v1/runs` | API key | List all runs (filterable by status) |
| `GET` | `/api/v1/runs/{run_id}` | API key | Get full run detail — use for polling async |

#### HITL Action Inbox

| Method | Path | Auth | Description |
|---|---|---|---|
| `POST` | `/api/v1/actions` | API key | Create a new `AgentAction` (called by `propose_action` tool) |
| `GET` | `/api/v1/actions` | API key | List actions. Filter by `status`, `agent_name`, `confidence`. Auto-marks stale on load. Returns `auto_staled_count`. |
| `GET` | `/api/v1/actions/counts` | API key | Summary counts by agent and status — for dashboard badges |
| `GET` | `/api/v1/actions/{id}` | API key | Get single action with full detail |
| `POST` | `/api/v1/actions/{id}/approve` | API key | Execute `approval_action`, mark `approved`. Returns 409 with drift diff if resource state changed. |
| `POST` | `/api/v1/actions/{id}/reject` | API key | Mark `rejected`, optionally execute `rejection_action` |
| `POST` | `/api/v1/actions/{id}/dismiss` | API key | Mark `dismissed` — analyst cannot decide now, no prejudice |
| `POST` | `/api/v1/actions/{id}/mark-drifted` | API key | Mark `drifted` after human acknowledges drift panel |
| `POST` | `/api/v1/actions/{id}/retry` | API key | Retry a failed `approval_action` execution |
| `POST` | `/api/v1/actions/{id}/refine/start` | API key | Start or resume a refinement session; validates `enable_refinement` flag; auto-registers refinement agent |
| `GET` | `/api/v1/actions/{id}/refine/messages` | API key | Return full conversation history for the active session — used on page reload |
| `POST` | `/api/v1/actions/{id}/refine/message` | API key | Send a chat turn; invokes refinement agent; streams response as SSE (`token` / `tool_use` / `done` events) |
| `POST` | `/api/v1/actions/{id}/refine/close` | API key | Close session without approving; session stays in DB; action remains `pending_review` |

#### Domain APIs

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/api/v1/orders` | API key | List orders (filter by status, limit) |
| `GET` | `/api/v1/orders/{id}` | API key | Get order detail |
| `PATCH` | `/api/v1/orders/{id}/dispatch` | API key | Dispatch an order (sets mode + decided_by) |
| `PATCH` | `/api/v1/orders/{id}/recommend` | API key | Legacy: set `pending_review` status |
| `POST` | `/api/v1/orders/{id}/approve` | API key | Legacy: approve from orders page |
| `POST` | `/api/v1/orders/{id}/reject` | API key | Legacy: reject from orders page |
| `POST` | `/api/v1/orders/seed` | API key | Seed test orders |
| `POST` | `/api/v1/outreach/send-email` | API key | Send (mock) outreach email — called by `propose_action` on approval |

#### UI Pages

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/dashboard` | None | Platform dashboard (agent list, run history) |
| `GET` | `/agents` | None | Agent management — activate/deactivate, run agent, **view YAML config with syntax highlighting** |
| `GET` | `/runs` | None | Run history page |
| `GET` | `/approvals` | None | **Generic Action Inbox** — review pending actions; "Refine with AI" canvas for eligible agents |
| `GET` | `/sandhar` | None | Sandhar command centre — KPI cards, alert badges, current shift status |
| `GET` | `/sandhar/plan` | None | Plan generation trigger, progress view, plan review with AI refinement canvas |
| `GET` | `/sandhar/floor` | None | Supervisor view — line plan, actuals entry, disruption reporting |
| `GET` | `/sandhar/master` | None | Master data CRUD — employees, lines, machines, products, customers, shifts |
| `GET` | `/sandhar/simulation` | None | Demo control panel — scenario triggers, attendance injection, state reset |
| `GET` | `/sandhar/plan/{header_id}/refine-preview` | API key | Server-rendered HTML partial for the live preview pane in the refinement canvas |

**Authentication:** `X-API-Key` header validated against `settings.api_key`.

**`decided_by` is always hardcoded as `"human"`** on all HITL endpoints (approve, reject,
dismiss, mark-drifted). This is a deliberate design decision — the platform knows the
decision came from a human via the approvals UI; no free-text field is exposed.

**Sync vs async run:**
- `POST /run` — agent executes in the request thread, response returned when done.
- `POST /run/async` — creates an `AgentRun` record, dispatches a Celery task, returns
  `{run_id, task_id}` immediately. Client polls `GET /runs/{run_id}` for status.

---

### 7. Database Layer (`src/agri_agent/db/`)

**What it is:** SQLAlchemy ORM models backed by PostgreSQL.

#### `agents` table — Agent Registry
```
id           UUID PK
name         unique slug matching the YAML filename
description  human-readable description
version      semver string from YAML
config       JSONB — full AgentConfig serialised WITHOUT outer "agent:" key wrapper
is_active    activation flag (default false)
created_at   timestamp
updated_at   timestamp (auto-updated)
```

#### `agent_runs` table — Audit Trail
```
id                  UUID PK
agent_id            FK → agents
thread_id           LangGraph thread — links runs in the same conversation
task_id             Celery task ID — use to look up async run status
status              pending | running | completed | failed | blocked | cancelled
input               JSONB — user message + extra context
output              JSONB — agent text response + tool call log
error               text — exception message if failed
input_tokens        int  — LLM input tokens consumed
output_tokens       int  — LLM output tokens consumed
cost_usd            float — actual cost fetched from LangSmith after each run
langsmith_run_id    text — LangSmith root trace UUID (set when tracing enabled)
langsmith_trace_url text — full LangSmith deep-link URL
otel_trace_id       text — OTel trace ID (32-char hex, set when OTEL_ENABLED)
otel_trace_url      text — full Jaeger deep-link URL
started_at          timestamp — when agent execution began
completed_at        timestamp — when execution finished
created_at          timestamp — when the run record was created
```

#### `agent_actions` table — HITL Action Inbox
```
id                UUID PK

── Provenance ──────────────────────────────────
agent_name        str               "order-dispatch-review"
agent_run_id      UUID FK           links to agent_runs for trace/audit

── What to show the human ──────────────────────
title             str               "Dispatch ORD-001 via AIR"
summary           str               "MedCorp · $14.2k · due in 2 days"
reasoning         text              AI's full justification
confidence        str               high | medium | low
display_data      JSONB             [{"label": "Order Ref", "value": "ORD-001"}, ...]
tags              JSONB             ["dispatch", "urgent"]

── What to execute on approval ─────────────────
approval_action   JSONB             {method, url, url_params?, body?, body_schema?}
rejection_action  JSONB (optional)  {method, url, body}  — e.g. log, notify

── Lifecycle ────────────────────────────────────
status            str               See lifecycle section below
decided_by        str | null        Always "human" when set by the platform
decided_at        datetime | null
decision_note     str | null        Analyst's optional note
override_body     JSONB | null      (stored but not exposed in the UI — internal use)
approval_error    text | null       Error message if approval_action HTTP call failed
expires_at        datetime | null

── Staleness ────────────────────────────────────
stale_after_seconds  int | null     Parsed from agent YAML stale_after at create time.
                                    Stored on the record so enforcement needs no YAML lookup.
stale_marked_at      datetime | null  When platform auto-marked the action stale

── Drift detection ──────────────────────────────
expected_state    JSONB | null      State snapshot captured by agent at propose time.
                                    {resource_id, field: value, ...}
drift_detected_at datetime | null   When drift was first detected at approval attempt
drift_details     JSONB | null      Full diff: {field: {expected: X, actual: Y}, ...}
drift_override    bool              true if human clicked "Approve Anyway" despite drift

created_at        datetime
updated_at        datetime
```

**AgentAction status lifecycle:**
```
                   Agent calls propose_action()
                            │
                            ▼
                     pending_review  ◄──── shown in active inbox
                            │
        ┌───────────────────┼──────────────────┬─────────────────────┐
        │                   │                  │                     │
 Human approves      Human rejects     Human dismisses     Inbox load +
        │                   │          (can't decide now)  stale_after exceeded
        ▼                   ▼                  │                     │
Platform calls          rejected           dismissed              stale
approval_action      (final, no             (neutral,         (auto, no human
   (HTTP)             API call)         action removed         action needed)
   ┌──┴──┐                               from inbox)
success failure
   │     │
approved  approval_failed  ──────── Human retries ─────────► (loops back)
          (retry available)

Human clicks "Mark as Drifted" (from drift panel):
                                               ▼
                                            drifted
                                        (human acknowledged,
                                         diff stored in drift_details)
```

**Active inbox:** shows only `pending_review`.
**History view:** shows `approved`, `rejected`, `dismissed`, `stale`, `drifted`, `approval_failed`, `expired`.

#### `orders` table — Domain Data
```
id                UUID PK
order_ref         str unique         "ORD-0626-001"
retailer_name     str
medicine_name     str
quantity          int
unit_price_usd    float
order_amount_usd  float
margin_percent    float
due_date          date
urgency_days      int (computed)
status            pending | dispatched | cancelled
shipment_mode     air | train | road | null
decided_by        human | ai | null
ai_recommended_mode  str | null
ai_confidence     high | medium | low | null
ai_reasoning      text | null
agent_run_id      UUID | null
dispatched_at     datetime | null
created_at        datetime
updated_at        datetime
```

#### `agent_refine_session` table — Refinement Canvas Sessions
```
id                UUID PK
action_id         UUID FK → agent_actions      One session per action (idempotent start)
refinement_agent  str                          Copied from feature_flags at creation
status            str                          active | approved | closed
opened_by         str | null                   "anonymous" for v1; reserved for auth
created_at        datetime
closed_at         datetime | null              Set on approve or explicit close
```

#### `agent_refine_message` table — Chat Message History
```
id                UUID PK
session_id        UUID FK → agent_refine_session
role              str                          user | assistant | system
content           TEXT
tool_calls        JSONB | null                 [{tool, args, result}] — LLMOps training signal
context_snapshot  JSONB | null                 Full domain state snapshot after this turn
langsmith_run_id  str | null
langsmith_trace_url TEXT | null
input_tokens      int | null
output_tokens     int | null
created_at        datetime
```

#### `platform_settings` table — Config Key-Value Store
```
key        str PK    e.g. "feature_flags.hitl_enabled"
value      JSONB     arbitrary JSON value
updated_at datetime
```

**Migrations:** Managed by Alembic (`alembic upgrade head`). Migration files in
`alembic/versions/` — platform tables (001–008) plus Sandhar domain tables (009–014)
plus refinement tables (015).

---

### 8. Generic HITL System

The HITL system is the core platform feature that decouples agent decision-making from
domain-specific review workflows. See `docs/generic-hitl-design.md` for the full design
rationale and failure case analysis. This section documents the implementation.

#### The `propose_action` Tool (`src/agri_agent/agent/tools/platform.py`)

A single platform tool that replaces all domain-specific `recommend_*` tools. The agent
calls it with everything the human needs to evaluate and execute the action:

```python
propose_action(
    agent_name="order-dispatch-review",
    title="Dispatch ORD-001 via AIR",
    summary="MedCorp · $14,200 · due in 2 days",
    reasoning="urgency_days=2, Rule 1: air. Margin 28% confirms.",
    confidence="high",
    display_data='[{"label":"Order Ref","value":"ORD-001"},...]',
    approval_action='{"method":"PATCH","url":"/api/v1/orders/{order_id}/dispatch",...}',
    tags='["dispatch"]',
    expected_state='{"resource_id":"uuid","status":"pending","shipment_mode":null,...}'
)
```

All arguments are JSON strings (LangGraph tool compatibility). The tool parses them,
calls `POST /api/v1/actions`, and returns the created action's `id` and `status`.

The `stale_after_seconds` value is **not** passed by the agent — it is looked up
server-side from the agent's DB config at action creation time and stored on the record.

#### Auto-Stale Enforcement

When `stale_after` is configured in the agent's YAML (e.g. `"4h"`), the value is
converted to seconds and stored as `stale_after_seconds` on each created `AgentAction`.

Auto-stale runs server-side at inbox load time — no background job or scheduler needed:

```
GET /api/v1/actions (or GET /approvals)
  → auto_mark_stale_actions() runs before results are returned
    → finds pending_review actions where created_at + stale_after_seconds < NOW()
    → marks them stale (status='stale', stale_marked_at=NOW())
    → returns auto_staled_count in response
```

The active inbox never shows stale actions — they were retired before the page rendered.
A dismissible banner shows if any were auto-staled: *"N actions automatically marked stale."*

#### Drift Detection

When `track_resource_state` is configured, the agent captures the resource state at
propose time as `expected_state`. At approval time:

1. Platform reads `expected_state` from the `AgentAction` record
2. Platform calls `check_url` (e.g. `GET /api/v1/orders/{resource_id}`) using
   `settings.api_base_url` + `settings.api_key` — the platform calls its own API
3. Compares current values of the tracked fields against `expected_state`
4. **No drift** → proceed with `approval_action` execution normally
5. **Drift detected** → return HTTP 409 with `{"conflict": "state_drift", "drift_details": {...}}`

The UI shows a drift panel on 409:
```
⚠ Resource state has changed since this action was proposed

  Field           When Proposed    Now
  ─────────────────────────────────────
  status          pending          dispatched  ← changed
  shipment_mode   —                road        ← changed
  due_date        2026-07-05       2026-07-05

  [Mark as Drifted]   [Approve Anyway]   [Cancel]
```

- **Mark as Drifted** → `POST /actions/{id}/mark-drifted`: transitions to `drifted`, stores diff
- **Approve Anyway** → re-calls approve with `override_drift: true`: records `drift_override=true`, proceeds to execute
- **Cancel** → card stays in inbox unchanged

#### Action Inbox UI (`/approvals`)

A single generic Jinja2 page. Driven entirely by `display_data` — no agent-specific templates.

**Active view** — cards for each `pending_review` action showing:
- Agent chip, confidence badge, timestamp
- `⏱ Stale after Xh` chip (static, shown when `stale_after_seconds` is set)
- Age countdown chip — amber when >50% of stale window elapsed, red when >90%
- Card title and summary
- Collapsible details: `display_data` key-value table + reasoning box
- **Resource state when proposed** — table of `expected_state` fields (excluding `resource_id`), shown when `expected_state` is present
- Action buttons: **Approve / Reject / Dismiss** + optional note field

**History view** (`?view=history`) — terminated actions with status badges for all
non-active statuses.

**Design decisions:**
- `decided_by` is always hardcoded as `"human"` server-side — no free-text field in UI
- Override panel (`override_body` mode selection) was removed — the approve button executes
  the action exactly as the agent proposed; if the agent's recommendation is wrong, the
  analyst rejects and the agent is re-run
- Dismiss is distinct from Reject: dismiss = "can't decide now" (neutral, no API call,
  action can be re-proposed); reject = definitive no

#### "Refine with AI" Canvas

A conversational canvas that lets a human planner iteratively edit a proposed action's underlying data through natural language, before deciding to approve or reject. It is a generic platform capability activated per-agent via feature flags — no domain-specific code in the platform layer.

**How it is enabled (in any agent's YAML):**
```yaml
feature_flags:
  enable_refinement: true
  refinement_agent: "sandhar-plan-refiner"   # agent invoked per chat turn
  refinement_preview: "sandhar-plan"          # Jinja preview partial name (optional)
```

**How the platform reads these flags:**
`actions.py` reads `feature_flags` from the action's agent config (YAML-first, DB fallback) at request time. If `enable_refinement` is false or absent, `POST /refine/start` returns 403.

**Session lifecycle:**

```
POST /refine/start
  └── Creates agent_refine_session (or returns existing active session idempotently)
  └── Auto-registers + auto-activates the refinement_agent if not yet in DB
  └── Returns {session_id, welcome_message}

POST /refine/message  (SSE streaming)
  └── Persists user message row
  └── Invokes refinement_agent with full conversation history
  └── Streams token events to browser:
        data: {"type": "token",    "content": "Done. WO-0003 moved..."}
        data: {"type": "tool_use", "tool": "sandhar_refine_move_wo"}
        data: {"type": "done",     "session_id": "..."}
  └── On done: persists assistant message row (tool_calls, context_snapshot, tokens)

POST /refine/close
  └── Sets session status = closed; action stays pending_review

[human clicks Approve in canvas]
  └── Calls existing POST /actions/{id}/approve — no new approval logic
```

**Deep linking** (plan page): When a refinement canvas is open, the URL is updated to `?refine=<action_id>` via `history.pushState`. On page reload the canvas auto-opens and restores the full conversation history. The "← Back to Plan" button calls `_goBackToPlan()` which closes the overlay without calling `refine/close` — the session stays `active` so history is fully restored next time.

**Canvas layout:**

```
┌──────────────────────────────────────────────────────┐
│  PREVIEW PANE (42%)          │  CHAT CANVAS (58%)     │
│                              │  Refining: <title>     │
│  Live domain view —          │  [✅ Approve] [← Back] │
│  server-rendered partial     │                        │
│  refreshed on every          │  🤖 Welcome message   │
│  event:done                  │  👤 User message       │
│                              │  🤖 AI response        │
│                              │  [tool badge]          │
│                              │                        │
│                              │  [ Type instruction ]  │
│                              │            [Send →]    │
└──────────────────────────────────────────────────────┘
```

**Preview partial resolution:** the platform fetches `GET /sandhar/plan/{header_id}/refine-preview` (a server-rendered HTML fragment with live DB data) and injects it into the preview pane. Refreshed after every `event:done`. Falls back to a formatted JSON viewer of `context_snapshot` if no named partial is found.

---

### 9. Task Queue — Celery + Redis (`src/agri_agent/queue/`)

**What it is:** An async job queue that decouples the API from agent execution.

**Configuration choices:**

| Setting | Value | Reason |
|---|---|---|
| `rate_limit` | `10/m` per task | Prevents LLM API rate limit errors under burst traffic |
| `worker_prefetch_multiplier` | `1` | Fair dispatch — each worker takes one job at a time |
| `acks_late` | `True` | Task only acknowledged after completion — safe restart on crash |
| `task_reject_on_worker_lost` | `True` | Re-queues task if worker process is killed |
| `max_retries` | `2` | Transient failures get two automatic retries |
| `default_retry_delay` | `10s` | Brief backoff before retry |

**Task lifecycle:**
```
POST /run/async
  └── AgentRun created (status=pending)
  └── run_agent_task.delay(run_id, agent_name, message)
        │
        ▼
  Celery worker picks up task
  └── AgentRun updated (status=running, started_at=now)
  └── load_agent_config() → run_agent()
        ├── success → AgentRun (status=completed, output=..., tokens=...)
        └── failure → AgentRun (status=failed, error=...) → retry up to 2x
```

**Concurrency:** The worker container runs 4 Celery processes (`--concurrency=4`).

---

### 10. Observability

**LangSmith (LLM-layer traces — optional):**
Set `LANGSMITH_TRACING=true` and `LANGSMITH_API_KEY` in `.env`. Every agent invocation
sends a trace with: full prompt/completion text, tool call inputs/outputs, token counts,
per-step latency, and cost. The platform reads `cost_usd` back via `client.read_run()`
and stores it in `agent_runs`.

**OpenTelemetry → Jaeger (infrastructure traces — optional):**
Set `OTEL_ENABLED=true` in `.env`. Auto-instruments:

| Layer | What is traced |
|---|---|
| FastAPI | Every HTTP request — method, path, status code, latency |
| SQLAlchemy | Every DB query — SQL statement, table, duration |
| Redis | Every redis-py call (Celery broker/result ops) |
| Celery | Task enqueue + execution, W3C TraceContext propagated across process boundary |
| `agent.run` | Manual span with model name, token counts, cost, tool count, LangSmith run ID |

**Platform audit trail (always on):**
Every run — sync or async — creates an `agent_runs` row regardless of LangSmith or OTel.

**Structured logging:**
FastAPI and Celery write structured logs at `INFO` by default.
Set `LOG_LEVEL=debug` for SQL queries and LangGraph step detail.

---

## Data Flow: Async Agent Run (End to End)

```
Client
  │  POST /api/v1/agents/pharma-outreach/run/async
  │  X-API-Key: <key>
  │  {"message": "Run outreach", "extra_context": {"region": "Mumbai"}}
  │
  ▼
FastAPI (api container :8000)
  ├── verify_api_key()
  ├── load_agent_config("pharma-outreach")
  ├── _validate_inputs(config, extra_context)
  ├── INSERT agent_runs (status=pending)
  ├── run_agent_task.delay(run_id, ...)
  └── return {run_id, task_id, status="queued"}  ← 202 response

Celery Worker (worker container)
  ├── picks up task
  ├── UPDATE agent_runs SET status=running
  ├── load_agent_config("pharma-outreach")
  ├── build_agent(config)
  ├── inject [Feature flags] block into system prompt
  ├── agent.invoke({"messages": [HumanMessage]})
  │     LangGraph ReAct loop:
  │       → LLM sees feature_flags.human_in_the_loop = true
  │       → LLM calls propose_action(...) for each prospect
  │       → propose_action POSTs to /api/v1/actions
  │       → AgentAction records created with status=pending_review
  │       → LLM formulates summary ("6 actions proposed for review")
  └── UPDATE agent_runs SET status=completed

Client polls:
  GET /api/v1/runs/{run_id}
  └── {status: "completed", output: "6 email review requests created at /approvals"}
```

---

## Data Flow: HITL Approval (End to End)

```
Analyst opens /approvals
  │
  ▼
GET /approvals (server-side)
  ├── auto_mark_stale_actions()
  │     → finds pending_review actions past their stale_after window
  │     → marks them stale; returns auto_staled_count
  ├── queries pending_review actions
  └── renders approvals.html with ACTIONS JSON embedded
        → JS renders age chips (amber/red countdown when >50% elapsed)
        → JS renders "⏱ Stale after Xh" chip for agents with stale_after
        → Details panel shows expected_state fields when present

Analyst expands card, reads display_data, clicks Approve
  │
  ▼
POST /api/v1/actions/{id}/approve
  ├── [if track_resource_state configured]
  │     → _check_drift(): GET check_url using settings.api_base_url + settings.api_key
  │     → compare tracked fields against expected_state
  │     → if drift: return HTTP 409 {conflict: "state_drift", drift_details: {...}}
  │           UI shows drift panel → analyst chooses:
  │             [Mark as Drifted] → POST /actions/{id}/mark-drifted → status=drifted
  │             [Approve Anyway]  → POST /actions/{id}/approve with override_drift=true
  │             [Cancel]          → nothing
  ├── [no drift] _execute_approval_action()
  │     → resolves url_params into URL
  │     → calls approval_action.method + URL with body (server-side HTTP)
  │     → success: status=approved, decided_by="human", decided_at=now
  │     → failure: status=approval_failed, approval_error=message
  └── UI removes card from active inbox (or shows error for retry)
```

---

## Deployment Topology

```
docker-compose.yml defines 6 services:

  postgres   ─ Single instance, one database: agri_agent
               Tables: agents, agent_runs, agent_actions, orders, platform_settings

  redis      ─ Single instance, three logical DBs:
               db/0  general cache
               db/1  Celery broker (task queue)
               db/2  Celery result backend

  api        ─ FastAPI + Uvicorn (agri_agent.api.app:app)
               reads: agents/configs/*.yaml (mounted read-only)
               reads/writes: postgres/agri_agent
               publishes: redis/db/1 (Celery tasks)
               sends OTLP traces: jaeger:4318
               exposes: :8000

  worker     ─ Celery worker (4 processes)
               reads: agents/configs/*.yaml (mounted read-only)
               reads/writes: postgres/agri_agent
               consumes: redis/db/1 (Celery tasks)
               writes: redis/db/2 (task results)
               sends OTLP traces: jaeger:4318

  jaeger     ─ Jaeger all-in-one (OTel collector + trace UI)
               receives: OTLP HTTP on :4318, OTLP gRPC on :4317
               exposes: :16686 (Jaeger UI)

  adminer    ─ Lightweight DB browser (PostgreSQL)
               exposes: :8080
```

---

## GitOps Workflow

```
Engineer writes new agent config
         │
         ▼
  agents/configs/new-agent.yaml  (committed to Git)
         │
         ▼
  Pull Request review
  ├── YAML validated by config loader in CI
  ├── Guardrails reviewed (cost cap, blocked patterns, max iterations)
  ├── Feature flags reviewed (stale_after window, drift tracking fields)
  └── Model choice reviewed (provider, token budget)
         │
         ▼
  Merge to main
         │
         ▼
  CI/CD pipeline
  ├── docker compose build / push image
  ├── docker compose up (rolling restart of api + worker)
  └── make ci-deploy AGENT=new-agent   (migrate → seed → smoke)
         │
         ▼
  Agent registered with is_active=false
         │
         ▼
  Dashboard / Ops: PATCH /api/v1/agents/new-agent/activate
         │
         ▼
  Agent live:
  POST /api/v1/agents/new-agent/run
```

---

## Security Model

| Concern | Current implementation | Production path |
|---|---|---|
| API authentication | `X-API-Key` header | Swap `dependencies.py` for JWT/OAuth2 |
| Secret management | `.env` file | Vault, AWS Secrets Manager, or k8s Secrets |
| LLM prompt injection | `blocked_patterns` regex guardrail | Add semantic classifier layer |
| Tool sandboxing | Calculator uses AST (no `eval`) | Restrict network/FS access per tool |
| Data residency | All data in your Postgres | Disable LangSmith if data must stay on-prem |
| TLS | Not configured (POC) | Terminate at load balancer (nginx/ALB) |
| HITL drift check | Platform calls its own API (internal) | Ensure `api_base_url` is correct per environment |

---

## Extension Points

| What you want to add | Where to do it |
|---|---|
| New LLM provider | `react_agent._build_model()` |
| New tool | `agent/tools/` + register in `tools/__init__.py` |
| New agent with HITL | Add YAML with `feature_flags.human_in_the_loop: true`, add `propose_action` to tools, update system prompt — zero platform code changes |
| Drift tracking for a new resource | Add `track_resource_state` to agent YAML; ensure the resource has a readable GET endpoint |
| New stale window | Change `stale_after` in agent YAML — parsed and stored at action create time |
| Agent activation | `PATCH /api/v1/agents/{name}/activate` — controlled by dashboard |
| New API endpoint | `api/routes/` |
| New DB table | New model in `db/models.py` + Alembic migration |
| JWT auth | Replace `dependencies.verify_api_key()` |
| OTel backend swap | Change `OTEL_EXPORTER_OTLP_ENDPOINT` to any OTLP-compatible backend |
| Conversation memory | Add `AsyncPostgresSaver` checkpointer in `react_agent.build_agent()` |
| Horizontal scaling | Add more `worker` containers; point at same Redis + Postgres |
| Kubernetes deployment | Replace `docker-compose.yml` with Helm chart — services map 1:1 |
| Multi-reviewer support | Add optimistic locking to `POST /actions/{id}/approve` (see Case 5 in generic-hitl-design.md) |

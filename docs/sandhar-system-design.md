# Sandhar Group — Smart Production Planning
## System Design: Extension to the Agent Platform

> **Read this alongside:** `docs/system-design.md` (the platform baseline) and
> `docs/sandhar-production-planning-prd.md` (the product requirements).  
> This document describes **only what changes or is added**. Everything not
> mentioned here stays exactly as it is today.

---

**Document Version:** 2.0  
**Status:** Implemented (POC complete)  
**Audience:** Engineering team — reference for the implemented system

---

## Table of Contents

1. [How the Platform Evolves](#1-how-the-platform-evolves)
2. [Architecture Overview — What Changes](#2-architecture-overview--what-changes)
3. [Namespace and Isolation Strategy](#3-namespace-and-isolation-strategy)
4. [New Database Tables](#4-new-database-tables)
5. [New API Endpoints](#5-new-api-endpoints)
6. [New Tool Registry](#6-new-tool-registry)
7. [New Agent Configurations](#7-new-agent-configurations)
8. [New UI Pages](#8-new-ui-pages)
9. [End-to-End Data Flows](#9-end-to-end-data-flows)
10. [HITL Integration](#10-hitl-integration)
11. [Simulation Layer](#11-simulation-layer)
12. [Alembic Migration Plan](#12-alembic-migration-plan)
13. [Implementation Sequence](#13-implementation-sequence)
14. [What the Platform Reuses Unchanged](#14-what-the-platform-reuses-unchanged)

---

## 1. How the Platform Evolves

The platform was built for Fundly — a single customer with two domain concerns (order
dispatch, retailer outreach). Sandhar is the second customer with a completely different
domain: assembly shop floor production planning.

The core architecture is sound and reusable without modification. What changes is:

| Layer | Fundly (today) | Sandhar (added) |
|---|---|---|
| Database | `orders`, `agent_actions`, `agent_runs`, `agents`, `platform_settings` | +18 `sandhar_*` domain tables |
| API routes | `/api/v1/orders`, `/api/v1/outreach`, `/api/v1/actions` | +`/api/v1/sandhar/…` namespace |
| Tools | `dispatch.py`, `outreach.py`, `platform.py` | +`sandhar/` tool package |
| Agent configs | `pharma-ops-supervisor.yaml`, `pharma-outreach.yaml`, etc. | +`sandhar-*.yaml` configs |
| UI pages | `/dashboard`, `/approvals`, `/runs` | +`/sandhar/…` pages |
| Simulation | `POST /api/v1/orders/seed` | +`POST /api/v1/sandhar/simulation/…` |

**Nothing in the existing platform is removed or modified.** Fundly agents continue to
work exactly as before. The Sandhar module is an additive extension.

---

## 2. Architecture Overview — What Changes

```
                        ┌────────────────────────────────────────────────────────────┐
                        │                         Client Layer                        │
                        │                                                             │
                        │  Fundly Clients      Sandhar Planner     Sandhar Supervisor │
                        │  /approvals          /sandhar/plan        /sandhar/floor    │
                        │  /dashboard          /sandhar/dashboard   /sandhar/kpi      │
                        └───────┬──────────────────────┬────────────────────┬─────────┘
                                │                      │                    │
                        ┌───────▼──────────────────────▼────────────────────▼─────────┐
                        │                         FastAPI :8000                        │
                        │                                                             │
                        │   /api/v1/agents/*           (existing — unchanged)         │
                        │   /api/v1/actions/*          (existing — unchanged)         │
                        │   /api/v1/orders/*           (existing — unchanged)         │
                        │                                                             │
                        │   /api/v1/sandhar/master/*   (NEW — master data CRUD)       │
                        │   /api/v1/sandhar/attendance/* (NEW — attendance feed)      │
                        │   /api/v1/sandhar/workorders/* (NEW — work order sync)      │
                        │   /api/v1/sandhar/constraints/* (NEW — machine/material)    │
                        │   /api/v1/sandhar/plan/*     (NEW — plan generate/approve)  │
                        │   /api/v1/sandhar/execution/ (NEW — actuals, disruptions)   │
                        │   /api/v1/sandhar/alerts/*   (NEW — alert management)       │
                        │   /api/v1/sandhar/kpi/*      (NEW — KPI data)               │
                        │   /api/v1/sandhar/simulation/* (NEW — demo controls)        │
                        └───────┬───────────────────────────────────┬─────────────────┘
                                │                                   │
                  ┌─────────────▼──────────┐           ┌───────────▼──────────────────┐
                  │    Agent Engine         │           │      Task Queue               │
                  │   (unchanged)           │           │      (unchanged)              │
                  │                        │           │                               │
                  │  + sandhar-*.yaml      │           │  Sandhar planning runs        │
                  │    configs loaded       │           │  queued identically to        │
                  │    by existing loader   │           │  Fundly agent runs            │
                  │                        │           └───────────────────────────────┘
                  │  Tool Registry:        │
                  │  + sandhar/ package    │
                  └─────────────┬──────────┘
                                │
                  ┌─────────────▼──────────────────────────────────────────────────────┐
                  │                       Persistence Layer                             │
                  │                                                                     │
                  │   PostgreSQL                                                        │
                  │   ├── agents              (registry — unchanged)                   │
                  │   ├── agent_runs          (audit trail — unchanged)                │
                  │   ├── agent_actions       (HITL inbox — unchanged)                 │
                  │   ├── orders              (Fundly domain — unchanged)              │
                  │   ├── platform_settings   (config KV — unchanged)                  │
                  │   │                                                                 │
                  │   ├── sandhar_employees           ┐                                │
                  │   ├── sandhar_lines               │                                │
                  │   ├── sandhar_machines            │  NEW — Sandhar master data     │
                  │   ├── sandhar_products            │                                │
                  │   ├── sandhar_customers           │                                │
                  │   └── sandhar_shifts              ┘                                │
                  │                                                                     │
                  │   ├── sandhar_employee_skill_matrix  ┐  NEW — skill & attendance   │
                  │   └── sandhar_attendance             ┘                             │
                  │                                                                     │
                  │   ├── sandhar_work_orders            ┐  NEW — production orders    │
                  │   └── sandhar_work_order_operations  ┘                             │
                  │                                                                     │
                  │   ├── sandhar_machine_status         ┐                             │
                  │   ├── sandhar_material_availability  │  NEW — constraints          │
                  │   └── sandhar_quality_hold           ┘                             │
                  │                                                                     │
                  │   ├── sandhar_resource_allocation    ┐                             │
                  │   ├── sandhar_plan_header            │  NEW — planning output      │
                  │   ├── sandhar_plan_detail            │                             │
                  │   ├── sandhar_production_actual      ┘                             │
                  │                                                                     │
                  │   ├── sandhar_alert                     NEW — alert management     │
                  │   └── sandhar_daily_kpi                 NEW — KPI summary          │
                  └─────────────────────────────────────────────────────────────────────┘
```

---

## 3. Namespace and Isolation Strategy

### 3.1 Database

All Sandhar tables use the `sandhar_` prefix. This avoids any collision with existing
Fundly tables and makes it obvious which tables belong to which customer. All tables
live in the same PostgreSQL database (`fde_agent`) and the same schema (`public`).

### 3.2 API Routes

All Sandhar API endpoints are under `/api/v1/sandhar/`. The same `X-API-Key`
authentication applies. No changes to `dependencies.py`.

### 3.3 Tools

Sandhar tools live in a new sub-package: `src/fde_agent/agent/tools/sandhar/`.
They are registered in `_TOOL_REGISTRY` in `tools/__init__.py` alongside existing
tools — there is no separate registry. Tool names use a `sandhar_` prefix (e.g.
`sandhar_get_attendance`, `sandhar_get_work_orders`) to avoid name collision.

### 3.4 Agent Configs

All Sandhar agent YAML files use the naming convention `sandhar-*.yaml` in
`agents/configs/`. The existing config loader picks them up automatically.

### 3.5 UI Pages

Sandhar UI pages are served under `/sandhar/` paths. They use the same Jinja2
templating engine and the same base layout as existing pages but are fully
independent templates.

---

## 4. New Database Tables

Add an Alembic migration for each group below. All tables are in SQLAlchemy ORM
(`src/fde_agent/db/models.py`). Migrations go in `alembic/versions/`.

### 4.1 Master Data Tables

#### `sandhar_employees`
```
id              UUID PK
employee_code   VARCHAR(20) UNIQUE NOT NULL    -- e.g. "EMP-001"
name            VARCHAR(100) NOT NULL
department      VARCHAR(50)                    -- Production | QA | Maintenance
designation     VARCHAR(50)                    -- Operator | Supervisor
grade           VARCHAR(20)
shift_group     VARCHAR(10)                    -- A | B | C
status          VARCHAR(20) DEFAULT 'active'   -- active | inactive
joining_date    DATE
created_at      DATETIME
updated_at      DATETIME
```

#### `sandhar_lines`
```
id              UUID PK
line_code       VARCHAR(20) UNIQUE NOT NULL    -- e.g. "L001"
line_name       VARCHAR(100) NOT NULL          -- e.g. "Assembly Line-1"
area            VARCHAR(100)
capacity_per_shift  INT
status          VARCHAR(20) DEFAULT 'active'
created_at      DATETIME
updated_at      DATETIME
```

#### `sandhar_machines`
```
id              UUID PK
machine_code    VARCHAR(20) UNIQUE NOT NULL    -- e.g. "M001"
machine_name    VARCHAR(100) NOT NULL
line_id         UUID FK → sandhar_lines
machine_type    VARCHAR(50)
capacity_per_hour   INT
status          VARCHAR(20) DEFAULT 'active'   -- active | inactive
created_at      DATETIME
updated_at      DATETIME
```

#### `sandhar_customers`
```
id              UUID PK
customer_code   VARCHAR(20) UNIQUE NOT NULL    -- e.g. "CUST-OEM-A"
customer_name   VARCHAR(100) NOT NULL
priority_level  VARCHAR(20)                    -- critical | high | medium | low
created_at      DATETIME
updated_at      DATETIME
```

#### `sandhar_products`
```
id                  UUID PK
product_code        VARCHAR(30) UNIQUE NOT NULL  -- e.g. "PROD-X"
product_name        VARCHAR(100) NOT NULL
customer_id         UUID FK → sandhar_customers
standard_cycle_time DECIMAL(8,2)               -- minutes per unit
standard_manpower   INT                        -- operators required
line_id             UUID FK → sandhar_lines    -- preferred line
created_at          DATETIME
updated_at          DATETIME
```

#### `sandhar_shifts`
```
id              UUID PK
shift_code      VARCHAR(10) UNIQUE NOT NULL    -- A | B | C
shift_name      VARCHAR(50)                    -- "Morning Shift"
start_time      TIME NOT NULL                  -- 06:00
end_time        TIME NOT NULL                  -- 14:00
working_hours   DECIMAL(4,2)                   -- 8.0
created_at      DATETIME
```

---

### 4.2 Skill and Attendance Tables

#### `sandhar_employee_skill_matrix`
```
id                  UUID PK
employee_id         UUID FK → sandhar_employees
line_id             UUID FK → sandhar_lines       (nullable — skill may be machine-only)
machine_id          UUID FK → sandhar_machines    (nullable — skill may be line-only)
skill_level         INT                           -- 1=Trainee 2=Basic 3=Skilled 4=Expert
certification_date  DATE
expiry_date         DATE
active_flag         BOOLEAN DEFAULT true
created_at          DATETIME
updated_at          DATETIME

CONSTRAINT: At least one of line_id or machine_id must be non-null.
INDEX: (employee_id), (line_id), (machine_id), (expiry_date)
```

Skill levels: `1` = Trainee, `2` = Basic, `3` = Skilled, `4` = Expert.

#### `sandhar_attendance`
```
id                  UUID PK
employee_id         UUID FK → sandhar_employees
attendance_date     DATE NOT NULL
shift_code          VARCHAR(10) FK → sandhar_shifts
check_in_time       DATETIME
check_out_time      DATETIME
status              VARCHAR(20)  -- present | absent | leave | late
is_manual_override  BOOLEAN DEFAULT false
override_by         VARCHAR(100)  -- HR admin who overrode
created_at          DATETIME
updated_at          DATETIME

UNIQUE INDEX: (employee_id, attendance_date, shift_code)
INDEX: (attendance_date, shift_code, status)
```

---

### 4.3 Work Order Tables

These simulate Oracle Fusion ERP data. In production they would be populated by an
ERP sync job; for the POC they are seeded and manipulated via simulation API.

#### `sandhar_work_orders`
```
id              UUID PK
wo_number       VARCHAR(30) UNIQUE NOT NULL    -- e.g. "WO-2026-1001"
customer_id     UUID FK → sandhar_customers
product_id      UUID FK → sandhar_products
order_qty       INT NOT NULL
due_date        DATE NOT NULL
priority        VARCHAR(20)                    -- high | medium | low
status          VARCHAR(20) DEFAULT 'open'     -- open | planned | in_progress | completed | cancelled
quality_hold    BOOLEAN DEFAULT false
created_at      DATETIME
updated_at      DATETIME

INDEX: (status, due_date), (priority)
```

#### `sandhar_work_order_operations`
```
id              UUID PK
wo_id           UUID FK → sandhar_work_orders
line_id         UUID FK → sandhar_lines
machine_id      UUID FK → sandhar_machines    (nullable)
planned_qty     INT
sequence_no     INT                           -- operation sequence within the WO
created_at      DATETIME
```

---

### 4.4 Constraint Tables

#### `sandhar_machine_status`
```
id              UUID PK
machine_id      UUID FK → sandhar_machines
status_datetime DATETIME NOT NULL
machine_status  VARCHAR(20)           -- running | breakdown | maintenance | idle
reason          VARCHAR(500)
estimated_restore_datetime  DATETIME  (nullable)
reported_by     VARCHAR(100)
created_at      DATETIME

INDEX: (machine_id, status_datetime DESC)
-- Latest record per machine_id = current status.
```

#### `sandhar_material_availability`
```
id              UUID PK
product_id      UUID FK → sandhar_products
plan_date       DATE NOT NULL
available_qty   DECIMAL(12,2)
required_qty    DECIMAL(12,2)
shortfall_qty   DECIMAL(12,2)  -- computed: max(0, required_qty - available_qty)
constraint_flag BOOLEAN DEFAULT false  -- true if shortfall_qty > 0
updated_at      DATETIME

UNIQUE INDEX: (product_id, plan_date)
```

#### `sandhar_quality_hold`
```
id              UUID PK
wo_id           UUID FK → sandhar_work_orders  (nullable)
product_id      UUID FK → sandhar_products     (nullable)
hold_reason     VARCHAR(500)
hold_status     VARCHAR(20) DEFAULT 'active'   -- active | released
raised_by       VARCHAR(100)
released_by     VARCHAR(100)
raised_at       DATETIME
released_at     DATETIME

INDEX: (hold_status), (wo_id), (product_id)
```

---

### 4.5 Planning Output Tables

#### `sandhar_resource_allocation`
Generated by the AI planning engine. One row per operator per shift per plan.

```
id                  UUID PK
plan_date           DATE NOT NULL
shift_code          VARCHAR(10)
employee_id         UUID FK → sandhar_employees
line_id             UUID FK → sandhar_lines
machine_id          UUID FK → sandhar_machines    (nullable)
wo_id               UUID FK → sandhar_work_orders
allocation_status   VARCHAR(20) DEFAULT 'allocated'  -- allocated | cancelled | reassigned
plan_header_id      UUID FK → sandhar_plan_header
created_at          DATETIME
updated_at          DATETIME

INDEX: (plan_date, shift_code), (employee_id, plan_date)
```

#### `sandhar_plan_header`
One row per shift per plan date. A plan for a full day = 3 header rows (A, B, C).

```
id              UUID PK
plan_date       DATE NOT NULL
shift_code      VARCHAR(10) NOT NULL
version         INT DEFAULT 1              -- increments on regeneration
status          VARCHAR(20) DEFAULT 'draft'  -- draft | pending_approval | approved | rejected | superseded
confidence      VARCHAR(20)               -- high | medium | low
planner_id      VARCHAR(100)              -- set on approval
approved_at     DATETIME
created_at      DATETIME
updated_at      DATETIME

UNIQUE INDEX: (plan_date, shift_code, version)
-- A new version is inserted on each regeneration; old version is marked 'superseded'.
```

#### `sandhar_plan_detail`
One row per line per shift plan. Linked to the header.

```
id                  UUID PK
plan_header_id      UUID FK → sandhar_plan_header
wo_id               UUID FK → sandhar_work_orders
product_id          UUID FK → sandhar_products
line_id             UUID FK → sandhar_lines
planned_qty         INT
planned_manpower    INT
available_manpower  INT
manpower_gap        INT                    -- planned_manpower - available_manpower (negative = shortage)
supervisor_employee_id  UUID FK → sandhar_employees
start_time          DATETIME
end_time            DATETIME
status              VARCHAR(20) DEFAULT 'planned'   -- planned | in_progress | completed | at_risk
created_at          DATETIME
updated_at          DATETIME

INDEX: (plan_header_id), (line_id)
```

#### `sandhar_production_actual`
Filled in by supervisors at shift close.

```
id                  UUID PK
plan_detail_id      UUID FK → sandhar_plan_detail
shift_code          VARCHAR(10)
produced_qty        INT DEFAULT 0
rejected_qty        INT DEFAULT 0
rework_qty          INT DEFAULT 0
downtime_minutes    INT DEFAULT 0
achievement_pct     DECIMAL(5,2)           -- (produced_qty / planned_qty) * 100; computed on save
submitted_by        VARCHAR(100)
submitted_at        DATETIME
created_at          DATETIME
updated_at          DATETIME
```

---

### 4.6 Alert and KPI Tables

#### `sandhar_alert`
```
id              UUID PK
alert_type      VARCHAR(50)    -- manpower_shortage | skill_gap | machine_breakdown |
                               --   material_shortage | quality_hold | production_delay |
                               --   certification_expiry | excess_capacity
alert_message   VARCHAR(1000)
severity        VARCHAR(20)    -- critical | high | medium | low | info
status          VARCHAR(20) DEFAULT 'active'  -- active | acknowledged | resolved
plan_date       DATE
shift_code      VARCHAR(10)
related_line_id     UUID FK → sandhar_lines      (nullable)
related_wo_id       UUID FK → sandhar_work_orders (nullable)
related_employee_id UUID FK → sandhar_employees   (nullable)
related_machine_id  UUID FK → sandhar_machines    (nullable)
acknowledged_by VARCHAR(100)
acknowledged_at DATETIME
resolved_by     VARCHAR(100)
resolved_at     DATETIME
created_at      DATETIME

INDEX: (status, severity), (plan_date, shift_code)
```

#### `sandhar_daily_kpi`
Populated at shift close by the system.

```
id                          UUID PK
kpi_date                    DATE NOT NULL
shift_code                  VARCHAR(10) NOT NULL
total_planned_qty           INT
total_produced_qty          INT
plan_achievement_pct        DECIMAL(5,2)
manpower_utilization_pct    DECIMAL(5,2)  -- allocated / present * 100
line_utilization_pct        DECIMAL(5,2)  -- active lines / total lines * 100
rejection_rate_pct          DECIMAL(5,2)  -- rejected_qty / produced_qty * 100
total_downtime_minutes      INT
oee                         DECIMAL(5,2)  -- availability * performance * quality
skill_gap_count             INT           -- lines with unresolved skill gaps
active_alert_count          INT
created_at                  DATETIME
updated_at                  DATETIME

UNIQUE INDEX: (kpi_date, shift_code)
```

---

## 5. New API Endpoints

All endpoints are under `/api/v1/sandhar/` and require the `X-API-Key` header.
Implement in `src/fde_agent/api/routes/sandhar/` — one file per module.

### 5.1 Master Data (`sandhar/master.py`)

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/v1/sandhar/employees` | List employees (filter: department, status, shift_group) |
| `POST` | `/api/v1/sandhar/employees` | Create employee |
| `PUT` | `/api/v1/sandhar/employees/{id}` | Update employee |
| `GET` | `/api/v1/sandhar/lines` | List lines |
| `POST` | `/api/v1/sandhar/lines` | Create line |
| `GET` | `/api/v1/sandhar/machines` | List machines (filter: line_id, status) |
| `POST` | `/api/v1/sandhar/machines` | Create machine |
| `GET` | `/api/v1/sandhar/products` | List products |
| `POST` | `/api/v1/sandhar/products` | Create product |
| `GET` | `/api/v1/sandhar/customers` | List customers |
| `POST` | `/api/v1/sandhar/customers` | Create customer |
| `GET` | `/api/v1/sandhar/shifts` | List shifts |

### 5.2 Skill Matrix (`sandhar/skills.py`)

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/v1/sandhar/skills` | List skill records (filter: employee_id, line_id, machine_id) |
| `POST` | `/api/v1/sandhar/skills` | Assign a skill to an employee |
| `PUT` | `/api/v1/sandhar/skills/{id}` | Update skill level or certification dates |
| `DELETE` | `/api/v1/sandhar/skills/{id}` | Deactivate a skill assignment |
| `GET` | `/api/v1/sandhar/skills/search` | Query: who can operate `line_id` or `machine_id` at minimum `skill_level`, present on `date` for `shift_code` |
| `GET` | `/api/v1/sandhar/skills/gaps` | Returns skill gaps for a given plan_date: lines/machines with no qualified present operator |

### 5.3 Attendance (`sandhar/attendance.py`)

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/v1/sandhar/attendance/upload` | Bulk upload attendance records (JSON array or CSV) — simulates face recognition feed |
| `GET` | `/api/v1/sandhar/attendance` | List attendance records (filter: date, shift_code, status) |
| `PUT` | `/api/v1/sandhar/attendance/{id}/override` | Manual override: mark employee present/absent with reason |
| `GET` | `/api/v1/sandhar/attendance/summary` | Shift-wise summary for a date: present count, absent count, by designation |

**Attendance summary response shape:**
```json
{
  "date": "2026-07-01",
  "shifts": {
    "A": { "present_operators": 85, "present_supervisors": 6, "absent": 8, "late": 3 },
    "B": { "present_operators": 78, "present_supervisors": 5, "absent": 12, "late": 1 },
    "C": { "present_operators": 42, "present_supervisors": 3, "absent": 5, "late": 0 }
  }
}
```

### 5.4 Work Orders (`sandhar/workorders.py`)

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/v1/sandhar/work-orders` | List WOs (filter: status, due_date, priority) |
| `POST` | `/api/v1/sandhar/work-orders` | Create work order (simulation: replaces ERP sync) |
| `PUT` | `/api/v1/sandhar/work-orders/{id}` | Update WO (priority, due date, status) |
| `GET` | `/api/v1/sandhar/work-orders/open` | Open WOs eligible for planning: status in (open, planned), not under quality_hold |
| `GET` | `/api/v1/sandhar/work-orders/{id}` | Full WO detail including operations and current constraint flags |

### 5.5 Constraints (`sandhar/constraints.py`)

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/v1/sandhar/machines/{id}/status` | Record machine status change (running/breakdown/maintenance/idle) |
| `GET` | `/api/v1/sandhar/machines/{id}/status` | Get current machine status (latest record) |
| `GET` | `/api/v1/sandhar/machines/status` | Current status of all machines |
| `PUT` | `/api/v1/sandhar/material/{product_id}` | Update material availability for a product on a date |
| `GET` | `/api/v1/sandhar/material` | List material availability (filter: date, constraint_flag=true) |
| `POST` | `/api/v1/sandhar/quality-hold` | Place WO or product under quality hold |
| `POST` | `/api/v1/sandhar/quality-hold/{id}/release` | Release a quality hold |
| `GET` | `/api/v1/sandhar/constraints/summary` | Consolidated summary for a date: affected WOs by constraint type |

**Constraint summary response shape:**
```json
{
  "date": "2026-07-01",
  "machine_breakdown": [{"machine_code": "M5", "line": "Line-3", "since": "..."}],
  "material_shortage": [{"product_code": "PROD-Y", "shortfall_qty": 200}],
  "quality_hold": [{"wo_number": "WO-1002", "reason": "..."}],
  "affected_wo_count": 2,
  "blocked_qty": 700
}
```

### 5.6 Planning (`sandhar/planning.py`)

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/v1/sandhar/plan/generate` | Trigger AI plan generation for a date. Queues a Celery task, returns `{plan_run_id, task_id}`. |
| `GET` | `/api/v1/sandhar/plan` | Get plans for a date — returns shift list each with `plan_header_id`, `action_id` (if a `pending_review` action exists), confidence, and status |
| `GET` | `/api/v1/sandhar/plan/{header_id}` | Get a specific plan version with all detail rows and allocations |
| `POST` | `/api/v1/sandhar/plan/{header_id}/approve` | Planner approves the plan — transitions status to `approved`, locks it |
| `POST` | `/api/v1/sandhar/plan/{header_id}/reject` | Planner rejects with a reason |
| `PATCH` | `/api/v1/sandhar/plan/{header_id}/details/{detail_id}` | Update a plan detail row: `planned_qty`, `planned_manpower`, or `line_id` |
| `POST` | `/api/v1/sandhar/plan/{header_id}/details` | Add a new plan detail row (WO added to plan) |
| `DELETE` | `/api/v1/sandhar/plan/{header_id}/details/{detail_id}` | Remove a plan detail row |
| `POST` | `/api/v1/sandhar/plan/{header_id}/allocate-line` | Manually allocate a line |
| `GET` | `/api/v1/sandhar/plan/versions` | List all plan headers for a date |

**`action_id` in plan response:** `GET /api/v1/sandhar/plan` now includes `action_id` per shift — set when a `pending_review` `AgentAction` exists for that plan header (queried by `agent_name = "sandhar-plan-generator"` and `url_params.plan_header_id`). This enables the plan page to render the "Refine with AI" button without a separate API call.

### 5.7 Plan Refinement Preview (`sandhar/pages.py`)

| Method | Path | Description |
|---|---|---|
| `GET` | `/sandhar/plan/{header_id}/refine-preview` | Server-rendered HTML fragment (Jinja partial) with live plan data — injected into the refinement canvas preview pane |

This endpoint fetches `SandharPlanHeader`, `SandharPlanDetail`, `SandharLine`, and `SandharWorkOrder` from the DB and renders `sandhar/_refine_preview_sandhar-plan.html`. Called by the canvas JS after every `event:done` SSE event to keep the preview in sync with AI changes.

**Generate plan request body:**
```json
{
  "plan_date": "2026-07-01",
  "shifts": ["A", "B", "C"],
  "override_context": {}
}
```

The `POST /generate` endpoint:
1. Validates attendance data exists for the date
2. Validates at least one open work order exists
3. Creates an `agent_runs` record (status=pending)
4. Dispatches `run_agent_task` for the `sandhar-planning-supervisor` agent
5. Returns `{agent_run_id, task_id}` — client polls `GET /api/v1/runs/{run_id}`

### 5.7 Execution (`sandhar/execution.py`)

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/v1/sandhar/execution/supervisor-view` | Filtered plan view for a supervisor: filter by `line_id`, `shift_code`, `plan_date` |
| `POST` | `/api/v1/sandhar/execution/{plan_detail_id}/acknowledge` | Supervisor acknowledges their line plan |
| `POST` | `/api/v1/sandhar/execution/{plan_detail_id}/actuals` | Submit shift actuals: produced_qty, rejected_qty, rework_qty, downtime_minutes |
| `POST` | `/api/v1/sandhar/execution/disruption` | Report mid-shift disruption: type, affected line/machine, description |
| `POST` | `/api/v1/sandhar/execution/{plan_detail_id}/shift-close` | Finalise a shift — computes KPIs, archives |

**Actuals submission triggers:**
- Computation of `achievement_pct` and storage in `sandhar_production_actual`
- Upsert to `sandhar_daily_kpi` for that date+shift
- If `achievement_pct < 70` and shift is past midpoint → create a `production_delay` alert

### 5.8 Alerts (`sandhar/alerts.py`)

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/v1/sandhar/alerts` | List alerts (filter: status, severity, plan_date, shift_code) |
| `POST` | `/api/v1/sandhar/alerts/{id}/acknowledge` | Acknowledge alert with a note |
| `POST` | `/api/v1/sandhar/alerts/{id}/resolve` | Mark alert resolved |
| `GET` | `/api/v1/sandhar/alerts/active-count` | Count of active alerts by severity — for dashboard badges |

### 5.9 KPI (`sandhar/kpi.py`)

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/v1/sandhar/kpi/daily` | Get KPI summary for a date (all shifts) |
| `GET` | `/api/v1/sandhar/kpi/trend` | Weekly/monthly KPI trend: filter by `metric`, `from_date`, `to_date` |

### 5.10 Simulation (`sandhar/simulation.py`)

Demo-mode only. These endpoints provide full control over the simulated environment.
They should be protected with an additional `X-Demo-Mode: true` header or an
environment flag to prevent accidental use in non-POC deployments.

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/v1/sandhar/simulation/seed` | Seeds all master data, skill matrix, and base WOs from the mock dataset |
| `POST` | `/api/v1/sandhar/simulation/reset` | Wipes all `sandhar_*` tables and re-seeds from scratch |
| `POST` | `/api/v1/sandhar/simulation/attendance` | Injects attendance for a date+shift with configurable absenteeism % |
| `POST` | `/api/v1/sandhar/simulation/scenario/{scenario_id}` | Triggers a named scenario (see below) |
| `GET` | `/api/v1/sandhar/simulation/scenarios` | List available scenarios |

**Scenario IDs** (match PRD Section 13.2):

| scenario_id | What it injects |
|---|---|
| `s1-normal` | Full attendance, all machines running, no material gaps, 3 open WOs |
| `s2-absenteeism` | Marks 20% of Shift A operators as absent |
| `s3-breakdown` | Sets Machine M5 to Breakdown status |
| `s4-material-shortage` | Sets material availability for PROD-Y to 600 (WO requires 800) |
| `s5-priority-conflict` | Creates two High-priority WOs competing for Line-1 |
| `s6-skill-gap` | Removes all Line-3 / M5 skill certifications from present operators |
| `s7-underachievement` | Injects actuals at 65% of planned qty for Line-3 |
| `s8-full-day` | Seeds attendance, WOs, and constraints for a full 3-shift day |

---

## 6. New Tool Registry

Create `src/fde_agent/agent/tools/sandhar/` as a Python package with the following
files. Register all tools in `_TOOL_REGISTRY` in `tools/__init__.py`.

**File layout:**
```
src/fde_agent/agent/tools/
  ├── __init__.py             (add sandhar tools to _TOOL_REGISTRY)
  ├── sandhar/
  │   ├── __init__.py
  │   ├── attendance.py       (attendance + skill tools)
  │   ├── workorders.py       (work order tools)
  │   ├── constraints.py      (constraint check tools)
  │   ├── planning.py         (allocation + plan assembly tools)
  │   └── execution.py        (actuals + alert creation tools)
```

### Tools in `sandhar/attendance.py`

| Tool name | Input | Returns | Used by agent |
|---|---|---|---|
| `sandhar_get_attendance_summary` | `plan_date: str`, `shift_code: str` | JSON: present/absent/late counts by designation | Attendance Analyst |
| `sandhar_get_present_operators` | `plan_date: str`, `shift_code: str` | JSON array: `[{employee_id, name, designation}]` of present operators | Attendance Analyst |
| `sandhar_get_operator_skills` | `employee_id: str` | JSON: `{lines: [{line_code, skill_level}], machines: [{machine_code, skill_level}]}` | Resource Allocator |
| `sandhar_find_qualified_operators` | `line_id: str`, `machine_id: str` (optional), `min_skill_level: int`, `plan_date: str`, `shift_code: str` | JSON array of qualified present operators | Resource Allocator |
| `sandhar_check_certification_expiry` | `plan_date: str` | JSON: employees whose certifications expire within 30 days | Attendance Analyst |

### Tools in `sandhar/workorders.py`

| Tool name | Input | Returns | Used by agent |
|---|---|---|---|
| `sandhar_get_open_work_orders` | `plan_date: str` | JSON array of open WOs sorted by priority rank | WO Prioritisation |
| `sandhar_get_work_order_detail` | `wo_id: str` | Full WO detail including operations | WO Prioritisation |
| `sandhar_rank_work_orders` | `wo_ids: list[str]`, `plan_date: str` | JSON: same list re-ordered by (due_date proximity, customer priority, WO priority) | WO Prioritisation |

### Tools in `sandhar/constraints.py`

| Tool name | Input | Returns | Used by agent |
|---|---|---|---|
| `sandhar_get_machine_status` | `plan_date: str` | JSON: all machines with current status. Running machines only used in allocation. | Constraint Validator |
| `sandhar_check_material_availability` | `plan_date: str` | JSON: products with shortfall_qty > 0. Includes affected WO numbers. | Constraint Validator |
| `sandhar_get_quality_holds` | `plan_date: str` | JSON: active quality holds on WOs or products | Constraint Validator |
| `sandhar_get_constraint_summary` | `plan_date: str` | Consolidated summary: affected WO count, blocked qty, by constraint type | Constraint Validator |

### Tools in `sandhar/planning.py`

| Tool name | Input | Returns | Used by agent |
|---|---|---|---|
| `sandhar_calculate_planned_qty` | `line_id: str`, `product_id: str`, `available_manpower: int`, `shift_code: str` | `{planned_qty: int, basis: str}` — qty based on cycle time × manpower × shift hours | Plan Generator |
| `sandhar_allocate_line` | `line_id: str`, `wo_id: str`, `shift_code: str`, `plan_date: str`, `operator_ids: list[str]`, `supervisor_id: str` | Creates `sandhar_resource_allocation` rows and a `sandhar_plan_detail` row; returns `plan_detail_id` | Resource Allocator |
| `sandhar_get_crossskill_candidates` | `line_id: str`, `plan_date: str`, `shift_code: str`, `count_needed: int` | JSON: operators from lower-priority lines who are qualified for this line | Resource Allocator |
| `sandhar_save_plan_header` | `plan_date: str`, `shift_code: str`, `confidence: str` | Creates `sandhar_plan_header` row; returns `plan_header_id` | Plan Generator |
| `sandhar_create_alert` | `alert_type: str`, `alert_message: str`, `severity: str`, `plan_date: str`, `shift_code: str`, kwargs | Creates `sandhar_alert` row; returns `alert_id` | Any agent |
| `sandhar_propose_plan_for_review` | Wraps `propose_action` with plan-specific display_data | Creates `AgentAction` in the HITL inbox; returns `action_id` | Plan Generator |

### Tools in `sandhar/execution.py`

| Tool name | Input | Returns | Used by agent |
|---|---|---|---|
| `sandhar_get_plan_for_supervisor` | `line_id: str`, `shift_code: str`, `plan_date: str` | Supervisor-filtered plan view | (API, not AI agent) |
| `sandhar_record_actuals` | `plan_detail_id: str`, `produced_qty: int`, `rejected_qty: int`, `rework_qty: int`, `downtime_minutes: int` | Saves `sandhar_production_actual`; returns `achievement_pct` | (API, not AI agent) |

> **Note:** Execution tools (`sandhar_get_plan_for_supervisor`, `sandhar_record_actuals`)
> are called directly from API route handlers, not from AI agents. They are utility
> functions in `execution.py` but do not need `@tool` decoration or registry entry.

---

## 7. New Agent Configurations

Six new YAML files in `agents/configs/`. All use the supervisor-worker pattern
already implemented in `supervisor_agent.py`.

### Agent Tree

```
sandhar-planning-supervisor
    │
    ├── sandhar-attendance-analyst
    │       Tools: sandhar_get_attendance_summary, sandhar_get_present_operators,
    │               sandhar_check_certification_expiry, sandhar_create_alert
    │       Output: shift-wise present operator list with skill summary
    │
    ├── sandhar-wo-prioritisation
    │       Tools: sandhar_get_open_work_orders, sandhar_rank_work_orders,
    │               sandhar_get_work_order_detail, sandhar_get_quality_holds
    │       Output: ranked WO list with constraint flags
    │
    ├── sandhar-constraint-validator
    │       Tools: sandhar_get_machine_status, sandhar_check_material_availability,
    │               sandhar_get_quality_holds, sandhar_get_constraint_summary,
    │               sandhar_create_alert
    │       Output: constraint summary; alerts created for each constraint
    │
    ├── sandhar-resource-allocator
    │       Tools: sandhar_find_qualified_operators, sandhar_get_operator_skills,
    │               sandhar_get_crossskill_candidates, sandhar_allocate_line,
    │               sandhar_create_alert
    │       Output: resource_allocation rows written; gap alerts created
    │
    └── sandhar-plan-generator
            Tools: sandhar_calculate_planned_qty, sandhar_save_plan_header,
                    sandhar_create_alert, sandhar_propose_plan_for_review
            Output: plan_header + plan_detail rows written; plan proposed to HITL inbox
            Feature flags:
              enable_refinement: true
              refinement_agent: "sandhar-plan-refiner"
              refinement_preview: "sandhar-plan"

sandhar-plan-refiner  (standalone — invoked per chat turn in the refinement canvas)
    Tools: sandhar_refine_get_plan, sandhar_refine_update_qty, sandhar_refine_move_wo,
           sandhar_refine_add_wo, sandhar_refine_remove_wo, sandhar_refine_explain_constraint
    Writes to: sandhar_plan_detail (same table the supervisor writes during generation)
    Config: agents/configs/sandhar-plan-refiner.yaml
```

### `sandhar-planning-supervisor.yaml`

```yaml
agent:
  name: sandhar-planning-supervisor
  type: supervisor
  description: >
    Orchestrates the daily production planning pipeline for Sandhar's assembly
    shop floor. Calls five specialist workers in sequence and assembles a
    shift-wise production plan for human review.

  workers:
    - agent: sandhar-attendance-analyst
      description: >
        Analyses shift-wise attendance and skill availability. Call first —
        all subsequent planning depends on knowing who is present.

    - agent: sandhar-wo-prioritisation
      description: >
        Imports and ranks open work orders by priority and due date.
        Identifies WOs blocked by quality holds.

    - agent: sandhar-constraint-validator
      description: >
        Checks machine availability, material stock, and quality holds.
        Creates alerts for each constraint found.

    - agent: sandhar-resource-allocator
      description: >
        Matches present operators to lines and machines. Detects skill gaps.
        Creates allocation records and gap alerts.

    - agent: sandhar-plan-generator
      description: >
        Assembles the final shift-wise plan from allocation output.
        Calculates planned quantities. Proposes the plan for planner review.

  routing:
    max_rounds: 12   # 5 workers × 2 (each may report back once) + buffer

  model:
    provider: anthropic
    name: claude-haiku-4-5-20251001
    temperature: 0.0
    max_tokens: 1024
    max_cost_usd: 0.20

  system_prompt: |
    You are the production planning supervisor for Sandhar Group's assembly
    shop floor. Your job is to coordinate five specialist agents to generate
    a complete shift-wise daily production plan.

    Call workers in this EXACT sequence:
    1. sandhar-attendance-analyst   — must run first
    2. sandhar-wo-prioritisation    — must run second
    3. sandhar-constraint-validator — must run third
    4. sandhar-resource-allocator   — must run fourth; depends on outputs from 1–3
    5. sandhar-plan-generator       — must run last; depends on outputs from 1–4

    Always pass context to each worker:
    - plan_date: the date being planned (from [Runtime context])
    - shifts: the shifts to plan (from [Runtime context])
    - Pass the output summary of each worker as context to the next worker.

    Do not skip workers. Do not call a worker out of sequence.
    If a worker reports a blocking error, call sandhar-constraint-validator
    to create an alert, then continue planning for unaffected lines.
    Call finish only when sandhar-plan-generator has completed.

  inputs:
    plan_date:
      type: string
      required: true
      description: "Date for which to generate the plan (YYYY-MM-DD)"
    shifts:
      type: string
      required: false
      default: "A,B,C"
      description: "Comma-separated shift codes to plan"

  guardrails:
    max_iterations: 20
    timeout_seconds: 600
    blocked_patterns:
      - "ignore previous instructions"

  observability:
    langsmith_tracing: true
    log_inputs: true
    log_outputs: true
    log_tool_calls: false
```

### `sandhar-attendance-analyst.yaml`

```yaml
agent:
  name: sandhar-attendance-analyst
  description: >
    Analyses shift-wise attendance for a planning date. Maps present operators
    to their skills. Raises certification expiry alerts.
  version: "1.0.0"

  inputs:
    plan_date:
      type: string
      required: true
      description: "Date to analyse (YYYY-MM-DD)"
    shifts:
      type: string
      required: false
      default: "A,B,C"
      description: "Comma-separated shift codes"

  model:
    provider: anthropic
    name: claude-sonnet-4-6
    temperature: 0.0
    max_tokens: 2048
    max_cost_usd: 0.50

  system_prompt: |
    You are an attendance and skills analyst for Sandhar's assembly floor.
    Read plan_date and shifts from [Runtime context].

    Your workflow:
    1. Call sandhar_get_attendance_summary for each shift in shifts.
    2. Call sandhar_get_present_operators for each shift to get the full list.
    3. Call sandhar_check_certification_expiry for plan_date.
       For each expiring certification, call sandhar_create_alert with:
         alert_type: "certification_expiry", severity: "low".
    4. Report: for each shift, present operator count, supervisor count,
       and a summary of available skill coverage across lines.

  tools:
    - name: sandhar_get_attendance_summary
      enabled: true
    - name: sandhar_get_present_operators
      enabled: true
    - name: sandhar_check_certification_expiry
      enabled: true
    - name: sandhar_create_alert
      enabled: true

  guardrails:
    max_iterations: 20
    timeout_seconds: 120
```

### `sandhar-wo-prioritisation.yaml`

```yaml
agent:
  name: sandhar-wo-prioritisation
  description: >
    Imports and ranks open work orders. Identifies WOs under quality hold.
  version: "1.0.0"

  inputs:
    plan_date:
      type: string
      required: true
      description: "Planning date (YYYY-MM-DD)"

  model:
    provider: anthropic
    name: claude-sonnet-4-6
    temperature: 0.0
    max_tokens: 2048
    max_cost_usd: 0.50

  system_prompt: |
    You are a work order analyst for Sandhar Group.
    Read plan_date from [Runtime context].

    Your workflow:
    1. Call sandhar_get_open_work_orders for plan_date.
    2. Call sandhar_get_quality_holds for plan_date.
       Mark any WO in the hold list as BLOCKED.
    3. Call sandhar_rank_work_orders with the non-blocked WO IDs.
    4. Report: ranked WO list with quantities, due dates, priorities,
       and which WOs are blocked by quality holds.

  tools:
    - name: sandhar_get_open_work_orders
      enabled: true
    - name: sandhar_get_quality_holds
      enabled: true
    - name: sandhar_rank_work_orders
      enabled: true
    - name: sandhar_get_work_order_detail
      enabled: true

  guardrails:
    max_iterations: 15
    timeout_seconds: 120
```

### `sandhar-constraint-validator.yaml`

```yaml
agent:
  name: sandhar-constraint-validator
  description: >
    Validates all production constraints: machine availability, material stock,
    quality holds. Creates alerts for each constraint found.
  version: "1.0.0"

  inputs:
    plan_date:
      type: string
      required: true
      description: "Planning date (YYYY-MM-DD)"
    shifts:
      type: string
      required: false
      default: "A,B,C"

  model:
    provider: anthropic
    name: claude-sonnet-4-6
    temperature: 0.0
    max_tokens: 2048
    max_cost_usd: 0.50

  system_prompt: |
    You are a constraint validation agent for Sandhar's assembly floor.
    Read plan_date and shifts from [Runtime context].

    Your workflow:
    1. Call sandhar_get_machine_status. For each machine NOT in 'running' status,
       call sandhar_create_alert:
         alert_type: "machine_breakdown" (or "maintenance" if status=maintenance),
         severity: "critical" (breakdown) or "high" (maintenance).
    2. Call sandhar_check_material_availability for plan_date.
       For each product with shortfall_qty > 0, call sandhar_create_alert:
         alert_type: "material_shortage", severity: "high".
    3. Call sandhar_get_constraint_summary for plan_date.
    4. Report the full constraint summary: which machines are unavailable,
       which products have material shortages, which WOs are blocked,
       total blocked quantity.

  tools:
    - name: sandhar_get_machine_status
      enabled: true
    - name: sandhar_check_material_availability
      enabled: true
    - name: sandhar_get_quality_holds
      enabled: true
    - name: sandhar_get_constraint_summary
      enabled: true
    - name: sandhar_create_alert
      enabled: true

  guardrails:
    max_iterations: 20
    timeout_seconds: 120
```

### `sandhar-resource-allocator.yaml`

```yaml
agent:
  name: sandhar-resource-allocator
  description: >
    Allocates present operators to lines and machines based on ranked work orders,
    constraints, and skill matrix. Detects and alerts on manpower and skill gaps.
  version: "1.0.0"

  feature_flags:
    human_in_the_loop: false   # allocation is written to DB; planner reviews the full plan

  inputs:
    plan_date:
      type: string
      required: true
    shifts:
      type: string
      required: false
      default: "A,B,C"
    attendance_summary:
      type: string
      required: false
      description: "JSON summary from sandhar-attendance-analyst"
    ranked_work_orders:
      type: string
      required: false
      description: "JSON ranked WO list from sandhar-wo-prioritisation"
    constraint_summary:
      type: string
      required: false
      description: "JSON constraint summary from sandhar-constraint-validator"

  model:
    provider: anthropic
    name: claude-sonnet-4-6
    temperature: 0.0
    max_tokens: 4096
    max_cost_usd: 1.00

  system_prompt: |
    You are a resource allocation agent for Sandhar's assembly shop floor.
    Read all inputs from [Runtime context].

    Allocation logic (apply for each shift in shifts):
    1. For each WO in ranked order (highest priority first):
       a. Identify the line required (from WO's product.line_id).
       b. Check if that line's machine is available (not in machine_breakdown list).
          If machine unavailable, skip this WO — it was flagged by constraint-validator.
       c. Determine standard_manpower required (from product master).
       d. Call sandhar_find_qualified_operators for that line, shift, plan_date,
          min_skill_level=2.
       e. Allocate up to standard_manpower operators from the qualified list.
       f. If available_qualified_operators < standard_manpower:
          - Call sandhar_get_crossskill_candidates for this line.
          - If cross-skill candidates found: allocate them too.
          - Calculate manpower_gap = standard_manpower - total_allocated.
          - If gap remains: call sandhar_create_alert (alert_type="manpower_shortage",
            severity="high") with line, shift, gap count, and resolution options:
            ["overtime", "accept_reduced_qty", "cross_skill_from_lower_priority"].
       g. Find the supervisor present on this shift qualified for this line (skill_level >= 3).
       h. Call sandhar_allocate_line with line_id, wo_id, shift_code, plan_date,
          operator_ids, supervisor_id.
    2. After all WOs processed, report: total allocations made, gaps by line,
       alerts created.

  tools:
    - name: sandhar_find_qualified_operators
      enabled: true
    - name: sandhar_get_operator_skills
      enabled: true
    - name: sandhar_get_crossskill_candidates
      enabled: true
    - name: sandhar_allocate_line
      enabled: true
    - name: sandhar_create_alert
      enabled: true

  guardrails:
    max_iterations: 60
    timeout_seconds: 300
```

### `sandhar-plan-generator.yaml`

```yaml
agent:
  name: sandhar-plan-generator
  description: >
    Assembles the final shift-wise production plan from allocation output.
    Calculates planned quantities. Proposes the plan for planner review via HITL.
  version: "1.0.0"

  feature_flags:
    human_in_the_loop: true   # plan is proposed to HITL inbox for planner approval

  inputs:
    plan_date:
      type: string
      required: true
    shifts:
      type: string
      required: false
      default: "A,B,C"
    allocation_summary:
      type: string
      required: false
      description: "JSON allocation summary from sandhar-resource-allocator"
    active_alert_count:
      type: integer
      required: false
      default: 0

  model:
    provider: anthropic
    name: claude-sonnet-4-6
    temperature: 0.0
    max_tokens: 4096
    max_cost_usd: 1.00

  system_prompt: |
    You are the production plan generator for Sandhar Group.
    Read all inputs from [Runtime context].

    Your workflow:
    1. For each shift in shifts, for each plan_detail_id in allocation_summary:
       a. Call sandhar_calculate_planned_qty with line_id, product_id,
          available_manpower (from allocation), shift_code.
       b. Update the plan_detail with planned_qty.
    2. Determine overall plan confidence:
       - high:   active_alert_count = 0 and all lines fully staffed
       - medium: active_alert_count between 1 and 3, or some gaps filled by cross-skill
       - low:    active_alert_count >= 4, or any line has unresolved manpower gap
    3. Call sandhar_save_plan_header for each shift with plan_date, shift_code, confidence.
    4. Call sandhar_propose_plan_for_review with:
         title: "Production Plan for {plan_date} — Shift {shift_code}"
         summary: "{total_planned_qty} units across {line_count} lines, {active_alert_count} alerts"
         confidence: (from step 2)
         display_data: JSON array showing each line's planned qty, operators, WO
         approval_action: '{"method":"POST","url":"/api/v1/sandhar/plan/{header_id}/approve"}'
    5. Report: plan header IDs created, total planned qty per shift, confidence, alert count.

  tools:
    - name: sandhar_calculate_planned_qty
      enabled: true
    - name: sandhar_save_plan_header
      enabled: true
    - name: sandhar_create_alert
      enabled: true
    - name: sandhar_propose_plan_for_review
      enabled: true

  guardrails:
    max_iterations: 40
    timeout_seconds: 300
```

---

## 8. New UI Pages

New Jinja2 templates in `src/fde_agent/templates/sandhar/`. New routes registered
in `src/fde_agent/api/routes/sandhar/pages.py`.

### 8.1 Page Inventory

| URL | Template | Persona | Description |
|---|---|---|---|
| `/sandhar` | `sandhar/dashboard.html` | Plant Manager | Command centre: live KPI cards, active alert badges, current shift status |
| `/sandhar/plan` | `sandhar/plan.html` | Planner | Plan generation, progress polling, plan review with **"Refine with AI" canvas**, approve/reject |
| `/sandhar/floor` | `sandhar/floor.html` | Supervisor | Line-filtered plan view, operator list, actuals entry, disruption reporting |
| `/sandhar/master` | `sandhar/master.html` | HR Admin | Master data CRUD: employees, lines, machines, products, customers, shifts |
| `/sandhar/simulation` | `sandhar/simulation.html` | Demo presenter | Simulation control panel: scenario buttons, attendance injector, state reset |

**Implemented templates:**
- `sandhar/dashboard.html` — command centre
- `sandhar/plan.html` — plan generation + refinement canvas (deep-linked via `?refine=<action_id>`)
- `sandhar/floor.html` — supervisor view
- `sandhar/master.html` — master data CRUD
- `sandhar/simulation.html` — demo control panel
- `sandhar/_refine_preview_sandhar-plan.html` — server-rendered plan preview partial for the refinement canvas

### 8.2 Key Page Designs

#### `/sandhar/plan` — Plan Generation and Review

This is the most important page. It has three states:

**State 1 — Pre-generation:** Shows attendance summary, open WO count, constraint summary, and a "Generate Plan for [date]" button.

**State 2 — Generating:** Shows a live progress view of the agent run, using polling against `GET /api/v1/runs/{run_id}`. Each worker's status is shown as a step: Attendance → WO Priority → Constraints → Allocation → Plan. The page updates in real time using JavaScript polling every 2 seconds.

**State 3 — Review:** Once the agent run completes, shows:
- Three shift tabs (A / B / C)
- Per shift: plan table (line, product, WO, planned qty, operators, supervisor)
- Exception cards (same component as `/approvals` but domain-specific content)
- Plan confidence badge (High / Medium / Low)
- Summary statistics bar: total qty, manpower utilization %, active alerts
- Approve and Reject buttons (calls `/api/v1/sandhar/plan/{header_id}/approve`)

The plan cards in the HITL `/approvals` inbox also show up here when `propose_plan_for_review` creates them — planners can approve directly from `/approvals` or from `/sandhar/plan`.

#### `/sandhar/floor` — Supervisor View

Filtered by `line_id` (query param). Shows:
- Plan header: date, shift, WO reference
- Operator assignment table: name, role, machine assigned
- Production target card: planned qty, shift time
- Actuals form (revealed at shift end): produced, rejected, rework, downtime
- Disruption report button (modal with type selector + description)

#### `/sandhar/simulation` — Demo Control Panel

Demo-mode only. Shown as a control bar at the bottom of every Sandhar page when
`DEMO_MODE=true` in environment, or as a standalone page at `/sandhar/simulation`.

Contains:
- Date selector ("Set current date")
- Shift selector ("Set active shift: A / B / C")
- Scenario buttons: one button per scenario (S1–S8 from PRD Section 13.2)
- "Reset to clean state" button
- Status indicator showing what data is currently loaded

---

## 9. End-to-End Data Flows

### 9.1 Daily Plan Generation Flow

```
Planner opens /sandhar/plan
  │  Selects date: 2026-07-01
  │  Clicks "Generate Plan"
  │
  ▼
POST /api/v1/sandhar/plan/generate
  ├── Validates: attendance data exists for date
  ├── Validates: at least one open WO exists
  ├── INSERT agent_runs (status=pending, agent="sandhar-planning-supervisor")
  ├── run_agent_task.delay(run_id, "sandhar-planning-supervisor", message)
  └── returns {agent_run_id, task_id}  ← 202

Browser polls GET /api/v1/runs/{run_id} every 2 seconds
  └── Shows each worker's progress in the step indicator on /sandhar/plan

Celery worker picks up task
  └── run_agent("sandhar-planning-supervisor", message, {plan_date, shifts})
        │
        └── run_supervisor() — pharma supervisor pattern, same code
              │
              ├── sandhar-attendance-analyst runs
              │     tools: sandhar_get_attendance_summary × 3 shifts
              │             sandhar_get_present_operators × 3 shifts
              │     output: {shift_A: {present: 85, ...}, shift_B: {...}, ...}
              │
              ├── sandhar-wo-prioritisation runs
              │     tools: sandhar_get_open_work_orders, sandhar_rank_work_orders
              │     output: [{wo_id, wo_number, priority_rank, blocked: false}, ...]
              │
              ├── sandhar-constraint-validator runs
              │     tools: sandhar_get_machine_status, sandhar_check_material_availability
              │             sandhar_create_alert (for each constraint)
              │     writes: sandhar_alert rows
              │     output: {machines_down: ["M5"], material_short: ["PROD-Y"], ...}
              │
              ├── sandhar-resource-allocator runs
              │     tools: sandhar_find_qualified_operators × (lines × shifts)
              │             sandhar_allocate_line × (successful allocations)
              │             sandhar_create_alert × (gaps found)
              │     writes: sandhar_resource_allocation rows
              │             sandhar_plan_detail rows (without planned_qty yet)
              │             sandhar_alert rows (for gaps)
              │
              └── sandhar-plan-generator runs
                    tools: sandhar_calculate_planned_qty × plan_details
                            sandhar_save_plan_header × shifts
                            sandhar_propose_plan_for_review × shifts
                    writes: sandhar_plan_header rows (status=draft)
                            updates sandhar_plan_detail with planned_qty
                            agent_actions rows (HITL inbox)
                    output: {plan_headers: [...], total_qty: 2500, confidence: "medium"}

UPDATE agent_runs SET status=completed

Browser polling detects completed
  └── /sandhar/plan re-renders in "Review" state
        Shows shift-wise plan table + exception cards
        Planner reviews, resolves exceptions, clicks Approve
```

### 9.2 Plan Approval Flow

```
Planner clicks "Approve" on /sandhar/plan (or approves from /approvals HITL inbox)
  │
  ▼
POST /api/v1/actions/{action_id}/approve    (existing HITL endpoint, unchanged)
  ├── No drift check (plans don't have track_resource_state)
  ├── Executes approval_action:
  │     POST /api/v1/sandhar/plan/{header_id}/approve
  │       └── UPDATE sandhar_plan_header SET status='approved', approved_at=now()
  │       └── UPDATE sandhar_work_orders SET status='planned' for included WOs
  └── AgentAction status → approved

Supervisor opens /sandhar/floor?line=L001&shift=A
  └── GET /api/v1/sandhar/execution/supervisor-view?line_id=...&shift_code=A&plan_date=...
        └── Returns plan_detail rows for that line + allocated operator list
        └── Supervisor sees their plan
```

### 9.3 Mid-Shift Disruption Flow

```
Supervisor on /sandhar/floor reports "Machine Breakdown" for M5
  │
  ▼
POST /api/v1/sandhar/machines/{M5_id}/status
  body: {machine_status: "breakdown", reason: "Hydraulic failure", shift_code: "B"}
  ├── Inserts sandhar_machine_status row
  └── Calls sandhar_create_alert internally:
        alert_type: "machine_breakdown", severity: "critical"

Alert appears in /sandhar/alerts + badge on /sandhar/dashboard
  └── Planner sees Critical alert; opens /sandhar/plan for current shift

Planner triggers re-generation for Shift B:
POST /api/v1/sandhar/plan/generate
  body: {plan_date: "2026-07-01", shifts: "B", override_context: {replan: true}}
  └── Runs the full agent pipeline again
  └── sandhar-constraint-validator detects M5 breakdown from sandhar_machine_status
  └── sandhar-resource-allocator skips Line-3 (M5 required) or finds alternate
  └── New plan version created (version=2); old version marked 'superseded'
  └── New HITL action created for Shift B re-plan

Planner approves re-plan → supervisors see updated plan
```

### 9.4 Shift Close Flow

```
Supervisor on /sandhar/floor at shift end
  │  Enters actuals: produced_qty=650, rejected=30, rework=20, downtime=15
  │
  ▼
POST /api/v1/sandhar/execution/{plan_detail_id}/actuals
  ├── Inserts sandhar_production_actual
  │     achievement_pct = (650 / 700) * 100 = 92.8%
  └── Returns {achievement_pct: 92.8}

POST /api/v1/sandhar/execution/{plan_detail_id}/shift-close
  ├── Aggregates actuals across all lines for this shift+date
  ├── Computes KPIs:
  │     plan_achievement_pct = total_produced / total_planned * 100
  │     manpower_utilization_pct = allocated / present * 100
  │     rejection_rate_pct = total_rejected / total_produced * 100
  ├── Upserts sandhar_daily_kpi row
  ├── If plan_achievement_pct < 70:
  │     Creates sandhar_alert (production_delay, severity=high)
  └── UPDATE sandhar_plan_header SET status='completed'

/sandhar/kpi dashboard updates with the new KPI row
```

---

## 10. HITL Integration

The existing HITL system (`/approvals`, `agent_actions` table, `propose_action` tool)
is **reused without modification** for production plan approval. Here is exactly how
the Sandhar plan feeds into it.

### 10.1 What `sandhar_propose_plan_for_review` Does

This tool (in `sandhar/planning.py`) is a thin wrapper around the existing `propose_action`
platform tool. It formats the production plan data into the standard `AgentAction` shape:

```python
propose_action(
    agent_name="sandhar-plan-generator",
    title="Production Plan 2026-07-01 — Shift A",
    summary="2 lines · 1,000 units planned · 85 operators · 2 alerts",
    confidence="medium",
    reasoning="Plan generated with 1 machine constraint (M5 breakdown) and cross-skill coverage applied on Line-3.",
    display_data=json.dumps([
        {"label": "Shift", "value": "A (06:00–14:00)"},
        {"label": "Lines Active", "value": "2 of 3"},
        {"label": "Total Planned Qty", "value": "1,000 units"},
        {"label": "Manpower", "value": "85 present, 80 allocated (94%)"},
        {"label": "Active Alerts", "value": "2 (1 Critical, 1 High)"},
        {"label": "Line-1", "value": "PROD-X · WO-1001 · 700 units · 22 operators"},
        {"label": "Line-2", "value": "PROD-Z · WO-1003 · 300 units · 12 operators"},
        {"label": "Line-3", "value": "PROD-Y — SKIPPED (M5 breakdown)"},
    ]),
    approval_action=json.dumps({
        "method": "POST",
        "url": "/api/v1/sandhar/plan/{header_id}/approve",
        "url_params": {"header_id": str(plan_header_id)},
        "body": {}
    }),
    tags=json.dumps(["sandhar", "production-plan", f"shift-{shift_code}"])
)
```

### 10.2 Where Plans Appear for Review

Production plan actions appear in:
- **`/approvals`** (existing page) — filtered by `agent_name=sandhar-plan-generator`
- **`/sandhar/plan`** (new page) — embedded view showing the same HITL cards

The planner can approve from either page. Both trigger the same `POST /api/v1/actions/{id}/approve`.

### 10.3 Stale Window

Set `stale_after: "8h"` on `sandhar-plan-generator.yaml`. A production plan proposed
at 5 AM that hasn't been reviewed by 1 PM is automatically staled. A new plan must
be generated for the next shift.

---

## 11. Simulation Layer

### 11.1 Seed Data (`simulation_seed.py`)

Create `src/fde_agent/api/routes/sandhar/simulation_seed.py`. This module contains
the canonical mock dataset used by `POST /api/v1/sandhar/simulation/seed`.

**Mock dataset summary (matches PRD Section 13.1):**

```
Employees:  50 operators + 10 supervisors = 60 total
            Distributed: 20 on shift A, 20 on shift B, 20 on shift C

Lines:      3 lines
            L001 — Assembly Line-1 (capacity 900 units/shift)
            L002 — Assembly Line-2 (capacity 600 units/shift)
            L003 — Assembly Line-3 (capacity 500 units/shift)

Machines:   8 machines
            L001: M001 (primary), M002 (secondary)
            L002: M003 (primary), M004 (secondary), M005 (auxiliary)
            L003: M005 (critical — used in S3/S6 scenarios), M006, M007

Products:   5 products
            PROD-X — Mirror bracket (cycle time: 2.5 min, std manpower: 20, line: L001)
            PROD-Y — Door handle assembly (cycle time: 3.0 min, std manpower: 15, line: L003)
            PROD-Z — Indicator housing (cycle time: 1.8 min, std manpower: 12, line: L002)
            PROD-A — Wiper arm (cycle time: 2.0 min, std manpower: 18, line: L001)
            PROD-B — Clutch cable set (cycle time: 4.0 min, std manpower: 10, line: L002)

Customers:  4 OEM customers
            CUST-OEM-A — Maruti Suzuki (priority: critical)
            CUST-OEM-B — Hero MotoCorp (priority: high)
            CUST-OEM-C — TVS Motor (priority: high)
            CUST-OEM-D — Mahindra (priority: medium)

Shifts:     A: 06:00–14:00, B: 14:00–22:00, C: 22:00–06:00
            Working hours: 8h (7h productive after 1h break)

Skill matrix:
            Every operator has at least one line skill (level 2+)
            ~30% are cross-skilled on two lines
            ~10% are certified to level 4 (Expert) on their primary line
            All supervisors have skill level 3+ on at least one line
```

### 11.2 Scenario Implementations

Each `POST /api/v1/sandhar/simulation/scenario/{id}` calls a specific function in
`simulation_seed.py`:

| Scenario | What the function does to DB state |
|---|---|
| `s1-normal` | Seeds attendance at 100%, all machines Running, 3 open WOs, no material gaps |
| `s2-absenteeism` | `UPDATE sandhar_attendance SET status='absent' WHERE shift_code='A' AND RANDOM() < 0.20` |
| `s3-breakdown` | `INSERT sandhar_machine_status (machine_id=M005, machine_status='breakdown')` |
| `s4-material-shortage` | `UPDATE sandhar_material_availability SET available_qty=600, shortfall_qty=200 WHERE product_id=PROD-Y` |
| `s5-priority-conflict` | Inserts two WOs both requiring Line-1 with priority=High and due_date=today |
| `s6-skill-gap` | `UPDATE sandhar_employee_skill_matrix SET active_flag=false WHERE line_id=L003 AND machine_id=M005` |
| `s7-underachievement` | Inserts production actuals at 65% for Line-3 past shift midpoint |
| `s8-full-day` | Calls s1-normal + sets all 3 shifts' attendance + 5 WOs |

---

## 12. Alembic Migration Plan

Add migrations in numbered sequence after the existing 8 migrations.
Each migration is a single file in `alembic/versions/`.

| Migration # | Tables Created |
|---|---|
| `009_sandhar_master` | `sandhar_employees`, `sandhar_lines`, `sandhar_machines`, `sandhar_customers`, `sandhar_products`, `sandhar_shifts` |
| `010_sandhar_skills_attendance` | `sandhar_employee_skill_matrix`, `sandhar_attendance` |
| `011_sandhar_workorders` | `sandhar_work_orders`, `sandhar_work_order_operations` |
| `012_sandhar_constraints` | `sandhar_machine_status`, `sandhar_material_availability`, `sandhar_quality_hold` |
| `013_sandhar_planning` | `sandhar_resource_allocation`, `sandhar_plan_header`, `sandhar_plan_detail`, `sandhar_production_actual` |
| `014_sandhar_alerts_kpi` | `sandhar_alert`, `sandhar_daily_kpi` |

Run with: `alembic upgrade head`

---

## 13. Implementation Sequence

Build in this order to ensure each phase is independently testable.

### Phase 1 — Data Foundation (Migrations + Master Data API)
1. Write and run migrations 009–014
2. Implement master data API (`sandhar/master.py`): employees, lines, machines, products, customers, shifts
3. Implement `POST /api/v1/sandhar/simulation/seed` with the mock dataset
4. Implement `/sandhar/master` UI page
5. **Verify:** Seed runs; master data is readable via API

### Phase 2 — Domain Data APIs
6. Implement skill matrix API and tools (`sandhar/skills.py`, `attendance.py`)
7. Implement attendance API (`sandhar/attendance.py`)
8. Implement work orders API (`sandhar/workorders.py`)
9. Implement constraints API (`sandhar/constraints.py`)
10. Implement all simulation scenarios (S1–S8)
11. **Verify:** All scenarios seed correctly; query APIs return expected data

### Phase 3 — Agent Tools
12. Implement all tools in `sandhar/attendance.py`, `sandhar/workorders.py`, `sandhar/constraints.py`
13. Implement planning tools in `sandhar/planning.py`
14. Register all tools in `tools/__init__.py`
15. **Verify:** Each tool works standalone (unit test with seeded DB data)

### Phase 4 — Agent Configs and Planning Engine
16. Write all 6 agent YAML files
17. Register agents: `POST /api/v1/agents/register` for each
18. Activate agents: `PATCH /api/v1/agents/{name}/activate`
19. Test each worker agent individually (direct `POST /run`)
20. Test the supervisor end-to-end (direct `POST /run` on `sandhar-planning-supervisor`)
21. **Verify:** Plan generates; plan_header + plan_detail rows written; HITL actions created

### Phase 5 — Plan API and HITL
22. Implement planning API (`sandhar/planning.py` routes)
23. Verify HITL approval flow: plan action appears in `/approvals`; approval calls plan approve endpoint
24. Implement plan versioning (re-generation creates new version; old marked superseded)
25. **Verify:** Full plan generation → HITL review → approval → plan status = approved

### Phase 6 — Execution and Alerts
26. Implement execution API (`sandhar/execution.py`)
27. Implement alerts API (`sandhar/alerts.py`)
28. Implement KPI API (`sandhar/kpi.py`)
29. **Verify:** Actuals entry → KPI computation → dashboard update; disruption → alert creation

### Phase 7 — UI
30. Implement `/sandhar/plan` page with agent progress view
31. Implement `/sandhar/floor` supervisor view
32. Implement `/sandhar/dashboard` command centre
33. Implement `/sandhar/alerts` and `/sandhar/kpi`
34. Implement `/sandhar/simulation` control panel
35. **Verify:** Full demo walkthrough of all 8 scenarios end to end

---

## 15. Plan Refinement Canvas — Implementation Notes

This section captures the as-built behaviour for the "Refine with AI" feature on the plan page. The generic platform design is in `docs/plan-refine-feature.md` and `docs/system-design.md` (Section 8). This section documents Sandhar-specific details and implementation decisions.

### 15.1 Feature Flag Location

The `enable_refinement` flag is on **`sandhar-plan-generator`**, not on `sandhar-planning-supervisor`. This is because `AgentAction.agent_name` is set to the agent that *created* the action — `propose_plan_for_review` (called by `sandhar-plan-generator`) sets `agent_name = "sandhar-plan-generator"`. The platform reads `feature_flags` from this agent's config, not from the supervisor.

### 15.2 YAML-First Feature Flag Lookup

`actions.py` uses a YAML-first, DB-fallback pattern when reading `feature_flags`:

```python
async def _agent_flags(session, agent_name):
    try:
        cfg = load_agent_config(agent_name)   # reads YAML from disk
        if cfg.feature_flags:
            return cfg.feature_flags
    except Exception:
        pass
    row = await session.execute(select(Agent).where(Agent.name == agent_name))
    agent = row.scalar_one_or_none()
    return agent.config.get("feature_flags", {}) if agent else {}
```

This ensures the YAML is always the source of truth. The DB `Agent.config` snapshot can be stale.

### 15.3 SSE Buffer Parsing

The SSE streaming endpoint (`POST /refine/message`) emits events separated by `\n\n`. The browser JS must split the buffer on `\n\n` (complete events), not on `\n` (lines). Splitting on `\n` and using `.pop()` to buffer incomplete lines fails when the final `done` event arrives in a split TCP chunk:

```javascript
const processBuffer = async (flush = false) => {
  const parts = buffer.split('\n\n');
  buffer = flush ? '' : parts.pop();
  for (const part of parts) {
    for (const line of part.split('\n')) {
      if (!line.startsWith('data: ')) continue;
      // parse and handle event
    }
  }
};
```

### 15.4 Deep Linking

When the canvas opens, `history.pushState` adds `?refine=<action_id>` to the URL. On page reload, `init()` reads the URL param, finds the matching plan from the already-loaded plans array, and calls `openRefineCanvas()` which calls `refine/start` (idempotent — returns the existing `active` session) and `refine/messages` to restore history.

"← Back to Plan" does **not** call `refine/close`. The session stays `active` so history is preserved across refreshes.

### 15.5 Preview Sync

The preview pane loads `GET /sandhar/plan/{header_id}/refine-preview` on every canvas open and after every `event:done`. This is a server-rendered HTML fragment with live DB data — no stale state is possible. The `planned_manpower` field is shown as `X/Y operators` where Y is `available_manpower`.

### 15.6 `planned_manpower` Support

The `sandhar_refine_update_qty` tool accepts an optional `new_planned_manpower` parameter. The `PATCH /api/v1/sandhar/plan/{header_id}/details/{detail_id}` endpoint and `UpdatePlanDetailRequest` schema both accept `planned_manpower: int | None`.

---

## 14. What the Platform Reuses Unchanged

| Platform Component | How Sandhar Uses It | No Change Required |
|---|---|---|
| YAML config loader (`loader.py`) | Loads all 6 `sandhar-*.yaml` configs | None |
| `AgentConfig` Pydantic model | Used as-is for all Sandhar agent configs | None |
| `react_agent.run_agent()` | Called by Celery task for all Sandhar agents | None |
| `supervisor_agent.py` | `sandhar-planning-supervisor` uses this graph builder unchanged | None |
| `run_agent_task` Celery task | Dispatches Sandhar plan runs identically to Fundly runs | None |
| `agent_runs` table | Records every Sandhar planning run | None |
| `agent_actions` table | Stores plan approval HITL records | None |
| `propose_action` tool | Called by `sandhar_propose_plan_for_review` wrapper | None |
| `/approvals` UI page | Displays Sandhar plan approval cards | None |
| `/api/v1/actions/*` endpoints | All HITL endpoints work for Sandhar plan actions | None |
| LangSmith tracing | All Sandhar agent runs traced automatically | None |
| OTel → Jaeger | Sandhar API routes and agent runs traced automatically | None |
| `X-API-Key` auth | Same authentication for all Sandhar endpoints | None |
| Docker Compose | No new services needed — same 6 containers | None |
| Alembic | New migrations follow existing pattern — `alembic upgrade head` picks them up | None |
| GitOps workflow | Sandhar agent YAMLs committed and deployed identically to Fundly agents | None |

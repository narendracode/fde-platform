# Sandhar Simulation Guide

A practical reference for using the Simulation Control Panel to demo the Smart Assembly Shop Floor Production Planning System.

---

## What the Simulation Is

The simulation gives you a self-contained, realistic demo environment. It populates the database with real-looking master data (employees, lines, machines, products, work orders), lets you inject attendance records, trigger pre-built failure scenarios, and then watch the planning agent respond to those conditions. At any point you can reset everything back to a clean state.

There is no live plant integration. All data is synthetic.

---

## Getting Started

### 1. Open the Simulation page

Navigate to `http://localhost:8000/sandhar/simulation` (or click **Simulation** in the nav).

### 2. Seed the database

Click **Seed Data**. This is always safe — it is idempotent and skips records that already exist.

What gets created:

| Entity | Count | Detail |
|---|---|---|
| Employees | 54 | 20 in Shift A (L001), 20 in Shift B (L002), 14 in Shift C (L003) — each split ~18 operators + 2 supervisors |
| Assembly Lines | 3 | L001 (900 units/shift), L002 (600), L003 (500) |
| Machines | 7 | M001–M007 spread across L001, L002, L003 |
| Products | 5 | Mirror Bracket (PROD-X), Wiper Arm (PROD-A), Indicator Housing (PROD-Z), Clutch Cable (PROD-B), Door Handle (PROD-Y) |
| Customers | 4 | Maruti Suzuki (critical), Hero MotoCorp (high), TVS Motor (high), Mahindra (medium) |
| Shifts | 3 | A = 06:00–14:00, B = 14:00–22:00, C = 22:00–06:00 |
| Skill Matrix | 64 | Each operator gets skill level 2 on their home line; first 5 per shift get level 1 on an adjacent line (cross-skill) |
| Work Orders | 20 | WO-SEED-0001 through WO-SEED-0020, mix of high/medium/low priority |
| Machine Statuses | 7 | All machines seeded as `running` |

Supervisors get skill level 3 on their home line.

### 3. Register the planning agents (one-time)

The planning agent must be registered in the database before you can generate a plan. Do this once after the first seed:

```bash
KEY="dev-secret-key-change-in-prod"
BASE="http://localhost:8000/api/v1/agents"

for agent in sandhar-planning-supervisor sandhar-attendance-analyst \
             sandhar-wo-prioritisation sandhar-constraint-validator \
             sandhar-resource-allocator sandhar-plan-generator; do
  curl -s -X POST "$BASE/register" \
    -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
    -d "{\"config_name\":\"$agent\"}"
done

# Activate the supervisor
curl -s -X PATCH "$BASE/sandhar-planning-supervisor/activate" -H "X-API-Key: $KEY"
```

Or do this through the Agents UI at `/agents`.

---

## Core Workflows

### Workflow A — Standard Planning Demo

The canonical end-to-end flow to show in a demo.

**Step 1: Seed data**
```
Simulation page → Seed Data
```

**Step 2: Inject attendance for the plan date**
```
Simulation page → Inject Attendance
  Date:          2026-07-03   (pick tomorrow or any future date)
  Shift:         A            (repeat for B and C)
  Absent %:      10
```

Do this for Shifts A, B, and C. Each call takes 20–20–14 employee records.

**Step 3: Generate the plan**
```
Plan page (/sandhar/plan)
  → Pick the same date
  → Select all shifts (A, B, C)
  → Click Generate Plan
```

The page polls every 3 seconds and shows 5-step progress (Attendance Analysis → WO Prioritisation → Constraint Check → Resource Allocation → Plan Generation).

**Step 4: Approve the plan**

Once the agent completes, shift tabs appear. Each shift card shows:
- Confidence level (high / medium / low)
- Line allocations with planned quantities
- Any alerts raised by the agent

Click **Approve Plan** per shift to approve. If `human_in_the_loop: true` is set in the plan-generator config, an approval request also lands in the Approvals inbox at `/approvals`.

**Step 5: Record actuals**
```
Floor page (/sandhar/floor)
  → Select the line (e.g., Assembly Line-1)
  → Select shift and date
  → Load Plan
  → Enter Actuals
```

Submit produced/rejected quantities. If achievement is below 70%, the system auto-creates an alert visible on the Command Centre.

---

### Workflow B — Inject Attendance via API

Use the API directly when you want finer control:

```bash
curl -s -X POST http://localhost:8000/api/v1/sandhar/simulation/attendance \
  -H "X-API-Key: dev-secret-key-change-in-prod" \
  -H "Content-Type: application/json" \
  -d '{
    "plan_date": "2026-07-03",
    "shift_code": "A",
    "absenteeism_pct": 25
  }'
```

**Fields:**

| Field | Type | Description |
|---|---|---|
| `plan_date` | `YYYY-MM-DD` | Date to inject attendance for |
| `shift_code` | `A`, `B`, or `C` | Which shift |
| `absenteeism_pct` | `0`–`100` | Percentage of employees randomly marked absent |

The call overwrites any existing records for that date+shift. Response:

```json
{ "created": 20, "present": 15, "absent": 5 }
```

Check results at:
```bash
curl "http://localhost:8000/api/v1/sandhar/attendance/summary?date=2026-07-03" \
  -H "X-API-Key: dev-secret-key-change-in-prod"
```

---

## The 8 Pre-Built Scenarios

Run any scenario from the Simulation page by clicking the card, or via API:

```bash
curl -s -X POST http://localhost:8000/api/v1/sandhar/simulation/scenario/{SCENARIO_ID} \
  -H "X-API-Key: dev-secret-key-change-in-prod" \
  -H "Content-Type: application/json" \
  -d '{"plan_date": "2026-07-03"}'
```

All scenarios operate on `today`'s date internally (the `plan_date` param is accepted for context).

---

### s1-normal — Normal Day

**What it does:**
- Creates 3 new work orders (WO-S1-…) across 3 products
- Seeds all 54 employees as `present` for all 3 shifts today
- Sets all 7 machines to `running`

**Use for:** Baseline demo. Everything works perfectly. The planning agent should produce a high-confidence plan.

**Expected agent output:** All 3 shifts get a plan, confidence = `high`, no alerts.

---

### s2-absenteeism — High Absenteeism (Shift A)

**What it does:**
- Randomly marks 20% of Shift A employees as `absent` for today
- Does not touch Shifts B or C

**Use for:** Demonstrating the attendance analysis step. The agent notices that available manpower for L001 is below the standard requirement and either downsizes the planned quantity or flags a `low_manpower` alert.

**Expected agent output:** Shift A plan confidence may drop to `medium`. Alert may be raised if headcount falls below the minimum required for any product's `standard_manpower`.

**What to verify:**
```bash
curl "http://localhost:8000/api/v1/sandhar/attendance/summary?date=2026-07-02" \
  -H "X-API-Key: dev-secret-key-change-in-prod"
# Shift A present count will be ~16 (80% of 20)
```

---

### s3-breakdown — Machine Breakdown

**What it does:**
- Sets machine M005 (Hydraulic Press on L003) to `breakdown` status
- Logs reason: "Hydraulic failure"
- Sets estimated restore time to 4 hours from now

**Use for:** Demonstrating constraint detection. The constraint validator agent sees M005 is down and flags L003 as capacity-constrained.

**Expected agent output:** L003 planned quantity reduced. A `machine_breakdown` alert raised. Plan confidence for Shift C drops.

**What to verify:**
```bash
curl "http://localhost:8000/api/v1/sandhar/constraints/summary?plan_date=2026-07-02" \
  -H "X-API-Key: dev-secret-key-change-in-prod"
# machine_constraints will list M005 as breakdown
```

To restore the machine before re-running a plan:
```bash
curl -s -X POST http://localhost:8000/api/v1/sandhar/machines/M005-UUID/status \
  -H "X-API-Key: dev-secret-key-change-in-prod" \
  -H "Content-Type: application/json" \
  -d '{"machine_status": "running", "reason": "Repaired", "reported_by": "maintenance"}'
```
(Replace M005-UUID with the actual ID from `GET /api/v1/sandhar/machines`.)

---

### s4-material-shortage — Material Shortage

**What it does:**
- Sets material availability for PROD-Y (Door Handle Assembly) to:
  - Available: 600 units
  - Required: 800 units
  - Shortfall: 200 units
  - `constraint_flag = true`

**Use for:** Demonstrating the material constraint path. Any work orders for PROD-Y will be flagged as partially constrained.

**Expected agent output:** Work orders for PROD-Y either get reduced planned quantities or are deferred. Alert type `material_shortage` raised.

**What to verify:**
```bash
curl "http://localhost:8000/api/v1/sandhar/constraints/summary?plan_date=2026-07-02" \
  -H "X-API-Key: dev-secret-key-change-in-prod"
# material_shortfalls will list PROD-Y
```

---

### s5-priority-conflict — Priority Conflict

**What it does:**
- Creates 2 new work orders for L001 products, both marked `priority = high`, both due **today**
- Both compete for L001's single available capacity slot

**Use for:** Demonstrating the WO prioritisation step. The agent must decide which order to fulfil first (or split capacity), and should flag the conflict.

**Expected agent output:** One WO gets priority based on customer priority level (Maruti Suzuki = critical wins). The deprioritised WO may be deferred or partially planned. A `priority_conflict` alert may be raised.

**Tip:** Combine with s1-normal to first set a clean baseline, then run s5 to inject the conflict.

---

### s6-skill-gap — Skill Gap on L003

**What it does:**
- Sets `active_flag = false` on all skill matrix entries for L003
- Effectively makes all L003 employees appear unqualified for L003 work

**Use for:** Demonstrating the skill check in resource allocation. The agent cannot assign operators to L003 because no certified operator exists.

**Expected agent output:** Shift C plan either cannot be generated (confidence = `low`) or defaults to pulling cross-skilled operators from L001/L002. Alert type `skill_gap` raised.

**To restore after this scenario** (reset skill matrix):
```
Simulation page → Reset & Re-seed
```
Reset is the cleanest way to restore L003 skills, as individual skill restoration isn't a separate endpoint.

---

### s7-underachievement — Underachievement

**Prerequisite:** A plan must already exist for L003 (run Workflow A first, generate and approve a plan).

**What it does:**
- Finds any plan detail allocated to L003
- Submits production actuals at **65% of planned quantity**
- Automatically creates a `production_delay` alert with severity `high`

**Use for:** Demonstrating the post-production monitoring path. Shows how the system catches below-threshold performance (threshold = 70%) and surfaces it on the Command Centre.

**Expected agent output (not applicable — this injects data directly):** Check the Command Centre dashboard — the alert will appear in Active Alerts immediately. KPI for L003 Shift C will show 65%.

**What to verify:**
```bash
curl "http://localhost:8000/api/v1/sandhar/alerts?status=active" \
  -H "X-API-Key: dev-secret-key-change-in-prod"
# Will include a production_delay alert for L003
```

---

### s8-full-day — Full Day Simulation

**What it does:**
- Creates 5 new work orders across all 5 products, random priorities
- Seeds all 54 employees as `present` for all 3 shifts today (100% attendance)

**Use for:** Quick full-coverage setup when you want a complete dataset without running all scenarios individually. Good starting point for a multi-step demo.

**Expected agent output:** All 3 shifts produce high-confidence plans. No constraints or gaps.

---

## Combining Scenarios

Scenarios are additive and can be stacked. Common combinations:

| Demo Goal | Sequence |
|---|---|
| Show everything working | s8-full-day → Generate plan → Approve |
| Show manpower impact | s1-normal → s2-absenteeism → Generate plan |
| Show multi-constraint planning | s1-normal → s3-breakdown → s4-material-shortage → Generate plan |
| Show HITL approval with a conflict | s5-priority-conflict → Generate plan → see Approvals inbox |
| Show monitoring & alerts | Workflow A → s7-underachievement → check dashboard |

---

## Reset

**Reset & Re-seed** deletes all Sandhar data (plans, actuals, attendance, alerts, KPIs, work orders, skills, employees, machines, lines, products, shifts) in dependency-safe order, then immediately re-seeds the baseline master data.

Use it to return to a clean state between demos.

**What is NOT deleted:** Agent registrations in the `agents` table. You do not need to re-register agents after a reset.

API:
```bash
curl -s -X POST http://localhost:8000/api/v1/sandhar/simulation/reset \
  -H "X-API-Key: dev-secret-key-change-in-prod"
```

---

## Seed Data Reference

### Employees (54 total)

| Group | Shift | Home Line | Count | Cross-Skill |
|---|---|---|---|---|
| Operators | A | L001 | 18 | First 5 also have L002 skill (level 1) |
| Supervisors | A | L001 | 2 | — |
| Operators | B | L002 | 18 | First 5 also have L001 skill (level 1) |
| Supervisors | B | L002 | 2 | — |
| Operators | C | L003 | 12 | — |
| Supervisors | C | L003 | 2 | — |

Skill levels: Operator home line = 2, Cross-skill = 1, Supervisor = 3.

### Machines

| Code | Name | Line | Capacity/hr |
|---|---|---|---|
| M001 | Press Machine 1 | L001 | 120 |
| M002 | Welding Robot A | L001 | 100 |
| M003 | Assembly Robot B | L002 | 80 |
| M004 | CNC Machine 1 | L002 | 75 |
| M005 | Hydraulic Press | L003 | 70 |
| M006 | Quality Scanner | L003 | 90 |
| M007 | Packaging Unit | L003 | 85 |

### Products

| Code | Name | Customer | Line | Cycle Time | Min Manpower |
|---|---|---|---|---|---|
| PROD-X | Mirror Bracket Assembly | Maruti Suzuki | L001 | 2.5 min | 20 |
| PROD-A | Wiper Arm Set | Maruti Suzuki | L001 | 2.0 min | 18 |
| PROD-Z | Indicator Housing | TVS Motor | L002 | 1.8 min | 12 |
| PROD-B | Clutch Cable Set | Mahindra | L002 | 4.0 min | 10 |
| PROD-Y | Door Handle Assembly | Hero MotoCorp | L003 | 3.0 min | 15 |

---

## Quick API Reference

All endpoints require the header `X-API-Key: dev-secret-key-change-in-prod`.

| Action | Method | URL |
|---|---|---|
| Seed data | POST | `/api/v1/sandhar/simulation/seed` |
| Reset & re-seed | POST | `/api/v1/sandhar/simulation/reset` |
| Inject attendance | POST | `/api/v1/sandhar/simulation/attendance` |
| List scenarios | GET | `/api/v1/sandhar/simulation/scenarios` |
| Run a scenario | POST | `/api/v1/sandhar/simulation/scenario/{id}` |
| Attendance summary | GET | `/api/v1/sandhar/attendance/summary?date=YYYY-MM-DD` |
| Constraint summary | GET | `/api/v1/sandhar/constraints/summary?plan_date=YYYY-MM-DD` |
| Active alerts | GET | `/api/v1/sandhar/alerts?status=active` |
| Generate plan | POST | `/api/v1/sandhar/plan/generate` |
| List plans | GET | `/api/v1/sandhar/plan?date=YYYY-MM-DD` |
| Approve plan | POST | `/api/v1/sandhar/plan/{id}/approve` |
| Floor / actuals | POST | `/api/v1/sandhar/execution/{plan_detail_id}/actuals` |
| KPI daily | GET | `/api/v1/sandhar/kpi/daily?date=YYYY-MM-DD` |

Full interactive API docs: `http://localhost:8000/docs` (filter by tag `sandhar`).

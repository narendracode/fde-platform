# Product Requirement Document
## Smart Assembly Shop Floor Production Planning System
### Client: Sandhar Group | POC & Simulation

---

**Document Version:** 1.0  
**Status:** Draft  
**Audience:** Product, Engineering, Client Stakeholders

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Problem Statement](#2-problem-statement)
3. [Product Vision](#3-product-vision)
4. [Users and Personas](#4-users-and-personas)
5. [Current State vs Future State](#5-current-state-vs-future-state)
6. [Product Scope](#6-product-scope)
7. [Feature Requirements](#7-feature-requirements)
8. [AI Agent Design](#8-ai-agent-design)
9. [User Workflows](#9-user-workflows)
10. [Dashboard and Reporting](#10-dashboard-and-reporting)
11. [Alert and Exception Management](#11-alert-and-exception-management)
12. [Human-in-the-Loop Controls](#12-human-in-the-loop-controls)
13. [Demo and Simulation Design](#13-demo-and-simulation-design)
14. [Success Metrics](#14-success-metrics)
15. [Assumptions and Constraints](#15-assumptions-and-constraints)
16. [Out of Scope](#16-out-of-scope)

---

## 1. Executive Summary

Sandhar Group operates a large-scale automotive parts manufacturing facility with multiple assembly lines, hundreds of operators across three shifts, and daily production obligations to OEM customers. Today, daily production planning is a manual, time-intensive process performed by experienced planners who log into multiple disconnected systems, mentally reconcile attendance data, work orders, machine availability, and operator skills — and produce a shift-wise plan through judgment and experience.

This document defines the product requirements for an AI-powered Smart Production Planning System that automates this process. The system ingests data from existing sources (face recognition attendance, ERP work orders, skill matrices, machine status), applies AI-driven planning logic, auto-allocates resources, and generates a comprehensive shift-wise daily production plan — turning a multi-hour manual effort into a minutes-long automated workflow with human oversight.

For this phase, the deliverable is a **web-based POC and simulation** that demonstrates the full end-to-end flow with realistic mock data, giving Sandhar stakeholders a clear, working model of how the system would operate.

---

## 2. Problem Statement

### 2.1 The Core Problem

Sandhar Group's assembly shop floor runs three shifts daily. Each shift requires a production plan that answers four questions:

1. **Who is available?** — Which operators showed up today, and what can each of them operate?
2. **What needs to be produced?** — Which customer orders are due, in what priority, and in what quantity?
3. **What can we actually run?** — Which machines and lines are available, and is material in stock?
4. **Who goes where?** — How do we assign available operators to lines and machines to best meet the plan?

Today, a production planner answers these questions manually every morning by:
- Logging into the face recognition / attendance system to get present operators per shift
- Cross-referencing the skill matrix (maintained in Excel or a standalone system)
- Checking the ERP (Oracle Fusion) for open work orders, due dates, and quantities
- Calling the maintenance team about machine availability
- Calling the warehouse about material shortages
- Using experience to allocate operators across lines
- Typing up the final plan in Excel or printing a sheet for supervisors

This process takes **2–4 hours per day**, relies entirely on one or two planners' institutional knowledge, is error-prone under time pressure, and cannot dynamically respond to mid-shift disruptions (machine breakdown, sudden absenteeism, urgent order escalation).

### 2.2 Impact of the Problem

| Pain Point | Business Impact |
|---|---|
| 2–4 hours of manual planning daily | Delayed plan delivery to shop floor; production starts late |
| Skill matching done from memory | Suboptimal allocation; wrong operator on wrong machine |
| No dynamic reallocation | A machine breakdown mid-shift causes chaos with no structured response |
| Plan lives in Excel | No historical data; no trend analysis; no performance tracking |
| Single point of knowledge | If the planner is absent, no one can build the plan |
| No exception visibility | Supervisors discover problems reactively, not proactively |

---

## 3. Product Vision

**"Every shift begins with an AI-generated, constraint-aware production plan — ready before the shift starts, requiring human review only where exceptions exist."**

The system acts as an intelligent planning co-pilot. It does not replace the planner's judgment — it does the data gathering, cross-referencing, and routine allocation automatically, surfacing only the decisions that require human attention: skill gaps, capacity shortfalls, conflicting priorities, constraint exceptions.

The planner's role shifts from **data collector and manual allocator** to **exception handler and approver**.

---

## 4. Users and Personas

### 4.1 Production Planner (Primary User)

**Who:** 1–2 senior planners responsible for generating the daily shift-wise plan.  
**Goal:** Generate an accurate, complete shift plan in under 15 minutes instead of 2–4 hours.  
**Key Actions:** Review AI-generated plan, approve allocations, handle exceptions, release plan to supervisors.  
**Pain Today:** Manually reconciling 4–5 systems; plan is always late; no structured way to handle exceptions.

---

### 4.2 Shop Floor Supervisor (Secondary User)

**Who:** Line supervisors (one per line per shift) who execute the plan on the floor.  
**Goal:** Know exactly who is assigned to their line, what to produce, and how much — before the shift starts.  
**Key Actions:** View their line's plan, acknowledge receipt, report actuals at end of shift.  
**Pain Today:** Receives plan late or verbally; no clear source of truth; reacts to disruptions without guidance.

---

### 4.3 Plant Manager / Operations Head (Management User)

**Who:** Senior leadership overseeing the shop floor.  
**Goal:** Real-time visibility into plan vs. actuals; early warning on order delay risks; utilization KPIs.  
**Key Actions:** View dashboard, review exception alerts, approve plan overrides for high-priority orders.  
**Pain Today:** No real-time view; learns about problems after they've caused delays.

---

### 4.4 HR / Attendance Administrator (Data Source Owner)

**Who:** HR team that manages the face recognition system and employee skill master.  
**Goal:** Ensure the planning system has accurate attendance and skill data.  
**Key Actions:** Maintain employee skill matrix, verify attendance feed, update skill certifications.

---

## 5. Current State vs Future State

| Activity | Current State | Future State |
|---|---|---|
| Attendance data collection | Planner logs into face recognition system manually | System auto-ingests shift-wise attendance |
| Skill matching | Planner checks skill matrix in Excel from memory | System automatically maps available operators to line/machine skills |
| Work order import | Planner logs into Oracle Fusion | System auto-syncs open work orders |
| Constraint check | Planner calls maintenance + warehouse | System queries machine status and material availability |
| Resource allocation | Manual, experience-based | AI auto-allocates optimally; flags gaps for human decision |
| Plan generation | 2–4 hours; typed in Excel | Generated in minutes; structured plan in the system |
| Exception handling | Reactive, discovered during production | Proactive alerts before shift start |
| Plan distribution | Printed sheet or WhatsApp | Digital plan visible to all supervisors in real time |
| Actuals tracking | Manual count at end of shift | Supervisors enter actuals; system calculates variances |
| KPI reporting | End-of-day manual summary | Live dashboard; shift-close auto-calculation |

---

## 6. Product Scope

### 6.1 What the Product Does

The system is a web application backed by AI agents that:

1. **Ingests** attendance, work orders, skill matrices, machine status, and material availability
2. **Validates** constraints across all inputs
3. **Generates** an AI-driven resource allocation and shift-wise production plan
4. **Presents** the plan to the planner for review, exception handling, and approval
5. **Distributes** the approved plan to supervisors
6. **Monitors** actuals against the plan and surfaces deviations in real time
7. **Alerts** on manpower shortages, machine breakdowns, material gaps, and delay risks

### 6.2 Modules

| # | Module | Description |
|---|---|---|
| M1 | Master Data Management | Employee, Line, Machine, Product, Customer, Shift master setup |
| M2 | Attendance Integration | Face recognition feed; shift-wise present operator list |
| M3 | Skill Matrix | Employee ↔ Line ↔ Machine skill mapping with levels and certifications |
| M4 | Work Order Sync | ERP/Fusion work order import; priority and due date management |
| M5 | Constraint Engine | Machine status, material availability, quality hold tracking |
| M6 | AI Planning Engine | Core agent: auto-allocate resources, generate shift plan |
| M7 | Plan Review & Approval | Planner reviews AI plan, resolves exceptions, approves |
| M8 | Shop Floor Execution | Supervisor view; actuals entry; shift-close |
| M9 | Alerts & Exceptions | Real-time alerts for all exception types |
| M10 | Dashboard & KPIs | Management view; plan vs. actual; utilization metrics |

---

## 7. Feature Requirements

### M1 — Master Data Management

**F1.1 Employee Master**  
The system maintains a registry of all employees with fields: employee code, name, department (Production / QA / Maintenance), designation (Operator / Supervisor), grade, shift group, status (Active / Inactive), and joining date.

**F1.2 Line Master**  
Assembly lines are registered with: line ID, name, area, capacity per shift, and current status (Active / Inactive).

**F1.3 Machine Master**  
Machines are registered with: machine ID, name, associated line, machine type, capacity per hour, and current status.

**F1.4 Product Master**  
Products are defined with: product ID, name, associated customer, standard cycle time (minutes per unit), standard manpower required, and the line this product is assembled on.

**F1.5 Customer Master**  
Customers are defined with: customer ID, name, and priority level (Critical / High / Medium / Low). OEM customers are typically Critical or High.

**F1.6 Shift Calendar**  
Shifts are configured with: shift code (A / B / C), name, start time, and end time. Standard configuration: A = 06:00–14:00, B = 14:00–22:00, C = 22:00–06:00.

---

### M2 — Attendance Integration

**F2.1 Face Recognition Feed**  
The system receives attendance data from the face recognition system — either via API integration or CSV upload — containing: employee ID, date, shift, check-in time, check-out time, and status (Present / Absent / Leave / Late).

**F2.2 Shift-wise Availability Summary**  
For each shift on a given date, the system automatically computes:
- Total present operators
- Total present supervisors
- Absent / leave count
- Late arrivals (present but after shift start)

**F2.3 Manual Override**  
Attendance administrators can manually mark an employee present or absent if the face recognition data is missing or incorrect.

**F2.4 Real-time Update**  
Attendance data refreshes automatically. If an operator clocks in late, the planning engine can re-evaluate allocations for the affected shift.

---

### M3 — Skill Matrix

**F3.1 Skill Assignment**  
Each employee can be assigned skills for one or more lines and one or more machines. Each skill assignment carries a level: 1 (Trainee), 2 (Basic), 3 (Skilled), 4 (Expert), along with certification date and expiry date.

**F3.2 Skill Search**  
Planners can search: "Who can operate Machine M5 at skill level ≥ 3?" or "Who is qualified for Line-3?" and get an instant list of available-today operators matching the criteria.

**F3.3 Skill Gap Detection**  
When generating a plan, if an allocation requires an operator with a specific skill that no present operator has, the system flags this as a Skill Gap exception with details: line, machine, required skill level, and count of operators short.

**F3.4 Cross-skill Suggestions**  
When a skill gap exists, the system suggests operators who are cross-skilled (qualified for multiple lines) who could be reassigned from a lower-priority line to cover the gap.

**F3.5 Certification Expiry Alert**  
The system alerts HR and the planner when a certification is within 30 days of expiry, so the operator is not allocated to work requiring that skill after expiry.

---

### M4 — Work Order Sync

**F4.1 Work Order Import**  
Work orders are imported from Oracle Fusion ERP — either via direct API integration or scheduled file upload. Each work order includes: WO number, customer, product, total quantity, due date, priority (High / Medium / Low), and current status.

**F4.2 Work Order Prioritisation**  
Orders are ranked for planning by: (1) due date proximity, (2) customer priority level, (3) order priority flag. The AI agent uses this ranking when allocating limited resources across competing orders.

**F4.3 Split Across Shifts**  
A single work order quantity may be split across multiple shifts and lines. The system tracks cumulative planned quantity vs. order quantity to prevent over- or under-planning.

**F4.4 WO Status Tracking**  
Work orders move through statuses: Open → Planned → In Progress → Completed. The system updates status automatically as planning and actuals are recorded.

---

### M5 — Constraint Engine

**F5.1 Machine Availability**  
Machine status is tracked in real time: Running / Breakdown / Planned Maintenance / Idle. A machine in Breakdown or Planned Maintenance status is excluded from allocation. The system records reason and estimated restoration time.

**F5.2 Material Availability**  
For each product in an open work order, material availability is checked against required quantity. If available quantity is less than required, a Material Shortage constraint is flagged with the shortfall quantity. Work orders with material constraints are deprioritised in the plan; the planner is alerted.

**F5.3 Quality Hold**  
Work orders or products under quality hold are flagged and excluded from the production plan until the hold is released. The system records hold reason and status.

**F5.4 Constraint Summary View**  
Before plan generation, the system presents a consolidated constraint summary: how many work orders are affected, by which type of constraint, and what impact this has on planned quantity.

---

### M6 — AI Planning Engine

This is the core of the product. The AI Planning Engine is an agent (or set of agents) that takes all inputs and generates a shift-wise daily production plan.

**F6.1 Plan Trigger**  
The planner triggers plan generation for a specific date. The system runs for all three shifts simultaneously.

**F6.2 Manpower Determination**  
For the target date, the engine identifies present operators per shift from attendance data and maps each against the skill matrix to know what each operator can do.

**F6.3 Order Prioritisation**  
Open work orders are ranked by priority and due date. Orders due today are treated as highest priority regardless of their stated priority flag.

**F6.4 Auto Resource Allocation**  
The engine allocates present operators to lines and machines by:
- Matching operator skills to line/machine requirements
- Respecting standard manpower requirements per product
- Prioritising higher-priority orders first
- Balancing across lines to avoid over- or under-staffing
- Flagging lines where available operators fall short of required manpower

**F6.5 Quantity Planning**  
For each line-shift combination, the engine calculates planned quantity using:
- Available manpower on that line
- Standard cycle time for the product
- Available shift hours (minus standard breaks)
- Machine capacity constraint (if applicable)

**F6.6 Gap Resolution Suggestions**  
Where planned manpower falls short of required manpower, the engine generates resolution options ranked by feasibility:
1. Cross-skilled operators from lower-priority lines
2. Overtime (if shift calendar permits)
3. Alternate line loading (produce partial quantity on a different line if qualified)
4. Accept reduced planned quantity with delay risk flag

**F6.7 Plan Output**  
The generated plan includes for each shift and line: line, product, work order number, planned quantity, allocated operator count, supervisor assignment, and start/end times.

**F6.8 Plan Confidence Score**  
Each generated plan carries a confidence indicator: High (all constraints met, full manpower), Medium (minor gaps or suggestions applied), Low (significant constraint — human decision required).

**F6.9 Plan Regeneration**  
If the planner modifies any input (adds an exception, overrides an allocation), the plan can be regenerated instantly incorporating the change.

---

### M7 — Plan Review and Approval

**F7.1 Planner Review Screen**  
The planner sees the AI-generated plan shift by shift, with:
- Line-wise allocation table (line, product, WO, qty, operators, supervisor)
- Exception cards for every unresolved gap or constraint
- Confidence score and summary statistics (total planned qty, manpower utilization %)

**F7.2 Exception Resolution**  
Each exception card shows: type, affected line/WO, suggested resolution options. The planner selects a resolution or dismisses the exception with a reason. Unresolved exceptions are visible to the plant manager.

**F7.3 Manual Override**  
The planner can override any AI allocation: reassign an operator, change a planned quantity, swap a work order between shifts, or mark a line as offline. All overrides are logged with the planner's name and reason.

**F7.4 Plan Approval**  
Once exceptions are resolved (or acknowledged), the planner approves the plan. An approved plan is locked and distributed to supervisors.

**F7.5 Plan Versioning**  
Every regeneration or override creates a new plan version. The system retains all versions with a diff view showing what changed and why.

---

### M8 — Shop Floor Execution

**F8.1 Supervisor Dashboard**  
Each supervisor has a view filtered to their line and shift showing: assigned operators, product to produce, WO reference, planned quantity, and shift timing.

**F8.2 Operator Acknowledgement**  
Supervisors can acknowledge the plan for their line. If an operator listed in the plan has not shown up at the line start, the supervisor can flag this for immediate re-allocation.

**F8.3 Actuals Entry**  
At the end of shift, supervisors enter: produced quantity, rejected quantity, rework quantity, and downtime minutes. The system calculates plan achievement % automatically.

**F8.4 Mid-Shift Disruption Reporting**  
Supervisors can raise mid-shift disruptions: machine breakdown, operator injury, material runout, quality hold. Each disruption triggers an alert to the planner for reallocation or plan adjustment.

**F8.5 Shift Close**  
On shift close, actuals are finalised and KPIs are computed for that shift. The system archives the shift plan and actuals for trend analysis.

---

## 8. AI Agent Design

The system uses a supervisor-worker agent architecture. The Supervisor Agent orchestrates the planning process and delegates specialist tasks to Worker Agents.

### 8.1 Agent Overview

```
Planning Supervisor Agent
    │
    ├── Attendance Analysis Agent
    │       Reads shift-wise attendance; maps operators to skills;
    │       identifies present, absent, late per shift.
    │
    ├── Work Order Prioritisation Agent
    │       Imports open work orders; applies priority and due date ranking;
    │       identifies WOs blocked by constraints.
    │
    ├── Constraint Validation Agent
    │       Checks machine availability, material stock, quality holds;
    │       produces constraint summary with impact assessment.
    │
    ├── Resource Allocation Agent
    │       Matches available operators to lines/machines;
    │       detects gaps; generates resolution options.
    │
    └── Production Plan Generation Agent
            Assembles shift-wise plan from allocation output;
            calculates planned quantities; assigns supervisors;
            generates exception summary and confidence score.
```

### 8.2 Supervisor Agent Responsibilities

The Planning Supervisor Agent:
- Triggers each worker in the correct sequence
- Passes outputs from one worker as inputs to the next
- Detects when a worker result requires human input before proceeding
- Assembles the final plan and presents it for planner review
- Handles re-planning when the planner makes overrides

### 8.3 Human-in-the-Loop Points

The system does not auto-approve the plan. Human review is required before the plan is released to the shop floor. Specific escalations that always require human decision:

| Situation | Human Action Required |
|---|---|
| Line has fewer operators than minimum required | Planner selects: cross-skill / overtime / accept reduced qty |
| Work order due today has a material shortage | Planner decides: expedite material / reschedule WO / partial plan |
| Machine breakdown affects a High-priority order | Planner decides: alternate line / hold WO / notify customer |
| Skill gap cannot be filled by any cross-skilled operator | Planner decides: escalate to HR / accept gap / bring in from another shift |
| Plan confidence is Low | Planner must review and explicitly approve |

---

## 9. User Workflows

### 9.1 Daily Planning Workflow (Planner)

```
1. Planner opens the system (before shift start)
2. System shows today's attendance summary for all shifts
3. System shows open work orders with due dates and priorities
4. System shows constraint summary (machine/material/quality issues)
5. Planner clicks "Generate Plan for [Date]"
6. AI agents run: attendance → WO priority → constraints → allocation → plan
7. Plan is displayed with confidence score and exception list
8. Planner reviews exceptions — selects resolutions for each
9. Planner overrides any allocations (optional)
10. Planner approves plan
11. Plan is distributed to supervisors
12. Supervisors acknowledge their line plan
```

### 9.2 Mid-Shift Disruption Workflow (Supervisor)

```
1. Machine M5 breaks down on Line-3 (Shift B)
2. Supervisor raises "Machine Breakdown" disruption
3. System generates alert → notified to planner
4. AI agent re-evaluates Line-3 allocation without M5
5. Planner reviews options: alternate machine / reduce qty / cross-line
6. Planner approves revised allocation
7. Supervisor sees updated plan on screen
```

### 9.3 Shift Close Workflow (Supervisor)

```
1. Supervisor opens shift-close screen at shift end
2. Enters: produced qty, rejected qty, rework qty, downtime minutes
3. System auto-calculates: plan achievement %, OEE, rejection rate
4. Supervisor submits
5. KPI dashboard updates
6. Variance alerts generated if achievement < 80%
```

---

## 10. Dashboard and Reporting

### 10.1 Operations Dashboard (Plant Manager View)

Real-time view showing current day's performance across all shifts:

- **Total orders in plan:** count and total quantity
- **Plan achievement %:** current shift actual vs. planned
- **Manpower utilization %:** allocated operators / present operators
- **Line utilization %:** active lines / total lines
- **Skill gap count:** lines with unresolved skill gaps
- **Active exceptions:** count by type (machine, material, manpower, quality)
- **Order delay risk:** orders at risk of missing due date based on current trajectory

### 10.2 Shift-wise Production Plan View

Tabular plan by shift:

| Line | Product | WO No | Planned Qty | Operators | Supervisor | Status |
|---|---|---|---|---|---|---|
| Line-1 | Product-X | WO1001 | 700 | 22 | Supervisor-A | On Track |
| Line-2 | Product-Z | WO1003 | 300 | 12 | Supervisor-B | On Track |
| Line-3 | Product-Y | WO1002 | 400 | 10 | At Risk |

### 10.3 Resource Allocation View

Operator-level allocation: who is assigned to which line, machine, WO, and shift. Filterable by line, shift, and skill level.

### 10.4 Historical KPI Trends

Weekly and monthly charts for:
- Plan achievement % trend
- Manpower utilization trend
- Line utilization trend
- Rejection rate trend
- Exception frequency by type

---

## 11. Alert and Exception Management

### 11.1 Alert Types

| Alert Type | Trigger Condition | Severity | Recipient |
|---|---|---|---|
| Manpower Shortage | Line has < minimum required operators | High | Planner, Plant Manager |
| Skill Gap | No qualified operator available for line/machine | High | Planner, HR |
| Machine Breakdown | Machine status = Breakdown during shift | Critical | Planner, Maintenance |
| Material Shortage | Available qty < required qty for WO | High | Planner, Warehouse |
| Quality Hold | WO or product placed on hold | Medium | Planner, QA |
| Production Delay | Achievement % < 70% at shift midpoint | High | Planner, Plant Manager |
| Certification Expiry | Skill certification expires within 30 days | Low | HR, Planner |
| Excess Capacity | Utilization < 60% — capacity available for additional orders | Info | Planner |

### 11.2 Alert Workflow

Each alert is visible in a dedicated Alerts panel. Alerts that block plan execution require acknowledgment and resolution before the plan can be approved. Informational alerts can be dismissed with a note.

---

## 12. Human-in-the-Loop Controls

The system follows a strict principle: **AI recommends, humans decide.** No production plan is released to the shop floor without planner approval. No resource allocation is final without planner review of exceptions.

### 12.1 Approval Gates

| Decision | Who Approves |
|---|---|
| Daily production plan release | Production Planner |
| Plan override (manual allocation change) | Production Planner |
| Mid-shift re-allocation after disruption | Production Planner |
| Actuals submission | Shift Supervisor |
| Quality hold release | QA Supervisor |
| Overtime approval | Plant Manager |

### 12.2 Audit Trail

Every AI-generated recommendation and every human action (approve, override, dismiss) is logged with: timestamp, user, action taken, and reason. This audit trail is visible to plant management and exportable for review.

---

## 13. Demo and Simulation Design

Since this is a POC, the system will run on simulated data that mirrors Sandhar Group's real operational profile. The simulation is designed to demonstrate every key scenario a stakeholder would want to see.

### 13.1 Simulated Data Set

**Employees:** 50 operators, 10 supervisors across 3 shifts and 3 lines.  
**Lines:** 3 assembly lines (Line-1, Line-2, Line-3).  
**Machines:** 8 machines across 3 lines.  
**Products:** 5 automotive part types (mirrors Sandhar's product categories).  
**Customers:** 4 OEM customers at varying priority levels.  
**Shifts:** Standard 3-shift configuration (A: 06–14, B: 14–22, C: 22–06).

### 13.2 Simulation Scenarios

Each scenario demonstrates a specific product capability:

| # | Scenario | What It Demonstrates |
|---|---|---|
| S1 | Normal day — all resources available, no constraints | Full auto-plan generation; clean allocation; plan approval |
| S2 | High absenteeism on Shift A (20% operators absent) | Skill-based cross-allocation; gap detection; planner exception handling |
| S3 | Machine M5 breaks down mid-shift | Mid-shift disruption alert; re-allocation workflow; supervisor view update |
| S4 | Material shortage for highest-priority WO | Constraint engine; partial planning; planner override decision |
| S5 | Two high-priority orders competing for same line | Priority-based allocation; planner override; plan versioning |
| S6 | Skill gap — Line-3 needs certified M5 operator, none present | Skill gap alert; cross-skill suggestion; HR notification |
| S7 | End-of-shift actuals entry with under-achievement | Actuals capture; variance alert; KPI update |
| S8 | Full day simulation — all 3 shifts | Complete plan generation; shift transitions; rolling KPI dashboard |

### 13.3 Demo Web Application

The simulation web application includes:

- **Home / Command Centre:** Live dashboard showing current shift status, active alerts, and KPI summary cards.
- **Plan Generation Screen:** Step-by-step visible execution of the AI agents — attendance analysis → WO prioritisation → constraint check → allocation → plan output. Each step shows what the agent found and decided.
- **Plan Review Screen:** Planner-facing view with the generated plan, exception cards, resolution options, and approve button.
- **Supervisor View:** Line-specific plan view with operator list, product target, and actuals entry.
- **Alerts Panel:** Live feed of all active alerts with severity and status.
- **KPI Dashboard:** Management view with charts and trend data.
- **Audit Trail:** Log of all AI decisions and human actions.

### 13.4 Simulation Controls

For demo purposes, the application includes a simulation control panel (visible only in demo mode) that lets the presenter:
- Set the current date and shift
- Trigger a machine breakdown
- Simulate sudden absenteeism (mark N operators as absent)
- Introduce a material shortage
- Escalate an order priority
- Fast-forward to shift close and inject actuals

This allows a live, interactive demonstration for Sandhar stakeholders showing how the system responds to real operational scenarios.

---

## 14. Success Metrics

### 14.1 POC Success Criteria

The POC is considered successful if Sandhar stakeholders can observe:

1. A daily production plan generated in under 5 minutes from data inputs
2. All simulation scenarios (S1–S8) executing correctly and visibly
3. Exception alerts firing and human-in-the-loop resolution working end to end
4. KPI dashboard reflecting realistic operational data
5. Audit trail capturing all AI decisions and human overrides

### 14.2 Production System Success Metrics (Post-POC Targets)

| Metric | Baseline (Manual) | Target (AI System) |
|---|---|---|
| Daily plan generation time | 2–4 hours | < 15 minutes |
| Plan accuracy (no revision needed) | ~60% | > 90% |
| Manpower utilization | ~75% (estimated) | > 88% |
| Order on-time delivery | Baseline TBD | +10% improvement |
| Planner effort (hours/day) | 3–5 hours | < 1 hour (exception handling only) |
| Exception detection time | Reactive (post-incident) | Proactive (pre-shift or within minutes of occurrence) |

---

## 15. Assumptions and Constraints

### 15.1 Assumptions

- **Attendance system integration:** For the POC, face recognition attendance data is simulated. In production, it will be received via API or CSV export from the existing face recognition system.
- **ERP integration:** For the POC, work orders are loaded from a mock dataset. In production, Oracle Fusion will provide work orders via API or scheduled extract.
- **Single facility:** This POC covers one assembly shop floor. Multi-plant expansion is out of scope for this phase.
- **Skill matrix seeded manually:** For the POC, the employee skill matrix is pre-loaded. In production, it would be maintained by HR through the web interface.
- **Single planning horizon:** The system plans one day at a time. Rolling multi-day planning is a future capability.
- **Manual actuals entry:** Production actuals are entered by supervisors. Direct machine data (IoT/SCADA) integration is out of scope for this phase.
- **Standard shifts:** Three fixed shifts (A, B, C) with standard timings. Variable shift patterns are a future enhancement.

### 15.2 Constraints

- The POC will use mock data only. No connection to Sandhar's live ERP, attendance, or skill systems.
- The system does not send automated communications to operators or supervisors (no SMS, WhatsApp, email) in the POC phase.
- Multi-language support (Hindi interface) is not required for the POC.

---

## 16. Out of Scope

The following are explicitly not in scope for this POC:

| Item | Reason |
|---|---|
| Live Oracle Fusion ERP integration | POC uses simulated WOs; full integration is post-POC |
| Live face recognition system integration | POC uses simulated attendance |
| IoT / SCADA machine data feed | Real-time machine data requires hardware integration |
| Multi-plant / multi-facility planning | Single assembly floor for this POC |
| Financial / costing module | Not requested in this phase |
| Mobile application | Web browser is sufficient for POC |
| Advanced ML forecasting (demand planning) | Beyond current scope; potential future phase |
| Direct ERP write-back (plan pushed into Fusion) | Read-only from ERP for this phase |
| Automated operator notifications | Out of scope for POC |
| Payroll / overtime calculation | HR system concern; out of scope |

---

## Appendix: Glossary

| Term | Definition |
|---|---|
| WO | Work Order — a production instruction from the ERP system to manufacture a specific quantity of a product |
| OEM | Original Equipment Manufacturer — Sandhar's customers (car/bike makers) |
| Skill Matrix | A mapping of which employees are qualified to operate which lines and machines, at what skill level |
| Cycle Time | Standard time (in minutes) required to assemble one unit of a product |
| HITL | Human-in-the-Loop — a control pattern where AI generates recommendations and a human approves before execution |
| OEE | Overall Equipment Effectiveness — a standard manufacturing KPI combining availability, performance, and quality |
| Cross-skill | An operator who is certified on more than one line or machine and can be reassigned flexibly |
| Constraint | Any condition that prevents or limits production: machine breakdown, material shortage, skill gap, quality hold |
| Plan Achievement % | (Actual produced qty / Planned qty) × 100 for a given shift |

# Plan Refinement via Conversational AI — Feature & Change Design

**Feature name:** "Refine with AI"  
**Target page:** `/sandhar/plan?date=YYYY-MM-DD`  
**Status:** Design / pre-implementation  
**Author:** Design session — 2026-07-06

---

## 1. What Are We Building

A conversational AI side-panel ("canvas") that opens when a planner clicks **"Refine with AI"** on a draft plan. The planner types natural-language instructions — "move WO-2345 to Line 3", "reduce Shift B qty by 10%", "why is there a manpower gap on L001?" — and the AI interprets them, executes the changes directly on the `sandhar_plan_detail` records, and instantly re-renders the plan preview. Once satisfied the planner clicks **"Approve Plan"** (either inside the panel or the existing button on the main page). The conversation is locked after approval but preserved for LLMOps.

### 1.1 Button naming rationale

| Candidate | Verdict |
|---|---|
| Adjust | Neutral, mechanical |
| **Refine with AI** | ✅ Chosen — implies iterative improvement with AI assistance |
| Co-pilot | Too product-brand-specific |
| Tune Plan | Too narrow (sounds only quantitative) |

---

## 2. User Experience Flow

```
Draft plan rendered on /sandhar/plan
        │
        ▼
Planner sees three buttons:
  [✅ Approve Plan]  [✗ Reject]  [✨ Refine with AI]   ← NEW
        │
        ▼  clicks "Refine with AI"
Right-side panel slides in (60 % width, full viewport height)
Left side still shows the live plan table (shrinks to 40 %)
        │
        ▼
Panel header:
  "Refining: Shift A · 2026-07-06 · v2"  [✅ Approve Plan]  [✕ Close]
        │
        ▼
Chat window — welcome message from assistant:
  "I have your Shift A plan loaded. I can adjust quantities,
   reassign WOs between lines, or explain any allocation.
   What would you like to change?"
        │
        ▼  planner types: "Move WO-SEED-0003 to Line 2"
        │
        ▼
[Thinking…] spinner → AI calls tool sandhar_refine_move_wo()
        │
        ▼
Plan preview on left updates — WO-SEED-0003 now on Line 2
AI responds: "Done. WO-SEED-0003 (Cylinder Head) moved from
Line 3 → Line 2. Line 2 manpower utilisation is now 92 %."
        │
        ▼  planner types: "Approve" OR clicks [✅ Approve Plan] in panel
        │
        ▼
Plan status → approved, chat locked, panel shows
  "✅ Plan approved at 14:32. This conversation is saved."
```

---

## 3. Architecture Overview

```
Browser                          FastAPI (api container)              DB (postgres)
──────                           ──────────────────────               ─────────────
plan.html                        /sandhar/plan/                        sandhar_plan_header
  │  open canvas                 {header_id}/refine/start  ──────────► sandhar_plan_refine_session
  │  POST start session ────────►                                       sandhar_plan_refine_message
  │◄──────── session_id ─────────
  │
  │  POST message ──────────────► /sandhar/plan/refine/    ──────────► runs agent turn:
  │  (SSE stream)                 {session_id}/message                  sandhar-plan-refiner (ReAct)
  │◄─── token stream / done ─────                                       │
  │                                                                     ▼ calls tools:
  │  preview auto-refresh ───────► GET /sandhar/plan/{hid}  ──────────► sandhar_plan_detail (mutated)
  │◄──── updated plan JSON ───────
  │
  │  POST approve ──────────────► /sandhar/plan/{hid}/approve  ──────► (existing endpoint, unchanged)
```

**Key design principle:** The existing `approve_plan` and `reject_plan` endpoints are **not touched**. The Refine canvas calls the same approve endpoint as the main page buttons do today.

---

## 4. New Data Models

### 4.1 `sandhar_plan_refine_session`

Represents one refinement session on one plan header. A plan can have at most one active session; past sessions are kept for audit/LLMOps.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `plan_header_id` | UUID FK → sandhar_plan_header | |
| `status` | VARCHAR(20) | `active` · `approved` · `closed` |
| `opened_by` | VARCHAR(100) | planner identity (future auth) |
| `created_at` | TIMESTAMPTZ | |
| `closed_at` | TIMESTAMPTZ | set on approve or close |

### 4.2 `sandhar_plan_refine_message`

One row per message turn (user or assistant).

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `session_id` | UUID FK → sandhar_plan_refine_session | |
| `role` | VARCHAR(20) | `user` · `assistant` · `system` |
| `content` | TEXT | |
| `tool_calls` | JSONB | list of `{tool, args, result}` dicts — LLMOps training signal |
| `plan_snapshot` | JSONB | full plan detail array *after* this turn — diff-able for LLMOps |
| `langsmith_run_id` | VARCHAR(100) | per-turn trace for cost/latency breakdown |
| `langsmith_trace_url` | TEXT | |
| `input_tokens` | INT | |
| `output_tokens` | INT | |
| `created_at` | TIMESTAMPTZ | |

**Why `plan_snapshot` per message?**  
Lets us reconstruct exactly what the plan looked like at each step. Enables offline analysis of "what did the human actually want vs what the AI changed" — the core LLMOps training signal.

### 4.3 Alembic migration

One new migration file: `alembic/versions/XXXX_add_plan_refine_tables.py` — creates both tables above. No changes to any existing table.

---

## 5. New API Endpoints

All prefixed under `/api/v1/sandhar`. All require `X-API-Key` header (same as existing).

### 5.1 Start a session

```
POST /plan/{header_id}/refine/start
Response: { session_id, plan_header_id, status, welcome_message }
```

- Creates a `sandhar_plan_refine_session` row.
- If an `active` session already exists for this header, returns it (idempotent — single-user system for v1).
- Validates the plan header exists and is in `draft` status — returns 422 if already approved/rejected.
- **Auto-registers and auto-activates** the `sandhar-plan-refiner` agent if it is not yet in the DB or is currently inactive. This removes the need for manual operator activation in the Agents dashboard for this internal agent.

### 5.2 Send a message (SSE streaming)

```
POST /plan/refine/{session_id}/message
Body: { "content": "Move WO-SEED-0003 to Line 2" }
Response: text/event-stream  (Server-Sent Events — streaming confirmed)
```

- Validates session is still `active`.
- Persists the user message row.
- Runs the `sandhar-plan-refiner` agent (new, described in §6) with the full conversation history as context.
- Streams the LLM token output back via SSE. No polling fallback — SSE is the single delivery mechanism.
- On completion: persists assistant message row (with `tool_calls`, `plan_snapshot`, tokens).
- Frontend reads the stream and appends tokens to the chat bubble; on `event: done` it refreshes the plan preview.

### 5.3 Get message history

```
GET /plan/refine/{session_id}/messages
Response: [ { id, role, content, tool_calls, created_at, ... } ]
```

- Used when reopening a session from a page reload.

### 5.4 Close session without approving

```
POST /plan/refine/{session_id}/close
```

- Sets session `status = closed`. Plan remains `draft`.
- Does **not** touch the plan header.

### 5.5 Approve from within canvas

```
POST /plan/{header_id}/approve   ← existing endpoint, zero changes
```

- The "Approve Plan" button inside the canvas calls this same endpoint.
- After success the frontend calls `close` on the session with `status = approved`.

**No new approve/reject endpoints.** The canvas reuses what already exists.

---

## 6. New Agent: `sandhar-plan-refiner`

### 6.1 YAML config

New file: `agents/configs/sandhar-plan-refiner.yaml`

```yaml
agent:
  name: sandhar-plan-refiner
  type: react
  companies: [sandhar]
  description: >
    Conversational plan refinement agent. Reads the current draft plan and
    applies targeted edits requested by the planner in natural language.
  inputs:
    plan_header_id:
      type: string
      required: true
    session_id:
      type: string
      required: true
```

### 6.2 New tool file: `src/agri_agent/agent/tools/sandhar/plan_refiner.py`

Six focused tools — all operate on a specific `plan_header_id`:

| Tool name | What it does |
|---|---|
| `sandhar_refine_get_plan` | Returns full current plan details (lines, WOs, quantities, gaps) |
| `sandhar_refine_update_qty` | Updates `planned_qty` on one `plan_detail` row |
| `sandhar_refine_move_wo` | Reassigns a WO from its current line to a different line (updates `line_id` on the detail row) |
| `sandhar_refine_add_wo` | Adds an open WO (not yet in the plan) as a new `plan_detail` row |
| `sandhar_refine_remove_wo` | Removes a `plan_detail` row (WO returns to "unplanned") |
| `sandhar_refine_explain_constraint` | Read-only — calls existing constraint tools and explains a specific gap or alert in plain language |

**Tools only mutate `sandhar_plan_detail` and `sandhar_resource_allocation`** — the same tables the planning supervisor writes to. No new table writes during refinement except those two.

### 6.3 Agent system prompt (key points)

The system prompt lives in Context Hub (`sandhar/plan-refiner-system:latest`), not hardcoded in the YAML. Key directives it contains:

- Told it has a live plan loaded for a specific header and date.
- Given the full plan context on the first turn via `[Runtime context]` (injected by the endpoint).
- Instructed to confirm every destructive change ("I am about to remove WO-X from the plan — confirm?") unless the user's message is unambiguous.
- Instructed to keep responses concise — one paragraph max, then show a brief summary of what changed.
- Told it cannot approve or reject; only the planner can do that.

### 6.4 Activation

This agent is **auto-registered and auto-activated** by the `POST /plan/{header_id}/refine/start` endpoint on first use. It does not require manual activation through the Agents dashboard (unlike operator-deployed agents). If it is already registered and active, the `start` call is a no-op for activation.

---

## 7. Frontend Changes

### 7.1 Files touched

| File | Change type |
|---|---|
| `templates/sandhar/plan.html` | Add button, canvas HTML, canvas CSS, canvas JS |

**No other template or route file is touched.**

### 7.2 "Refine with AI" button

Added in the `plan-actions` div alongside Approve and Reject. Only rendered when `!isApproved` (exact same condition as today's Approve/Reject buttons). Styled with a new `.btn-refine` class (indigo outline style — visually distinct from the green Approve and red Reject).

```html
<!-- existing -->
<button class="btn btn-success" onclick="approvePlan(...)">✅ Approve Plan</button>
<button class="btn btn-danger"  onclick="rejectPlan(...)">✗ Reject</button>
<!-- new -->
<button class="btn btn-refine"  onclick="openRefineCanvas('${plan.id}','${shift}')">✨ Refine with AI</button>
```

### 7.3 Canvas structure

A fixed overlay panel attached to the right side of the viewport. Does not replace the plan table — the plan table shrinks to ~42 % width; the canvas takes ~58 %. Both are visible simultaneously.

```
┌──────────────────────────────────────────────────────────────────┐
│  PLAN TABLE (42 %)         │  REFINE CANVAS (58 %)               │
│                            │  ┌────────────────────────────────┐ │
│  Line │ WO    │ Qty │ ...  │  │ Refining: Shift A · 2026-07-06 │ │
│  L001   WO-01  400         │  │           [✅ Approve] [✕ Close]│ │
│  L002   WO-03  320  ←live  │  ├────────────────────────────────┤ │
│  ...                       │  │ 🤖 I have your plan loaded...  │ │
│                            │  │ 👤 Move WO-0003 to Line 2      │ │
│                            │  │ 🤖 Done. WO-0003 moved...      │ │
│                            │  ├────────────────────────────────┤ │
│                            │  │ [  Type your instruction...  ] │ │
│                            │  │                        [Send →]│ │
│                            │  └────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────┘
```

### 7.4 Canvas JavaScript logic

```
openRefineCanvas(headerId, shift)
  │
  ├── POST /plan/{headerId}/refine/start  (auto-activates agent if needed)
  ├── store session_id
  ├── disable "↺ Re-generate Shift" button on the main page
  ├── slide canvas in
  └── render welcome message

sendMessage(content)
  │
  ├── append user bubble immediately
  ├── show [Thinking…] bubble
  ├── POST /plan/refine/{session_id}/message  (SSE — confirmed delivery mechanism)
  │     read stream → append tokens to assistant bubble
  └── on event:done → refreshPlanPreview()
            │
            └── GET /plan/versions?date=...  → re-render plan table on left

closeCanvas()
  │
  ├── POST /plan/refine/{session_id}/close
  └── restore plan table to full width
      └── re-enable "↺ Re-generate Shift" button

approveFromCanvas(headerId)
  │
  ├── same as existing approvePlan() — calls POST /plan/{headerId}/approve
  └── on success → lock chat, show "✅ Plan approved" banner
```

### 7.5 Re-generate button guard

When a refinement canvas is open (active session exists), the **"↺ Re-generate Shift"** button on the main page is disabled. If the planner closes the canvas without approving, the button is re-enabled.

If a planner tries to click re-generate while a session is active (e.g., via direct URL or keyboard), the endpoint `POST /plan/generate` checks for an active refine session on that header and returns:
```json
{ "detail": "A refinement session is active. Close or approve it before re-generating." }
```

This is a hard guard — re-generating will permanently discard all refinements in the active session.

### 7.6 Chat locked state

After approval (from either button): input box becomes `disabled`, send button removed, header shows "✅ Approved". The message history remains visible for audit. The main page "↺ Re-generate Shift" button remains disabled (plan is approved; re-generate is already blocked by existing logic).

---

## 8. LLMOps: LangSmith Engine + Context Hub

The observability and continuous-improvement layer has two distinct responsibilities handled by two distinct tools:

| Tool | Role |
|---|---|
| **LangSmith Engine** | Capture, annotate, evaluate, and curate every agent interaction |
| **Context Hub** (LangSmith Hub) | Version, store, and serve system prompts + domain knowledge at runtime |

---

### 8.1 What LangSmith already does in this project

LangSmith tracing is already active (`LANGCHAIN_TRACING_V2`, `LANGCHAIN_API_KEY`, project `agri-agent-poc` in settings). Every `AgentRun` already stores `langsmith_run_id` and `langsmith_trace_url`. The refine feature extends this existing integration — it does not introduce a new tracing mechanism.

---

### 8.2 LangSmith Engine — per-turn trace enrichment

Each time the planner sends a message, the endpoint invokes the `sandhar-plan-refiner` agent, which runs under LangSmith tracing exactly like any other agent in this platform. Two additions are made on top of the existing trace:

**LangSmith project:** Refiner turns are logged to the existing `agri-agent-poc` project — no separate project is created. Separation is achieved via tags, which is sufficient for annotation queue filtering and dataset curation.

**Run-level tags written on every turn:**

| Tag | Value | Why |
|---|---|---|
| `agent` | `sandhar-plan-refiner` | Distinguishes refiner turns from all other runs in the shared `agri-agent-poc` project |
| `session_id` | UUID | Group all turns in a session |
| `plan_date` | `2026-07-06` | Filter by date for seasonal analysis |
| `shift_code` | `A` / `B` | Shift-level performance splits |
| `turn_index` | `1`, `2`, `3`… | Detect repeated instructions |
| `outcome` | `in_progress` / `approved` / `reversed` | Applied retroactively on session close |

**`outcome: reversed`** is the most valuable signal — set on any turn where the planner's next message undoes what the AI just did (e.g., AI moved WO to Line 2; planner then says "move it back to Line 3"). Detecting reversals is done by comparing consecutive `plan_snapshot` diffs at session-close time.

---

### 8.3 LangSmith Engine — Annotation Queues

Not every session needs human review. Sessions are automatically queued for annotation under these conditions:

Annotation queues are filtered using the `agent: sandhar-plan-refiner` tag within the shared `agri-agent-poc` project.

| Trigger | Queue name | What annotators do |
|---|---|---|
| Planner needed > 3 turns to approve | `sandhar-refiner-complex` | Label *why* first attempt failed (wrong WO ID, wrong line, wrong qty interpretation, constraint gap) |
| At least one `reversed` turn | `sandhar-refiner-reversals` | Confirm intent label, mark gold tool-call sequence |
| Session closed without approve | `sandhar-refiner-abandoned` | Free-text note on what the AI got wrong |

Annotations are written back to the LangSmith run as `feedback` records (score 0–1 + label). This is the ground truth that feeds the dataset.

---

### 8.4 LangSmith Engine — Dataset and Evaluators

**Dataset: `sandhar-refiner-training`**

Built from annotated sessions. Each row is one planner turn:

```
input:  { plan_context: <current plan JSON>, conversation_history: [...], user_message: "..." }
output: { tool_calls: [...], response_text: "..." }
label:  "positive" | "corrected"   (from annotation)
```

- **Positive examples** — approved in ≤ 2 turns, no reversals. These are the direct fine-tuning signal.
- **Corrected examples** — annotator wrote the *correct* tool-call sequence alongside the AI's wrong one. Used for contrastive fine-tuning or DPO.

**Evaluators run on every new agent version:**

| Evaluator | Checks |
|---|---|
| `tool_call_accuracy` | Did the agent call the right tool for the stated intent? (LLM-as-judge against annotated gold) |
| `plan_validity` | After tool execution, does the plan still satisfy hard constraints (line capacity, shift hours)? (deterministic) |
| `response_conciseness` | Response ≤ 120 words? (heuristic) |
| `turns_to_approve` | Average turns per session ≤ 2? (population metric over eval dataset) |

---

### 8.5 Context Hub (LangSmith Hub)

Context Hub is the **versioned prompt registry** for this platform, implemented using LangSmith Hub (`hub.langchain.com`). System prompts and domain-knowledge templates are stored in the Hub rather than hardcoded in YAML files. This decouples prompt iteration from code deployments.

**Hub artifacts are public** for this POC phase — readable without credentials. The `LANGCHAIN_API_KEY` already present in the environment is sufficient for writes. This can be tightened to private org-scoped access in a production rollout.

**What goes into the Hub:**

| Hub artifact | Slug | Contents |
|---|---|---|
| Refiner system prompt | `sandhar/plan-refiner-system` | Role definition, tool-use instructions, confirmation rules, response style |
| Planning domain context | `sandhar/planning-domain-context` | Sandhar line codes, shift structure, capacity rules, priority hierarchy — injected as a system-level context block every turn |
| Supervisor system prompt | `sandhar/planning-supervisor-system` | Existing planning supervisor prompt — moved to Hub when supervisor improvements are identified via LLMOps |

**How agents pull from the Hub at runtime:**

```python
# In the refiner endpoint, before invoking the agent:
from langchain import hub
system_prompt = hub.pull("sandhar/plan-refiner-system")           # latest tagged version
domain_context = hub.pull("sandhar/planning-domain-context")      # pinned commit hash in config
```

The YAML config for `sandhar-plan-refiner` gains two new fields:

```yaml
context_hub:
  system_prompt: "sandhar/plan-refiner-system:latest"
  domain_context: "sandhar/planning-domain-context:v1.2"
```

Using `:latest` for the system prompt means prompt improvements are picked up on the next container restart without a code change. Domain context uses a pinned version so accidental upstream edits don't silently change agent behaviour.

---

### 8.6 Continuous improvement loop

```
Planner sessions (daily)
        │
        ▼
LangSmith Engine — auto-tags, Annotation Queues
        │
        ▼
Annotators review flagged sessions → label intents, write gold tool calls
        │
        ▼
Curated examples appended to Dataset: sandhar-refiner-training
        │
        ▼
Prompt engineer iterates system prompt in Context Hub (draft commit)
        │
        ▼
Run LangSmith Evaluators against dataset with new prompt version
        │
   improvement?
   ├─ YES → merge to :latest tag in Hub → picked up on next restart
   └─ NO  → iterate further, keep current :latest
        │
        ▼
Downstream: if pattern is in upstream supervisor (e.g., AI always
mis-allocates Line 5 for WO type X), the supervisor system prompt
in sandhar/planning-supervisor-system is also updated → fewer
refinement sessions needed in future
```

---

### 8.7 LangSmith setup (one-time)

No new LangSmith project is needed. All refiner traces land in the existing `agri-agent-poc` project. The one-time setup tasks are:

1. **Define annotation queues** in the `agri-agent-poc` project with the filter `agent = sandhar-plan-refiner` — three queues as listed in §8.3.
2. **Create Hub artifacts** `sandhar/plan-refiner-system` and `sandhar/planning-domain-context` in LangSmith Hub with an initial version tagged `:latest`.
3. No `LANGCHAIN_PROJECT` env var override is required in the refiner endpoint.

---

## 9. What Is NOT Changed

This section explicitly lists existing code that is **not modified**:

| Component | Status |
|---|---|
| `POST /plan/{header_id}/approve` | Unchanged — canvas calls it as-is |
| `POST /plan/{header_id}/reject` | Unchanged |
| `POST /plan/generate` | Unchanged |
| `SandharPlanHeader` model | Unchanged — no new columns |
| `SandharPlanDetail` model | Unchanged — tool writes to it via existing columns |
| `sandhar-planning-supervisor` agent | Unchanged |
| All existing planning tools | Unchanged — refiner tools are new files |
| Action Inbox / HITL flow | Unchanged — approve still goes through same path |
| `AgentRun` table | Unchanged — per-turn runs still stored there |
| Runs page (`/runs`) | Unchanged — refiner turns appear there naturally |
| Simulation, master, dashboard pages | Unchanged |

The only existing file touched is `templates/sandhar/plan.html` (add button + canvas).

---

## 10. Implementation Sequence

The feature can be built and merged incrementally without ever breaking the live system:

| Step | What | Risk if stopped here |
|---|---|---|
| 0 | **Context Hub + LangSmith setup** — create Hub artifacts `sandhar/plan-refiner-system` and `sandhar/planning-domain-context` in LangSmith Hub; define three annotation queues in the existing `agri-agent-poc` project (no new project needed) | Zero — nothing in code pulls from Hub yet |
| 1 | Alembic migration (two new tables only) | Zero — no code reads them yet |
| 2 | New API endpoints (start, message, close) | Zero — no UI calls them yet |
| 3 | New agent YAML + refiner tools; wire `context_hub` fields to pull prompts at runtime | Zero — not wired to anything |
| 4 | Canvas HTML/CSS in plan.html (hidden by default) | Zero — button not rendered yet |
| 5 | Canvas JS + button render | Feature live, existing buttons unchanged |
| 6 | **Post-launch** — annotate first 20 sessions; run first evaluator pass; promote first prompt revision to `:latest` | Additive — does not affect running feature |

---

## 11. Design Decisions Log

All design questions have been resolved. This section records each decision for traceability.

| # | Question | Decision | Impact on design |
|---|---|---|---|
| 1 | Auth / planner identity | `opened_by = "anonymous"` for v1; auth deferred | No login check on session start |
| 2 | Concurrent refinement | Single-user system assumed for v1; no lock needed | `start` returns existing session idempotently; no presence detection |
| 3 | Re-generate after refine | Re-generate **discards all refinements** | Re-generate button disabled while canvas is open; hard server-side guard added to `POST /plan/generate` (§7.5) |
| 4 | Streaming vs polling | **SSE confirmed** — no polling fallback | `POST /refine/{session_id}/message` is `text/event-stream` only |
| 5 | Plan snapshot storage | **Full snapshot per turn in v1**; diff format in v2 | `plan_snapshot` column stores complete JSON; no migration needed for v2 upgrade |
| 6 | Agent activation | **Auto-register + auto-activate on first `start` call** | `start` endpoint handles activation; Agents dashboard not needed for this agent |
| 7 | Hub artifact visibility | **Public** for POC phase | No credential overhead for Hub reads; tighten in production rollout |
| 8 | Hub prompt pinning ownership | Updates to pinned commit hash require a **PR + eval pass** before merge | Documented as a dependency upgrade workflow; no tooling change needed |
| 9 | LangSmith project | **Same project (`agri-agent-poc`)**, differentiated by `agent: sandhar-plan-refiner` tag | No new project; no `LANGCHAIN_PROJECT` override; annotation queues filtered by tag |

# Conversational Plan Refinement — Feature & Platform Design

**Feature name:** "Refine with AI"  
**Target page (first consumer):** `/sandhar/plan?date=YYYY-MM-DD`  
**Status:** Implemented (Phases 1–3 complete)  
**Author:** Design session — 2026-07-06

---

## 1. What Are We Building

Two things, layered on top of each other:

**Layer 1 — Platform generic capability**  
A "Refine with AI" canvas attached to the existing `AgentAction` / HITL system. Any agent can opt into it via a feature flag in its YAML config. The canvas (chat + live preview), the session/message storage, the SSE streaming endpoint, and the LLMOps wiring are all platform-level and shared across every agent that enables the flag. No custom UI code is needed per domain.

**Layer 2 — Sandhar reference implementation**  
The first consumer of the platform capability. When a Sandhar production plan is generated and proposed for review, the planner sees **Approve**, **Reject**, and **Refine with AI** in the action inbox (and also on the plan page). The refinement agent has Sandhar-specific tools (move WO, update quantity, etc.) and a Sandhar-specific live preview partial. These domain pieces are the only custom code written for Sandhar; everything else is inherited from the platform layer.

### 1.1 Why generic matters

The `AgentAction` system already uses this exact pattern for `track_resource_state` — `actions.py` reads `agent.config.get("feature_flags", {})` at runtime (line 166). The refinement feature is a second feature flag following the same convention. Once the platform layer exists, the cost of giving any future agent a conversational refinement canvas is:
1. Write a domain-specific refinement agent + tools
2. Optionally write a domain-specific preview template partial
3. Set three lines in the agent's YAML

---

## 2. User Experience Flow

### 2.1 Generic (action inbox)

```
Action Inbox — pending actions list
        │
        ▼
Action row for any HITL agent with enable_refinement: true
shows three buttons:

  [✅ Approve]  [✗ Reject]  [✨ Refine with AI]   ← platform button
        │
        ▼  planner clicks "Refine with AI"
Right-side canvas slides in (58 % width, full viewport height)
Action card stays visible on left (42 %)
        │
        ▼
Canvas header:
  "Refining: <action title>"  [✅ Approve]  [✕ Close]
        │
        ▼
Chat window — welcome message from the configured refinement agent.
        │
        ▼  planner types: "Move WO-SEED-0003 to Line 2"
        │
        ▼
[Thinking…] spinner → agent calls domain-specific tools
Live preview panel on left updates
AI responds with summary of change
        │
        ▼  planner types "Approve" OR clicks [✅ Approve] in canvas
        │
        ▼
Platform calls existing POST /api/v1/actions/{action_id}/approve
Chat locked, canvas shows "✅ Approved at 14:32. Conversation saved."
```

### 2.2 Sandhar plan page (additional entry point)

The Sandhar plan page (`/sandhar/plan`) has its own inline Approve/Reject buttons that bypass the inbox. The "Refine with AI" button on this page also appears alongside them. It calls the same generic endpoint using the `action_id` of the pending AgentAction for that plan — obtained from the existing plan-load response (a small addition: `action_id` returned when a `pending_review` action exists for the plan header).

---

## 3. Architecture

```
Browser                         FastAPI (api container)              DB (postgres)
──────                          ──────────────────────               ─────────────
approvals.html /                                                      ─ PLATFORM ─
sandhar/plan.html               /api/v1/actions/                      agent_actions (existing)
  │                             {action_id}/refine/start  ─────────► agent_refine_session  ← NEW
  │  POST start ───────────────►                                       agent_refine_message ← NEW
  │◄──── session_id ────────────
  │
  │  POST message ─────────────► /api/v1/actions/          ─────────► invokes refinement agent
  │  (SSE stream)                {action_id}/refine/message            (name from feature_flags)
  │◄─── token stream / done ────                                       │
  │                                                                    ▼ calls domain tools
  │  preview refresh ──────────► domain-specific GET  ─────────────►  domain tables mutated
  │◄─── updated preview JSON ───
  │
  │  POST approve ─────────────► /api/v1/actions/          ─────────► (existing endpoint, unchanged)
                                 {action_id}/approve
```

**Key principle:** Every path through the approval funnel — Approve, Reject, Refine-then-Approve — converges at the same existing `POST /api/v1/actions/{action_id}/approve` endpoint. No new approval logic is added.

**Layering:**

```
┌────────────────────────────────────────────────────────────┐
│  PLATFORM  (built once, shared)                            │
│  agent_refine_session / message tables                     │
│  /api/v1/actions/{id}/refine/* endpoints                   │
│  Generic canvas component in approvals.html                │
│  LangSmith tagging + annotation queue wiring               │
└────────────────────────────────────────────────────────────┘
        ▲ consumes
┌────────────────────────────────────────────────────────────┐
│  SANDHAR DOMAIN  (first consumer)                          │
│  sandhar-plan-refiner YAML + tools                         │
│  _refine_preview_sandhar-plan.html partial                 │
│  feature_flags on sandhar-planning-supervisor YAML         │
└────────────────────────────────────────────────────────────┘
```

---

## 4. Platform Layer: Feature Flag Convention

No changes to `AgentConfig` model are needed. The existing `feature_flags: dict[str, Any] = {}` field accepts arbitrary keys. The same pattern already used for `track_resource_state` in `actions.py` is extended with three new keys:

```yaml
# In any agent YAML that wants conversational refinement:
feature_flags:
  enable_refinement: true
  refinement_agent: "sandhar-plan-refiner"    # name of the agent to invoke in the canvas
  refinement_preview: "sandhar-plan"           # name of the Jinja preview partial (optional)
```

| Flag key | Type | Required | Default | Purpose |
|---|---|---|---|---|
| `enable_refinement` | bool | yes | false | Shows/hides "Refine with AI" button in the inbox and on domain pages |
| `refinement_agent` | string | if enabled | — | The agent name to run each chat turn; must be a valid registered agent |
| `refinement_preview` | string | no | `""` | Name of the Jinja partial for the live preview panel; falls back to generic JSON viewer if absent |

**How the platform reads this** (in `actions.py`, following the existing `_check_drift` pattern):
```python
flags = agent.config.get("feature_flags", {})
refinement_enabled = flags.get("enable_refinement", False)
refinement_agent   = flags.get("refinement_agent", "")
refinement_preview = flags.get("refinement_preview", "")
```

---

## 5. Platform Layer: New Data Models

### 5.1 `agent_refine_session`

One row per refinement session on one `AgentAction`. A session is the container for the full conversation.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `action_id` | UUID FK → agent_actions | The action being refined |
| `refinement_agent` | VARCHAR(100) | Copied from feature_flags at session creation; stable even if YAML changes |
| `status` | VARCHAR(20) | `active` · `approved` · `closed` |
| `opened_by` | VARCHAR(100) | `"anonymous"` for v1; auth field reserved |
| `created_at` | TIMESTAMPTZ | |
| `closed_at` | TIMESTAMPTZ | Set on approve or explicit close |

### 5.2 `agent_refine_message`

One row per message turn (user or assistant).

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `session_id` | UUID FK → agent_refine_session | |
| `role` | VARCHAR(20) | `user` · `assistant` · `system` |
| `content` | TEXT | |
| `tool_calls` | JSONB | `[{tool, args, result}]` — LLMOps training signal |
| `context_snapshot` | JSONB | Full domain context JSON *after* this turn — generic name covers plan, email, dispatch, etc. |
| `langsmith_run_id` | VARCHAR(100) | Per-turn trace |
| `langsmith_trace_url` | TEXT | |
| `input_tokens` | INT | |
| `output_tokens` | INT | |
| `created_at` | TIMESTAMPTZ | |

**Column naming:** `context_snapshot` replaces the earlier `plan_snapshot` name — generic enough for any domain object (plan details, email body, dispatch order JSON, etc.).

### 5.3 Alembic migration

One new migration file: `alembic/versions/XXXX_add_agent_refine_tables.py` — creates both tables. No changes to any existing table.

---

## 6. Platform Layer: New API Endpoints

All added to the existing `actions.py` router (`prefix="/api/v1/actions"`). All require `X-API-Key` (same as all other action endpoints).

### 6.1 Start a session

```
POST /api/v1/actions/{action_id}/refine/start
Response: { session_id, action_id, refinement_agent, status, welcome_message }
```

- Validates the action exists and is in `pending_review` status.
- Reads `feature_flags.enable_refinement` from the action's agent config — returns 403 if `false`.
- Creates an `agent_refine_session` row (or returns the existing `active` session idempotently).
- **Auto-registers and auto-activates** the `refinement_agent` if not yet active in the DB. This internal agent does not require manual activation via the Agents dashboard.
- Returns a context-aware `welcome_message` (generated by briefly describing the action to the refinement agent).

### 6.2 Send a message — SSE streaming

```
POST /api/v1/actions/{action_id}/refine/message
Body:     { "content": "Move WO-SEED-0003 to Line 2" }
Response: text/event-stream
```

- Validates session is still `active`.
- Persists user message row.
- Invokes the `refinement_agent` with conversation history + injected domain context.
- Streams LLM token output via SSE. SSE is the sole delivery mechanism — no polling fallback.
- On stream completion: persists assistant message row (tool_calls, context_snapshot, tokens).
- Emits `event: done` — frontend uses this to trigger a preview refresh.

SSE event format:
```
data: {"type": "token",    "content": "Done. WO-SEED-0003 moved..."}
data: {"type": "tool_use", "tool": "sandhar_refine_move_wo", "args": {...}}
data: {"type": "done",     "session_id": "..."}
```

### 6.3 Get message history

```
GET /api/v1/actions/{action_id}/refine/messages
Response: [{ id, role, content, tool_calls, created_at, ... }]
```

Used on page reload to restore an in-progress session.

### 6.4 Close session without approving

```
POST /api/v1/actions/{action_id}/refine/close
```

- Sets session `status = closed`. Does not touch the action or its underlying domain state.
- The action remains `pending_review` — the planner can still Approve or Reject normally.

### 6.5 Approve (existing endpoint — unchanged)

```
POST /api/v1/actions/{action_id}/approve   ← no changes
```

After approval, the canvas JS calls `close` on the session with `status = approved`. No new approval endpoint.

---

## 7. Platform Layer: Canvas UI

### 7.1 Files touched

| File | Change type |
|---|---|
| `templates/approvals.html` | Add "Refine with AI" button, generic canvas HTML/CSS/JS |
| `templates/base.html` | Add `.btn-refine` style and canvas container (if not in approvals.html already) |

**Domain-specific files:** each Sandhar-specific preview partial is in `templates/sandhar/`.  
**No other existing template is touched.**

### 7.2 Button visibility

The platform injects `enable_refinement` into the action row context when rendering the inbox. The button is shown only when:
- `feature_flags.enable_refinement == true` on the action's agent, AND
- Action status is `pending_review`

```html
<!-- Platform-rendered in approvals.html action row (pseudocode) -->
{% if action.enable_refinement %}
<button class="btn btn-refine" onclick="openRefineCanvas('{{action.id}}')">
  ✨ Refine with AI
</button>
{% endif %}
```

### 7.3 Canvas structure (generic)

```
┌──────────────────────────────────────────────────────────────────────┐
│  PREVIEW PANEL (42 %)              │  CHAT CANVAS (58 %)             │
│                                    │  ┌──────────────────────────────┐│
│  Rendered by:                      │  │ Refining: <action.title>     ││
│  _refine_preview_{name}.html       │  │      [✅ Approve]  [✕ Close] ││
│  OR generic JSON viewer            │  ├──────────────────────────────┤│
│                                    │  │ 🤖 <welcome message>         ││
│  Updates live after every          │  │ 👤 <user message>            ││
│  event:done SSE event              │  │ 🤖 <assistant response>      ││
│                                    │  ├──────────────────────────────┤│
│                                    │  │ [ Type your instruction... ] ││
│                                    │  │                     [Send →] ││
│                                    │  └──────────────────────────────┘│
└──────────────────────────────────────────────────────────────────────┘
```

**Preview panel resolution order:**
1. `_refine_preview_{refinement_preview}.html` — domain-specific partial (e.g., `_refine_preview_sandhar-plan.html`)
2. If not found → generic JSON viewer that renders `context_snapshot` from the last message turn

### 7.4 Canvas JavaScript logic (generic)

```
openRefineCanvas(actionId)
  │
  ├── POST /api/v1/actions/{actionId}/refine/start
  ├── store session_id, refinement_preview name
  ├── render preview panel (load partial or JSON viewer)
  ├── slide canvas in
  └── render welcome message

sendMessage(content)
  │
  ├── append user bubble immediately
  ├── show [Thinking…] / tool-use indicator
  ├── POST /api/v1/actions/{actionId}/refine/message  (SSE)
  │     read stream:
  │       type:token    → append to assistant bubble
  │       type:tool_use → show tool badge in bubble
  │       type:done     → call refreshPreview()
  └── refreshPreview()
        └── re-fetch domain data + re-render preview partial

closeCanvas()
  │
  ├── POST /api/v1/actions/{actionId}/refine/close
  └── restore full-width action view

approveFromCanvas(actionId)
  │
  ├── POST /api/v1/actions/{actionId}/approve  (existing)
  └── on success → lock chat input, show "✅ Approved" banner
```

### 7.5 Chat locked state

After approval (from any entry point): chat input disabled, send button removed, header shows "✅ Approved at HH:MM". Message history stays visible for audit.

---

## 8. Sandhar Reference Implementation

### 8.1 YAML change: `sandhar-planning-supervisor.yaml`

Add three lines to the existing supervisor's `feature_flags`:

```yaml
feature_flags:
  # ... existing flags unchanged ...
  enable_refinement: true
  refinement_agent: "sandhar-plan-refiner"
  refinement_preview: "sandhar-plan"
```

This is the **only change** to the existing supervisor YAML. No tools, system prompt, or routing config is touched.

### 8.2 New agent: `agents/configs/sandhar-plan-refiner.yaml`

```yaml
agent:
  name: sandhar-plan-refiner
  type: react
  companies: [sandhar]
  description: >
    Conversational plan refinement agent. Reads the current draft Sandhar
    production plan and applies targeted edits in response to planner instructions.
  context_hub:
    system_prompt: "sandhar/plan-refiner-system:latest"
    domain_context: "sandhar/planning-domain-context:v1.0"
  inputs:
    plan_header_id:
      type: string
      required: true
      description: "UUID of the SandharPlanHeader being refined"
```

The `context_hub` field instructs the platform endpoint to pull the system prompt and domain context from LangSmith Hub before invoking the agent. `plan_header_id` is extracted from the AgentAction's `display_data` at session start and injected as `extra_context`.

### 8.3 New tool file: `src/fde_agent/agent/tools/sandhar/plan_refiner.py`

Six focused tools. All operate against a specific `plan_header_id` passed via agent context:

| Tool | What it does | Tables mutated |
|---|---|---|
| `sandhar_refine_get_plan` | Returns full current plan details — lines, WOs, quantities, gaps, manpower | None (read-only) |
| `sandhar_refine_update_qty` | Updates `planned_qty` on one `plan_detail` row | `sandhar_plan_detail` |
| `sandhar_refine_move_wo` | Reassigns a WO to a different line (`line_id` update) | `sandhar_plan_detail` |
| `sandhar_refine_add_wo` | Adds an open WO as a new `plan_detail` row | `sandhar_plan_detail` |
| `sandhar_refine_remove_wo` | Removes a `plan_detail` row (WO returns to unplanned) | `sandhar_plan_detail` |
| `sandhar_refine_explain_constraint` | Explains a specific gap or alert in plain language | None (read-only) |

Only `sandhar_plan_detail` and `sandhar_resource_allocation` are ever written to — the same tables the planning supervisor writes during generation.

### 8.4 New preview partial: `templates/sandhar/_refine_preview_sandhar-plan.html`

A Jinja partial that renders the current plan as a live table (same visual as the plan page). Receives the current plan detail JSON and re-renders after each `event:done`. This is the only new Sandhar-specific template file.

### 8.5 System prompt — Context Hub

System prompt stored in LangSmith Hub as `sandhar/plan-refiner-system:latest`. Key directives:
- Role: plan refinement assistant with Sandhar manufacturing context
- Given the full current plan as `[Runtime context]` on the first turn
- Confirm every destructive operation before executing unless user intent is unambiguous
- Keep responses concise: one short paragraph + a "What changed" summary line
- Cannot approve or reject — only the human can do that

Domain context stored as `sandhar/planning-domain-context:v1.0`:
- Sandhar line codes (L001–L010), their product families, and capacity constraints
- Shift structure (A/B/C) and working hours
- WO priority rules and hard constraints

### 8.6 Plan page entry point

The Sandhar plan page (`/sandhar/plan`) exposes Approve/Reject inline. To surface "Refine with AI" here too, a small addition is made to the existing plan-load response:

```python
# GET /api/v1/sandhar/plan/versions?date=...  (existing endpoint)
# Add to response per plan header:
"action_id": "<uuid>"   # if a pending_review AgentAction exists for this header, else null
```

Frontend logic:
```javascript
// When rendering plan actions:
if (plan.action_id) {
  // show Refine with AI button — calls generic platform endpoint
  renderRefineButton(plan.action_id);
}
```

If `action_id` is null (e.g., plan was generated but `propose_plan_for_review` wasn't called), the Refine button is simply not rendered — Approve and Reject remain available as today.

### 8.7 Re-generate button guard

When a refinement canvas is open (active session exists for the plan's action), the **"↺ Re-generate Shift"** button on the plan page is disabled. On canvas close, it re-enables.

Server-side guard: `POST /sandhar/plan/generate` checks for an active `agent_refine_session` on any action linked to that plan header — returns 422 if found:
```json
{ "detail": "A refinement session is active. Close or approve it before re-generating." }
```

---

## 9. LLMOps: LangSmith Engine + Context Hub

### 9.1 Two tools, two roles

| Tool | Role |
|---|---|
| **LangSmith Engine** | Capture, tag, annotate, evaluate, and curate every refinement turn |
| **Context Hub** (LangSmith Hub) | Version, store, and serve system prompts + domain knowledge |

### 9.2 LangSmith — per-turn trace enrichment

LangSmith tracing is already active (`LANGCHAIN_TRACING_V2`, `LANGCHAIN_API_KEY`, project `fde-agent-poc`). Refiner turns land in the **same project**, distinguished by tags.

**Run-level tags written on every turn:**

| Tag | Value | Purpose |
|---|---|---|
| `feature` | `refine` | Distinguish all refinement turns from other agent runs in the project |
| `refinement_agent` | `sandhar-plan-refiner` | Per-agent filtering (generic — works for any future refiner) |
| `session_id` | UUID | Group all turns in a session |
| `turn_index` | 1, 2, 3… | Detect repeated instructions |
| `outcome` | `in_progress` / `approved` / `reversed` | Applied retroactively at session close |

**`outcome: reversed`** — set on any turn where the planner's next message undoes the AI's previous action. Detected by diffing consecutive `context_snapshot` values at session-close time. This is the most valuable LLMOps signal.

### 9.3 LangSmith — Annotation Queues

Sessions are auto-queued for annotation based on outcome signals. All queues filter on `feature = refine` within the shared `fde-agent-poc` project.

| Trigger | Queue | Annotator task |
|---|---|---|
| > 3 turns to approve | `refine-complex` | Label root cause of AI misunderstanding |
| ≥ 1 reversed turn | `refine-reversals` | Write correct tool-call sequence (gold label) |
| Session closed without approve | `refine-abandoned` | Free-text note on what went wrong |

### 9.4 LangSmith — Dataset and Evaluators

**Dataset: `refiner-training`** — one row per annotated turn:

```
input:  { context: <domain JSON>, history: [...], user_message: "..." }
output: { tool_calls: [...], response_text: "..." }
label:  "positive" | "corrected"
```

**Evaluators:**

| Evaluator | Type | Checks |
|---|---|---|
| `tool_call_accuracy` | LLM-as-judge | Did agent call the right tool for the stated intent? |
| `context_validity` | Deterministic | Does the domain context after tool execution satisfy hard constraints? |
| `response_conciseness` | Heuristic | Response ≤ 120 words? |
| `turns_to_approve` | Population metric | Average turns per approved session ≤ 2? |

### 9.5 Context Hub (LangSmith Hub)

Versioned prompt registry at `hub.langchain.com`. System prompts and domain knowledge live here — decoupled from code deployments. **Public access** for POC phase; tighten to org-private in production.

| Artifact | Slug | Contents |
|---|---|---|
| Sandhar refiner system prompt | `sandhar/plan-refiner-system` | Role, tool instructions, confirmation rules, response style |
| Sandhar domain context | `sandhar/planning-domain-context` | Line codes, capacity rules, shift structure, WO priority hierarchy |
| Sandhar supervisor prompt | `sandhar/planning-supervisor-system` | Moved here when upstream supervisor improvements are identified |

Pull at runtime (in refine endpoint, before invoking agent):
```python
from langchain import hub
system_prompt  = hub.pull("sandhar/plan-refiner-system")          # :latest
domain_context = hub.pull("sandhar/planning-domain-context:v1.0") # pinned
```

Pinned domain context updates require a PR with an eval pass against `refiner-training` dataset before the commit hash is updated in the YAML.

### 9.6 Continuous improvement loop

```
Daily planner refinement sessions
        │
        ▼
LangSmith Engine — auto-tags per turn, outcome applied at session close
        │
        ▼
Sessions meeting annotation triggers → queued in LangSmith (§9.3)
        │
        ▼
Annotators label turns → feedback records written back to runs
        │
        ▼
Curated rows appended to Dataset: refiner-training
        │
        ▼
Prompt engineer drafts improved system prompt in Context Hub
        │
        ▼
Run evaluators on refiner-training against new prompt version
        │
   improvement?
   ├─ YES → tag new commit as :latest → picked up on next container restart
   └─ NO  → iterate; current :latest unchanged
        │
        ▼
Patterns that survive to the upstream supervisor (e.g., recurring
misallocation) → sandhar/planning-supervisor-system also updated
→ fewer sessions needed to reach approval
```

### 9.7 LangSmith setup (one-time)

No new project. One-time tasks in the `fde-agent-poc` project:
1. Define three annotation queues with filter `feature = refine` (§9.3)
2. Create Hub artifacts `sandhar/plan-refiner-system` and `sandhar/planning-domain-context` with initial content tagged `:latest` / `v1.0`

---

## 10. What Is NOT Changed

| Component | Status |
|---|---|
| `POST /api/v1/actions/{id}/approve` | Unchanged — canvas calls it as-is |
| `POST /api/v1/actions/{id}/reject` | Unchanged |
| `AgentAction` model | Unchanged — no new columns |
| `AgentConfig` model | Unchanged — `feature_flags: dict` already accepts new keys |
| `sandhar-planning-supervisor` agent logic | Unchanged — only YAML `feature_flags` block gets 3 new lines |
| All existing planning tools | Unchanged — refiner tools are a new file |
| `SandharPlanHeader` / `SandharPlanDetail` models | Unchanged — refiner tools write to existing columns |
| `AgentRun` table | Unchanged — refiner turns stored there naturally |
| Action Inbox approve/reject flow | Unchanged |
| Runs page (`/runs`) | Unchanged |
| Simulation, master, dashboard pages | Unchanged |

Files modified:
- `templates/approvals.html` — add canvas component + button
- `templates/sandhar/plan.html` — add `action_id` usage + Refine button
- `sandhar-planning-supervisor.yaml` — 3 new `feature_flags` lines
- `src/fde_agent/api/routes/actions.py` — new refine sub-routes added
- `GET /api/v1/sandhar/plan/versions` — add `action_id` to response (small addition)

Files created:
- `alembic/versions/XXXX_add_agent_refine_tables.py`
- `agents/configs/sandhar-plan-refiner.yaml`
- `src/fde_agent/agent/tools/sandhar/plan_refiner.py`
- `templates/sandhar/_refine_preview_sandhar-plan.html`

---

## 11. Phase-Based Development Plan

The work is split into three phases ordered strictly by dependency. Each phase produces a shippable state with zero regression risk to the existing system. Code review happens between phases.

---

### Phase 1 — Data Foundation + Session Lifecycle

**Goal:** The data layer exists and a refinement session can be started, listed, and closed. No AI is invoked, no UI changes. Fully testable by direct API calls. Zero user-visible change.

**Why first:** Everything else — the agent, the SSE pipeline, the canvas — needs these tables and endpoints to exist before they can be built or tested.

#### Tasks

| # | File | What |
|---|---|---|
| 1.1 | `alembic/versions/XXXX_add_agent_refine_tables.py` | Migration: create `agent_refine_session` and `agent_refine_message` tables (§5.1, §5.2) |
| 1.2 | `src/fde_agent/db/models.py` | Add `AgentRefineSession` and `AgentRefineMessage` SQLAlchemy model classes |
| 1.3 | `src/fde_agent/api/routes/actions.py` | Extend `_action_out()` to include `enable_refinement`, `refinement_agent`, `refinement_preview` from the action's agent `feature_flags` — same pattern as `track_resource_state` at line 166 |
| 1.4 | `src/fde_agent/api/routes/actions.py` | Add `POST /{action_id}/refine/start` — creates session, validates action status, auto-registers + auto-activates the `refinement_agent` if not already active (§6.1) |
| 1.5 | `src/fde_agent/api/routes/actions.py` | Add `GET /{action_id}/refine/messages` — returns message list for the active session (§6.3) |
| 1.6 | `src/fde_agent/api/routes/actions.py` | Add `POST /{action_id}/refine/close` — sets session status to `closed`; no reversal detection yet (added in Phase 2) |
| 1.7 | `src/fde_agent/api/routes/sandhar/planning.py` | Add `action_id` field to `GET /plan/versions` response — queries `agent_actions` for a `pending_review` action whose `display_data` references this plan header (§8.6) |

#### Dependencies between tasks
```
1.1 migration
  └─► 1.2 models
        └─► 1.3 _action_out (reads feature_flags — no table access)
        └─► 1.4 start endpoint
        └─► 1.5 messages endpoint
        └─► 1.6 close endpoint
        └─► 1.7 plan-load action_id (queries agent_actions — table already exists)
```

#### Exit criteria (Phase 1 complete when all pass)
- `POST /api/v1/actions/{id}/refine/start` → 200, returns `{ session_id, status: "active" }`
- `GET /api/v1/actions/{id}/refine/messages` → 200, returns `[]`
- `POST /api/v1/actions/{id}/refine/close` → 200, session status = `closed`
- `GET /api/v1/sandhar/plan/versions?date=...` → plan header JSON includes `action_id` when a `pending_review` action exists, `null` otherwise
- `GET /api/v1/actions/{id}` response includes `enable_refinement: false` (no agent has the flag yet — correct)
- No existing tests broken; all existing action endpoints return identical responses for existing fields

---

### Phase 2 — Sandhar Refiner Agent + SSE Message Pipeline

**Goal:** Full conversational backend. A planner can POST a message to the refine endpoint and receive a streaming AI response that makes real changes to `sandhar_plan_detail`. LangSmith traces appear. Re-generate is blocked. No UI yet — testable entirely via API/curl.

**Why second:** The SSE message endpoint depends on Phase 1 tables (to persist messages) and on the agent + tools (to invoke). The agent tools depend on the existing sandhar DB models (already exist). Context Hub artifacts must exist before the endpoint tries to pull them.

#### Tasks

| # | File / Location | What |
|---|---|---|
| 2.1 | LangSmith Hub (ops) | Create `sandhar/plan-refiner-system:latest` with initial system prompt content (§8.5); create `sandhar/planning-domain-context:v1.0` with Sandhar line/shift/WO domain knowledge |
| 2.2 | `agents/configs/sandhar-plan-refiner.yaml` | New agent config — `type: react`, `companies: [sandhar]`, `context_hub` fields pointing to Hub artifacts, `inputs: plan_header_id` (§8.2) |
| 2.3 | `src/fde_agent/agent/tools/sandhar/plan_refiner.py` | 6 refiner tools: `sandhar_refine_get_plan`, `sandhar_refine_update_qty`, `sandhar_refine_move_wo`, `sandhar_refine_add_wo`, `sandhar_refine_remove_wo`, `sandhar_refine_explain_constraint` (§8.3) |
| 2.4 | `src/fde_agent/api/routes/actions.py` | Add `POST /{action_id}/refine/message` — SSE streaming endpoint: validate session, persist user message, pull prompts from Context Hub, extract `plan_header_id` from `AgentAction.display_data`, invoke `refinement_agent` with conversation history, stream tokens as SSE events (`token` / `tool_use` / `done`), persist assistant message row with `tool_calls`, `context_snapshot`, token counts, `langsmith_run_id`, write LangSmith tags (`feature=refine`, `refinement_agent`, `session_id`, `turn_index`) (§6.2) |
| 2.5 | `src/fde_agent/api/routes/actions.py` | Update `POST /{action_id}/refine/close` — add reversal detection: diff consecutive `context_snapshot` JSON pairs, retroactively apply `outcome` feedback tag to LangSmith runs in the session (`approved` / `reversed` / `closed`) |
| 2.6 | `src/fde_agent/api/routes/sandhar/planning.py` | Re-generate hard guard: `POST /plan/generate` queries for an `active` `agent_refine_session` on any `pending_review` action linked to the plan header → returns 422 with `"A refinement session is active"` detail (§8.7) |

#### Dependencies between tasks
```
2.1 Hub artifacts (ops — must exist before 2.4 can pull them)

2.2 sandhar-plan-refiner.yaml
  └─► 2.3 plan_refiner.py tools (referenced by agent)
        └─► 2.4 SSE message endpoint (invokes the agent)
              └─► 2.5 close update (reads context_snapshots written by 2.4)

2.6 re-generate guard (reads agent_refine_session from Phase 1 — independent of 2.2–2.5)
```

#### Exit criteria (Phase 2 complete when all pass)
- `POST /api/v1/actions/{id}/refine/message` with `{"content": "Move WO-0003 to Line 2"}` → SSE stream of token events → `event: done`
- Corresponding `sandhar_plan_detail` row updated in DB (WO on new line)
- `agent_refine_message` row persisted with non-null `tool_calls` and `context_snapshot`
- LangSmith `fde-agent-poc` project shows trace with tags `feature=refine`, `refinement_agent=sandhar-plan-refiner`, `session_id=<uuid>`, `turn_index=1`
- Closing the session after an AI reversal sets `outcome=reversed` tag on the relevant LangSmith run
- `POST /sandhar/plan/generate` with an active refine session → 422

---

### Phase 3 — Canvas UI + Go-Live

**Goal:** Full user-facing feature. The canvas appears in the action inbox and on the Sandhar plan page. The feature flag is flipped on the supervisor. Rollback path verified before deploying.

**Why third:** The canvas JS calls Phase 1 endpoints (start, close) and Phase 2 endpoint (message SSE). Both must be working before the UI can be built and tested. The Sandhar preview partial requires the agent and tools from Phase 2 to produce a meaningful `context_snapshot` to render.

#### Tasks

| # | File | What |
|---|---|---|
| 3.1 | LangSmith (ops, pre-launch) | Define 3 annotation queues in `fde-agent-poc` project with filter `feature = refine`: `refine-complex` (> 3 turns), `refine-reversals` (reversed outcome), `refine-abandoned` (closed without approve) (§9.3) |
| 3.2 | `templates/approvals.html` | CSS: `.btn-refine`, split-screen canvas layout, chat bubble styles (user/assistant), tool-use badge, locked state dimming |
| 3.3 | `templates/approvals.html` | HTML: canvas panel structure — header bar (title + Approve + Close), preview pane (partial slot), message list, input row |
| 3.4 | `templates/approvals.html` | JS — `openRefineCanvas(actionId)`: call `start`, store `session_id` + `refinement_preview`, slide panel in, render welcome message |
| 3.5 | `templates/approvals.html` | JS — `sendMessage(content)`: append user bubble, POST message endpoint, read SSE `EventSource`, append tokens to assistant bubble, show tool-use badges on `type:tool_use` events, call `refreshPreview()` on `type:done` |
| 3.6 | `templates/approvals.html` | JS — `closeCanvas()`: call close endpoint, restore full-width view; `approveFromCanvas(actionId)`: call existing `POST /actions/{id}/approve`, lock chat on success |
| 3.7 | `templates/approvals.html` | JS — `refreshPreview()`: re-fetch domain data (endpoint varies by `refinement_preview` name), re-render preview partial via fetch + innerHTML swap; fallback: render last `context_snapshot` as formatted JSON |
| 3.8 | `templates/sandhar/_refine_preview_sandhar-plan.html` | Jinja partial — renders current plan details as a styled table (same visual language as the plan page); receives plan detail JSON, highlights rows changed in the last turn |
| 3.9 | `templates/sandhar/plan.html` | Render "Refine with AI" button when `plan.action_id` is non-null; wire to `openRefineCanvas(plan.action_id)`; disable "↺ Re-generate" while canvas is open; re-enable on close |
| 3.10 | `agents/configs/sandhar-planning-supervisor.yaml` | Add `enable_refinement: true`, `refinement_agent: "sandhar-plan-refiner"`, `refinement_preview: "sandhar-plan"` to `feature_flags` — **this is the go-live switch** |

#### Dependencies between tasks
```
3.1 annotation queues (ops — no code dependency, best done before go-live)

3.2 CSS
  └─► 3.3 HTML (uses CSS classes)
        └─► 3.4 JS openRefineCanvas (uses Phase 1 start endpoint)
              └─► 3.5 JS sendMessage (uses Phase 2 SSE endpoint)
                    └─► 3.7 JS refreshPreview (fetches domain data)
                          └─► 3.8 Sandhar preview partial (rendered by refreshPreview)
              └─► 3.6 JS closeCanvas + approveFromCanvas (uses Phase 1 close + existing approve)

3.9 plan.html (calls openRefineCanvas from 3.4; uses action_id from Phase 1 task 1.7)

3.10 supervisor YAML flag ← deploy last, after 3.1–3.9 are deployed and smoke-tested
     Rollback = remove the 3 lines from the YAML
```

#### Exit criteria (Phase 3 complete when all pass)
- "Refine with AI" button visible in action inbox for any pending Sandhar plan action
- Canvas opens, shows welcome message from agent
- Chat message sent → tokens stream in real time → plan table updates on left
- Tool-use badge appears in chat bubble when agent calls a tool
- Approve from canvas → existing approve executes → chat locked → action status `approved`
- Plan page shows Refine button when `action_id` is present; hides it when null
- Re-generate button disabled while canvas open; re-enabled on close
- Rollback verified: remove 3 `feature_flags` lines from supervisor YAML → button disappears, all other action inbox behaviour unchanged

---

### Phase summary

```
Phase 1 ── Data Foundation + Session Lifecycle
  Output: Tables exist, session CRUD API works
  Visible to user: NO
  Blocks: Phase 2

Phase 2 ── Sandhar Refiner Agent + SSE Pipeline
  Output: Full conversational API, plan changes, LangSmith traces
  Visible to user: NO (API only)
  Blocks: Phase 3

Phase 3 ── Canvas UI + Go-Live
  Output: Feature live in inbox + plan page
  Visible to user: YES (after task 3.10)
  Go-live switch: sandhar-planning-supervisor.yaml feature_flags
  Rollback: remove 3 lines from that YAML
```

---

## 12. Design Decisions Log

| # | Question | Decision | Where applied |
|---|---|---|---|
| 1 | Custom Sandhar vs generic platform | **Generic platform layer first**, Sandhar as first consumer | Entire document restructured; §3–§7 are platform, §8 is Sandhar |
| 2 | Where does refine session attach? | **`agent_actions.id`** — the canonical HITL unit | §5.1 session table FK; §6.1 start endpoint |
| 3 | Sandhar plan page entry point | Look up `action_id` from plan-load response; button hidden if no action exists | §8.6 |
| 4 | Auth / planner identity | `opened_by = "anonymous"` for v1 | §5.1 |
| 5 | Concurrent refinement | Single-user system for v1; `start` is idempotent | §6.1 |
| 6 | Re-generate after refine | Discards all refinements; hard server-side guard | §8.7 |
| 7 | Streaming | **SSE confirmed**, no polling fallback | §6.2 |
| 8 | Context snapshot | **Full snapshot per turn** in v1; diff format deferred to v2 | §5.2 |
| 9 | Agent activation | **Auto-register + auto-activate** on first `start` call | §6.1 |
| 10 | Hub artifact visibility | **Public** for POC phase | §9.5 |
| 11 | Hub prompt pinning ownership | Pinned hash changes require **PR + eval pass** | §9.5 |
| 12 | LangSmith project | **Same project** (`fde-agent-poc`), tags for separation | §9.2, §9.7 |

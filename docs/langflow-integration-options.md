# LangFlow Integration Options

LangFlow is a visual flow builder. It does not auto-discover agents from YAML files or the
custom FastAPI service. This document describes every practical way to bridge the two systems.

---

## The Architectural Gap

```
┌─────────────────────────────────────┐   ┌─────────────────────────────────────┐
│       Custom Agent Platform         │   │            LangFlow UI               │
│                                     │   │                                     │
│  agents/configs/*.yaml              │   │  Flows built visually in browser    │
│  FastAPI  →  LangGraph  →  Celery   │   │  Stored in LangFlow's own Postgres  │
│  Audit trail in agri_agent DB       │   │  Runs managed by LangFlow runtime   │
└─────────────────────────────────────┘   └─────────────────────────────────────┘
         Two separate systems — must be deliberately bridged
```

---

## Option 1 — Build Agents Natively in LangFlow

**What it is:** Use LangFlow's drag-and-drop canvas to assemble an agent from its
built-in components (LLM, tools, memory, prompts). The agent lives entirely inside
LangFlow and is independent of the YAML configs.

**Steps:**
1. Go to `http://localhost:7860` → login → **New Flow → Blank Flow**
2. From the sidebar, drag:
   - **Chat Input** — user message entry point
   - **Agent** — LangFlow's built-in ReAct executor
   - **Anthropic** — LLM component (set API key + model `claude-sonnet-4-6`)
   - **Tool** components — Calculator, Tavily Search, custom Python tools
   - **Chat Output** — final response
3. Wire: Chat Input → Agent → Chat Output; Anthropic model → Agent; Tools → Agent
4. Open **Playground** to test interactively
5. Use **API** tab in LangFlow to get a cURL/Python snippet for that flow

**Pros:**
- Zero code — product managers and non-engineers can build and iterate
- Built-in versioning, sharing, and playback inside LangFlow
- Fastest path to a working demo in the UI

**Cons:**
- Config is siloed inside LangFlow's database, not in your Git repo
- Parallel maintenance burden: changes must be made in both YAML and LangFlow
- No automatic link to your audit trail or Celery queue

**Best for:** Rapid prototyping, demos, non-technical stakeholders experimenting with flows.

---

## Option 2 — LangFlow as a Visual Frontend to the FastAPI Service

**What it is:** Build a thin flow in LangFlow that uses its **HTTP Request** component
to call your FastAPI `/run` endpoint. LangFlow handles the UI; all execution, logging,
and queuing happen in your platform.

**Steps:**
1. In LangFlow, create a **Blank Flow**
2. Drag: **Chat Input** → **HTTP Request** → **Parse Data** → **Chat Output**
3. Configure **HTTP Request**:
   - URL: `http://api:8000/api/v1/agents/react-agent/run`
   - Method: `POST`
   - Headers: `{"X-API-Key": "dev-secret-key-change-in-prod", "Content-Type": "application/json"}`
   - Body: `{"message": "{Chat Input.text}"}`
4. Wire **Parse Data** to extract `output` from the JSON response
5. Wire to **Chat Output**

**Pros:**
- Single source of truth: agent logic lives in YAML, LangFlow is just a UI shell
- All runs flow through your audit trail, token counting, and Celery queue
- Engineers change YAML; LangFlow flow only changes when the API interface changes

**Cons:**
- Synchronous only via this pattern (use `/run/async` + polling for long tasks)
- LangFlow canvas is shallow — just an HTTP call, not a real visual of the agent graph
- Requires the `api` Docker service to be reachable from within LangFlow's container
  (use service name `api`, not `localhost`)

**Best for:** Production-style setups where the platform is the engine and LangFlow is
purely a trigger/test surface.

---

## Option 3 — Custom LangFlow Component (Python)

**What it is:** Write a Python class that extends LangFlow's `Component` base class.
It appears as a native drag-and-drop block in LangFlow's sidebar but internally calls
your LangGraph agent or any custom logic.

**Example component file** (`langflow_components/agri_agent_component.py`):

```python
from langflow.custom import Component
from langflow.io import MessageTextInput, Output
from langflow.schema import Data
import httpx


class AgriAgentComponent(Component):
    display_name = "AgriScience Agent"
    description = "Runs a YAML-configured AgriScience agent via the platform API."
    icon = "🌾"

    inputs = [
        MessageTextInput(name="message", display_name="User Message"),
        MessageTextInput(name="agent_name", display_name="Agent Name", value="react-agent"),
    ]

    outputs = [
        Output(display_name="Response", name="response", method="run"),
    ]

    def run(self) -> Data:
        resp = httpx.post(
            f"http://api:8000/api/v1/agents/{self.agent_name}/run",
            json={"message": self.message},
            headers={"X-API-Key": "dev-secret-key-change-in-prod"},
            timeout=120,
        )
        resp.raise_for_status()
        return Data(data=resp.json())
```

**To load the component into LangFlow**, mount the file and set the env var in
`docker-compose.yml`:

```yaml
langflow:
  environment:
    LANGFLOW_COMPONENTS_PATH: /app/custom_components
  volumes:
    - ./langflow_components:/app/custom_components
```

**Pros:**
- Appears as a native first-class block in LangFlow's sidebar
- Encapsulates the API call — users don't configure URLs or headers manually
- Can be extended to show agent name dropdown populated from the API

**Cons:**
- Requires deploying the component file alongside LangFlow
- Still a thin wrapper — LangFlow doesn't visualise the internal agent graph

**Best for:** Teams who want LangFlow to feel seamless but keep execution in the platform.

---

## Option 4 — Programmatic Flow Import via LangFlow API (GitOps Bridge)

**What it is:** A CI/CD step that converts your YAML agent config into a LangFlow flow
JSON and `POST`s it to LangFlow's REST API. Engineers push YAML to Git; the pipeline
automatically creates or updates the matching flow in LangFlow.

> **This option is fully implemented.** See `scripts/sync_langflow_flows.py` and
> `docs/launch-new-agent.md` for the complete usage guide. The summary below describes
> how it works.

**LangFlow API endpoints used:**
```
POST   /api/v1/flows/          Create a new flow
PATCH  /api/v1/flows/{flow_id} Update an existing flow
GET    /api/v1/flows/          List all flows
DELETE /api/v1/flows/{flow_id} Delete a flow
```

**How `scripts/sync_langflow_flows.py` works:**

Each YAML agent config is converted to a LangFlow flow with three nodes wired together:
```
ChatInput  ──►  AgriAgent Custom Component  ──►  ChatOutput
```
The custom component is embedded Python code that calls the platform API
(`POST /api/v1/agents/{name}/run`), so all execution stays in the platform's audit trail.

The script is idempotent: it tracks platform-managed flows via an `agri-platform` tag,
creates on first run, and updates in place on subsequent runs while preserving any edges
you wired manually.

```bash
uv run python scripts/sync_langflow_flows.py            # sync all configs
uv run python scripts/sync_langflow_flows.py --agent react-agent   # sync one
uv run python scripts/sync_langflow_flows.py --dry-run  # print JSON, no changes
uv run python scripts/sync_langflow_flows.py --delete-all  # remove all platform flows
```

Or via Makefile:
```bash
make sync-flows
make sync-flows AGENT=react-agent
make ci-deploy          # full pipeline: migrate → seed → sync → smoke test
```

**Pros:**
- True GitOps: YAML is the single source of truth for both platforms
- New agent in Git → `make ci-deploy` → appears in LangFlow automatically
- No manual work in the UI to keep flows in sync
- All runs go through the platform audit trail — LangFlow is just a visual trigger

**Cons:**
- LangFlow Custom Components have lazy port discovery — edges can be rejected on
  first import and must be wired manually once (see `docs/launch-new-agent.md` Step 6)
- LangFlow's internal flow JSON schema can change across versions

**Best for:** Mature teams with GitOps discipline who want LangFlow to reflect Git state
without any manual clicks.

---

## Option 5 — Use LangFlow Only for Observability / Replay

**What it is:** Don't build agents in LangFlow at all. Instead, use LangFlow's
**API endpoints** (or embed its components in a custom UI) purely as a run inspector —
pass it stored run data from your `agent_runs` table to visualise message traces.

This is less about building flows and more about using LangFlow as a lightweight
tracing UI alongside LangSmith.

**Pros:** Keeps all execution in your platform; LangFlow used only for visualisation.  
**Cons:** Non-standard use of LangFlow; LangSmith already does this better.

---

## Recommendation for AgriScience

| Phase | Recommended option | Reason |
|---|---|---|
| **POC / now** | **Option 4** (already implemented) | `make ci-deploy` syncs all YAML configs to LangFlow automatically |
| **Non-engineer testing** | Option 1 (native LangFlow) | Fastest path for product/design to iterate without touching YAML |
| **Team onboarding** | Option 3 (custom component) | Engineers stay in LangFlow, execution stays in the platform |
| **Production** | Option 2 + Option 4 | API is the engine; GitOps keeps LangFlow in sync |

The current state: YAML in Git is the authoritative config, Option 4 (`sync_langflow_flows.py`)
syncs it to LangFlow on every CI deploy, and the Custom Component (Option 4's output) ensures
all runs flow through the platform's audit trail and queue.

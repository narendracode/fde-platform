# Launching a New Agent — Step-by-Step Guide

This guide covers the complete lifecycle of creating a new agent on the AgriScience
platform using the GitOps Option 4 approach: YAML in Git → CI/CD syncs to LangFlow.

---

## Overview

```
1. Write YAML config      →  agents/configs/my-agent.yaml
2. Add custom tools       →  src/agri_agent/agent/tools/   (optional)
3. Test locally           →  uv run agri-agent run my-agent "question"
4. Commit + PR review     →  git commit / pull request
5. Simulate CI/CD deploy  →  make ci-deploy AGENT=my-agent
6. Verify in LangFlow UI  →  http://localhost:7860
7. Invoke via APIs        →  LangFlow API or Platform API
```

---

## Platform Scripts

Before walking through the steps, here is what each piece of tooling does.

### `scripts/sync_langflow_flows.py`

Reads every YAML agent config and pushes it to LangFlow as a visual flow via the
LangFlow REST API. Each flow is built from three nodes wired together:

```
ChatInput  ──►  AgriAgent Custom Component  ──►  ChatOutput
```

The **Custom Component** is embedded Python code that calls
`POST /api/v1/agents/{name}/run` on the platform API. This means:

- All execution stays inside the platform's audit trail, token accounting, and Celery
  queue — LangFlow is purely a visual trigger and display layer.
- The flow is registered with `endpoint_name = agent-name`, so LangFlow exposes it at
  `POST /api/v1/run/{agent-name}` in addition to the Playground.

The script is idempotent: it creates the flow on first run, updates it in place on
subsequent runs. It tracks which flows it owns via the `agri-platform` tag so it never
touches flows you created manually.

```bash
uv run python scripts/sync_langflow_flows.py            # sync all configs
uv run python scripts/sync_langflow_flows.py --agent react-agent   # sync one
uv run python scripts/sync_langflow_flows.py --dry-run  # print JSON, no changes
uv run python scripts/sync_langflow_flows.py --delete-all  # remove all platform flows
```

> **Edge connections note:** LangFlow validates Custom Component code at import time.
> If a version mismatch causes the edges to not connect on first import, wire the three
> nodes manually once in the UI (Chat Input → AgriAgent → Chat Output → Save).
> All future `sync-flows` calls update that flow in place without touching the edges —
> this is a one-time fix.

---

### `scripts/ci_deploy.sh`

Simulates a full CI/CD pipeline in five ordered steps:

| Step | What it does |
|---|---|
| **1 — Health checks** | Verifies platform API, LangFlow, and PostgreSQL are all reachable before proceeding |
| **2 — DB migrations** | Runs `alembic upgrade head` inside the api container — idempotent, only applies new migrations |
| **3 — Register agents** | Upserts agent records into the `agents` table via `seed_data.py` |
| **4 — Sync to LangFlow** | Calls `sync_langflow_flows.py` — creates or updates flows for every YAML config |
| **5 — Smoke test** | Calls the platform API with a simple query, verifies a non-empty response |

This script is what would run in GitHub Actions (or any CI system) after a YAML file
is merged to main.

---

### Makefile targets

```bash
# LangFlow sync (Option 4 GitOps)
make sync-flows                      # sync all YAML configs → LangFlow flows
make sync-flows AGENT=react-agent    # sync one agent
make sync-flows DRY_RUN=1            # print flow JSON without sending

# CI/CD simulation
make ci-deploy                       # full pipeline: migrate → seed → sync → smoke test
make ci-deploy AGENT=my-agent        # pipeline for a single agent
make ci-deploy AGENT=my-agent DRY_RUN=1   # dry run — no mutations, no API calls
make ci-deploy SKIP_SMOKE=1          # skip smoke test (useful when no LLM key is set)
```

---

## Step 1 — Write the YAML Config

Create `agents/configs/my-agent.yaml`. Use an existing config as a reference.

**Mandatory fields:**
```yaml
agent:
  name: my-agent           # lowercase, hyphens only — becomes the API endpoint slug
  description: >
    One sentence describing what this agent does.

  model:
    provider: anthropic    # anthropic | openai
    name: claude-sonnet-4-6
    temperature: 0.2       # 0.0 = deterministic, 1.0 = creative
    max_tokens: 4096
    max_cost_usd: 0.50     # hard cap — run rejected if estimated cost exceeds this

  system_prompt: |
    You are a specialist in <domain>.
    Always <key behaviour>.
    When uncertain, use the web_search tool.

  tools:
    - name: calculator
      enabled: true
    - name: web_search
      enabled: true
      config:
        max_results: 5
    # Add any tools from the registry below:
    - name: get_crop_recommendation
      enabled: true
    - name: get_pest_alert
      enabled: true
    - name: calculate_fertilizer
      enabled: true
    - name: get_weather_data
      enabled: true

  guardrails:
    max_iterations: 15     # hard stop for runaway ReAct loops
    timeout_seconds: 120
    blocked_patterns:      # regex — request rejected if matched
      - "ignore previous instructions"
      - "jailbreak"

  observability:
    langsmith_tracing: true
    log_inputs: true
    log_outputs: true
    log_tool_calls: true
```

**Available tools** (check `src/agri_agent/agent/tools/__init__.py` for the full list):

| Name | What it does |
|---|---|
| `calculator` | Safe math expressions (no eval) |
| `web_search` | Tavily web search (mock if no API key) |
| `get_crop_recommendation` | Crops by season + soil type |
| `get_pest_alert` | Pest risks + IPM for a crop |
| `calculate_fertilizer` | NPK requirements by crop, area, pH |
| `get_weather_data` | Current weather by Indian state |

---

## Step 2 — Add Custom Tools (optional)

If your agent needs a tool that doesn't exist yet:

1. Create `src/agri_agent/agent/tools/my_tool.py`:

```python
from langchain_core.tools import tool

@tool
def my_custom_tool(param: str) -> str:
    """One-line docstring — this is what the LLM reads to decide when to use the tool.

    Args:
        param: What this parameter does.
    """
    # your logic here
    return f"Result for {param}"
```

2. Register it in `src/agri_agent/agent/tools/__init__.py`:

```python
from agri_agent.agent.tools.my_tool import my_custom_tool

_TOOL_REGISTRY: dict[str, BaseTool] = {
    # existing tools ...
    "my_custom_tool": my_custom_tool,   # add this line
}
```

3. Enable it in your YAML:

```yaml
tools:
  - name: my_custom_tool
    enabled: true
```

---

## Step 3 — Test Locally (no Docker needed)

```bash
# List all available agent configs
uv run agri-agent list

# Run the agent from the CLI
uv run agri-agent run my-agent "Your question here"

# Run with a thread ID (maintains conversation context)
uv run agri-agent run my-agent "Follow-up question" --thread-id session-001
```

Expected output:
```
Running agent: my-agent
────────────────────────────────────────────────────────────
Output:
<agent's final answer>

Tool calls: [{"name": "calculator", "args": {"expression": "..."}}]

Tokens — in: 350  out: 120  time: 4.2s
```

> **Note:** A real LLM API key (`ANTHROPIC_API_KEY` or `OPENAI_API_KEY`) must be set
> in `.env` for the agent to actually call the model.

---

## Step 4 — Commit and PR Review

The YAML file is the contract. The PR review checklist:

- [ ] `name` is a unique, descriptive slug
- [ ] `max_cost_usd` is set appropriately for the use case
- [ ] `max_iterations` is not excessively high
- [ ] `system_prompt` is clear and contains safety instructions
- [ ] `blocked_patterns` cover obvious prompt injection attempts
- [ ] Tools listed are the minimum needed (principle of least privilege)
- [ ] `temperature` is appropriate (low for factual tasks, higher for creative)

---

## Step 5 — Simulate CI/CD Deploy

This runs the full pipeline locally, simulating what GitHub Actions would do after merge.

```bash
# Deploy all agents
make ci-deploy

# Deploy just your new agent
make ci-deploy AGENT=my-agent

# Dry run — see what would happen without making changes
make ci-deploy AGENT=my-agent DRY_RUN=1

# Deploy without smoke test (useful if no LLM key set)
make ci-deploy AGENT=my-agent SKIP_SMOKE=1
```

**What `ci-deploy` does:**
```
Step 1  Health checks     — API, LangFlow, PostgreSQL all reachable
Step 2  DB migrations     — alembic upgrade head
Step 3  Register in DB    — upserts agent record in agents table
Step 4  Sync to LangFlow  — creates/updates flow via LangFlow API
Step 5  Smoke test        — calls the agent, verifies a response
```

**Sample output:**
```
━━━ STEP 4 — Sync flows to LangFlow ━━━
LangFlow sync  →  http://localhost:7860
Found 2 existing platform flow(s) in LangFlow

Syncing 1 config(s)...

  ✓  my-agent  [created]
       Playground : http://localhost:7860/flow/abc123
       API run    : POST http://localhost:7860/api/v1/run/my-agent

━━━ DEPLOYMENT SUMMARY ━━━

  Platform API  →  http://localhost:8000/docs
  LangFlow UI   →  http://localhost:7860
```

---

## Step 6 — Verify in LangFlow UI

1. Open **http://localhost:7860** — login with `admin` / `adminpass123`
2. Your flow appears in **My Flows** with the agent's name
3. Click the flow → click **Playground** (bottom-right chat icon)
4. Type a message and see the agent respond

**If nodes are not connected** (edges missing in the UI):

LangFlow validates Custom Component code at import time. Depending on the LangFlow
version, edge connections can be rejected during the first programmatic import even
though the flow JSON is correct.

Fix it **once** manually:
1. Drag the output port of **Chat Input** → input port of the **AgriAgent** component
2. Drag the output port of **AgriAgent** → input port of **Chat Output**
3. Click **Save**

All future `make sync-flows` calls update this flow in place without touching the edges.
You will never need to wire it again.

---

## Step 7 — Invoke via APIs

### Option A — LangFlow Playground (browser)

Open the flow → click **Playground** → type your message.
No API key required. Good for interactive testing.

---

### Option B — LangFlow REST API

Every flow gets a named endpoint: `POST /api/v1/run/{agent-name}`

```bash
# Basic run
curl -s -X POST "http://localhost:7860/api/v1/run/my-agent" \
  -H "Content-Type: application/json" \
  -d '{
    "input_value": "What crops for Punjab rabi season on loamy soil?",
    "output_type": "chat",
    "input_type": "chat"
  }' | python3 -m json.tool

# With session ID (conversation continuity)
curl -s -X POST "http://localhost:7860/api/v1/run/my-agent" \
  -H "Content-Type: application/json" \
  -d '{
    "input_value": "Follow-up question",
    "output_type": "chat",
    "input_type": "chat",
    "session_id": "user-session-001"
  }'
```

**Response shape:**
```json
{
  "outputs": [
    {
      "inputs": { "input_value": "What crops..." },
      "outputs": [
        {
          "results": {
            "message": {
              "text": "For Punjab rabi season on loamy soil, recommended crops are..."
            }
          },
          "component_display_name": "Chat Output"
        }
      ]
    }
  ],
  "session_id": "user-session-001"
}
```

**Extract just the text:**
```bash
curl -s -X POST "http://localhost:7860/api/v1/run/my-agent" \
  -H "Content-Type: application/json" \
  -d '{"input_value": "your question", "output_type": "chat", "input_type": "chat"}' \
  | python3 -c "
import json, sys
data = json.load(sys.stdin)
print(data['outputs'][0]['outputs'][0]['results']['message']['text'])
"
```

---

### Option C — Platform API (direct, recommended for production)

Bypasses LangFlow entirely. Execution goes through the platform's audit trail and queue.

```bash
# Synchronous run (waits for result)
curl -s -X POST "http://localhost:8000/api/v1/agents/my-agent/run" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: dev-secret-key-change-in-prod" \
  -d '{
    "message": "What crops for Punjab rabi season on loamy soil?",
    "thread_id": "optional-session-id"
  }' | python3 -m json.tool
```

**Response:**
```json
{
  "run_id": "550e8400-e29b-41d4-a716-446655440000",
  "output": "For Punjab rabi season on loamy soil, recommended crops are wheat, mustard, chickpea, and lentil.",
  "thread_id": "optional-session-id",
  "tool_calls": [
    {"name": "get_crop_recommendation", "args": {"season": "rabi", "soil_type": "loamy", "region": "Punjab"}}
  ],
  "input_tokens": 412,
  "output_tokens": 87,
  "elapsed_seconds": 3.2,
  "blocked": false
}
```

```bash
# Asynchronous run (immediate 202, poll for result)
RUN=$(curl -s -X POST "http://localhost:8000/api/v1/agents/my-agent/run/async" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: dev-secret-key-change-in-prod" \
  -d '{"message": "Long running question..."}')

RUN_ID=$(echo $RUN | python3 -c "import json,sys; print(json.load(sys.stdin)['run_id'])")
echo "Run queued: $RUN_ID"

# Poll until completed
until [ "$(curl -s "http://localhost:8000/api/v1/runs/$RUN_ID" \
  -H "X-API-Key: dev-secret-key-change-in-prod" | python3 -c "import json,sys; print(json.load(sys.stdin)['status'])")" = "completed" ]
do
  echo "Waiting..."; sleep 2
done

# Fetch final result
curl -s "http://localhost:8000/api/v1/runs/$RUN_ID" \
  -H "X-API-Key: dev-secret-key-change-in-prod" | python3 -m json.tool
```

```bash
# View run history (audit trail)
curl -s "http://localhost:8000/api/v1/runs?limit=10" \
  -H "X-API-Key: dev-secret-key-change-in-prod" | python3 -m json.tool

# Filter by status
curl -s "http://localhost:8000/api/v1/runs?status=failed" \
  -H "X-API-Key: dev-secret-key-change-in-prod"
```

---

### Option D — Python SDK

Use `httpx` directly in your Python code:

```python
import httpx

PLATFORM_API = "http://localhost:8000"
API_KEY = "dev-secret-key-change-in-prod"

def run_agent(agent_name: str, message: str, thread_id: str = None) -> dict:
    with httpx.Client() as client:
        resp = client.post(
            f"{PLATFORM_API}/api/v1/agents/{agent_name}/run",
            json={"message": message, "thread_id": thread_id},
            headers={"X-API-Key": API_KEY},
            timeout=120,
        )
        resp.raise_for_status()
        return resp.json()

# Usage
result = run_agent("my-agent", "What crops for Punjab rabi season?")
print(result["output"])
print(f"Tokens: {result['input_tokens']} in / {result['output_tokens']} out")
```

---

## Sync-flows Reference

```bash
# Sync all configs to LangFlow
make sync-flows

# Sync a single agent
make sync-flows AGENT=my-agent

# Preview the flow JSON without sending (debugging)
make sync-flows AGENT=my-agent DRY_RUN=1

# Delete all platform-managed flows from LangFlow (clean slate)
uv run python scripts/sync_langflow_flows.py --delete-all
```

---

## Troubleshooting

**"Agent config not found"**
→ Check that `agents/configs/my-agent.yaml` exists and `agent.name` matches what you passed.

**"Invalid or missing API key"**
→ Pass `-H "X-API-Key: dev-secret-key-change-in-prod"` (or whatever is in your `.env`).

**LangFlow flow has no edges**
→ Wire nodes manually once in the UI. See Step 6 above. Future syncs keep the flow updated.

**Agent responds but doesn't use tools**
→ The system prompt must mention tools are available. LangGraph only calls tools if the
LLM decides to — a clear system prompt helps.

**"blocked": true in response**
→ The input matched a `blocked_patterns` regex in the YAML config. Adjust the pattern
or the message.

**Smoke test fails with empty output**
→ LLM API key is not set. The agent config loads fine, but the model call fails.
Set `ANTHROPIC_API_KEY` in `.env` and run `docker compose up -d`.

```
curl -s -X POST 'http://localhost:8000/api/v1/agents/agri-assistant/run' \
  -H 'Content-Type: application/json' \
  -H 'X-API-Key: dev-secret-key-change-in-prod' \
  -d '{"message": "What crops for Punjab rabi season?"}'
```

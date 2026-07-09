# Launching a New Agent — Step-by-Step Guide

This guide covers the complete lifecycle of creating a new agent on the Fundly
platform: from idea to deployed, invocable endpoint.

---

## Workflow overview

```
Developer                    Platform                      Ops / Dashboard
─────────────────────────────────────────────────────────────────────────────
make launch-agent
  └─ conversation with Claude
  └─ YAML manifest generated
  └─ review + accept
  └─ git push + PR opened
                             ← PR merged
                             CI/CD: make ci-deploy
                               → alembic migrate
                               → seed_data.py (is_active=false)
                             Agent is INACTIVE
                                                      Dashboard: activate
                                                      PATCH /agents/{name}/activate
                             Agent is ACTIVE
POST /agents/{name}/run ──►  agent executes
                             result returned + audit trail updated
```

Key principle: **agents start inactive**. CI/CD registers them; a platform admin
activates them from the dashboard. This prevents unreviewed agents from being
invoked in production.

---

## Recommended: Agent Launcher (automated)

```bash
make launch-agent
```

Or with options:
```bash
make launch-agent MODEL=claude-opus-4-8   # more capable model
make launch-agent NO_GIT=1               # skip git/PR step
uv run python scripts/launch_agent.py --help
```

**What the Launcher does:**

| Phase | What happens |
|---|---|
| **1 — Discovery** | Shows available tools; Claude asks about purpose, target users, and tool selection |
| **2 — Generate** | Claude generates the YAML manifest using the spec + platform defaults |
| **3 — Review** | Displays the YAML; you can accept, request modifications, or quit |
| **4 — Write** | Writes `agents/configs/{name}.yaml` to disk |
| **5 — Git + PR** | Creates branch `agent/add-{name}`, commits, pushes, runs `gh pr create` |

> **Important:** The Launcher only creates the YAML manifest. It does not write
> Python code. If your agent needs a new tool (an API wrapper), a developer must
> implement it separately — see [Adding a new tool](#adding-a-new-tool) below.

**Prerequisites:** `ANTHROPIC_API_KEY` set in `.env`. For the git step, `origin`
must be configured. For auto-PR, `gh` CLI must be installed and authenticated.

---

## Activating an agent (dashboard / API)

After CI/CD deploys the agent, it is inactive by default.

```bash
# Activate
curl -X PATCH http://localhost:8000/api/v1/agents/my-agent/activate \
  -H "X-API-Key: dev-secret-key-change-in-prod"

# Deactivate
curl -X PATCH http://localhost:8000/api/v1/agents/my-agent/deactivate \
  -H "X-API-Key: dev-secret-key-change-in-prod"

# Check status
curl http://localhost:8000/api/v1/agents \
  -H "X-API-Key: dev-secret-key-change-in-prod"
# Returns all agents with is_active field
```

Attempting to invoke an inactive agent returns:
```json
{
  "detail": "Agent 'my-agent' is not active. Activate it via PATCH /api/v1/agents/my-agent/activate"
}
```

---

## Manual approach (reference)

The sections below describe how to create an agent YAML by hand — useful for
modifying existing agents or understanding the manifest format.

---

## Manifest format

Create `agents/configs/my-agent.yaml`:

```yaml
agent:
  name: my-agent           # lowercase, hyphens only — becomes the API endpoint slug
  description: >
    One sentence describing what this agent does.
  version: "1.0.0"

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

**Available tools** (check `src/fde_agent/agent/tools/__init__.py` for the full list):

| Name | What it does |
|---|---|
| `calculator` | Safe math expressions (no eval) |
| `web_search` | Tavily web search (mock if no API key) |
| `list_retailers` | Mock: pharma retailers by region |
| `filter_prospects` | Filters retailers by revenue threshold |
| `send_email` | Mock: sends marketing email (prints to console) |

---

## Adding a new tool

Tools are API wrappers written by developers. The Launcher does not generate tool code.

**Step 1** — Implement in `src/fde_agent/agent/tools/my_tool.py`:

```python
from langchain_core.tools import tool

@tool
def lookup_order_status(order_id: str) -> str:
    """Look up the current status of a customer order by order ID.

    Args:
        order_id: The unique order identifier (e.g. ORD-12345).
    """
    # Call your internal order management API here
    resp = httpx.get(f"https://api.internal/orders/{order_id}")
    resp.raise_for_status()
    data = resp.json()
    return f"Order {order_id}: {data['status']} — {data['updated_at']}"
```

**Step 2** — Register in `src/fde_agent/agent/tools/__init__.py`:

```python
from fde_agent.agent.tools.my_tool import lookup_order_status

_TOOL_REGISTRY: dict[str, BaseTool] = {
    # existing tools ...
    "lookup_order_status": lookup_order_status,
}
```

**Step 3** — Reference in YAML:

```yaml
tools:
  - name: lookup_order_status
    enabled: true
```

Once registered, the tool is automatically available to the Launcher's recommendations
for all future agents.

---

## Test locally (no Docker needed)

```bash
# List all available agent configs
uv run fde-agent list

# Run the agent from the CLI
uv run fde-agent run my-agent "Your question here"
```

> Requires `ANTHROPIC_API_KEY` (or `OPENAI_API_KEY`) in `.env`.

---

## CI/CD deploy

```bash
make ci-deploy AGENT=my-agent        # migrate → seed → smoke test
make ci-deploy AGENT=my-agent DRY_RUN=1   # dry run
make ci-deploy SKIP_SMOKE=1          # skip smoke test (useful without LLM key)
```

After CI completes the agent is registered as **inactive**. Activate it from the
dashboard or via the API (see above).

---

## Platform Scripts reference

### `scripts/ci_deploy.sh`

Full pipeline: health checks → alembic migrate → seed agents → smoke test.

### Makefile targets

```bash
make launch-agent                    # create new agent (conversational)
make ci-deploy AGENT=my-agent        # full deploy pipeline
make ci-deploy DRY_RUN=1             # dry run
```

---

## Invoke via API

### Synchronous run

```bash
curl -s -X POST "http://localhost:8000/api/v1/agents/my-agent/run" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: dev-secret-key-change-in-prod" \
  -d '{"message": "your question", "thread_id": "session-001"}' \
  | python3 -m json.tool
```

Response:
```json
{
  "run_id": "550e8400-...",
  "output": "...",
  "thread_id": "session-001",
  "tool_calls": [],
  "input_tokens": 412,
  "output_tokens": 87,
  "cost_usd": 0.0024,
  "elapsed_seconds": 3.2,
  "blocked": false,
  "langsmith_trace_url": "https://smith.langchain.com/...",
  "otel_trace_url": "http://localhost:16686/trace/..."
}
```

### Asynchronous run (Celery queue)

```bash
# Submit
RUN=$(curl -s -X POST "http://localhost:8000/api/v1/agents/my-agent/run/async" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: dev-secret-key-change-in-prod" \
  -d '{"message": "long running task..."}')

RUN_ID=$(echo $RUN | python3 -c "import json,sys; print(json.load(sys.stdin)['run_id'])")

# Poll
until [ "$(curl -s "http://localhost:8000/api/v1/runs/$RUN_ID" \
  -H "X-API-Key: dev-secret-key-change-in-prod" \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['status'])")" = "completed" ]
do sleep 2; done

# Fetch result
curl -s "http://localhost:8000/api/v1/runs/$RUN_ID" \
  -H "X-API-Key: dev-secret-key-change-in-prod" | python3 -m json.tool
```

---

## Troubleshooting

**Agent not found (404)**
→ Check `agents/configs/my-agent.yaml` exists and `agent.name` matches the URL slug.

**Agent is not active (403)**
→ The agent was deployed but not activated. Run `PATCH /api/v1/agents/my-agent/activate`.

**"Invalid or missing API key"**
→ Pass `-H "X-API-Key: dev-secret-key-change-in-prod"` (value from `.env`).

**Tool not available**
→ The tool name in YAML is not in the registry. Check `GET /api/v1/agents/tools` for the
current list and add missing tools to `src/fde_agent/agent/tools/__init__.py`.

**Agent responds but doesn't use tools**
→ The system prompt must mention tools are available. LangGraph only calls tools when the
LLM decides to — a clear system prompt helps.

**`blocked: true` in response**
→ The input matched a `blocked_patterns` regex in the YAML. Adjust the pattern or message.

**`langsmith_trace_url` is null**
→ Set `LANGSMITH_TRACING=true`, `LANGSMITH_API_KEY`, and `LANGSMITH_PROJECT` in `.env`.

**`otel_trace_url` is null**
→ Set `OTEL_ENABLED=true` in `.env`. Check `docker compose ps jaeger` and open
http://localhost:16686.

**`cost_usd` is 0**
→ Requires `LANGSMITH_TRACING=true` and a valid `LANGSMITH_API_KEY`. Cost is read from
LangSmith after each run.

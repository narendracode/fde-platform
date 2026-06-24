# AgriScience Agent Platform — System Design

## Overview

The platform is a centralised infrastructure layer that standardises how AI agents are
**defined, deployed, executed, observed, and audited** across AgriScience. Any team
can ship a new agent by committing a YAML file — without inventing their own tooling,
choosing their own models, or bypassing compliance guardrails.

---

## Architecture Diagram

```
                           ┌──────────────────────────────────────────────────┐
                           │                  Client Layer                     │
                           │                                                  │
                           │   LangFlow UI        REST Clients     CLI        │
                           │   :7860              (curl / SDK)     agri-agent │
                           └──────┬───────────────────┬────────────────┬──────┘
                                  │                   │                │
                          ────────┼───────────────────┼────────────────┼────────
                                  │                   │                │
                           ┌──────▼───────────────────▼────────────────▼──────┐
                           │                  API Layer                        │
                           │                                                  │
                           │               FastAPI  :8000                     │
                           │                                                  │
                           │   /api/v1/agents/{name}/run        (sync)        │
                           │   /api/v1/agents/{name}/run/async  (queued)      │
                           │   /api/v1/agents                   (CRUD)        │
                           │   /api/v1/runs                     (audit)       │
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
                    │   ├── agents         (registry)      ├── Celery broker   │
                    │   └── agent_runs     (audit trail)   └── Celery results  │
                    │   └── LangFlow DB    (flows/UI)                          │
                    └───────────────────────────────────────────────────────────┘
                                  │
                    ┌─────────────▼──────────────────────────────────────────────┐
                    │                  Observability Layer                        │
                    │                                                             │
                    │   LangSmith  — per-run traces, token counts, latency       │
                    │   agent_runs table — input/output/cost stored in Postgres  │
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
- Which tools are enabled and their per-tool settings
- The system prompt
- Guardrails: max iterations, timeout, blocked input patterns
- Observability switches: whether to trace to LangSmith, what to log

**Why it matters for the platform vision:**
A new engineer creates a new agent by writing one YAML file. There is no code to write,
no infrastructure to provision. The file is the contract — it is reviewed in a PR,
versioned in Git, and deployed via CI just like application code.

**Example:**
```yaml
agent:
  name: agri-assistant
  model:
    provider: anthropic
    name: claude-sonnet-4-6
    max_cost_usd: 1.00
  tools:
    - name: get_crop_recommendation
      enabled: true
  guardrails:
    max_iterations: 20
    blocked_patterns: ["ignore previous instructions"]
```

---

### 2. Config Loader (`src/agri_agent/config/loader.py`)

**What it is:** A Python module that reads YAML files and validates them into typed
Pydantic models (`AgentConfig`, `ModelConfig`, `ToolConfig`, etc.).

**How it works:**
1. `load_agent_config("react-agent")` searches `agents/configs/` for a matching file
2. Parses YAML with PyYAML
3. Validates and coerces all fields via Pydantic v2 — invalid configs fail fast with
   clear error messages before any LLM call is made
4. Returns a fully typed `AgentConfig` object consumed by the agent engine

**Key guarantee:** If a YAML file passes the loader, it is guaranteed to have all
required fields in the right types. Misconfiguration is caught at load time, not at
runtime.

---

### 3. Pydantic Settings (`src/agri_agent/config/settings.py`)

**What it is:** A `pydantic-settings` class that reads all runtime secrets and
environment variables from `.env` or the container environment.

**What it manages:**
- Database URL (async + sync variants for SQLAlchemy and Celery)
- Redis and Celery broker URLs
- LLM API keys (Anthropic, OpenAI)
- LangSmith API key and project name
- Platform API key for request authentication
- Log level

**Why it matters:** Every configurable value is typed and documented in one place.
Adding a new environment variable means adding one line here — not hunting through code.

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

**ReAct loop detail:**
LangGraph's `create_react_agent` implements the Reasoning + Acting pattern. At each
step the LLM either calls a tool (with arguments) or produces a final text response.
Tool results are appended to the message history and the LLM reasons over the full
accumulated context until it decides to stop.

**Guardrails applied here:**
- `blocked_patterns` — regex match on input before any LLM call
- `max_iterations` — passed as `recursion_limit` to LangGraph, hard-stops runaway loops

---

### 5. Tool Registry (`src/agri_agent/agent/tools/`)

**What it is:** A dictionary mapping tool names (as used in YAML) to LangChain
`@tool`-decorated Python functions.

**Tools included in the POC:**

| Tool name | File | What it does |
|---|---|---|
| `calculator` | `calculator.py` | Safe AST-based math — no `eval()`, no code injection risk |
| `web_search` | `search.py` | Tavily search if API key set; graceful mock fallback otherwise |
| `get_crop_recommendation` | `agri.py` | Returns crop list by season + soil type from an in-memory DB |
| `get_pest_alert` | `agri.py` | Returns pest risks + IPM strategies for a crop |
| `calculate_fertilizer` | `agri.py` | NPK requirements adjusted for area and soil pH |
| `get_weather_data` | `agri.py` | Current weather mock by Indian state |

**How new tools are added:**
1. Write a `@tool`-decorated function in `src/agri_agent/agent/tools/`
2. Register it in `_TOOL_REGISTRY` in `tools/__init__.py`
3. Reference it by name in any agent's YAML config

No changes to the agent engine, API, or database are needed.

---

### 6. FastAPI Service (`src/agri_agent/api/`)

**What it is:** The HTTP interface to the platform. All external systems — LangFlow,
CI pipelines, dashboards, mobile apps — interact with agents through this API.

**Endpoints:**

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/health` | None | Liveness probe |
| `GET` | `/health/db` | None | DB connectivity check |
| `GET` | `/api/v1/agents` | API key | List registered agents from DB |
| `GET` | `/api/v1/agents/configs` | API key | List YAML configs from disk |
| `GET` | `/api/v1/agents/tools` | API key | List all available tools |
| `POST` | `/api/v1/agents/register` | API key | Load YAML config into DB (upsert) |
| `GET` | `/api/v1/agents/{name}` | API key | Get agent config from DB |
| `POST` | `/api/v1/agents/{name}/run` | API key | **Sync run** — waits for result |
| `POST` | `/api/v1/agents/{name}/run/async` | API key | **Async run** — returns task ID immediately |
| `GET` | `/api/v1/runs` | API key | List all runs (filterable by status) |
| `GET` | `/api/v1/runs/{run_id}` | API key | Get full run detail — use for polling async |

**Authentication:** `X-API-Key` header validated against `settings.api_key`.
Designed to be swapped for JWT/OAuth2 by updating `dependencies.py` only.

**Sync vs async run:**
- `POST /run` — agent executes in the request thread, response returned when done.
  Use for short tasks or interactive testing.
- `POST /run/async` — creates a `AgentRun` record, dispatches a Celery task, returns
  `{run_id, task_id}` immediately. Client polls `GET /runs/{run_id}` for status.
  Use for long-running agents or when you need throttling/concurrency control.

---

### 7. Database Layer (`src/agri_agent/db/`)

**What it is:** Two SQLAlchemy ORM models backed by PostgreSQL, providing persistent
storage for agent registry and run history.

**`agents` table — Agent Registry:**
```
id           UUID PK
name         unique slug matching the YAML filename
description  human-readable description
version      semver string from YAML
config       JSONB — full AgentConfig serialised for audit / replay
is_active    soft-delete flag
created_at   timestamp
updated_at   timestamp (auto-updated)
```

**`agent_runs` table — Audit Trail:**
```
id             UUID PK
agent_id       FK → agents
thread_id      LangGraph thread — links runs in the same conversation
task_id        Celery task ID — use to look up async run status
status         pending | running | completed | failed | blocked | cancelled
input          JSONB — user message + extra context
output         JSONB — agent text response + tool call log
error          text — exception message if failed
input_tokens   int  — LLM input tokens consumed
output_tokens  int  — LLM output tokens consumed
cost_usd       float — estimated cost (populated from token counts)
started_at     timestamp — when agent execution began
completed_at   timestamp — when execution finished
created_at     timestamp — when the run record was created
```

**Migrations:** Managed by Alembic (`alembic upgrade head`). Every schema change
is a versioned migration file in `alembic/versions/` — safe to run in CI/CD.

---

### 8. Task Queue — Celery + Redis (`src/agri_agent/queue/`)

**What it is:** An async job queue that decouples the API from agent execution.
The API creates a run record and enqueues a task; one of the Celery workers picks
it up and executes the agent.

**Configuration choices and their reasons:**

| Setting | Value | Reason |
|---|---|---|
| `rate_limit` | `10/m` per task | Prevents LLM API rate limit errors under burst traffic |
| `worker_prefetch_multiplier` | `1` | Fair dispatch — each worker takes one job at a time |
| `acks_late` | `True` | Task only acknowledged after completion — safe restart on crash |
| `task_reject_on_worker_lost` | `True` | Re-queues task if worker process is killed |
| `max_retries` | `2` | Transient failures (network, LLM timeout) get two automatic retries |
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
Scale horizontally by adding more `worker` containers in Docker Compose or Kubernetes.

---

### 9. LangFlow UI (`localhost:7860`)

**What it is:** An open-source visual flow builder. In this platform it serves as:
- A prototyping canvas for trying new agent designs quickly
- A non-engineer-friendly interface for testing existing agents (via HTTP component)
- A visual documentation layer showing how agent components connect

**What it is not:** The execution engine. LangFlow uses its own runtime for flows
built natively. For production runs the custom FastAPI service is the engine.

**Storage:** LangFlow has its own PostgreSQL database (`langflow` DB) separate from
the platform's `agri_agent` DB. Flows built in LangFlow are stored there.

**See `docs/langflow-integration-options.md`** for detailed options on bridging
LangFlow with the custom agent platform.

---

### 10. Observability

**LangSmith (optional):**
Set `LANGCHAIN_TRACING_V2=true` in `.env`. Every agent invocation automatically
sends a trace to LangSmith including: full message history, tool call inputs/outputs,
token counts, latency per step. No code changes required — LangChain instruments
automatically when tracing is enabled.

**Platform audit trail (always on):**
Every run — sync or async — creates an `agent_runs` row regardless of LangSmith.
This is the compliance-grade record: stores input, output, token counts, cost,
timing, and status in your own PostgreSQL. You own the data.

**Structured logging:**
The FastAPI service and Celery worker write structured logs at `INFO` level by default.
Set `LOG_LEVEL=debug` to see SQL queries and LangGraph step details.

---

## Data Flow: Async Agent Run (End to End)

```
Client
  │  POST /api/v1/agents/react-agent/run/async
  │  X-API-Key: <key>
  │  {"message": "What should I plant in Punjab this rabi season?"}
  │
  ▼
FastAPI (api container :8000)
  ├── verify_api_key()                    ← auth check
  ├── load_agent_config("react-agent")   ← validate config exists
  ├── INSERT agent_runs (status=pending)  ← create audit record
  ├── run_agent_task.delay(run_id, ...)   ← enqueue to Redis
  └── return {run_id, task_id, status="queued"}  ← immediate 202 response

Redis (broker)
  └── task sits in "agent_runs" queue

Celery Worker (worker container)
  ├── picks up task
  ├── UPDATE agent_runs SET status=running
  ├── load_agent_config("react-agent")
  ├── build_agent(config)
  │     ├── ChatAnthropic(claude-sonnet-4-6)
  │     └── tools: [calculator, web_search, get_crop_recommendation, ...]
  ├── agent.invoke({"messages": [HumanMessage("What should I plant...")]})
  │     LangGraph ReAct loop:
  │       → LLM decides to call get_crop_recommendation(season=rabi, soil=loamy)
  │       → tool returns ["wheat", "mustard", "chickpea", "lentil"]
  │       → LLM calls get_weather_data(location=Punjab)
  │       → tool returns temp/humidity/rainfall
  │       → LLM formulates final answer
  └── UPDATE agent_runs SET status=completed, output=..., tokens=..., completed_at=now

Client polls:
  GET /api/v1/runs/{run_id}
  └── returns {status: "completed", output: "For Punjab rabi season on loamy soil..."}
```

---

## Deployment Topology

```
docker-compose.yml defines 5 services:

  postgres   ─ Single instance, two databases:
               agri_agent  (platform data)
               langflow    (LangFlow flows)

  redis      ─ Single instance, three logical DBs:
               db/0  general cache
               db/1  Celery broker (task queue)
               db/2  Celery result backend

  langflow   ─ LangFlow server
               reads/writes: postgres/langflow
               exposes: :7860

  api        ─ FastAPI + Uvicorn
               reads: agents/configs/*.yaml (mounted read-only)
               reads/writes: postgres/agri_agent
               publishes: redis/db/1 (Celery tasks)
               exposes: :8000

  worker     ─ Celery worker (4 processes)
               reads: agents/configs/*.yaml (mounted read-only)
               reads/writes: postgres/agri_agent
               consumes: redis/db/1 (Celery tasks)
               writes: redis/db/2 (task results)
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
  └── Model choice reviewed (provider, token budget)
         │
         ▼
  Merge to main
         │
         ▼
  CI/CD pipeline
  ├── docker compose build / push image
  ├── docker compose up (rolling restart of api + worker)
  └── make migrate && make seed   (registers new agent in DB)
         │
         ▼
  New agent available at:
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
| LangFlow access | Protected by username/password | Add SSO via LangFlow's enterprise config |
| TLS | Not configured (POC) | Terminate at load balancer (nginx/ALB) |

---

## Extension Points

| What you want to add | Where to do it |
|---|---|
| New LLM provider | `react_agent._build_model()` |
| New tool | `agent/tools/` + register in `tools/__init__.py` |
| New API endpoint | `api/routes/` |
| New DB table | New model in `db/models.py` + Alembic migration |
| JWT auth | Replace `dependencies.verify_api_key()` |
| Cost calculation | `queue/tasks.py` — map token counts to USD per model |
| Conversation memory | Add `AsyncPostgresSaver` checkpointer in `react_agent.build_agent()` |
| Horizontal scaling | Add more `worker` containers; point them at the same Redis + Postgres |
| Kubernetes deployment | Replace `docker-compose.yml` with Helm chart — services map 1:1 |

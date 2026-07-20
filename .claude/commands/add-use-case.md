# Add a New Use Case

Scaffold all boilerplate files needed to add a new domain use case to the platform, following the 5-step pattern in CLAUDE.md.

**Arguments**: `$ARGUMENTS` — format: `<domain> <use-case-name>` (e.g. `crm lead-scoring` or `propguru cp-scoring`)

## Steps

### 1. Parse arguments
Extract `DOMAIN` (e.g. `crm`) and `USE_CASE` (e.g. `lead-scoring`) from `$ARGUMENTS`.
Derive:
- `DOMAIN_SNAKE` = domain with hyphens replaced by underscores (e.g. `crm`)
- `USE_CASE_SNAKE` = use case with hyphens replaced by underscores (e.g. `lead_scoring`)
- `AGENT_PREFIX` = `<domain>-<use-case>` (e.g. `crm-lead-scoring`)

### 2. Create the domain tools directory and stub tool file

Create `src/fde_agent/agent/tools/<DOMAIN_SNAKE>/__init__.py` (empty file if it doesn't exist).

Create `src/fde_agent/agent/tools/<DOMAIN_SNAKE>/<USE_CASE_SNAKE>.py` with this stub:

```python
"""<DOMAIN> <USE_CASE> tools."""

from langchain_core.tools import tool


@tool
def <DOMAIN_SNAKE>_get_<USE_CASE_SNAKE>(entity_id: str) -> dict:
    """Fetch the <USE_CASE> entity by ID. Replace with real DB query."""
    raise NotImplementedError("Implement this tool")
```

### 3. Register the tool in `src/fde_agent/agent/tools/__init__.py`

Read the current file, then add:
- An import at the top (grouped with other domain imports):
  ```python
  from fde_agent.agent.tools.<DOMAIN_SNAKE>.<USE_CASE_SNAKE> import (
      <DOMAIN_SNAKE>_get_<USE_CASE_SNAKE>,
  )
  ```
- An entry in the `_TOOL_REGISTRY` dict:
  ```python
  "<DOMAIN_SNAKE>_get_<USE_CASE_SNAKE>": <DOMAIN_SNAKE>_get_<USE_CASE_SNAKE>,
  ```

### 4. Create the supervisor YAML manifest

Create `agents/configs/<AGENT_PREFIX>-supervisor.yaml`:

```yaml
agent:
  name: <AGENT_PREFIX>-supervisor
  type: supervisor
  description: >
    Orchestrates the <DOMAIN> <USE_CASE> pipeline.
    TODO: describe what workers this supervisor coordinates.
  version: "1.0.0"
  companies: [<DOMAIN>]

  workers:
    - agent: <AGENT_PREFIX>-worker
      description: >
        TODO: describe what this worker does and when to call it.

  routing:
    max_rounds: 10

  model:
    provider: openai
    name: gpt-4o-mini
    temperature: 0.0
    max_tokens: 1024
    max_cost_usd: 0.20

  system_prompt: |
    You are the <USE_CASE> supervisor for <DOMAIN>.
    TODO: describe the workflow and worker call order.

  inputs:
    entity_id:
      type: string
      required: true
      description: "Primary entity ID or code to process"

  guardrails:
    max_iterations: 20
    timeout_seconds: 600

  observability:
    langsmith_tracing: true
    log_inputs: true
    log_outputs: true
    log_tool_calls: false

  feature_flags:
    verification_loop: false
```

Also create `agents/configs/<AGENT_PREFIX>-worker.yaml`:

```yaml
agent:
  name: <AGENT_PREFIX>-worker
  type: react
  description: >
    TODO: describe what this worker agent does.
  version: "1.0.0"
  companies: [<DOMAIN>]

  model:
    provider: openai
    name: gpt-4o
    temperature: 0.0
    max_tokens: 4096
    max_cost_usd: 0.50

  tools:
    - <DOMAIN_SNAKE>_get_<USE_CASE_SNAKE>

  system_prompt: |
    You are a specialist agent for <DOMAIN> <USE_CASE>.
    TODO: describe your role, the tools available, and the output you should produce.

  guardrails:
    max_iterations: 10
    timeout_seconds: 300
```

### 5. Create the API routes stub

Create `src/fde_agent/api/routes/<DOMAIN_SNAKE>/` directory with:

`src/fde_agent/api/routes/<DOMAIN_SNAKE>/__init__.py` (empty, if not exists)

`src/fde_agent/api/routes/<DOMAIN_SNAKE>/pages.py`:

```python
"""<DOMAIN> HTML page routes."""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

router = APIRouter(prefix="/<DOMAIN>", tags=["<DOMAIN>-pages"])
templates = Jinja2Templates(directory="src/fde_agent/templates")


@router.get("/<USE_CASE_SNAKE>", response_class=HTMLResponse)
async def <USE_CASE_SNAKE>_page(request: Request):
    return templates.TemplateResponse(
        "<DOMAIN_SNAKE>/<USE_CASE_SNAKE>.html", {"request": request}
    )
```

### 6. Report what was created + remaining manual steps

After creating all files, print a checklist:

```
✅ Created:
  src/fde_agent/agent/tools/<DOMAIN_SNAKE>/__init__.py
  src/fde_agent/agent/tools/<DOMAIN_SNAKE>/<USE_CASE_SNAKE>.py
  agents/configs/<AGENT_PREFIX>-supervisor.yaml
  agents/configs/<AGENT_PREFIX>-worker.yaml
  src/fde_agent/api/routes/<DOMAIN_SNAKE>/__init__.py
  src/fde_agent/api/routes/<DOMAIN_SNAKE>/pages.py

📋 Remaining manual steps:
  1. DB schema  — create an Alembic migration: `uv run alembic revision --autogenerate -m "add <DOMAIN> <USE_CASE> tables"`
  2. DB models  — add SQLAlchemy models to `src/fde_agent/db/models.py`
  3. Tool impl  — replace NotImplementedError in `tools/<DOMAIN_SNAKE>/<USE_CASE_SNAKE>.py` with real DB queries
  4. Register router — add `from fde_agent.api.routes.<DOMAIN_SNAKE> import pages` and `app.include_router(pages.router)` in `src/fde_agent/api/app.py`
  5. Template   — create `src/fde_agent/templates/<DOMAIN_SNAKE>/<USE_CASE_SNAKE>.html`
  6. Activate   — `curl -s -X POST http://localhost:8000/api/v1/agents/<AGENT_PREFIX>-supervisor/activate -H "X-API-Key: dev-secret-key-change-in-prod"`
  7. Worker restart — `make up` to reload Celery worker with the new tools
```

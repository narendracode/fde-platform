# Project

Multi-domain AI agent platform POC. Architecture: **Platform Core → Domain → Use Case**.

- **Platform Core** — LangGraph react agents, Celery task queue, HITL workflow, tool registry, model-agnostic LLM factory
- **Domains** — Propguru (real-estate), Sandhar (manufacturing), Pharma/Fundly (demos)
- **Use Cases** — Propguru: property acquisition evaluation (4-agent pipeline, 30-criteria scoring, refinement canvas)

Stack: FastAPI + SQLAlchemy (async) + PostgreSQL + Redis + Celery | Jinja2 + HTMX-style partials | `uv` | Docker Compose

App: `http://localhost:8000` — API docs: `/docs` — Default API key: `dev-secret-key-change-in-prod`

# Goals

Build a modular, model-agnostic, tool-agnostic agent platform where new use cases can be added by:
1. Creating domain DB tables + SQLAlchemy models
2. Writing LangChain tools in `src/fde_agent/agent/tools/<domain>/`
3. Registering tools in `_TOOL_REGISTRY` (`tools/__init__.py`)
4. Writing agent YAML manifests in `agents/configs/`
5. Adding FastAPI routes + Jinja2 templates

Platform core (LangGraph, Celery, HITL, verification loop) is never touched per use case.

Propguru evaluation pipeline is the **reference implementation** for the platform pattern, not the product itself.

# Commands

```bash
make up          # Build + start all services (api, worker, postgres, redis, mysql, jaeger, adminer)
make migrate     # Run Alembic migrations
make seed        # Seed agent configs into DB (also runs automatically on every `make up`)

# Propguru: reset + reseed evaluation data (wipes reports + scores, keeps HITL history)
curl -s -X POST http://localhost:8000/api/v1/propguru/simulation/reset \
  -H "X-API-Key: dev-secret-key-change-in-prod" | python3 -m json.tool
curl -s -X POST http://localhost:8000/api/v1/propguru/simulation/seed \
  -H "X-API-Key: dev-secret-key-change-in-prod" | python3 -m json.tool

# Activate evaluation supervisor (also activates sub-agents)
curl -s -X POST http://localhost:8000/api/v1/agents/propguru-evaluation-supervisor/activate \
  -H "X-API-Key: dev-secret-key-change-in-prod" | python3 -m json.tool

# Trigger evaluation for a deal
curl -s -X POST "http://localhost:8000/api/v1/propguru/deals/{deal_id}/evaluate" \
  -H "X-API-Key: dev-secret-key-change-in-prod" | python3 -m json.tool

# List deals
curl -s http://localhost:8000/api/v1/propguru/deals \
  -H "X-API-Key: dev-secret-key-change-in-prod" | python3 -m json.tool

# PATCH a property
curl -s -X PATCH "http://localhost:8000/api/v1/propguru/properties/{id_or_code}" \
  -H "X-API-Key: dev-secret-key-change-in-prod" -H "Content-Type: application/json" \
  -d '{"address_line1": "New Address", "locality": "Koramangala"}'
```

Run Python/tests inside container: `uv run python ...` / `uv run pytest ...`

# Code Style / Conventions

- **Async everywhere** — all routes use `async/await` with `AsyncSession` from `get_session` dependency
- **`uv` not pip** — never use `pip install` or `poetry`; always `uv run` inside container
- **PATCH pattern** — Pydantic model + `model_dump(exclude_none=True)` for partial updates; never overwrite unset fields
- **ID lookup** — try `uuid.UUID(id_or_code)` first, fall back to code field (e.g. `DEAL-001`)
- **Score write** — always fetch the criterion row BEFORE writing a score (validation requires criterion type)
- **Agents** — YAML manifests in `agents/configs/`. Loaded fresh from disk on every task run. DB `agents` table is metadata only.
- **Tools** — registered in `_TOOL_REGISTRY` dict in `tools/__init__.py` at process start. Never stored in DB.
- **Async vs sync agent runs** — `POST /run/async` + `run_agent_task.delay()` for all domain pipelines (Celery). `POST /run` (sync) blocks the HTTP thread and is unused in production.
- **Refinement** runs synchronously inside the HTTP request as a streaming SSE response — not via Celery.

@docs/propguru-prd.md
@docs/propguru-system-design.md

# Hard stops

1. **Boolean scores are strictly `0.0` or `1.0`** — `evaluation.py:_validate_score` rejects anything else with HTTP 422. Storing `3.0` for a boolean criterion causes >100% category scores.
2. **`/simulation/reset` wipes evaluation data** — drops all `propguru_evaluation_reports` and `propguru_evaluation_scores` rows. `agent_actions` and `propguru_refine_sessions` are preserved.
3. **Normalization must match in all three places** — `evaluation.py:calculate_price`, `pages.py` refine preview partial, and any future display logic must use the same formula per scoring type (`boolean`: clamp 0–1, `scale_1_5`: `(s-1)/4`, `proximity_km`: `s/5`).
4. **`await` chains in JS** — `selectDeal()` and `renderReport()` are async. Missing `await` causes race conditions where `scrollIntoView` or canvas open fires before the report renders.

# Known gotchas / issues

- **Celery worker doesn't hot-reload** — code changes to tools or agent task logic require `make up` (container restart), not just saving the file.
- **OpenAI structured output** — requires `method="function_calling"` in `with_structured_output()`; the default JSON mode fails for complex nested schemas.
- **Deal stuck in `evaluation_pending`** — if a Celery task crashes mid-run, the deal stage is not rolled back automatically. Reset via `/simulation/reset` + `/simulation/seed` or manually patch the deal stage.

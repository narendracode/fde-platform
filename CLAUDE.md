# AI Agent Platform — LangFlow POC

## Project Overview

Multi-domain AI agent platform POC. Domains currently implemented:
- **Propguru** — Real-estate deal evaluation with 4-agent AI pipeline and HITL refinement
- **Sandhar** — Manufacturing shift planning (separate agent chain)
- **Pharma / Fundly** — Order dispatch and email marketing demos

App runs at `http://localhost:8000`. API docs at `/docs`.

## Tech Stack

- **Backend**: FastAPI + SQLAlchemy (async) + PostgreSQL + Redis
- **AI**: LangGraph react agents, LangChain tools, Anthropic Claude models
- **Templates**: Jinja2 HTML (server-rendered pages + HTMX-style partial responses)
- **Task queue**: Celery workers (background evaluations)
- **Package manager**: `uv` (not pip/poetry)
- **Container**: Docker Compose (`make up`)

## Running the Project

```bash
make up          # Start all services (Docker). Builds and runs api + worker + postgres + redis
make migrate     # Run Alembic migrations (run after first up)
make seed        # Seed agent configs into DB

# Propguru-specific reset (wipes + reseeds evaluation tables)
curl -s -X POST http://localhost:8000/api/v1/propguru/simulation/reset \
  -H "X-API-Key: dev-secret-key-change-in-prod" | python3 -m json.tool

curl -s -X POST http://localhost:8000/api/v1/propguru/simulation/seed \
  -H "X-API-Key: dev-secret-key-change-in-prod" | python3 -m json.tool
```

Default API key: `dev-secret-key-change-in-prod` (set via `API_KEY` env var or `.env`).

## Project Structure

```
src/fde_agent/
  api/
    routes/
      propguru/
        pages.py          # HTML page routes (GET /propguru/*)
        deals.py          # Deal CRUD + evaluation trigger
        evaluation.py     # Score CRUD + price calculation
        master.py         # Properties, channel partners CRUD + PATCH
        simulation.py     # /simulation/seed + /simulation/reset
      actions.py          # HITL approve/reject/refine endpoints
  agent/
    tools/propguru/       # LangGraph tool definitions (data-collector, scorer, etc.)
  db/
    models.py             # SQLAlchemy ORM models
    session.py            # get_session dependency
  templates/propguru/
    evaluation.html       # Main evaluation page with URL deep-linking + refine canvas
    deals.html            # Deals pipeline table
    master.html           # Properties + channel partners management
    _refine_preview_propguru-evaluation.html  # Server-rendered partial for canvas left pane
  config/settings.py      # App settings (API key, DB URL, model names)

agents/configs/           # YAML manifests for each agent
  propguru-data-collector.yaml
  propguru-market-analyst.yaml
  propguru-scorer.yaml
  propguru-evaluator.yaml
  propguru-evaluation-refiner.yaml
  propguru-evaluation-supervisor.yaml
```

## Propguru Domain

### Deal Lifecycle

```
lead → evaluation_pending → evaluation_done → listed → sold
```

Seeded deals (DEAL-001 to DEAL-005) always start in `lead` stage so the full pipeline can be demoed.

### Evaluation Pipeline (4 agents)

1. **data-collector** — Gathers property facts from DB
2. **market-analyst** — Fetches market comps, calculates base price per sqft
3. **scorer** — Scores all 30 criteria for the property
4. **evaluator** — Computes final price, confidence, reasoning

Triggered via: `POST /api/v1/propguru/deals/{deal_id}/evaluate`

### Evaluation Criteria

30 criteria across 4 categories: `amenity`, `location`, `property`, `society`

Three scoring types:
| Type | Stored value | Normalization to [0,1] |
|------|-------------|------------------------|
| `boolean` | **0.0 or 1.0 only** | `min(1, max(0, score))` |
| `scale_1_5` | 1.0 – 5.0 | `(score - 1) / 4` |
| `proximity_km` | 0.0 – 5.0 | `score / 5` |

**Critical**: The API (`evaluation.py:_validate_score`) rejects boolean scores that are not exactly `0.0` or `1.0` with HTTP 422. Agents that try to store `3.0` for a boolean criterion will get an error — this was an intentional fix to prevent >100% category scores.

### Score Display (UI)

- `boolean` → rendered as `✓ Yes` / `✗ No` badges (green/grey pill)
- `scale_1_5` / `proximity_km` → rendered as `N / 5` with a progress bar

### DB Tables (Propguru)

```
propguru_channel_partners
propguru_evaluation_criteria
propguru_properties
propguru_deals
propguru_evaluation_reports     (one per deal per evaluation run; version increments on refinement)
propguru_evaluation_scores      (one row per criterion per report)
propguru_market_comps
```

`agent_actions` and `propguru_refine_sessions` tables are NOT cleared by `/simulation/reset` — they persist for audit/HITL history.

### Seeded Data

- 10 properties (PROP-001 to PROP-010) — real Indian apartment addresses in Bengaluru, Pune, Hyderabad, Mumbai, Chennai
- 5 deals (DEAL-001 to DEAL-005) — all start in `lead` stage
- 2 channel partners (CP-001, CP-002)

## URL State Machine (evaluation.html)

Three URL states:
```
/propguru/evaluation              → deal list, nothing selected
/propguru/evaluation?deal_id=X    → deal selected, report visible, scrolled to report
/propguru/evaluation?deal_id=X&refine=1  → refine canvas open
```

- `history.pushState` on opening deal/canvas (creates back-button entry)
- `history.replaceState` on closing canvas (no extra history entry)
- `popstate` event handles all three states on browser back/forward
- On page load, `loadDeals()` reads URL params and auto-selects deal / opens canvas

## API Conventions

- All routes use `async/await` with `AsyncSession` from `get_session` dependency
- Auth: `Depends(verify_api_key)` — reads `X-API-Key` header
- `PATCH` endpoints use Pydantic models with `model_dump(exclude_none=True)` for partial updates
- Lookup by UUID or code: try `uuid.UUID(id_or_code)` first, fall back to code field
- Score fetch in update endpoints: always fetch the criterion BEFORE writing the score (needed for validation)

## Agent Config Pattern

Agent manifests live in `agents/configs/*.yaml`. To activate an agent:
```bash
curl -s -X POST http://localhost:8000/api/v1/agents/{agent-name}/activate \
  -H "X-API-Key: dev-secret-key-change-in-prod"
```

The propguru evaluation supervisor activates its sub-agents automatically.

## Important Constraints

1. **`/simulation/reset` wipes evaluation data** — Do not call this carelessly if you need to preserve existing evaluation reports or refinement history.
2. **Boolean scores are strictly 0 or 1** — Any other value will cause >100% category totals and is rejected at the API layer.
3. **Normalization must match in all three places** — `evaluation.py:calculate_price`, `pages.py` (refine preview partial), and any future display logic must all use the same per-type normalization formula.
4. **`await` chains in JS** — `selectDeal()` and `renderReport()` are async. Missing `await` causes race conditions where `scrollIntoView` or canvas open fires before the report is rendered.
5. **`uv` not pip** — Always use `uv run python` or `uv run pytest` inside the container.

## Common curl Commands

```bash
API=http://localhost:8000
KEY="dev-secret-key-change-in-prod"

# List all deals
curl -s "$API/api/v1/propguru/deals" -H "X-API-Key: $KEY" | python3 -m json.tool

# Trigger evaluation for a deal
curl -s -X POST "$API/api/v1/propguru/deals/{deal_id}/evaluate" \
  -H "X-API-Key: $KEY" | python3 -m json.tool

# Activate propguru evaluation supervisor
curl -s -X POST "$API/api/v1/agents/propguru-evaluation-supervisor/activate" \
  -H "X-API-Key: $KEY" | python3 -m json.tool

# Reset + reseed Propguru data
curl -s -X POST "$API/api/v1/propguru/simulation/reset" -H "X-API-Key: $KEY"
curl -s -X POST "$API/api/v1/propguru/simulation/seed"  -H "X-API-Key: $KEY"

# Update a property address
curl -s -X PATCH "$API/api/v1/propguru/properties/{property_id_or_code}" \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"address_line1": "New Address", "locality": "Koramangala"}'
```

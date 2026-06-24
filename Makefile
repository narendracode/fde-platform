.PHONY: help up down logs migrate seed sync-flows ci-deploy lint test shell-api shell-worker

help:
	@echo "AgriScience Agent Platform — POC"
	@echo ""
	@echo "  ── Infrastructure ────────────────────────────────────"
	@echo "  make up            Start all services (Docker)"
	@echo "  make down          Stop all services"
	@echo "  make logs          Tail logs for all services"
	@echo ""
	@echo "  ── Database ──────────────────────────────────────────"
	@echo "  make migrate       Run Alembic DB migrations"
	@echo "  make seed          Seed agent configs into platform DB"
	@echo ""
	@echo "  ── LangFlow (GitOps Option 4) ────────────────────────"
	@echo "  make sync-flows    Sync all YAML configs → LangFlow flows"
	@echo "  make sync-flows AGENT=react-agent   Sync one agent"
	@echo "  make sync-flows DRY_RUN=1  Preview JSON, no changes"
	@echo ""
	@echo "  ── CI/CD Simulation ──────────────────────────────────"
	@echo "  make ci-deploy     Full pipeline: migrate→seed→sync→smoke"
	@echo "  make ci-deploy AGENT=agri-assistant  Single agent deploy"
	@echo "  make ci-deploy DRY_RUN=1   Dry run (no mutations)"
	@echo "  make ci-deploy SKIP_SMOKE=1  Skip smoke test"
	@echo ""
	@echo "  ── Development ───────────────────────────────────────"
	@echo "  make lint          Run ruff + mypy"
	@echo "  make test          Run pytest"
	@echo "  make shell-api     Shell inside running api container"
	@echo "  make shell-worker  Shell inside running worker container"

# ── Infrastructure ─────────────────────────────────────────────────────────────
up:
	cp -n .env.example .env 2>/dev/null || true
	chmod +x scripts/init_postgres.sh scripts/start_api.sh scripts/start_worker.sh
	docker compose up --build
	@echo ""
	@echo "✓ LangFlow UI  → http://localhost:7860  (admin / adminpass123)"
	@echo "✓ Agent API    → http://localhost:8000/docs"

down:
	docker compose down

logs:
	docker compose logs -f

# ── Database ───────────────────────────────────────────────────────────────────
migrate:
	docker compose exec api uv run alembic upgrade head

seed:
	docker compose exec api uv run python scripts/seed_data.py

# ── LangFlow sync (Option 4) ───────────────────────────────────────────────────
# Examples:
#   make sync-flows
#   make sync-flows AGENT=react-agent
#   make sync-flows DRY_RUN=1
AGENT     ?=
DRY_RUN   ?=
_SYNC_FLAGS = $(if $(AGENT),--agent $(AGENT),) $(if $(DRY_RUN),--dry-run,)

sync-flows:
	uv run python scripts/sync_langflow_flows.py $(_SYNC_FLAGS)

# ── CI/CD simulation ───────────────────────────────────────────────────────────
# Examples:
#   make ci-deploy
#   make ci-deploy AGENT=agri-assistant
#   make ci-deploy DRY_RUN=1
#   make ci-deploy SKIP_SMOKE=1
SKIP_SMOKE ?=
_CI_FLAGS = $(if $(AGENT),--agent=$(AGENT),) \
            $(if $(DRY_RUN),--dry-run,) \
            $(if $(SKIP_SMOKE),--skip-smoke,)

ci-deploy:
	bash scripts/ci_deploy.sh $(_CI_FLAGS)

# ── Development ────────────────────────────────────────────────────────────────
lint:
	uv run ruff check src/ && uv run mypy src/

test:
	uv run pytest tests/ -v

shell-api:
	docker compose exec api /bin/bash

shell-worker:
	docker compose exec worker /bin/bash

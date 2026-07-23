.PHONY: help up upd down logs migrate seed seed-orders dashboard ci-deploy launch-agent lint test shell-api shell-worker

help:
	@echo "Agent Platform"
	@echo ""
	@echo "  ── Infrastructure ────────────────────────────────────"
	@echo "  make up            Start all services (Docker, foreground)
  make upd           Start all services (Docker, daemon / background)"
	@echo "  make down          Stop all services"
	@echo "  make logs          Tail logs for all services"
	@echo ""
	@echo "  ── Database ──────────────────────────────────────────"
	@echo "  make migrate       Run Alembic DB migrations"
	@echo "  make seed          Seed agent configs into platform DB (inactive)"
	@echo "  make seed-orders   Seed 20 demo pharma orders"
	@echo ""
	@echo "  ── Dispatch Demo ─────────────────────────────────────"
	@echo "  make dashboard     Open dispatch dashboard in browser"
	@echo "  make run-dispatch-review  Run AI + human review agent once"
	@echo "  make run-dispatch-auto    Run full-auto dispatch agent once"
	@echo ""
	@echo "  ── CI/CD Simulation ──────────────────────────────────"
	@echo "  make ci-deploy     Full pipeline: migrate→seed→smoke"
	@echo "  make ci-deploy AGENT=pharma-outreach  Single agent deploy"
	@echo "  make ci-deploy DRY_RUN=1   Dry run (no mutations)"
	@echo "  make ci-deploy SKIP_SMOKE=1  Skip smoke test"
	@echo ""
	@echo "  ── Agent Launcher ────────────────────────────────────"
	@echo "  make launch-agent  Conversational agent creator (YAML only)"
	@echo "  make launch-agent MODEL=claude-opus-4-8  Use a different model"
	@echo "  make launch-agent NO_GIT=1   Skip git/PR step"
	@echo ""
	@echo "  ── Development ───────────────────────────────────────"
	@echo "  make lint          Run ruff + mypy"
	@echo "  make test          Run pytest"
	@echo "  make shell-api     Shell inside running api container"
	@echo "  make shell-worker  Shell inside running worker container"

# ── Infrastructure ─────────────────────────────────────────────────────────────
up:
	cp -n .env.example .env 2>/dev/null || true
	chmod +x scripts/start_api.sh scripts/start_worker.sh
	docker compose up --build
	@echo ""
	@echo "✓ Agent API    → http://localhost:8000/docs"
	@echo "✓ Jaeger UI    → http://localhost:16686"
	@echo "✓ Adminer      → http://localhost:8080"

upd:
	cp -n .env.example .env 2>/dev/null || true
	chmod +x scripts/start_api.sh scripts/start_worker.sh
	docker compose up --build -d
	@echo ""
	@echo "✓ Agent API    → http://localhost:8000/docs"
	@echo "✓ Jaeger UI    → http://localhost:16686"
	@echo "✓ Adminer      → http://localhost:8080"
	@echo ""
	@echo "  Tip: run 'make logs' to tail all service logs"

down:
	docker compose down

logs:
	docker compose logs -f

# ── Database ───────────────────────────────────────────────────────────────────
migrate:
	docker compose exec api uv run alembic upgrade head

seed:
	docker compose exec api uv run python scripts/seed_data.py

seed-orders:
	docker compose exec api uv run python scripts/seed_orders.py

# ── Dispatch Demo ───────────────────────────────────────────────────────────────
API_URL ?= http://localhost:8000
API_KEY ?= dev-secret-key-change-in-prod

dashboard:
	open $(API_URL)/dashboard

run-dispatch-review:
	curl -s -X POST $(API_URL)/api/v1/agents/order-dispatch-review/run \
	  -H "X-API-Key: $(API_KEY)" \
	  -H "Content-Type: application/json" \
	  -d '{"message":"Process all pending orders and recommend shipment mode for each."}' \
	  | python3 -m json.tool

run-dispatch-auto:
	curl -s -X POST $(API_URL)/api/v1/agents/order-dispatch-auto/run \
	  -H "X-API-Key: $(API_KEY)" \
	  -H "Content-Type: application/json" \
	  -d '{"message":"Process all pending orders and dispatch them with the optimal shipment mode."}' \
	  | python3 -m json.tool

# ── CI/CD simulation ───────────────────────────────────────────────────────────
AGENT   ?=
DRY_RUN ?=
SKIP_SMOKE ?=
_CI_FLAGS = $(if $(AGENT),--agent=$(AGENT),) \
            $(if $(DRY_RUN),--dry-run,) \
            $(if $(SKIP_SMOKE),--skip-smoke,)

ci-deploy:
	bash scripts/ci_deploy.sh $(_CI_FLAGS)

# ── Agent Launcher ─────────────────────────────────────────────────────────────
# Interactive CLI: converse → generate YAML manifest → review → push PR
# The launcher creates the YAML only; developers add tool implementations separately.
# Examples:
#   make launch-agent
#   make launch-agent MODEL=claude-opus-4-8
#   make launch-agent NO_GIT=1
MODEL  ?= claude-sonnet-4-6
NO_GIT ?=
_LAUNCH_FLAGS = --model $(MODEL) $(if $(NO_GIT),--no-git,)

launch-agent:
	uv run python scripts/launch_agent.py $(_LAUNCH_FLAGS)

# ── Development ────────────────────────────────────────────────────────────────
lint:
	uv run ruff check src/ && uv run mypy src/

test:
	uv run pytest tests/ -v

shell-api:
	docker compose exec api /bin/bash

shell-worker:
	docker compose exec worker /bin/bash

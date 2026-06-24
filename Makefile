.PHONY: help up down logs migrate seed lint test shell-api shell-worker

help:
	@echo "AgriScience Agent Platform — POC"
	@echo ""
	@echo "  make up          Start all services"
	@echo "  make down        Stop all services"
	@echo "  make logs        Tail logs for all services"
	@echo "  make migrate     Run Alembic DB migrations"
	@echo "  make seed        Seed demo agent configs into DB"
	@echo "  make lint        Run ruff + mypy"
	@echo "  make test        Run pytest"
	@echo "  make shell-api   Open shell in running API container"
	@echo "  make shell-worker  Open shell in running worker container"

up:
	cp -n .env.example .env 2>/dev/null || true
	docker compose up --build -d
	@echo "\n✓ LangFlow UI  → http://localhost:7860"
	@echo "✓ Agent API    → http://localhost:8000/docs"

down:
	docker compose down

logs:
	docker compose logs -f

migrate:
	docker compose exec api uv run alembic upgrade head

seed:
	docker compose exec api uv run python scripts/seed_data.py

lint:
	uv run ruff check src/ && uv run mypy src/

test:
	uv run pytest tests/ -v

shell-api:
	docker compose exec api /bin/bash

shell-worker:
	docker compose exec worker /bin/bash

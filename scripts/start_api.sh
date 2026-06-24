#!/bin/sh
# API container startup — runs on every `docker compose up`.
# Order: wait → migrate → seed → serve
set -e

echo "=== AgriScience API startup ==="

echo "--- [1/4] Waiting for PostgreSQL..."
uv run python scripts/wait_for_db.py

echo "--- [2/4] Running Alembic migrations..."
uv run alembic upgrade head

echo "--- [3/4] Seeding agent configs..."
uv run python scripts/seed_data.py

echo "--- [4/4] Starting API server..."
exec uv run uvicorn agri_agent.api.app:app \
    --host 0.0.0.0 --port 8000 --reload

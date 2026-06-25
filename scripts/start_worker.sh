#!/bin/sh
# Celery worker container startup.
# Waits for the DB (migrations run by api container) before starting.
set -e

echo "=== Fundly Worker startup ==="

echo "--- [1/2] Waiting for PostgreSQL..."
uv run python scripts/wait_for_db.py

echo "--- [2/2] Starting Celery worker..."
exec uv run celery -A agri_agent.queue.celery_app worker \
    --loglevel=info --concurrency=4

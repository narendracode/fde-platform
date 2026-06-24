FROM python:3.11-slim AS base

# Copy uv binary from the official uv image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_SYSTEM_PYTHON=1 \
    UV_COMPILE_BYTECODE=1

COPY pyproject.toml uv.lock* README.md ./
COPY src/ ./src/

RUN uv sync --frozen --no-dev

COPY agents/ ./agents/
COPY alembic/ ./alembic/
COPY alembic.ini ./
COPY scripts/ ./scripts/

# ── API target ──────────────────────────────────────────────────────────────
FROM base AS api

CMD ["uv", "run", "uvicorn", "agri_agent.api.app:app", \
     "--host", "0.0.0.0", "--port", "8000", "--reload"]

# ── Celery worker target ─────────────────────────────────────────────────────
FROM base AS worker

CMD ["uv", "run", "celery", "-A", "agri_agent.queue.celery_app", \
     "worker", "--loglevel=info", "--concurrency=4"]

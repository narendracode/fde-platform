"""Celery tasks for async agent execution."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from celery.utils.log import get_task_logger
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from fde_agent.agent.react_agent import run_agent
from fde_agent.config.loader import load_agent_config
from fde_agent.config.settings import settings
from fde_agent.db.models import AgentRun
from fde_agent.queue.celery_app import celery_app

logger = get_task_logger(__name__)

# Celery tasks run synchronously, so we use a sync SQLAlchemy engine
_sync_engine = create_engine(settings.database_url_sync, pool_pre_ping=True)


def _get_sync_session() -> Session:
    return Session(_sync_engine)


@celery_app.task(
    name="fde_agent.queue.tasks.run_agent_task",
    bind=True,
    max_retries=2,
    default_retry_delay=10,
)
def run_agent_task(
    self,
    run_id: str,
    agent_name: str,
    user_message: str,
    thread_id: str | None = None,
    extra_context: dict | None = None,
) -> dict:
    """Execute an agent run and persist results to the DB.

    Called via .delay() or .apply_async() from the API layer.
    """
    with _get_sync_session() as session:
        run: AgentRun | None = session.get(AgentRun, uuid.UUID(run_id))
        if run is None:
            raise ValueError(f"AgentRun {run_id} not found")

        run.status = "running"
        run.started_at = datetime.now(UTC)
        session.commit()

    try:
        config = load_agent_config(agent_name)
        result = run_agent(
            config=config,
            user_message=user_message,
            thread_id=thread_id,
            extra_context=extra_context,
        )

        with _get_sync_session() as session:
            run = session.get(AgentRun, uuid.UUID(run_id))
            if run:
                run.status = "completed"
                run.output = {
                    "text": result["output"],
                    "tool_calls": result["tool_calls"],
                    "sub_agents": result.get("sub_agents", []),
                }
                run.thread_id = result["thread_id"]
                run.input_tokens = result["input_tokens"]
                run.output_tokens = result["output_tokens"]
                run.cost_usd = result.get("cost_usd", 0.0)
                run.langsmith_run_id = result.get("langsmith_run_id")
                run.langsmith_trace_url = result.get("langsmith_trace_url")
                run.otel_trace_id = result.get("otel_trace_id")
                run.otel_trace_url = result.get("otel_trace_url")
                run.completed_at = datetime.now(UTC)
                session.commit()

        return result

    except Exception as exc:
        logger.exception("Agent run %s failed: %s", run_id, exc)
        with _get_sync_session() as session:
            run = session.get(AgentRun, uuid.UUID(run_id))
            if run:
                run.status = "failed"
                run.error = str(exc)
                run.completed_at = datetime.now(UTC)
                session.commit()
        raise self.retry(exc=exc)

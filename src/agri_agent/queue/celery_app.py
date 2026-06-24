"""Celery application configuration."""

from celery import Celery

from agri_agent.config.settings import settings

celery_app = Celery(
    "agri_agent",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["agri_agent.queue.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    # Throttle: at most 10 agent tasks per minute across all workers
    task_annotations={"agri_agent.queue.tasks.run_agent_task": {"rate_limit": "10/m"}},
    # Concurrency control per queue
    task_routes={
        "agri_agent.queue.tasks.run_agent_task": {"queue": "agent_runs"},
    },
    worker_prefetch_multiplier=1,  # fair dispatch — don't pre-fetch more than 1 task
    task_acks_late=True,           # ack only after task completes (safe retry on crash)
    task_reject_on_worker_lost=True,
)

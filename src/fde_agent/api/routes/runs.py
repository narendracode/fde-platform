"""Run history and audit endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from fde_agent.api.dependencies import verify_api_key
from fde_agent.db.models import AgentRun
from fde_agent.db.session import get_session

router = APIRouter(prefix="/api/v1/runs", tags=["runs"])


@router.get("")
async def list_runs(
    limit: int = 50,
    offset: int = 0,
    status: str | None = None,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """List agent runs with optional status filter. Ordered by newest first."""
    query = select(AgentRun).order_by(desc(AgentRun.created_at)).offset(offset).limit(limit)
    if status:
        query = query.where(AgentRun.status == status)
    rows = await session.execute(query)
    runs = rows.scalars().all()
    return [_run_dict(r) for r in runs]


@router.get("/{run_id}")
async def get_run(
    run_id: str,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Get full details of a specific run — use this to poll async run status."""
    import uuid as _uuid
    try:
        uid = _uuid.UUID(run_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid run_id format")

    run = await session.get(AgentRun, uid)
    if not run:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    return _run_dict(run, include_output=True)


def _run_dict(run: AgentRun, include_output: bool = False) -> dict:
    d = {
        "id": str(run.id),
        "agent_id": str(run.agent_id),
        "thread_id": run.thread_id,
        "status": run.status,
        "task_id": run.task_id,
        "input_tokens": run.input_tokens,
        "output_tokens": run.output_tokens,
        "cost_usd": run.cost_usd,
        "langsmith_run_id": run.langsmith_run_id,
        "langsmith_trace_url": run.langsmith_trace_url,
        "otel_trace_id": run.otel_trace_id,
        "otel_trace_url": run.otel_trace_url,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        "created_at": run.created_at.isoformat(),
    }
    if include_output:
        d["input"] = run.input
        d["output"] = run.output
        d["error"] = run.error
    return d

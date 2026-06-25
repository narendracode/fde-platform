"""Agent management endpoints — CRUD + sync run."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agri_agent.agent.react_agent import run_agent
from agri_agent.agent.tools import list_available_tools
from agri_agent.api.dependencies import verify_api_key
from agri_agent.config.loader import list_agent_configs, load_agent_config
from agri_agent.db.models import Agent, AgentRun
from agri_agent.db.session import get_session

router = APIRouter(prefix="/api/v1/agents", tags=["agents"])


# ── Request / Response schemas ────────────────────────────────────────────────

class RegisterAgentRequest(BaseModel):
    config_name: str  # filename without .yaml — must exist in agents/configs/


class RunRequest(BaseModel):
    message: str
    thread_id: str | None = None
    extra_context: dict[str, Any] | None = None


class RunResponse(BaseModel):
    run_id: str
    output: str
    thread_id: str
    tool_calls: list[dict]
    input_tokens: int
    output_tokens: int
    elapsed_seconds: float
    blocked: bool
    langsmith_run_id: str | None = None
    langsmith_trace_url: str | None = None
    otel_trace_id: str | None = None
    otel_trace_url: str | None = None


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("")
async def list_agents(
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """List all registered agents from the database."""
    rows = await session.execute(select(Agent).where(Agent.is_active == True))
    agents = rows.scalars().all()
    return [
        {
            "id": str(a.id),
            "name": a.name,
            "description": a.description,
            "version": a.version,
            "created_at": a.created_at.isoformat(),
        }
        for a in agents
    ]


@router.get("/configs")
async def list_configs(_: str = Depends(verify_api_key)):
    """List all YAML agent config files available on disk."""
    configs = list_agent_configs()
    return [
        {
            "name": c.name,
            "description": c.description,
            "version": c.version,
            "model": c.model.model_dump(),
            "enabled_tools": c.enabled_tools(),
        }
        for c in configs
    ]


@router.get("/tools")
async def list_tools(_: str = Depends(verify_api_key)):
    """List all tools available in the tool registry."""
    return {"tools": list_available_tools()}


@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register_agent(
    req: RegisterAgentRequest,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Load a YAML config and register/upsert the agent in the database."""
    try:
        cfg = load_agent_config(req.config_name)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    # Upsert: update if exists, insert if not
    result = await session.execute(select(Agent).where(Agent.name == cfg.name))
    agent = result.scalar_one_or_none()
    if agent:
        agent.description = cfg.description
        agent.version = cfg.version
        agent.config = cfg.model_dump()
    else:
        agent = Agent(
            name=cfg.name,
            description=cfg.description,
            version=cfg.version,
            config=cfg.model_dump(),
        )
        session.add(agent)

    await session.commit()
    await session.refresh(agent)
    return {"id": str(agent.id), "name": agent.name, "status": "registered"}


@router.get("/{agent_name}")
async def get_agent(
    agent_name: str,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Get a registered agent by name."""
    result = await session.execute(select(Agent).where(Agent.name == agent_name))
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_name}' not found")
    return {"id": str(agent.id), "name": agent.name, "config": agent.config}


@router.post("/{agent_name}/run", response_model=RunResponse)
async def run_agent_sync(
    agent_name: str,
    req: RunRequest,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Run an agent synchronously and return the result immediately.

    Use POST /{agent_name}/run/async for long-running tasks.
    """
    try:
        cfg = load_agent_config(agent_name)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Agent config '{agent_name}' not found")

    # Persist the run record
    now = datetime.now(timezone.utc)
    run = AgentRun(
        agent_id=await _resolve_agent_id(session, agent_name),
        input={"message": req.message, "extra_context": req.extra_context},
        status="running",
        started_at=now,
    )
    session.add(run)
    await session.commit()

    result = run_agent(
        config=cfg,
        user_message=req.message,
        thread_id=req.thread_id,
        extra_context=req.extra_context,
    )

    run.status = "completed" if not result.get("blocked") else "blocked"
    run.output = {"text": result["output"], "tool_calls": result["tool_calls"]}
    run.thread_id = result["thread_id"]
    run.input_tokens = result["input_tokens"]
    run.output_tokens = result["output_tokens"]
    run.cost_usd = result.get("cost_usd", 0.0)
    run.langsmith_run_id = result.get("langsmith_run_id")
    run.langsmith_trace_url = result.get("langsmith_trace_url")
    run.otel_trace_id = result.get("otel_trace_id")
    run.otel_trace_url = result.get("otel_trace_url")
    run.completed_at = datetime.now(timezone.utc)
    await session.commit()

    return RunResponse(
        run_id=str(run.id),
        output=result["output"],
        thread_id=result["thread_id"],
        tool_calls=result["tool_calls"],
        input_tokens=result["input_tokens"],
        output_tokens=result["output_tokens"],
        elapsed_seconds=result["elapsed_seconds"],
        blocked=result.get("blocked", False),
        langsmith_run_id=result.get("langsmith_run_id"),
        langsmith_trace_url=result.get("langsmith_trace_url"),
        otel_trace_id=result.get("otel_trace_id"),
        otel_trace_url=result.get("otel_trace_url"),
    )


@router.post("/{agent_name}/run/async", status_code=status.HTTP_202_ACCEPTED)
async def run_agent_async(
    agent_name: str,
    req: RunRequest,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Queue an agent run via Celery and return a task/run ID for polling.

    Poll GET /api/v1/runs/{run_id} for status.
    """
    from agri_agent.queue.tasks import run_agent_task

    try:
        load_agent_config(agent_name)  # validate config exists
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Agent config '{agent_name}' not found")

    run = AgentRun(
        agent_id=await _resolve_agent_id(session, agent_name),
        input={"message": req.message, "extra_context": req.extra_context},
        status="pending",
    )
    session.add(run)
    await session.commit()

    task = run_agent_task.delay(
        run_id=str(run.id),
        agent_name=agent_name,
        user_message=req.message,
        thread_id=req.thread_id,
        extra_context=req.extra_context,
    )

    run.task_id = task.id
    await session.commit()

    return {"run_id": str(run.id), "task_id": task.id, "status": "queued"}


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _resolve_agent_id(session: AsyncSession, agent_name: str) -> uuid.UUID:
    result = await session.execute(select(Agent).where(Agent.name == agent_name))
    agent = result.scalar_one_or_none()
    if agent:
        return agent.id
    # Auto-register on first use
    cfg = load_agent_config(agent_name)
    agent = Agent(
        name=cfg.name,
        description=cfg.description,
        version=cfg.version,
        config=cfg.model_dump(),
    )
    session.add(agent)
    await session.flush()
    return agent.id

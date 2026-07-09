"""Agent management endpoints — CRUD, activation, and run."""

from __future__ import annotations

import uuid
import yaml
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fde_agent.agent.react_agent import run_agent
from fde_agent.agent.tools import list_available_tools, list_tools_with_descriptions
from fde_agent.api.dependencies import verify_api_key
from fde_agent.config.loader import agent_is_visible, list_agent_configs, load_agent_config
from fde_agent.config.settings import settings
from fde_agent.db.models import Agent, AgentRun
from fde_agent.db.session import get_session

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
    cost_usd: float
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
    """List registered agents visible for the active company configuration."""
    active = [c.strip().lower() for c in settings.companies_to_show.split(",") if c.strip()]
    cfg_map = {c.name: c for c in list_agent_configs()}

    rows = await session.execute(select(Agent))
    return [
        {
            "id": str(a.id),
            "name": a.name,
            "description": a.description,
            "version": a.version,
            "is_active": a.is_active,
            "created_at": a.created_at.isoformat(),
        }
        for a in rows.scalars().all()
        if (agent_is_visible(cfg_map[a.name], active) if a.name in cfg_map else True)
    ]


@router.get("/configs")
async def list_configs(_: str = Depends(verify_api_key)):
    """List YAML agent configs visible for the active company configuration."""
    active = [c.strip().lower() for c in settings.companies_to_show.split(",") if c.strip()]
    configs = [c for c in list_agent_configs() if agent_is_visible(c, active)]
    return [
        {
            "name": c.name,
            "description": c.description,
            "version": c.version,
            "model": c.model.model_dump(),
            "enabled_tools": c.enabled_tools(),
            "inputs": {
                name: param.model_dump()
                for name, param in c.inputs.items()
            },
        }
        for c in configs
    ]


@router.get("/tools")
async def list_tools(_: str = Depends(verify_api_key)):
    """List all tools available in the registry (name + description)."""
    return {"tools": list_tools_with_descriptions()}


@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register_agent(
    req: RegisterAgentRequest,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Load a YAML config and register/upsert the agent in the database.

    Newly registered agents are inactive by default. Activate via PATCH /{name}/activate.
    """
    try:
        cfg = load_agent_config(req.config_name)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    result = await session.execute(select(Agent).where(Agent.name == cfg.name))
    agent = result.scalar_one_or_none()
    if agent:
        agent.description = cfg.description
        agent.version = cfg.version
        agent.config = cfg.model_dump()
        # is_active is NOT changed on update — preserves dashboard-set state
    else:
        agent = Agent(
            name=cfg.name,
            description=cfg.description,
            version=cfg.version,
            config=cfg.model_dump(),
            is_active=False,
        )
        session.add(agent)

    await session.commit()
    await session.refresh(agent)
    return {
        "id": str(agent.id),
        "name": agent.name,
        "is_active": agent.is_active,
        "status": "registered",
    }


@router.patch("/{agent_name}/activate")
async def activate_agent(
    agent_name: str,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Mark an agent as active — it can now accept run requests."""
    agent = await _get_agent_or_404(session, agent_name)
    agent.is_active = True
    await session.commit()
    return {"name": agent.name, "is_active": True}


@router.patch("/{agent_name}/deactivate")
async def deactivate_agent(
    agent_name: str,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Mark an agent as inactive — run requests will be rejected."""
    agent = await _get_agent_or_404(session, agent_name)
    agent.is_active = False
    await session.commit()
    return {"name": agent.name, "is_active": False}


@router.get("/{agent_name}/yaml", response_class=PlainTextResponse)
async def get_agent_yaml(
    agent_name: str,
    _: str = Depends(verify_api_key),
):
    """Return the raw YAML config file for an agent as plain text."""
    config_dir = Path(settings.agents_config_dir)
    for candidate in (f"{agent_name}.yaml", f"{agent_name.replace('-', '_')}.yaml"):
        path = config_dir / candidate
        if path.exists():
            return PlainTextResponse(path.read_text())
    for path in config_dir.glob("*.yaml"):
        raw = yaml.safe_load(path.read_text())
        agent_raw = raw.get("agent", raw)
        if agent_raw.get("name") == agent_name:
            return PlainTextResponse(path.read_text())
    raise HTTPException(status_code=404, detail=f"YAML config for '{agent_name}' not found")


@router.get("/{agent_name}")
async def get_agent(
    agent_name: str,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Get a registered agent by name."""
    agent = await _get_agent_or_404(session, agent_name)
    return {
        "id": str(agent.id),
        "name": agent.name,
        "is_active": agent.is_active,
        "config": agent.config,
    }


@router.post("/{agent_name}/run", response_model=RunResponse)
async def run_agent_sync(
    agent_name: str,
    req: RunRequest,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Run an agent synchronously and return the result immediately.

    The agent must be active (PATCH /{name}/activate) before it can be invoked.
    Use POST /{agent_name}/run/async for long-running tasks.
    """
    try:
        cfg = load_agent_config(agent_name)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Agent config '{agent_name}' not found")

    await _require_active(session, agent_name)
    _validate_inputs(cfg, req.extra_context)

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
        cost_usd=result.get("cost_usd", 0.0),
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
    """Queue an agent run via Celery and return a run ID for polling.

    The agent must be active. Poll GET /api/v1/runs/{run_id} for status.
    """
    from fde_agent.queue.tasks import run_agent_task

    try:
        cfg = load_agent_config(agent_name)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Agent config '{agent_name}' not found")

    await _require_active(session, agent_name)
    _validate_inputs(cfg, req.extra_context)

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

async def _get_agent_or_404(session: AsyncSession, agent_name: str) -> Agent:
    result = await session.execute(select(Agent).where(Agent.name == agent_name))
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_name}' not found")
    return agent


async def _require_active(session: AsyncSession, agent_name: str) -> None:
    """Raise 403 if the agent exists but is not active."""
    result = await session.execute(select(Agent).where(Agent.name == agent_name))
    agent = result.scalar_one_or_none()
    if agent and not agent.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Agent '{agent_name}' is not active. Activate it via PATCH /api/v1/agents/{agent_name}/activate",
        )


def _validate_inputs(cfg: Any, extra_context: dict | None) -> None:
    """Raise 422 if required inputs are missing. No-op when no inputs are declared."""
    if not cfg.inputs:
        return
    try:
        cfg.resolve_context(extra_context or {})
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        )


async def _resolve_agent_id(session: AsyncSession, agent_name: str) -> uuid.UUID:
    result = await session.execute(select(Agent).where(Agent.name == agent_name))
    agent = result.scalar_one_or_none()
    if agent:
        return agent.id
    cfg = load_agent_config(agent_name)
    agent = Agent(
        name=cfg.name,
        description=cfg.description,
        version=cfg.version,
        config=cfg.model_dump(),
        is_active=False,
    )
    session.add(agent)
    await session.flush()
    return agent.id

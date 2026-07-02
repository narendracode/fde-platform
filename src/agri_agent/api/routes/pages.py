"""Server-rendered UI pages — /agents and /runs."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from agri_agent.api._templates import templates
from agri_agent.config.loader import list_agent_configs
from agri_agent.config.settings import settings
from agri_agent.db.models import Agent, AgentRun
from agri_agent.db.session import get_session

router = APIRouter(tags=["ui"])


@router.get("/agents", response_class=HTMLResponse)
async def agents_page(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    yaml_configs = {cfg.name: cfg for cfg in list_agent_configs()}

    rows = await session.execute(select(Agent))
    db_agents = {a.name: a for a in rows.scalars().all()}

    # Auto-register any YAML config not yet in the DB
    new_agents = []
    for name, cfg in yaml_configs.items():
        if name not in db_agents:
            agent = Agent(
                name=cfg.name,
                description=cfg.description,
                version=cfg.version,
                config=cfg.model_dump(),
                is_active=False,
            )
            session.add(agent)
            new_agents.append(name)
    if new_agents:
        await session.commit()
        rows = await session.execute(select(Agent).order_by(Agent.name))
        db_agents = {a.name: a for a in rows.scalars().all()}

    agents = []
    for a in sorted(db_agents.values(), key=lambda x: x.name):
        cfg = yaml_configs.get(a.name)
        agents.append({
            "id": str(a.id),
            "name": a.name,
            "description": a.description,
            "version": a.version,
            "is_active": a.is_active,
            "inputs": [
                {
                    "name": k,
                    "type": v.type,
                    "required": v.required,
                    "default": v.default,
                    "description": v.description,
                }
                for k, v in cfg.inputs.items()
            ] if cfg else [],
            "tools": [t.name for t in cfg.tools if t.enabled] if cfg else [],
            "model_name": cfg.model.name if cfg else "—",
            "max_cost_usd": cfg.model.max_cost_usd if cfg else 0,
            "feature_flags": cfg.feature_flags if cfg else {},
            "has_yaml": cfg is not None,
        })

    return templates.TemplateResponse(
        request,
        "agents.html",
        {"agents": agents, "api_key": settings.api_key, "active_page": "platform_agents"},
    )


@router.get("/runs", response_class=HTMLResponse)
async def runs_page(
    request: Request,
    agent_name: str | None = Query(None),
    status: str | None = Query(None),
    session: AsyncSession = Depends(get_session),
):
    rows = await session.execute(select(Agent))
    all_agents = rows.scalars().all()
    agent_map = {str(a.id): a.name for a in all_agents}
    agent_names = sorted(a.name for a in all_agents)

    q = select(AgentRun).order_by(desc(AgentRun.created_at)).limit(100)
    if agent_name:
        ag_row = await session.execute(select(Agent).where(Agent.name == agent_name))
        ag = ag_row.scalar_one_or_none()
        if ag:
            q = q.where(AgentRun.agent_id == ag.id)
    if status:
        q = q.where(AgentRun.status == status)

    run_rows = await session.execute(q)
    runs = []
    for r in run_rows.scalars().all():
        extra = (r.input or {}).get("extra_context") or {}
        elapsed = None
        if r.started_at and r.completed_at:
            elapsed = round((r.completed_at - r.started_at).total_seconds(), 1)
        runs.append({
            "id": str(r.id),
            "agent_name": agent_map.get(str(r.agent_id), "—"),
            "status": r.status,
            "input_summary": extra,
            "message": (r.input or {}).get("message", ""),
            "output_text": (r.output or {}).get("text", ""),
            "tool_calls": (r.output or {}).get("tool_calls", []),
            "input_tokens": r.input_tokens or 0,
            "output_tokens": r.output_tokens or 0,
            "cost_usd": r.cost_usd or 0,
            "elapsed": elapsed,
            "langsmith_trace_url": r.langsmith_trace_url,
            "otel_trace_url": r.otel_trace_url,
            "otel_trace_id": r.otel_trace_id,
            "created_at": r.created_at.isoformat(),
            "started_at": r.started_at.isoformat() if r.started_at else None,
        })

    return templates.TemplateResponse(
        request,
        "runs.html",
        {
            "runs": runs,
            "agent_names": agent_names,
            "selected_agent": agent_name or "",
            "selected_status": status or "",
            "api_key": settings.api_key,
            "active_page": "platform_runs",
        },
    )

"""Generic approvals inbox UI route."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agri_agent.config.settings import settings
from agri_agent.db.models import AgentAction
from agri_agent.db.session import get_session

router = APIRouter(tags=["approvals"])

_templates_dir = Path(__file__).parent.parent.parent / "templates"
templates = Jinja2Templates(directory=str(_templates_dir))


def _action_dict(a: AgentAction) -> dict:
    return {
        "id": str(a.id),
        "agent_name": a.agent_name,
        "agent_run_id": str(a.agent_run_id) if a.agent_run_id else None,
        "title": a.title,
        "summary": a.summary,
        "reasoning": a.reasoning,
        "confidence": a.confidence or "medium",
        "display_data": a.display_data or [],
        "tags": a.tags or [],
        "approval_action": a.approval_action,
        "status": a.status,
        "approval_error": a.approval_error,
        "created_at": a.created_at.isoformat(),
    }


@router.get("/approvals", response_class=HTMLResponse)
async def approvals_inbox(
    request: Request,
    agent_name: str | None = Query(None),
    confidence: str | None = Query(None),
    session: AsyncSession = Depends(get_session),
):
    q = (
        select(AgentAction)
        .where(AgentAction.status == "pending_review")
        .order_by(AgentAction.created_at.asc())
    )
    if agent_name:
        q = q.where(AgentAction.agent_name == agent_name)
    if confidence:
        q = q.where(AgentAction.confidence == confidence)

    rows = await session.execute(q)
    actions = [_action_dict(a) for a in rows.scalars().all()]

    # Distinct agent names for the filter dropdown
    all_q = await session.execute(
        select(AgentAction.agent_name)
        .where(AgentAction.status == "pending_review")
        .distinct()
    )
    agent_names = sorted(r[0] for r in all_q.all())

    return templates.TemplateResponse(
        request,
        "approvals.html",
        {
            "actions": actions,
            "agent_names": agent_names,
            "selected_agent": agent_name or "",
            "selected_confidence": confidence or "",
            "total": len(actions),
            "api_key": settings.api_key,
        },
    )

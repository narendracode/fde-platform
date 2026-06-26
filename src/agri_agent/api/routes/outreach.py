"""Outreach API — pharma retailer email delivery + dashboard UI.

POST /api/v1/outreach/send-email  — called by the platform approval engine when
    a human approves a pharma-outreach propose_action.  Mock: logs to console.
GET  /outreach                     — dashboard UI for triggering outreach runs.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agri_agent.api.dependencies import verify_api_key
from agri_agent.config.loader import load_agent_config
from agri_agent.config.settings import settings
from agri_agent.db.models import Agent, AgentRun
from agri_agent.db.session import get_session

log = logging.getLogger(__name__)

router = APIRouter(tags=["outreach"])

_templates_dir = Path(__file__).parent.parent.parent / "templates"
templates = Jinja2Templates(directory=str(_templates_dir))

_AGENT_NAME = "pharma-outreach"


# ── Email delivery endpoint ────────────────────────────────────────────────────

class SendEmailRequest(BaseModel):
    to: str
    subject: str
    body: str


class SendEmailResponse(BaseModel):
    status: str
    to: str
    subject: str


@router.post(
    "/api/v1/outreach/send-email",
    response_model=SendEmailResponse,
    dependencies=[Depends(verify_api_key)],
)
async def send_email(req: SendEmailRequest) -> SendEmailResponse:
    """Send a pharma outreach email (mock: logs to console)."""
    border = "─" * 60
    output = (
        f"\n{'═' * 60}\n"
        f"  📧  OUTREACH EMAIL DISPATCHED\n"
        f"{'═' * 60}\n"
        f"  To      : {req.to}\n"
        f"  Subject : {req.subject}\n"
        f"{border}\n"
        f"{req.body}\n"
        f"{'═' * 60}\n"
    )
    log.info("Mock email sent to %s | subject: %s", req.to, req.subject)
    print(output)
    return SendEmailResponse(status="sent", to=req.to, subject=req.subject)


# ── Dashboard UI ───────────────────────────────────────────────────────────────

@router.get("/outreach", response_class=HTMLResponse)
async def outreach_dashboard(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    # Auto-register & activate pharma-outreach if not already done
    result = await session.execute(select(Agent).where(Agent.name == _AGENT_NAME))
    agent = result.scalar_one_or_none()
    if not agent:
        cfg = load_agent_config(_AGENT_NAME)
        agent = Agent(
            name=cfg.name,
            description=cfg.description,
            version=cfg.version,
            config=cfg.model_dump(),
            is_active=True,
        )
        session.add(agent)
        await session.commit()
        await session.refresh(agent)
    elif not agent.is_active:
        agent.is_active = True
        await session.commit()

    # Determine if human_in_the_loop is enabled from config
    try:
        cfg = load_agent_config(_AGENT_NAME)
        hitl = bool(cfg.feature_flags.get("human_in_the_loop", False))
    except Exception:
        hitl = False

    # Recent runs for this agent (last 20)
    runs_q = await session.execute(
        select(AgentRun)
        .where(AgentRun.agent_id == agent.id)
        .order_by(AgentRun.created_at.desc())
        .limit(20)
    )
    runs = [
        {
            "id": str(r.id),
            "status": r.status,
            "input": r.input or {},
            "started_at": r.started_at.isoformat() if r.started_at else None,
        }
        for r in runs_q.scalars().all()
    ]

    return templates.TemplateResponse(
        request,
        "outreach_dashboard.html",
        {
            "runs": runs,
            "hitl": hitl,
            "api_key": settings.api_key,
        },
    )

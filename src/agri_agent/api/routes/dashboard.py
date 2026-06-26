"""Dashboard UI — server-rendered HTML for the Order Dispatch demo."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agri_agent.config.settings import settings
from agri_agent.db.models import Order, PlatformSettings
from agri_agent.db.session import get_session

router = APIRouter(tags=["dashboard"])

_templates_dir = Path(__file__).parent.parent.parent / "templates"
templates = Jinja2Templates(directory=str(_templates_dir))

_SETTING_DEFAULTS = {
    "ai_automation_enabled": False,
    "active_dispatch_agent": "order-dispatch-review",
}


async def _get_setting(session: AsyncSession, key: str):
    result = await session.execute(
        select(PlatformSettings).where(PlatformSettings.key == key)
    )
    row = result.scalar_one_or_none()
    return row.value if row else _SETTING_DEFAULTS.get(key)


def _urgency_days(due: date) -> int:
    return (due - date.today()).days


def _enrich(o: Order) -> dict:
    return {
        "id": str(o.id),
        "order_ref": o.order_ref,
        "retailer_name": o.retailer_name,
        "medicine_name": o.medicine_name,
        "quantity": o.quantity,
        "order_amount_usd": o.order_amount_usd,
        "margin_percent": o.margin_percent,
        "urgency_days": _urgency_days(o.due_date),
        "status": o.status,
        "shipment_mode": o.shipment_mode,
        "decided_by": o.decided_by,
        "ai_recommended_mode": o.ai_recommended_mode,
        "ai_confidence": o.ai_confidence,
        "ai_reasoning": o.ai_reasoning,
    }


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    ai_enabled = await _get_setting(session, "ai_automation_enabled")
    active_agent = await _get_setting(session, "active_dispatch_agent")

    pending_q = await session.execute(
        select(Order)
        .where(Order.status == "pending")
        .order_by(Order.due_date.asc(), Order.order_amount_usd.desc())
    )
    ready_q = await session.execute(
        select(Order)
        .where(Order.status == "ready_to_dispatch")
        .order_by(Order.order_amount_usd.desc())
    )

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "pending": [_enrich(o) for o in pending_q.scalars().all()],
            "ready": [_enrich(o) for o in ready_q.scalars().all()],
            "ai_automation_enabled": bool(ai_enabled),
            "active_agent": active_agent,
            "api_key": settings.api_key,
        },
    )

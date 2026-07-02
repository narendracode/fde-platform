"""Dashboard UI — server-rendered HTML for the Order Dispatch demo."""

from __future__ import annotations

from datetime import date
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agri_agent.api._templates import templates
from agri_agent.config.settings import settings
from agri_agent.db.models import Order
from agri_agent.db.session import get_session

router = APIRouter(tags=["dashboard"])


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
            "api_key": settings.api_key,
            "active_page": "fundly_orders",
        },
    )

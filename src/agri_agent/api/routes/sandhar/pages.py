"""Sandhar UI page routes."""
from __future__ import annotations

import uuid
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agri_agent.api._templates import templates
from agri_agent.api.dependencies import verify_api_key
from agri_agent.config.settings import settings
from agri_agent.db.models import SandharPlanDetail, SandharPlanHeader, SandharLine, SandharWorkOrder
from agri_agent.db.session import get_session

router = APIRouter(tags=["sandhar-ui"])


@router.get("/sandhar", response_class=HTMLResponse)
async def sandhar_dashboard(request: Request):
    return templates.TemplateResponse(request, "sandhar/dashboard.html", {
        "api_key": settings.api_key,
        "today": date.today().isoformat(),
        "active_page": "sandhar_dashboard",
    })


@router.get("/sandhar/plan", response_class=HTMLResponse)
async def sandhar_plan(request: Request):
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    return templates.TemplateResponse(request, "sandhar/plan.html", {
        "api_key": settings.api_key,
        "default_date": tomorrow,
        "today": date.today().isoformat(),
        "active_page": "sandhar_plan",
    })


@router.get("/sandhar/floor", response_class=HTMLResponse)
async def sandhar_floor(request: Request):
    return templates.TemplateResponse(request, "sandhar/floor.html", {
        "api_key": settings.api_key,
        "today": date.today().isoformat(),
        "active_page": "sandhar_floor",
    })


@router.get("/sandhar/master", response_class=HTMLResponse)
async def sandhar_master(request: Request):
    return templates.TemplateResponse(request, "sandhar/master.html", {
        "api_key": settings.api_key,
        "active_page": "sandhar_master",
    })


@router.get("/sandhar/plan/{header_id}/refine-preview", response_class=HTMLResponse)
async def sandhar_plan_refine_preview(
    header_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Server-rendered preview partial for the 'Refine with AI' canvas.

    Returns an HTML fragment (not a full page) showing the current plan state.
    Called by the canvas JS `refreshPreview()` after each agent turn.
    """
    try:
        hid = uuid.UUID(header_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid header_id")

    header = (await session.execute(
        select(SandharPlanHeader).where(SandharPlanHeader.id == hid)
    )).scalar_one_or_none()
    if not header:
        raise HTTPException(status_code=404, detail="Plan header not found")

    details_rows = (await session.execute(
        select(SandharPlanDetail).where(SandharPlanDetail.plan_header_id == hid)
    )).scalars().all()

    # Enrich details with line_code and wo_number
    enriched = []
    for d in details_rows:
        entry: dict = {
            "detail_id": str(d.id),
            "line_id": str(d.line_id) if d.line_id else None,
            "wo_id": str(d.wo_id) if d.wo_id else None,
            "planned_qty": d.planned_qty or 0,
            "planned_manpower": d.planned_manpower,
            "available_manpower": d.available_manpower,
            "manpower_gap": d.manpower_gap,
            "order_qty": 0,
        }
        if d.line_id:
            line = (await session.execute(
                select(SandharLine).where(SandharLine.id == d.line_id)
            )).scalar_one_or_none()
            if line:
                entry["line_code"] = line.line_code
        if d.wo_id:
            wo = (await session.execute(
                select(SandharWorkOrder).where(SandharWorkOrder.id == d.wo_id)
            )).scalar_one_or_none()
            if wo:
                entry["wo_number"] = wo.wo_number
                entry["order_qty"] = wo.order_qty or 0
        enriched.append(entry)

    total_qty = sum(d["planned_qty"] for d in enriched)
    plan_ctx = {
        "shift_code": header.shift_code,
        "plan_date": header.plan_date.isoformat(),
        "confidence": header.confidence,
        "status": header.status,
    }
    return templates.TemplateResponse(
        request,
        "sandhar/_refine_preview_sandhar-plan.html",
        {
            "plan": plan_ctx,
            "details": enriched,
            "total_qty": total_qty,
            "changed_ids": [],
        },
    )


@router.get("/sandhar/simulation", response_class=HTMLResponse)
async def sandhar_simulation(request: Request):
    return templates.TemplateResponse(request, "sandhar/simulation.html", {
        "api_key": settings.api_key,
        "today": date.today().isoformat(),
        "active_page": "sandhar_simulation",
    })

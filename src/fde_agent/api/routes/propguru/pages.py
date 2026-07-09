"""Propguru UI page routes."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from fde_agent.api._templates import templates
from fde_agent.api.dependencies import verify_api_key
from fde_agent.config.settings import settings
from fde_agent.db.models import (
    PropguruEvaluationCriteria,
    PropguruEvaluationReport,
    PropguruEvaluationScore,
)
from fde_agent.db.session import get_session

router = APIRouter(tags=["propguru-ui"])


@router.get("/propguru", response_class=HTMLResponse)
async def propguru_dashboard(request: Request):
    return templates.TemplateResponse(request, "propguru/dashboard.html", {
        "api_key": settings.api_key,
        "active_page": "propguru_dashboard",
    })


@router.get("/propguru/deals", response_class=HTMLResponse)
async def propguru_deals(request: Request):
    return templates.TemplateResponse(request, "propguru/deals.html", {
        "api_key": settings.api_key,
        "active_page": "propguru_deals",
    })


@router.get("/propguru/evaluation", response_class=HTMLResponse)
async def propguru_evaluation(request: Request):
    return templates.TemplateResponse(request, "propguru/evaluation.html", {
        "api_key": settings.api_key,
        "active_page": "propguru_evaluation",
    })


@router.get("/propguru/master", response_class=HTMLResponse)
async def propguru_master(request: Request):
    return templates.TemplateResponse(request, "propguru/master.html", {
        "api_key": settings.api_key,
        "active_page": "propguru_master",
    })


@router.get("/propguru/simulation", response_class=HTMLResponse)
async def propguru_simulation(request: Request):
    return templates.TemplateResponse(request, "propguru/simulation.html", {
        "api_key": settings.api_key,
        "active_page": "propguru_simulation",
    })


@router.get("/propguru/evaluation/{report_id}/refine-preview", response_class=HTMLResponse)
async def propguru_evaluation_refine_preview(
    report_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Server-rendered preview partial for the 'Refine with AI' canvas."""
    try:
        rid = uuid.UUID(report_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid report_id")

    report = (await session.execute(
        select(PropguruEvaluationReport).where(PropguruEvaluationReport.id == rid)
    )).scalar_one_or_none()
    if not report:
        raise HTTPException(status_code=404, detail="Evaluation report not found")

    scores = (await session.execute(
        select(PropguruEvaluationScore).where(PropguruEvaluationScore.report_id == rid)
    )).scalars().all()

    # Enrich scores with criterion info, sort by weight desc
    enriched = []
    for s in scores:
        crit = (await session.execute(
            select(PropguruEvaluationCriteria).where(PropguruEvaluationCriteria.id == s.criterion_id)
        )).scalar_one_or_none()
        enriched.append({
            "score_id": str(s.id),
            "criterion_code": crit.criterion_code if crit else "?",
            "criterion_name": crit.name if crit else "Unknown",
            "category": crit.category if crit else "",
            "weight": crit.weight if crit else 0,
            "scoring_type": crit.scoring_type if crit else "",
            "score": s.score,
            "raw_value": s.raw_value or "",
            "source": s.source,
            "notes": s.notes or "",
        })
    enriched.sort(key=lambda x: x["weight"], reverse=True)

    # Category subtotals
    cat_totals: dict = {}
    for item in enriched:
        cat = item["category"]
        if cat not in cat_totals:
            cat_totals[cat] = {"weighted_sum": 0.0, "total_weight": 0.0, "count": 0}
        w = item["weight"]
        if item["scoring_type"] == "boolean":
            norm = min(1.0, max(0.0, item["score"]))
        elif item["scoring_type"] == "scale_1_5":
            norm = min(1.0, max(0.0, (item["score"] - 1) / 4.0))
        else:  # proximity_km
            norm = min(1.0, max(0.0, item["score"] / 5.0))
        cat_totals[cat]["weighted_sum"] += w * norm
        cat_totals[cat]["total_weight"] += w
        cat_totals[cat]["count"] += 1

    cat_scores = {
        cat: round(v["weighted_sum"] / v["total_weight"] * 100, 1) if v["total_weight"] > 0 else 0
        for cat, v in cat_totals.items()
    }

    return templates.TemplateResponse(
        request,
        "propguru/_refine_preview_propguru-evaluation.html",
        {
            "report": {
                "id": str(report.id),
                "status": report.status,
                "market_rate_per_sqft": report.market_rate_per_sqft,
                "base_price": report.base_price,
                "score_factor": report.score_factor,
                "price_premium_pct": report.price_premium_pct,
                "recommended_price": report.recommended_price,
                "final_price": report.final_price,
                "confidence": report.confidence,
            },
            "scores": enriched[:10],  # top 10 by weight
            "total_scored": len(enriched),
            "cat_scores": cat_scores,
        },
    )

"""Sandhar KPI endpoints."""
from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from fde_agent.api.dependencies import verify_api_key
from fde_agent.db.models import SandharDailyKpi
from fde_agent.db.session import get_session

router = APIRouter(prefix="/api/v1/sandhar", tags=["sandhar"])


# ── Serializer ─────────────────────────────────────────────────────────────────

def _kpi_out(k: SandharDailyKpi) -> dict[str, Any]:
    return {
        "id": str(k.id),
        "kpi_date": k.kpi_date.isoformat(),
        "shift_code": k.shift_code,
        "total_planned_qty": k.total_planned_qty,
        "total_produced_qty": k.total_produced_qty,
        "plan_achievement_pct": k.plan_achievement_pct,
        "manpower_utilization_pct": k.manpower_utilization_pct,
        "line_utilization_pct": k.line_utilization_pct,
        "rejection_rate_pct": k.rejection_rate_pct,
        "total_downtime_minutes": k.total_downtime_minutes,
        "oee": k.oee,
        "skill_gap_count": k.skill_gap_count,
        "active_alert_count": k.active_alert_count,
        "created_at": k.created_at.isoformat(),
        "updated_at": k.updated_at.isoformat(),
    }


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.get("/kpi/daily")
async def get_daily_kpi(
    date: str = Query(...),
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Return all KPI records for a given date."""
    try:
        from datetime import date as _date
        kpi_date = _date.fromisoformat(date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format, use YYYY-MM-DD")

    rows = await session.execute(
        select(SandharDailyKpi)
        .where(SandharDailyKpi.kpi_date == kpi_date)
        .order_by(SandharDailyKpi.shift_code)
    )
    return [_kpi_out(k) for k in rows.scalars().all()]


@router.get("/kpi/trend")
async def get_kpi_trend(
    metric: str = Query(...),
    from_date: str = Query(...),
    to_date: str = Query(...),
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Return trend data for a specific KPI metric over a date range."""
    try:
        from datetime import date as _date
        start = _date.fromisoformat(from_date)
        end = _date.fromisoformat(to_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format, use YYYY-MM-DD")

    _valid_metrics = {
        "total_planned_qty",
        "total_produced_qty",
        "plan_achievement_pct",
        "manpower_utilization_pct",
        "line_utilization_pct",
        "rejection_rate_pct",
        "total_downtime_minutes",
        "oee",
        "skill_gap_count",
        "active_alert_count",
    }
    if metric not in _valid_metrics:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid metric '{metric}'. Valid metrics: {sorted(_valid_metrics)}",
        )

    rows = await session.execute(
        select(SandharDailyKpi)
        .where(
            and_(
                SandharDailyKpi.kpi_date >= start,
                SandharDailyKpi.kpi_date <= end,
            )
        )
        .order_by(SandharDailyKpi.kpi_date, SandharDailyKpi.shift_code)
    )
    kpis = rows.scalars().all()

    return [
        {
            "kpi_date": k.kpi_date.isoformat(),
            "shift_code": k.shift_code,
            "value": getattr(k, metric),
        }
        for k in kpis
    ]

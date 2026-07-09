"""Sandhar alerts endpoints."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, and_, func
from sqlalchemy.ext.asyncio import AsyncSession

from fde_agent.api.dependencies import verify_api_key
from fde_agent.db.models import SandharAlert
from fde_agent.db.session import get_session

router = APIRouter(prefix="/api/v1/sandhar", tags=["sandhar"])


# ── Serializer ─────────────────────────────────────────────────────────────────

def _alert_out(a: SandharAlert) -> dict[str, Any]:
    return {
        "id": str(a.id),
        "alert_type": a.alert_type,
        "alert_message": a.alert_message,
        "severity": a.severity,
        "status": a.status,
        "plan_date": a.plan_date.isoformat() if a.plan_date else None,
        "shift_code": a.shift_code,
        "related_line_id": str(a.related_line_id) if a.related_line_id else None,
        "related_wo_id": str(a.related_wo_id) if a.related_wo_id else None,
        "related_employee_id": str(a.related_employee_id) if a.related_employee_id else None,
        "related_machine_id": str(a.related_machine_id) if a.related_machine_id else None,
        "acknowledged_by": a.acknowledged_by,
        "acknowledged_at": a.acknowledged_at.isoformat() if a.acknowledged_at else None,
        "resolved_by": a.resolved_by,
        "resolved_at": a.resolved_at.isoformat() if a.resolved_at else None,
        "created_at": a.created_at.isoformat(),
    }


# ── Pydantic schemas ───────────────────────────────────────────────────────────

class AcknowledgeRequest(BaseModel):
    acknowledged_by: str


class ResolveRequest(BaseModel):
    resolved_by: str


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.get("/alerts/active-count")
async def get_active_alert_count(
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Count active alerts by severity."""
    rows = await session.execute(
        select(
            SandharAlert.severity,
            func.count(SandharAlert.id).label("cnt"),
        )
        .where(SandharAlert.status == "active")
        .group_by(SandharAlert.severity)
    )
    counts: dict[str, int] = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
    for row in rows:
        severity = (row.severity or "").lower()
        if severity in counts:
            counts[severity] = row.cnt

    counts["total"] = sum(counts.values())
    return counts


@router.get("/alerts")
async def list_alerts(
    status: str | None = Query(None),
    severity: str | None = Query(None),
    plan_date: str | None = Query(None),
    shift_code: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """List alerts with optional filters, ordered by created_at desc."""
    q = select(SandharAlert).order_by(SandharAlert.created_at.desc()).limit(limit)

    if status:
        q = q.where(SandharAlert.status == status)
    if severity:
        q = q.where(SandharAlert.severity == severity)
    if plan_date:
        try:
            pdate = date.fromisoformat(plan_date)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid plan_date format, use YYYY-MM-DD")
        q = q.where(SandharAlert.plan_date == pdate)
    if shift_code:
        q = q.where(SandharAlert.shift_code == shift_code)

    rows = await session.execute(q)
    return [_alert_out(a) for a in rows.scalars().all()]


@router.post("/alerts/{alert_id}/acknowledge")
async def acknowledge_alert(
    alert_id: str,
    req: AcknowledgeRequest,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Acknowledge an alert."""
    try:
        aid = uuid.UUID(alert_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid alert ID format")

    result = await session.execute(select(SandharAlert).where(SandharAlert.id == aid))
    alert = result.scalar_one_or_none()
    if not alert:
        raise HTTPException(status_code=404, detail=f"Alert '{alert_id}' not found")

    alert.status = "acknowledged"
    alert.acknowledged_by = req.acknowledged_by
    alert.acknowledged_at = datetime.now(timezone.utc)

    await session.commit()
    await session.refresh(alert)
    return _alert_out(alert)


@router.post("/alerts/{alert_id}/resolve")
async def resolve_alert(
    alert_id: str,
    req: ResolveRequest,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Resolve an alert."""
    try:
        aid = uuid.UUID(alert_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid alert ID format")

    result = await session.execute(select(SandharAlert).where(SandharAlert.id == aid))
    alert = result.scalar_one_or_none()
    if not alert:
        raise HTTPException(status_code=404, detail=f"Alert '{alert_id}' not found")

    alert.status = "resolved"
    alert.resolved_by = req.resolved_by
    alert.resolved_at = datetime.now(timezone.utc)

    await session.commit()
    await session.refresh(alert)
    return _alert_out(alert)

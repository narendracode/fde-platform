"""Sandhar execution and actuals endpoints."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, and_, func
from sqlalchemy.ext.asyncio import AsyncSession

from fde_agent.api.dependencies import verify_api_key
from fde_agent.db.models import (
    SandharAlert,
    SandharCustomer,
    SandharDailyKpi,
    SandharEmployee,
    SandharEmployeeSkill,
    SandharLine,
    SandharPlanDetail,
    SandharPlanHeader,
    SandharProduct,
    SandharProductionActual,
    SandharResourceAllocation,
    SandharWorkOrder,
)
from fde_agent.db.session import get_session

router = APIRouter(prefix="/api/v1/sandhar", tags=["sandhar"])


async def _line_id_from(session: AsyncSession, value: str) -> uuid.UUID | None:
    """Accept UUID or line_code (e.g. 'L001'). Returns None if not found."""
    try:
        uid = uuid.UUID(value)
        row = await session.execute(select(SandharLine).where(SandharLine.id == uid))
    except ValueError:
        row = await session.execute(select(SandharLine).where(SandharLine.line_code == value))
    obj = row.scalar_one_or_none()
    return obj.id if obj else None


# ── Serializers ────────────────────────────────────────────────────────────────

def _detail_out(d: SandharPlanDetail) -> dict[str, Any]:
    return {
        "id": str(d.id),
        "plan_header_id": str(d.plan_header_id),
        "wo_id": str(d.wo_id) if d.wo_id else None,
        "product_id": str(d.product_id) if d.product_id else None,
        "line_id": str(d.line_id) if d.line_id else None,
        "planned_qty": d.planned_qty,
        "planned_manpower": d.planned_manpower,
        "available_manpower": d.available_manpower,
        "manpower_gap": d.manpower_gap,
        "supervisor_employee_id": str(d.supervisor_employee_id) if d.supervisor_employee_id else None,
        "start_time": d.start_time.isoformat() if d.start_time else None,
        "end_time": d.end_time.isoformat() if d.end_time else None,
        "status": d.status,
        "created_at": d.created_at.isoformat(),
        "updated_at": d.updated_at.isoformat(),
    }


def _actual_out(a: SandharProductionActual) -> dict[str, Any]:
    return {
        "id": str(a.id),
        "plan_detail_id": str(a.plan_detail_id),
        "shift_code": a.shift_code,
        "produced_qty": a.produced_qty,
        "rejected_qty": a.rejected_qty,
        "rework_qty": a.rework_qty,
        "downtime_minutes": a.downtime_minutes,
        "achievement_pct": a.achievement_pct,
        "submitted_by": a.submitted_by,
        "submitted_at": a.submitted_at.isoformat() if a.submitted_at else None,
        "created_at": a.created_at.isoformat(),
        "updated_at": a.updated_at.isoformat(),
    }


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
        "created_at": a.created_at.isoformat(),
    }


# ── Pydantic schemas ───────────────────────────────────────────────────────────

class ActualsRequest(BaseModel):
    produced_qty: int
    rejected_qty: int = 0
    rework_qty: int = 0
    downtime_minutes: int = 0
    submitted_by: str = "supervisor"


class DisruptionRequest(BaseModel):
    alert_type: str
    alert_message: str
    severity: str
    plan_date: str
    shift_code: str
    related_line_id: str | None = None
    related_machine_id: str | None = None


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.get("/execution/supervisor-view")
async def supervisor_view(
    line_id: str = Query(...),
    shift_code: str = Query(...),
    plan_date: str = Query(...),
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Get supervisor view for a line, shift, and date."""
    lid = await _line_id_from(session, line_id)
    if lid is None:
        raise HTTPException(status_code=404, detail=f"Line '{line_id}' not found")

    try:
        pdate = date.fromisoformat(plan_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid plan_date format, use YYYY-MM-DD")

    # Find the latest approved plan header for this date + shift
    header_result = await session.execute(
        select(SandharPlanHeader)
        .where(
            and_(
                SandharPlanHeader.plan_date == pdate,
                SandharPlanHeader.shift_code == shift_code,
                SandharPlanHeader.status == "approved",
            )
        )
        .order_by(SandharPlanHeader.version.desc())
        .limit(1)
    )
    header = header_result.scalar_one_or_none()

    if not header:
        return {
            "plan_date": plan_date,
            "shift_code": shift_code,
            "line_id": line_id,
            "header": None,
            "details": [],
            "operators": [],
            "message": "No approved plan found for this date/shift/line",
        }

    # Get plan details for this header + line
    details_result = await session.execute(
        select(SandharPlanDetail).where(
            and_(
                SandharPlanDetail.plan_header_id == header.id,
                SandharPlanDetail.line_id == lid,
            )
        )
    )
    details = [_detail_out(d) for d in details_result.scalars().all()]

    # Fallback: if approved header has no details, look for the most recent header
    # that does have details for this line (handles re-run where second pass got
    # approved without writing new plan details)
    if not details:
        fallback_result = await session.execute(
            select(SandharPlanHeader)
            .where(
                and_(
                    SandharPlanHeader.plan_date == pdate,
                    SandharPlanHeader.shift_code == shift_code,
                )
            )
            .order_by(SandharPlanHeader.version.desc())
        )
        for fallback_header in fallback_result.scalars().all():
            if fallback_header.id == header.id:
                continue
            fb_details_result = await session.execute(
                select(SandharPlanDetail).where(
                    and_(
                        SandharPlanDetail.plan_header_id == fallback_header.id,
                        SandharPlanDetail.line_id == lid,
                    )
                )
            )
            fb_details = [_detail_out(d) for d in fb_details_result.scalars().all()]
            if fb_details:
                details = fb_details
                break

    # Get resource allocations for this date + shift + line
    alloc_result = await session.execute(
        select(SandharResourceAllocation).where(
            and_(
                SandharResourceAllocation.plan_date == pdate,
                SandharResourceAllocation.shift_code == shift_code,
                SandharResourceAllocation.line_id == lid,
            )
        )
    )
    allocations = alloc_result.scalars().all()

    # Enrich details with WO / product / customer / supervisor info
    enriched_details = []
    for det in details:
        entry = dict(det)
        if det.get("wo_id"):
            wo_r = await session.execute(select(SandharWorkOrder).where(SandharWorkOrder.id == uuid.UUID(det["wo_id"])))
            wo = wo_r.scalar_one_or_none()
            if wo:
                entry["wo_number"] = wo.wo_number
                entry["priority"] = wo.priority
                entry["due_date"] = wo.due_date.isoformat()
                entry["order_qty"] = wo.order_qty
                if wo.product_id:
                    prod_r = await session.execute(select(SandharProduct).where(SandharProduct.id == wo.product_id))
                    prod = prod_r.scalar_one_or_none()
                    if prod:
                        entry["product_code"] = prod.product_code
                        entry["product_name"] = prod.product_name
                        entry["standard_cycle_time"] = prod.standard_cycle_time
                        entry["standard_manpower"] = prod.standard_manpower
                        if prod.standard_cycle_time and prod.standard_cycle_time > 0:
                            entry["target_rate_per_hour"] = round(60 / prod.standard_cycle_time, 1)
                if wo.customer_id:
                    cust_r = await session.execute(select(SandharCustomer).where(SandharCustomer.id == wo.customer_id))
                    cust = cust_r.scalar_one_or_none()
                    if cust:
                        entry["customer_name"] = cust.customer_name
                        entry["customer_priority"] = cust.priority_level
        if det.get("supervisor_employee_id"):
            sup_r = await session.execute(select(SandharEmployee).where(SandharEmployee.id == uuid.UUID(det["supervisor_employee_id"])))
            sup = sup_r.scalar_one_or_none()
            if sup:
                entry["supervisor_name"] = sup.name
                entry["supervisor_code"] = sup.employee_code
        enriched_details.append(entry)

    # Enrich operators with employee info and skill level
    operator_list = []
    for alloc in allocations:
        emp_result = await session.execute(
            select(SandharEmployee).where(SandharEmployee.id == alloc.employee_id)
        )
        emp = emp_result.scalar_one_or_none()
        skill_level = None
        if emp:
            skill_r = await session.execute(
                select(SandharEmployeeSkill).where(
                    and_(
                        SandharEmployeeSkill.employee_id == emp.id,
                        SandharEmployeeSkill.line_id == lid,
                        SandharEmployeeSkill.active_flag == True,
                    )
                ).limit(1)
            )
            skill = skill_r.scalar_one_or_none()
            skill_level = skill.skill_level if skill else None
        operator_list.append({
            "employee_id": str(alloc.employee_id),
            "employee_code": emp.employee_code if emp else None,
            "name": emp.name if emp else None,
            "designation": emp.designation if emp else None,
            "grade": emp.grade if emp else None,
            "shift_group": emp.shift_group if emp else None,
            "skill_level": skill_level,
            "allocation_status": alloc.allocation_status,
        })

    return {
        "plan_date": plan_date,
        "shift_code": shift_code,
        "line_id": line_id,
        "header": {
            "id": str(header.id),
            "version": header.version,
            "status": header.status,
            "confidence": header.confidence,
            "approved_at": header.approved_at.isoformat() if header.approved_at else None,
        },
        "details": enriched_details,
        "operators": operator_list,
    }


@router.post("/execution/{plan_detail_id}/acknowledge")
async def acknowledge_plan_detail(
    plan_detail_id: str,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Acknowledge a plan detail — sets status to in_progress."""
    try:
        did = uuid.UUID(plan_detail_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid plan_detail_id format")

    result = await session.execute(
        select(SandharPlanDetail).where(SandharPlanDetail.id == did)
    )
    detail = result.scalar_one_or_none()
    if not detail:
        raise HTTPException(status_code=404, detail=f"Plan detail '{plan_detail_id}' not found")

    detail.status = "in_progress"
    await session.commit()
    await session.refresh(detail)
    return _detail_out(detail)


@router.post("/execution/{plan_detail_id}/actuals")
async def submit_actuals(
    plan_detail_id: str,
    req: ActualsRequest,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Submit production actuals for a plan detail line."""
    try:
        did = uuid.UUID(plan_detail_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid plan_detail_id format")

    # 1. Get plan detail
    detail_result = await session.execute(
        select(SandharPlanDetail).where(SandharPlanDetail.id == did)
    )
    detail = detail_result.scalar_one_or_none()
    if not detail:
        raise HTTPException(status_code=404, detail=f"Plan detail '{plan_detail_id}' not found")

    # 2. Compute achievement
    planned_qty = detail.planned_qty or 0
    achievement_pct = (req.produced_qty / planned_qty * 100.0) if planned_qty > 0 else 0.0

    # 3. Create production actual
    actual = SandharProductionActual(
        plan_detail_id=did,
        shift_code=None,
        produced_qty=req.produced_qty,
        rejected_qty=req.rejected_qty,
        rework_qty=req.rework_qty,
        downtime_minutes=req.downtime_minutes,
        achievement_pct=round(achievement_pct, 2),
        submitted_by=req.submitted_by,
        submitted_at=datetime.now(timezone.utc),
    )
    session.add(actual)

    # 4. Update plan detail status
    detail.status = "completed"

    alert_created = False

    # 5. Create alert if underperforming
    if achievement_pct < 70:
        alert = SandharAlert(
            alert_type="production_delay",
            alert_message=f"Production achievement {achievement_pct:.1f}% is below 70% threshold for plan detail {plan_detail_id}",
            severity="high",
            status="active",
            related_line_id=detail.line_id,
            related_wo_id=detail.wo_id,
        )
        session.add(alert)
        alert_created = True

    # 6. Update or create KPI record for this header's date + shift
    header_result = await session.execute(
        select(SandharPlanHeader).where(SandharPlanHeader.id == detail.plan_header_id)
    )
    header = header_result.scalar_one_or_none()

    if header:
        # Sum all actuals for this header's plan details
        actuals_sum_result = await session.execute(
            select(
                func.sum(SandharProductionActual.produced_qty).label("total_produced"),
                func.sum(SandharPlanDetail.planned_qty).label("total_planned"),
            )
            .join(SandharPlanDetail, SandharProductionActual.plan_detail_id == SandharPlanDetail.id)
            .where(SandharPlanDetail.plan_header_id == header.id)
        )
        sums = actuals_sum_result.one_or_none()
        total_produced = sums.total_produced or 0 if sums else 0
        total_planned = sums.total_planned or 0 if sums else 0
        kpi_achievement = (total_produced / total_planned * 100.0) if total_planned > 0 else 0.0

        # Upsert KPI
        kpi_result = await session.execute(
            select(SandharDailyKpi).where(
                and_(
                    SandharDailyKpi.kpi_date == header.plan_date,
                    SandharDailyKpi.shift_code == header.shift_code,
                )
            )
        )
        kpi = kpi_result.scalar_one_or_none()
        if kpi:
            kpi.total_produced_qty = total_produced
            kpi.total_planned_qty = total_planned
            kpi.plan_achievement_pct = round(kpi_achievement, 2)
        else:
            kpi = SandharDailyKpi(
                kpi_date=header.plan_date,
                shift_code=header.shift_code,
                total_planned_qty=total_planned,
                total_produced_qty=total_produced,
                plan_achievement_pct=round(kpi_achievement, 2),
            )
            session.add(kpi)

    await session.commit()

    return {
        "plan_detail_id": plan_detail_id,
        "achievement_pct": round(achievement_pct, 2),
        "alert_created": alert_created,
    }


@router.post("/execution/disruption")
async def report_disruption(
    req: DisruptionRequest,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Report a disruption event — creates a SandharAlert."""
    try:
        plan_date = date.fromisoformat(req.plan_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid plan_date format, use YYYY-MM-DD")

    related_line_id = await _line_id_from(session, req.related_line_id) if req.related_line_id else None

    related_machine_id = None
    if req.related_machine_id:
        try:
            related_machine_id = uuid.UUID(req.related_machine_id)
        except ValueError:
            pass  # silently drop invalid machine ID — alert still created

    alert = SandharAlert(
        alert_type=req.alert_type,
        alert_message=req.alert_message,
        severity=req.severity,
        status="active",
        plan_date=plan_date,
        shift_code=req.shift_code,
        related_line_id=related_line_id,
        related_machine_id=related_machine_id,
    )
    session.add(alert)
    await session.commit()
    await session.refresh(alert)
    return _alert_out(alert)

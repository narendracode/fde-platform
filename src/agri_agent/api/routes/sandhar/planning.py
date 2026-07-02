"""Sandhar production plan endpoints."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, and_, desc
from sqlalchemy.ext.asyncio import AsyncSession

from agri_agent.api.dependencies import verify_api_key
from agri_agent.db.models import (
    Agent,
    AgentRun,
    SandharAlert,
    SandharAttendance,
    SandharEmployee,
    SandharLine,
    SandharMachine,
    SandharPlanDetail,
    SandharPlanHeader,
    SandharResourceAllocation,
    SandharWorkOrder,
)
from agri_agent.db.session import get_session

router = APIRouter(prefix="/api/v1/sandhar", tags=["sandhar"])


# ── Serializers ────────────────────────────────────────────────────────────────

def _header_out(h: SandharPlanHeader) -> dict[str, Any]:
    return {
        "id": str(h.id),
        "plan_date": h.plan_date.isoformat(),
        "shift_code": h.shift_code,
        "version": h.version,
        "status": h.status,
        "confidence": h.confidence,
        "planner_id": h.planner_id,
        "approved_at": h.approved_at.isoformat() if h.approved_at else None,
        "created_at": h.created_at.isoformat(),
        "updated_at": h.updated_at.isoformat(),
    }


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


def _allocation_out(a: SandharResourceAllocation) -> dict[str, Any]:
    return {
        "id": str(a.id),
        "plan_date": a.plan_date.isoformat(),
        "shift_code": a.shift_code,
        "employee_id": str(a.employee_id),
        "line_id": str(a.line_id) if a.line_id else None,
        "machine_id": str(a.machine_id) if a.machine_id else None,
        "wo_id": str(a.wo_id) if a.wo_id else None,
        "allocation_status": a.allocation_status,
        "plan_header_id": str(a.plan_header_id) if a.plan_header_id else None,
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
        "related_employee_id": str(a.related_employee_id) if a.related_employee_id else None,
        "related_machine_id": str(a.related_machine_id) if a.related_machine_id else None,
        "acknowledged_by": a.acknowledged_by,
        "acknowledged_at": a.acknowledged_at.isoformat() if a.acknowledged_at else None,
        "resolved_by": a.resolved_by,
        "resolved_at": a.resolved_at.isoformat() if a.resolved_at else None,
        "created_at": a.created_at.isoformat(),
    }


# ── Pydantic schemas ───────────────────────────────────────────────────────────

class GeneratePlanRequest(BaseModel):
    plan_date: str
    shifts: str = "A,B,C"
    override_context: dict = {}


class ApprovePlanRequest(BaseModel):
    planner_id: str = "human"


class RejectPlanRequest(BaseModel):
    reason: str | None = None


class PlanHeaderCreate(BaseModel):
    plan_date: str
    shift_code: str
    confidence: str = "medium"


class AllocateLineRequest(BaseModel):
    line_id: str
    wo_id: str
    shift_code: str
    plan_date: str
    operator_ids: list[str]
    supervisor_id: str
    planned_qty: int | None = None  # computed by sandhar_calculate_planned_qty


class CreateAlertRequest(BaseModel):
    alert_type: str
    alert_message: str
    severity: str
    plan_date: str | None = None
    shift_code: str | None = None
    related_line_id: str | None = None
    related_wo_id: str | None = None
    related_employee_id: str | None = None
    related_machine_id: str | None = None


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.post("/plan/generate")
async def generate_plan(
    req: GeneratePlanRequest,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Validate data and dispatch a planning agent run."""
    # 1. Check open WOs exist
    wo_result = await session.execute(
        select(SandharWorkOrder).where(
            and_(
                SandharWorkOrder.status == "open",
                SandharWorkOrder.quality_hold == False,
            )
        ).limit(1)
    )
    if not wo_result.scalar_one_or_none():
        raise HTTPException(
            status_code=422,
            detail="No open work orders found. Create work orders before generating a plan.",
        )

    # 2. Check attendance exists for plan_date
    try:
        plan_date = date.fromisoformat(req.plan_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid plan_date format, use YYYY-MM-DD")

    att_result = await session.execute(
        select(SandharAttendance).where(
            SandharAttendance.attendance_date == plan_date
        ).limit(1)
    )
    if not att_result.scalar_one_or_none():
        raise HTTPException(
            status_code=422,
            detail=f"No attendance records found for {req.plan_date}. Upload attendance data first.",
        )

    # 3. Lookup planning agent
    agent_result = await session.execute(
        select(Agent).where(Agent.name == "sandhar-planning-supervisor")
    )
    agent = agent_result.scalar_one_or_none()
    if not agent:
        raise HTTPException(
            status_code=404,
            detail="Planning agent 'sandhar-planning-supervisor' not found. Register the agent first.",
        )

    # 4. Create AgentRun
    run = AgentRun(
        agent_id=agent.id,
        status="pending",
        input={
            "message": f"Generate production plan for {req.plan_date}",
            "extra_context": {
                "plan_date": req.plan_date,
                "shifts": req.shifts,
            },
        },
    )
    session.add(run)
    await session.commit()
    await session.refresh(run)

    # 5. Dispatch celery task
    try:
        from agri_agent.queue.tasks import run_agent_task
        run_agent_task.delay(
            str(run.id),
            "sandhar-planning-supervisor",
            f"Generate production plan for {req.plan_date}, shifts: {req.shifts}",
            {"plan_date": req.plan_date, "shifts": req.shifts},
        )
    except Exception as e:
        # Don't fail if celery is unavailable — run is queued in DB
        pass

    return {
        "agent_run_id": str(run.id),
        "plan_date": req.plan_date,
        "shifts": req.shifts,
        "status": "queued",
    }


@router.get("/plan/versions")
async def list_plan_versions(
    date: str = Query(...),
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """List all plan header versions for a given date."""
    try:
        from datetime import date as _date
        plan_date = _date.fromisoformat(date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format, use YYYY-MM-DD")

    rows = await session.execute(
        select(SandharPlanHeader)
        .where(SandharPlanHeader.plan_date == plan_date)
        .order_by(desc(SandharPlanHeader.version))
    )
    return [_header_out(h) for h in rows.scalars().all()]


@router.get("/plan/{header_id}")
async def get_plan_detail(
    header_id: str,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Get full plan including details and resource allocations."""
    try:
        hid = uuid.UUID(header_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid header ID format")

    header_result = await session.execute(
        select(SandharPlanHeader).where(SandharPlanHeader.id == hid)
    )
    header = header_result.scalar_one_or_none()
    if not header:
        raise HTTPException(status_code=404, detail=f"Plan header '{header_id}' not found")

    details_result = await session.execute(
        select(SandharPlanDetail).where(SandharPlanDetail.plan_header_id == hid)
    )
    details = [_detail_out(d) for d in details_result.scalars().all()]

    alloc_result = await session.execute(
        select(SandharResourceAllocation).where(SandharResourceAllocation.plan_header_id == hid)
    )
    allocations = [_allocation_out(a) for a in alloc_result.scalars().all()]

    return {
        **_header_out(header),
        "details": details,
        "allocations": allocations,
    }


@router.get("/plan")
async def list_plans(
    date: str = Query(...),
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """List plan headers for a given date."""
    try:
        from datetime import date as _date
        plan_date = _date.fromisoformat(date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format, use YYYY-MM-DD")

    rows = await session.execute(
        select(SandharPlanHeader)
        .where(SandharPlanHeader.plan_date == plan_date)
        .order_by(SandharPlanHeader.shift_code, desc(SandharPlanHeader.version))
    )
    return [_header_out(h) for h in rows.scalars().all()]


@router.post("/plan/{header_id}/approve")
async def approve_plan(
    header_id: str,
    req: ApprovePlanRequest,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Approve a plan header and update included WOs to 'planned' status."""
    try:
        hid = uuid.UUID(header_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid header ID format")

    header_result = await session.execute(
        select(SandharPlanHeader).where(SandharPlanHeader.id == hid)
    )
    header = header_result.scalar_one_or_none()
    if not header:
        raise HTTPException(status_code=404, detail=f"Plan header '{header_id}' not found")

    header.status = "approved"
    header.approved_at = datetime.now(timezone.utc)
    header.planner_id = req.planner_id

    # Update included WOs to 'planned'
    details_result = await session.execute(
        select(SandharPlanDetail).where(SandharPlanDetail.plan_header_id == hid)
    )
    details = details_result.scalars().all()
    wo_ids = {d.wo_id for d in details if d.wo_id}

    for wo_id in wo_ids:
        wo_result = await session.execute(
            select(SandharWorkOrder).where(SandharWorkOrder.id == wo_id)
        )
        wo = wo_result.scalar_one_or_none()
        if wo:
            wo.status = "planned"

    await session.commit()
    await session.refresh(header)
    return _header_out(header)


@router.post("/plan/{header_id}/reject")
async def reject_plan(
    header_id: str,
    req: RejectPlanRequest,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Reject a plan header."""
    try:
        hid = uuid.UUID(header_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid header ID format")

    header_result = await session.execute(
        select(SandharPlanHeader).where(SandharPlanHeader.id == hid)
    )
    header = header_result.scalar_one_or_none()
    if not header:
        raise HTTPException(status_code=404, detail=f"Plan header '{header_id}' not found")

    header.status = "rejected"
    await session.commit()
    await session.refresh(header)
    return _header_out(header)


@router.post("/plan/header")
async def create_plan_header(
    req: PlanHeaderCreate,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Create a plan header. Auto-increments version if same plan_date+shift_code exists."""
    try:
        plan_date = date.fromisoformat(req.plan_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid plan_date format, use YYYY-MM-DD")

    # Find latest version for this plan_date + shift_code
    existing_result = await session.execute(
        select(SandharPlanHeader)
        .where(
            and_(
                SandharPlanHeader.plan_date == plan_date,
                SandharPlanHeader.shift_code == req.shift_code,
            )
        )
        .order_by(desc(SandharPlanHeader.version))
        .limit(1)
    )
    existing = existing_result.scalar_one_or_none()
    next_version = (existing.version + 1) if existing else 1

    header = SandharPlanHeader(
        plan_date=plan_date,
        shift_code=req.shift_code,
        version=next_version,
        status="draft",
        confidence=req.confidence,
    )
    session.add(header)
    await session.commit()
    await session.refresh(header)
    return _header_out(header)


@router.post("/plan/{header_id}/allocate-line")
async def allocate_line(
    header_id: str,
    req: AllocateLineRequest,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Create a plan detail row and resource allocation rows for a line."""
    try:
        hid = uuid.UUID(header_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid header ID format")

    try:
        line_id = uuid.UUID(req.line_id)
        wo_id = uuid.UUID(req.wo_id)
        supervisor_id = uuid.UUID(req.supervisor_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid UUID: {e}")

    try:
        plan_date = date.fromisoformat(req.plan_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid plan_date format, use YYYY-MM-DD")

    # Validate header exists
    header_result = await session.execute(
        select(SandharPlanHeader).where(SandharPlanHeader.id == hid)
    )
    if not header_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail=f"Plan header '{header_id}' not found")

    operator_uuids = []
    for op_id in req.operator_ids:
        try:
            operator_uuids.append(uuid.UUID(op_id))
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid operator_id format: {op_id}")

    # 1. Create plan detail row
    manpower_count = len(operator_uuids)
    plan_detail = SandharPlanDetail(
        plan_header_id=hid,
        line_id=line_id,
        wo_id=wo_id,
        planned_qty=req.planned_qty,
        planned_manpower=manpower_count,
        available_manpower=manpower_count,
        supervisor_employee_id=supervisor_id,
        status="planned",
    )
    session.add(plan_detail)
    await session.flush()  # Get plan_detail.id before creating allocations

    # 2. Create resource allocation rows for each operator
    allocations = []
    for op_uuid in operator_uuids:
        alloc = SandharResourceAllocation(
            plan_date=plan_date,
            shift_code=req.shift_code,
            employee_id=op_uuid,
            line_id=line_id,
            wo_id=wo_id,
            allocation_status="allocated",
            plan_header_id=hid,
        )
        session.add(alloc)
        allocations.append(alloc)

    await session.commit()
    await session.refresh(plan_detail)

    return {
        "plan_detail_id": str(plan_detail.id),
        "allocations_created": len(allocations),
    }


@router.post("/alerts")
async def create_alert(
    req: CreateAlertRequest,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Create a Sandhar alert (used by AI planning tools)."""
    plan_date = None
    if req.plan_date:
        try:
            plan_date = date.fromisoformat(req.plan_date)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid plan_date format, use YYYY-MM-DD")

    async def _resolve_fk(value: str | None, model, code_col=None) -> uuid.UUID | None:
        """Parse UUID string and verify it exists in the given table.
        Returns None (silently) if the value is missing, malformed, or not found.
        Optionally falls back to a code lookup (line_code, machine_code, etc.).
        """
        if not value:
            return None
        try:
            uid = uuid.UUID(value)
        except ValueError:
            if code_col is None:
                return None
            # Try code lookup
            row = await session.execute(select(model).where(code_col == value))
            entity = row.scalar_one_or_none()
            return entity.id if entity else None
        # Verify FK exists
        row = await session.execute(select(model).where(model.id == uid))
        return uid if row.scalar_one_or_none() else None

    related_line_id = await _resolve_fk(req.related_line_id, SandharLine, SandharLine.line_code)
    related_wo_id = await _resolve_fk(req.related_wo_id, SandharWorkOrder)
    related_employee_id = await _resolve_fk(req.related_employee_id, SandharEmployee)
    related_machine_id = await _resolve_fk(req.related_machine_id, SandharMachine, SandharMachine.machine_code)

    alert = SandharAlert(
        alert_type=req.alert_type,
        alert_message=req.alert_message,
        severity=req.severity,
        status="active",
        plan_date=plan_date,
        shift_code=req.shift_code,
        related_line_id=related_line_id,
        related_wo_id=related_wo_id,
        related_employee_id=related_employee_id,
        related_machine_id=related_machine_id,
    )
    session.add(alert)
    await session.commit()
    await session.refresh(alert)
    return {"alert_id": str(alert.id), "status": "active"}

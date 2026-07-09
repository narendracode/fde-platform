"""Sandhar production plan endpoints."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, and_, desc, delete
from sqlalchemy.ext.asyncio import AsyncSession

from fde_agent.api.dependencies import verify_api_key
from fde_agent.db.models import (
    Agent,
    AgentAction,
    AgentRefineSession,
    AgentRun,
    SandharAlert,
    SandharAttendance,
    SandharCustomer,
    SandharEmployee,
    SandharLine,
    SandharMachine,
    SandharPlanDetail,
    SandharPlanHeader,
    SandharProduct,
    SandharResourceAllocation,
    SandharWorkOrder,
)
from fde_agent.db.session import get_session

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


class UpdatePlanDetailRequest(BaseModel):
    planned_qty: int | None = None
    line_id: str | None = None
    planned_manpower: int | None = None


class AddPlanDetailRequest(BaseModel):
    wo_id: str
    line_id: str
    planned_qty: int
    planned_manpower: int | None = None


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

    # 3a. Block if an active refinement session exists for any plan on this date
    header_ids_on_date = {
        str(h) for h in (await session.execute(
            select(SandharPlanHeader.id).where(SandharPlanHeader.plan_date == plan_date)
        )).scalars().all()
    }
    if header_ids_on_date:
        pending_plan_actions = (await session.execute(
            select(AgentAction)
            .where(AgentAction.status == "pending_review")
            .where(AgentAction.agent_name == "sandhar-plan-generator")
        )).scalars().all()
        matching_ids = [
            a.id for a in pending_plan_actions
            if (a.approval_action or {}).get("url_params", {}).get("plan_header_id") in header_ids_on_date
        ]
        if matching_ids:
            active_refine = (await session.execute(
                select(AgentRefineSession)
                .where(AgentRefineSession.action_id.in_(matching_ids))
                .where(AgentRefineSession.status == "active")
                .limit(1)
            )).scalar_one_or_none()
            if active_refine:
                raise HTTPException(
                    status_code=422,
                    detail="A refinement session is active for this plan date. Close or approve it before re-generating.",
                )

    # 3b. Block if a generation run is already in progress for this date
    in_progress = await session.execute(
        select(AgentRun)
        .where(
            and_(
                AgentRun.status.in_(["pending", "running"]),
                AgentRun.input["extra_context"]["plan_date"].astext == req.plan_date,
            )
        )
        .limit(1)
    )
    if in_progress.scalar_one_or_none():
        raise HTTPException(
            status_code=409,
            detail=f"A plan generation is already in progress for {req.plan_date}. Wait for it to complete.",
        )

    # 4. Lookup planning agent
    agent_result = await session.execute(
        select(Agent).where(Agent.name == "sandhar-planning-supervisor")
    )
    agent = agent_result.scalar_one_or_none()
    if not agent:
        raise HTTPException(
            status_code=404,
            detail="Planning agent 'sandhar-planning-supervisor' not found. Register the agent first.",
        )
    if not agent.is_active:
        raise HTTPException(
            status_code=403,
            detail="Planning agent 'sandhar-planning-supervisor' is not active. Activate it from the Agents dashboard first.",
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
        from fde_agent.queue.tasks import run_agent_task
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
    """List all plan header versions for a given date.

    Each header includes `action_id` (UUID string or null) pointing to the
    pending_review AgentAction for that plan, so the UI can wire up the
    'Refine with AI' button without a second API call.
    """
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
    headers = rows.scalars().all()
    if not headers:
        return []

    # Batch-fetch pending_review actions for sandhar-plan-generator that reference
    # any of these plan headers via approval_action.url_params.plan_header_id
    action_rows = await session.execute(
        select(AgentAction)
        .where(AgentAction.status == "pending_review")
        .where(AgentAction.agent_name == "sandhar-plan-generator")
    )
    action_map: dict[str, str] = {}
    for a in action_rows.scalars().all():
        hid = (a.approval_action or {}).get("url_params", {}).get("plan_header_id")
        if hid:
            action_map[hid] = str(a.id)

    result = []
    for h in headers:
        d = _header_out(h)
        d["action_id"] = action_map.get(str(h.id))
        result.append(d)
    return result


@router.get("/plan/{header_id}")
async def get_plan_detail(
    header_id: str,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Get full plan including enriched details and resource allocations."""
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
    raw_details = details_result.scalars().all()

    # Cache lookups to avoid N+1 queries
    _line_cache: dict = {}
    _wo_cache: dict = {}
    _prod_cache: dict = {}
    _cust_cache: dict = {}
    _emp_cache: dict = {}

    async def _line(lid):
        if lid not in _line_cache:
            r = await session.execute(select(SandharLine).where(SandharLine.id == lid))
            _line_cache[lid] = r.scalar_one_or_none()
        return _line_cache[lid]

    async def _wo(wid):
        if wid not in _wo_cache:
            r = await session.execute(select(SandharWorkOrder).where(SandharWorkOrder.id == wid))
            _wo_cache[wid] = r.scalar_one_or_none()
        return _wo_cache[wid]

    async def _prod(pid):
        if pid not in _prod_cache:
            r = await session.execute(select(SandharProduct).where(SandharProduct.id == pid))
            _prod_cache[pid] = r.scalar_one_or_none()
        return _prod_cache[pid]

    async def _cust(cid):
        if cid not in _cust_cache:
            r = await session.execute(select(SandharCustomer).where(SandharCustomer.id == cid))
            _cust_cache[cid] = r.scalar_one_or_none()
        return _cust_cache[cid]

    async def _emp(eid):
        if eid not in _emp_cache:
            r = await session.execute(select(SandharEmployee).where(SandharEmployee.id == eid))
            _emp_cache[eid] = r.scalar_one_or_none()
        return _emp_cache[eid]

    enriched_details = []
    for d in raw_details:
        entry = _detail_out(d)
        if d.line_id:
            line = await _line(d.line_id)
            if line:
                entry["line_code"] = line.line_code
                entry["line_name"] = line.line_name
        if d.wo_id:
            wo = await _wo(d.wo_id)
            if wo:
                entry["wo_number"] = wo.wo_number
                entry["order_qty"] = wo.order_qty
                entry["due_date"] = wo.due_date.isoformat() if wo.due_date else None
                entry["priority"] = wo.priority
                if wo.product_id:
                    prod = await _prod(wo.product_id)
                    if prod:
                        entry["product_code"] = prod.product_code
                        entry["product_name"] = prod.product_name
                if wo.customer_id:
                    cust = await _cust(wo.customer_id)
                    if cust:
                        entry["customer_name"] = cust.customer_name
        if d.supervisor_employee_id:
            sup = await _emp(d.supervisor_employee_id)
            if sup:
                entry["supervisor_name"] = sup.name or sup.employee_code
        enriched_details.append(entry)

    alloc_result = await session.execute(
        select(SandharResourceAllocation).where(SandharResourceAllocation.plan_header_id == hid)
    )
    allocations = [_allocation_out(a) for a in alloc_result.scalars().all()]

    return {
        **_header_out(header),
        "details": enriched_details,
        "allocations": allocations,
    }


@router.patch("/plan/{header_id}/details/{detail_id}")
async def update_plan_detail(
    header_id: str,
    detail_id: str,
    req: UpdatePlanDetailRequest,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Update planned_qty and/or line_id on a single plan detail row. Used by refiner tools."""
    try:
        hid = uuid.UUID(header_id)
        did = uuid.UUID(detail_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid UUID format")

    result = await session.execute(
        select(SandharPlanDetail).where(
            SandharPlanDetail.id == did,
            SandharPlanDetail.plan_header_id == hid,
        )
    )
    detail = result.scalar_one_or_none()
    if not detail:
        raise HTTPException(status_code=404, detail="Plan detail not found")

    if req.planned_qty is not None:
        detail.planned_qty = req.planned_qty
    if req.planned_manpower is not None:
        detail.planned_manpower = req.planned_manpower
    if req.line_id is not None:
        try:
            new_line = uuid.UUID(req.line_id)
        except ValueError:
            # Try looking up by line_code
            lc = await session.execute(select(SandharLine).where(SandharLine.line_code == req.line_id))
            line = lc.scalar_one_or_none()
            if not line:
                raise HTTPException(status_code=404, detail=f"Line '{req.line_id}' not found")
            new_line = line.id
        detail.line_id = new_line

    await session.commit()
    await session.refresh(detail)
    return _detail_out(detail)


@router.delete("/plan/{header_id}/details/{detail_id}", status_code=204)
async def remove_plan_detail(
    header_id: str,
    detail_id: str,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Remove a plan detail row (WO returns to unplanned). Used by refiner tools."""
    try:
        hid = uuid.UUID(header_id)
        did = uuid.UUID(detail_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid UUID format")

    result = await session.execute(
        select(SandharPlanDetail).where(
            SandharPlanDetail.id == did,
            SandharPlanDetail.plan_header_id == hid,
        )
    )
    detail = result.scalar_one_or_none()
    if not detail:
        raise HTTPException(status_code=404, detail="Plan detail not found")

    await session.delete(detail)
    await session.commit()


@router.post("/plan/{header_id}/details", status_code=201)
async def add_plan_detail(
    header_id: str,
    req: AddPlanDetailRequest,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Add an open WO as a new plan detail row. Used by refiner tools."""
    try:
        hid = uuid.UUID(header_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid header_id format")

    header = (await session.execute(select(SandharPlanHeader).where(SandharPlanHeader.id == hid))).scalar_one_or_none()
    if not header:
        raise HTTPException(status_code=404, detail="Plan header not found")

    # Resolve wo_id
    try:
        wo_id = uuid.UUID(req.wo_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid wo_id format")

    # Resolve line_id (accept UUID or line_code)
    try:
        line_id = uuid.UUID(req.line_id)
    except ValueError:
        lc = await session.execute(select(SandharLine).where(SandharLine.line_code == req.line_id))
        line = lc.scalar_one_or_none()
        if not line:
            raise HTTPException(status_code=404, detail=f"Line '{req.line_id}' not found")
        line_id = line.id

    wo_row = (await session.execute(select(SandharWorkOrder).where(SandharWorkOrder.id == wo_id))).scalar_one_or_none()
    product_id = wo_row.product_id if wo_row else None

    detail = SandharPlanDetail(
        plan_header_id=hid,
        wo_id=wo_id,
        product_id=product_id,
        line_id=line_id,
        planned_qty=req.planned_qty,
        planned_manpower=req.planned_manpower,
        status="planned",
    )
    session.add(detail)
    await session.commit()
    await session.refresh(detail)
    return _detail_out(detail)


@router.get("/plan")
async def list_plans(
    date: str = Query(...),
    all_versions: bool = Query(False, description="Return all versions; default returns latest per shift"),
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """List plan headers for a given date.
    By default returns one entry per shift (the latest version) with version history embedded.
    Pass all_versions=true to get the raw flat list.
    """
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
    all_headers = rows.scalars().all()

    if all_versions:
        return [_header_out(h) for h in all_headers]

    # Group by shift: return the latest version per shift as the primary entry,
    # with older versions embedded in a `history` field.
    from collections import defaultdict
    by_shift: dict[str, list] = defaultdict(list)
    for h in all_headers:
        by_shift[h.shift_code].append(h)  # already sorted desc by version

    # Batch-load pending_review actions to enrich each plan header with action_id
    pending_actions = (await session.execute(
        select(AgentAction)
        .where(AgentAction.status == "pending_review")
        .where(AgentAction.agent_name == "sandhar-plan-generator")
    )).scalars().all()
    action_map: dict[str, str] = {
        (a.approval_action or {}).get("url_params", {}).get("plan_header_id"): str(a.id)
        for a in pending_actions
        if (a.approval_action or {}).get("url_params", {}).get("plan_header_id")
    }

    result = []
    for shift in sorted(by_shift.keys()):
        versions = by_shift[shift]
        latest = versions[0]
        entry = _header_out(latest)
        entry["action_id"] = action_map.get(str(latest.id))
        entry["history"] = [
            {**_header_out(h), "is_superseded": True}
            for h in versions[1:]
        ]
        result.append(entry)
    return result


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

    # Idempotency: reuse the existing draft — same header_id so allocate_line writes to the right header.
    # Do NOT wipe here: the supervisor agent calls sandhar_save_plan_header a second time (to get
    # header IDs for propose_plan_for_review) AFTER allocate_line has already written the allocations.
    # Wiping here would erase all that work. allocate_line already upserts (delete+insert per line),
    # so stale data from a previous run is cleaned up there, not here.
    if existing and existing.status == "draft":
        return _header_out(existing)

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

    # Remove any existing detail+allocations for this header+line (idempotent upsert)
    await session.execute(
        delete(SandharResourceAllocation).where(
            and_(
                SandharResourceAllocation.plan_header_id == hid,
                SandharResourceAllocation.line_id == line_id,
            )
        )
    )
    await session.execute(
        delete(SandharPlanDetail).where(
            and_(
                SandharPlanDetail.plan_header_id == hid,
                SandharPlanDetail.line_id == line_id,
            )
        )
    )

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

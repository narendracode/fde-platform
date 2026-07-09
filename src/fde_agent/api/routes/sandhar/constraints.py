"""Sandhar constraint management endpoints."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, and_, desc, func
from sqlalchemy.ext.asyncio import AsyncSession

from fde_agent.api.dependencies import verify_api_key
from fde_agent.db.models import (
    SandharMachine,
    SandharMachineStatus,
    SandharMaterialAvailability,
    SandharProduct,
    SandharQualityHold,
    SandharWorkOrder,
)
from fde_agent.db.session import get_session


async def _resolve_machine(session: AsyncSession, value: str) -> tuple[uuid.UUID, object]:
    """Accept machine UUID or machine_code (e.g. 'M001'). Raises 404 if not found."""
    try:
        mid = uuid.UUID(value)
        row = await session.execute(select(SandharMachine).where(SandharMachine.id == mid))
    except ValueError:
        row = await session.execute(select(SandharMachine).where(SandharMachine.machine_code == value))
    machine = row.scalar_one_or_none()
    if machine is None:
        raise HTTPException(status_code=404, detail=f"Machine '{value}' not found")
    return machine.id, machine


async def _resolve_product(session: AsyncSession, value: str) -> tuple[uuid.UUID, object]:
    """Accept product UUID or product_code (e.g. 'PROD-X'). Raises 404 if not found."""
    try:
        pid = uuid.UUID(value)
        row = await session.execute(select(SandharProduct).where(SandharProduct.id == pid))
    except ValueError:
        row = await session.execute(select(SandharProduct).where(SandharProduct.product_code == value))
    product = row.scalar_one_or_none()
    if product is None:
        raise HTTPException(status_code=404, detail=f"Product '{value}' not found")
    return product.id, product


router = APIRouter(prefix="/api/v1/sandhar", tags=["sandhar"])


# ── Serializers ────────────────────────────────────────────────────────────────

def _machine_status_out(s: SandharMachineStatus) -> dict[str, Any]:
    return {
        "id": str(s.id),
        "machine_id": str(s.machine_id),
        "status_datetime": s.status_datetime.isoformat(),
        "machine_status": s.machine_status,
        "reason": s.reason,
        "estimated_restore_datetime": s.estimated_restore_datetime.isoformat() if s.estimated_restore_datetime else None,
        "reported_by": s.reported_by,
        "created_at": s.created_at.isoformat(),
    }


def _material_out(m: SandharMaterialAvailability) -> dict[str, Any]:
    return {
        "id": str(m.id),
        "product_id": str(m.product_id),
        "plan_date": m.plan_date.isoformat(),
        "available_qty": m.available_qty,
        "required_qty": m.required_qty,
        "shortfall_qty": m.shortfall_qty,
        "constraint_flag": m.constraint_flag,
        "updated_at": m.updated_at.isoformat(),
    }


def _qhold_out(q: SandharQualityHold) -> dict[str, Any]:
    return {
        "id": str(q.id),
        "wo_id": str(q.wo_id) if q.wo_id else None,
        "product_id": str(q.product_id) if q.product_id else None,
        "hold_reason": q.hold_reason,
        "hold_status": q.hold_status,
        "raised_by": q.raised_by,
        "released_by": q.released_by,
        "raised_at": q.raised_at.isoformat() if q.raised_at else None,
        "released_at": q.released_at.isoformat() if q.released_at else None,
    }


# ── Pydantic schemas ───────────────────────────────────────────────────────────

class MachineStatusCreate(BaseModel):
    machine_status: str  # running | breakdown | maintenance | idle
    reason: str | None = None
    estimated_restore_datetime: str | None = None
    reported_by: str | None = None
    shift_code: str | None = None


class MaterialAvailabilityUpdate(BaseModel):
    plan_date: str
    available_qty: float
    required_qty: float


class QualityHoldCreate(BaseModel):
    wo_id: str | None = None
    product_id: str | None = None
    hold_reason: str | None = None
    raised_by: str | None = None


class QualityHoldRelease(BaseModel):
    released_by: str


# ── Machine status endpoints — /machines/status BEFORE /machines/{machine_id}/status ──

@router.get("/machines/status")
async def get_all_machine_statuses(
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Get current status of all machines."""
    # Get all machines
    machine_rows = await session.execute(select(SandharMachine).order_by(SandharMachine.machine_code))
    machines = machine_rows.scalars().all()

    result = []
    for machine in machines:
        # Get latest status record for this machine
        status_result = await session.execute(
            select(SandharMachineStatus)
            .where(SandharMachineStatus.machine_id == machine.id)
            .order_by(desc(SandharMachineStatus.status_datetime))
            .limit(1)
        )
        latest_status = status_result.scalar_one_or_none()

        result.append({
            "machine_id": str(machine.id),
            "machine_code": machine.machine_code,
            "machine_name": machine.machine_name,
            "machine_status": latest_status.machine_status if latest_status else "running",
            "since": latest_status.status_datetime.isoformat() if latest_status else None,
            "reason": latest_status.reason if latest_status else None,
        })
    return result


@router.post("/machines/{machine_id}/status")
async def create_machine_status(
    machine_id: str,
    req: MachineStatusCreate,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Record a new machine status event."""
    mid, machine = await _resolve_machine(session, machine_id)

    estimated_restore = None
    if req.estimated_restore_datetime:
        try:
            estimated_restore = datetime.fromisoformat(req.estimated_restore_datetime)
            if estimated_restore.tzinfo is None:
                estimated_restore = estimated_restore.replace(tzinfo=timezone.utc)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid estimated_restore_datetime format")

    status_record = SandharMachineStatus(
        machine_id=mid,
        status_datetime=datetime.now(timezone.utc),
        machine_status=req.machine_status,
        reason=req.reason,
        estimated_restore_datetime=estimated_restore,
        reported_by=req.reported_by,
    )
    session.add(status_record)
    await session.commit()
    await session.refresh(status_record)
    return _machine_status_out(status_record)


@router.get("/machines/{machine_id}/status")
async def get_machine_status(
    machine_id: str,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Get the latest status record for a specific machine."""
    mid, _ = await _resolve_machine(session, machine_id)

    result = await session.execute(
        select(SandharMachineStatus)
        .where(SandharMachineStatus.machine_id == mid)
        .order_by(desc(SandharMachineStatus.status_datetime))
        .limit(1)
    )
    status_record = result.scalar_one_or_none()
    if not status_record:
        raise HTTPException(status_code=404, detail=f"No status records found for machine '{machine_id}'")
    return _machine_status_out(status_record)


# ── Material availability endpoints ────────────────────────────────────────────

@router.put("/material/{product_id}")
async def upsert_material_availability(
    product_id: str,
    req: MaterialAvailabilityUpdate,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Upsert material availability for a product on a plan date."""
    pid, _ = await _resolve_product(session, product_id)

    try:
        plan_date = date.fromisoformat(req.plan_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid plan_date format, use YYYY-MM-DD")

    shortfall = max(0.0, req.required_qty - req.available_qty)
    constraint_flag = shortfall > 0

    # Check for existing record
    existing_result = await session.execute(
        select(SandharMaterialAvailability).where(
            and_(
                SandharMaterialAvailability.product_id == pid,
                SandharMaterialAvailability.plan_date == plan_date,
            )
        )
    )
    existing = existing_result.scalar_one_or_none()

    if existing:
        existing.available_qty = req.available_qty
        existing.required_qty = req.required_qty
        existing.shortfall_qty = shortfall
        existing.constraint_flag = constraint_flag
        await session.commit()
        await session.refresh(existing)
        return _material_out(existing)
    else:
        record = SandharMaterialAvailability(
            product_id=pid,
            plan_date=plan_date,
            available_qty=req.available_qty,
            required_qty=req.required_qty,
            shortfall_qty=shortfall,
            constraint_flag=constraint_flag,
        )
        session.add(record)
        await session.commit()
        await session.refresh(record)
        return _material_out(record)


@router.get("/material")
async def list_material_availability(
    date: str | None = Query(None),
    constraint_flag: bool | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """List material availability records."""
    q = select(SandharMaterialAvailability).order_by(
        SandharMaterialAvailability.plan_date.desc()
    ).limit(limit)

    if date:
        try:
            from datetime import date as _date
            plan_date = _date.fromisoformat(date)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date format, use YYYY-MM-DD")
        q = q.where(SandharMaterialAvailability.plan_date == plan_date)

    if constraint_flag is not None:
        q = q.where(SandharMaterialAvailability.constraint_flag == constraint_flag)

    rows = await session.execute(q)
    return [_material_out(m) for m in rows.scalars().all()]


# ── Quality hold endpoints ─────────────────────────────────────────────────────

@router.get("/quality-hold")
async def list_quality_holds(
    status: str = Query("active"),
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """List quality holds, defaulting to active holds."""
    q = select(SandharQualityHold).where(SandharQualityHold.hold_status == status)
    rows = await session.execute(q)
    return [_qhold_out(h) for h in rows.scalars().all()]


@router.post("/quality-hold")
async def create_quality_hold(
    req: QualityHoldCreate,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Create a quality hold. Optionally sets quality_hold=True on the work order."""
    wo_id = None
    if req.wo_id:
        try:
            wo_id = uuid.UUID(req.wo_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid wo_id format")

    product_id = None
    if req.product_id:
        try:
            product_id = uuid.UUID(req.product_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid product_id format")

    hold = SandharQualityHold(
        wo_id=wo_id,
        product_id=product_id,
        hold_reason=req.hold_reason,
        hold_status="active",
        raised_by=req.raised_by,
        raised_at=datetime.now(timezone.utc),
    )
    session.add(hold)

    # Set quality_hold flag on the work order
    if wo_id:
        wo_result = await session.execute(
            select(SandharWorkOrder).where(SandharWorkOrder.id == wo_id)
        )
        wo = wo_result.scalar_one_or_none()
        if wo:
            wo.quality_hold = True

    await session.commit()
    await session.refresh(hold)
    return _qhold_out(hold)


@router.post("/quality-hold/{hold_id}/release")
async def release_quality_hold(
    hold_id: str,
    req: QualityHoldRelease,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Release a quality hold and optionally clear the WO quality_hold flag."""
    try:
        hid = uuid.UUID(hold_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid hold ID format")

    result = await session.execute(
        select(SandharQualityHold).where(SandharQualityHold.id == hid)
    )
    hold = result.scalar_one_or_none()
    if not hold:
        raise HTTPException(status_code=404, detail=f"Quality hold '{hold_id}' not found")

    hold.hold_status = "released"
    hold.released_at = datetime.now(timezone.utc)
    hold.released_by = req.released_by

    # Clear quality_hold flag on WO
    if hold.wo_id:
        wo_result = await session.execute(
            select(SandharWorkOrder).where(SandharWorkOrder.id == hold.wo_id)
        )
        wo = wo_result.scalar_one_or_none()
        if wo:
            wo.quality_hold = False

    await session.commit()
    await session.refresh(hold)
    return _qhold_out(hold)


# ── Constraints summary ────────────────────────────────────────────────────────

@router.get("/constraints/summary")
async def get_constraints_summary(
    plan_date: str = Query(...),
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Return a consolidated constraints summary for a plan date."""
    try:
        pdate = date.fromisoformat(plan_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid plan_date format, use YYYY-MM-DD")

    # 1. Get machines not in 'running' status
    machine_rows = await session.execute(select(SandharMachine).order_by(SandharMachine.machine_code))
    machines = machine_rows.scalars().all()

    broken_machines = []
    for machine in machines:
        status_result = await session.execute(
            select(SandharMachineStatus)
            .where(SandharMachineStatus.machine_id == machine.id)
            .order_by(desc(SandharMachineStatus.status_datetime))
            .limit(1)
        )
        latest = status_result.scalar_one_or_none()
        if latest and latest.machine_status != "running":
            broken_machines.append({
                "machine_id": str(machine.id),
                "machine_code": machine.machine_code,
                "machine_name": machine.machine_name,
                "machine_status": latest.machine_status,
                "reason": latest.reason,
            })

    # 2. Material shortfalls for plan_date
    mat_rows = await session.execute(
        select(SandharMaterialAvailability).where(
            and_(
                SandharMaterialAvailability.plan_date == pdate,
                SandharMaterialAvailability.shortfall_qty > 0,
            )
        )
    )
    material_shortfalls = [_material_out(m) for m in mat_rows.scalars().all()]

    # 3. Active quality holds
    qhold_rows = await session.execute(
        select(SandharQualityHold).where(SandharQualityHold.hold_status == "active")
    )
    quality_holds = [_qhold_out(h) for h in qhold_rows.scalars().all()]

    # 4. Affected WO count (WOs on quality hold)
    wo_hold_result = await session.execute(
        select(func.count(SandharWorkOrder.id)).where(SandharWorkOrder.quality_hold == True)
    )
    affected_wo_count = wo_hold_result.scalar() or 0

    # Blocked qty from material shortfalls
    blocked_qty = sum(m.get("shortfall_qty", 0) or 0 for m in material_shortfalls)

    return {
        "plan_date": plan_date,
        "machine_constraints": broken_machines,
        "material_shortfalls": material_shortfalls,
        "quality_holds": quality_holds,
        "affected_wo_count": affected_wo_count,
        "blocked_qty": blocked_qty,
        "total_constraints": len(broken_machines) + len(material_shortfalls) + len(quality_holds),
    }

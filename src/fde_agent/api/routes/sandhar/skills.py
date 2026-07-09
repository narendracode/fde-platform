"""Sandhar skill matrix endpoints."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from fde_agent.api.dependencies import verify_api_key
from fde_agent.db.models import (
    SandharAttendance,
    SandharEmployee,
    SandharEmployeeSkill,
    SandharLine,
    SandharMachine,
)
from fde_agent.db.session import get_session

router = APIRouter(prefix="/api/v1/sandhar", tags=["sandhar"])


# ── Serializer ─────────────────────────────────────────────────────────────────

def _skill_out(s: SandharEmployeeSkill) -> dict[str, Any]:
    return {
        "id": str(s.id),
        "employee_id": str(s.employee_id),
        "line_id": str(s.line_id) if s.line_id else None,
        "machine_id": str(s.machine_id) if s.machine_id else None,
        "skill_level": s.skill_level,
        "certification_date": s.certification_date.isoformat() if s.certification_date else None,
        "expiry_date": s.expiry_date.isoformat() if s.expiry_date else None,
        "active_flag": s.active_flag,
        "created_at": s.created_at.isoformat(),
        "updated_at": s.updated_at.isoformat(),
    }


# ── Pydantic schemas ───────────────────────────────────────────────────────────

class CreateSkill(BaseModel):
    employee_id: str
    line_id: str | None = None
    machine_id: str | None = None
    skill_level: int = 2
    certification_date: str | None = None
    expiry_date: str | None = None


class UpdateSkill(BaseModel):
    skill_level: int | None = None
    certification_date: str | None = None
    expiry_date: str | None = None
    active_flag: bool | None = None


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _resolve_entity_id(session: AsyncSession, value: str, model, code_col) -> uuid.UUID | None:
    """Accept either a UUID string or a code string (e.g. 'L001').
    Returns the UUID if found, None if not found at all.
    """
    try:
        return uuid.UUID(value)
    except ValueError:
        # Not a UUID — try looking up by code column
        row = await session.execute(select(model).where(code_col == value))
        entity = row.scalar_one_or_none()
        return entity.id if entity else None


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.get("/skills/qualified-operators")
async def get_qualified_operators(
    line_id: str | None = Query(None),
    machine_id: str | None = Query(None),
    min_skill_level: int = Query(2),
    plan_date: str = Query(...),
    shift_code: str = Query(...),
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Get operators qualified for a line/machine who are present on the plan_date/shift."""
    q = select(SandharEmployeeSkill).where(
        and_(
            SandharEmployeeSkill.active_flag == True,
            SandharEmployeeSkill.skill_level >= min_skill_level,
        )
    )

    conditions = []
    if line_id:
        lid = await _resolve_entity_id(session, line_id, SandharLine, SandharLine.line_code)
        if lid is None:
            return []  # unknown line — return empty rather than 400
        conditions.append(SandharEmployeeSkill.line_id == lid)
    if machine_id:
        mid = await _resolve_entity_id(session, machine_id, SandharMachine, SandharMachine.machine_code)
        if mid is None:
            return []
        conditions.append(SandharEmployeeSkill.machine_id == mid)

    if conditions:
        from sqlalchemy import or_
        q = q.where(or_(*conditions))

    skill_rows = await session.execute(q)
    skills = skill_rows.scalars().all()

    if not skills:
        return []

    employee_ids = list({s.employee_id for s in skills})

    try:
        att_date = date.fromisoformat(plan_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid plan_date format, use YYYY-MM-DD")

    att_q = select(SandharAttendance).where(
        and_(
            SandharAttendance.employee_id.in_(employee_ids),
            SandharAttendance.attendance_date == att_date,
            SandharAttendance.shift_code == shift_code,
            SandharAttendance.status == "present",
        )
    )
    att_rows = await session.execute(att_q)
    present_ids = {a.employee_id for a in att_rows.scalars().all()}

    # Filter skills to only present employees
    present_skills = [s for s in skills if s.employee_id in present_ids]
    present_emp_ids = list({s.employee_id for s in present_skills})

    if not present_emp_ids:
        return []

    emp_q = select(SandharEmployee).where(SandharEmployee.id.in_(present_emp_ids))
    emp_rows = await session.execute(emp_q)
    emp_map = {e.id: e for e in emp_rows.scalars().all()}

    result = []
    seen = set()
    for s in present_skills:
        if s.employee_id in seen:
            continue
        seen.add(s.employee_id)
        emp = emp_map.get(s.employee_id)
        result.append({
            "employee_id": str(s.employee_id),
            "employee_code": emp.employee_code if emp else None,
            "name": emp.name if emp else None,
            "designation": emp.designation if emp else None,
            "skill_level": s.skill_level,
            "line_id": str(s.line_id) if s.line_id else None,
            "machine_id": str(s.machine_id) if s.machine_id else None,
        })
    return result


@router.get("/skills/expiring")
async def get_expiring_skills(
    plan_date: str | None = Query(None),
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Return skills expiring within 30 days of plan_date."""
    try:
        base_date = date.fromisoformat(plan_date) if plan_date else date.today()
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid plan_date format, use YYYY-MM-DD")

    cutoff = base_date + timedelta(days=30)

    q = select(SandharEmployeeSkill).where(
        and_(
            SandharEmployeeSkill.active_flag == True,
            SandharEmployeeSkill.expiry_date <= cutoff,
        )
    )
    rows = await session.execute(q)
    skills = rows.scalars().all()

    if not skills:
        return []

    emp_ids = list({s.employee_id for s in skills})
    emp_q = select(SandharEmployee).where(SandharEmployee.id.in_(emp_ids))
    emp_rows = await session.execute(emp_q)
    emp_map = {e.id: e for e in emp_rows.scalars().all()}

    result = []
    for s in skills:
        emp = emp_map.get(s.employee_id)
        entry = _skill_out(s)
        entry["employee_code"] = emp.employee_code if emp else None
        entry["employee_name"] = emp.name if emp else None
        result.append(entry)
    return result


@router.get("/skills")
async def list_skills(
    employee_id: str | None = Query(None),
    line_id: str | None = Query(None),
    machine_id: str | None = Query(None),
    active_flag: bool = Query(True),
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """List skills with optional filters."""
    q = select(SandharEmployeeSkill).where(SandharEmployeeSkill.active_flag == active_flag)
    if employee_id:
        try:
            eid = uuid.UUID(employee_id)
            q = q.where(SandharEmployeeSkill.employee_id == eid)
        except ValueError:
            pass  # ignore invalid employee_id filter
    if line_id:
        lid = await _resolve_entity_id(session, line_id, SandharLine, SandharLine.line_code)
        if lid:
            q = q.where(SandharEmployeeSkill.line_id == lid)
    if machine_id:
        mid = await _resolve_entity_id(session, machine_id, SandharMachine, SandharMachine.machine_code)
        if mid:
            q = q.where(SandharEmployeeSkill.machine_id == mid)
    rows = await session.execute(q)
    return [_skill_out(s) for s in rows.scalars().all()]


@router.post("/skills")
async def create_skill(
    req: CreateSkill,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Create a new skill record."""
    employee_id = await _resolve_entity_id(session, req.employee_id, SandharEmployee, SandharEmployee.employee_code)
    if employee_id is None:
        raise HTTPException(status_code=404, detail=f"Employee '{req.employee_id}' not found")

    line_id = await _resolve_entity_id(session, req.line_id, SandharLine, SandharLine.line_code) if req.line_id else None
    machine_id = await _resolve_entity_id(session, req.machine_id, SandharMachine, SandharMachine.machine_code) if req.machine_id else None

    cert_date = date.fromisoformat(req.certification_date) if req.certification_date else None
    expiry_date = date.fromisoformat(req.expiry_date) if req.expiry_date else None

    skill = SandharEmployeeSkill(
        employee_id=employee_id,
        line_id=line_id,
        machine_id=machine_id,
        skill_level=req.skill_level,
        certification_date=cert_date,
        expiry_date=expiry_date,
        active_flag=True,
    )
    session.add(skill)
    await session.commit()
    await session.refresh(skill)
    return _skill_out(skill)


@router.put("/skills/{skill_id}")
async def update_skill(
    skill_id: str,
    req: UpdateSkill,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Update non-null fields of a skill record."""
    try:
        sid = uuid.UUID(skill_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid skill ID format")

    result = await session.execute(select(SandharEmployeeSkill).where(SandharEmployeeSkill.id == sid))
    skill = result.scalar_one_or_none()
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill '{skill_id}' not found")

    if req.skill_level is not None:
        skill.skill_level = req.skill_level
    if req.certification_date is not None:
        skill.certification_date = date.fromisoformat(req.certification_date)
    if req.expiry_date is not None:
        skill.expiry_date = date.fromisoformat(req.expiry_date)
    if req.active_flag is not None:
        skill.active_flag = req.active_flag

    await session.commit()
    await session.refresh(skill)
    return _skill_out(skill)


@router.delete("/skills/{skill_id}")
async def deactivate_skill(
    skill_id: str,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Soft-delete a skill by setting active_flag=False."""
    try:
        sid = uuid.UUID(skill_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid skill ID format")

    result = await session.execute(select(SandharEmployeeSkill).where(SandharEmployeeSkill.id == sid))
    skill = result.scalar_one_or_none()
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill '{skill_id}' not found")

    skill.active_flag = False
    await session.commit()
    await session.refresh(skill)
    return _skill_out(skill)

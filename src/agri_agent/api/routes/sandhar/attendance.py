"""Sandhar attendance endpoints."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, and_, func
from sqlalchemy.ext.asyncio import AsyncSession

from agri_agent.api.dependencies import verify_api_key
from agri_agent.db.models import SandharAttendance, SandharEmployee
from agri_agent.db.session import get_session

router = APIRouter(prefix="/api/v1/sandhar", tags=["sandhar"])


# ── Serializer ─────────────────────────────────────────────────────────────────

def _att_out(a: SandharAttendance) -> dict[str, Any]:
    return {
        "id": str(a.id),
        "employee_id": str(a.employee_id),
        "attendance_date": a.attendance_date.isoformat(),
        "shift_code": a.shift_code,
        "check_in_time": a.check_in_time.isoformat() if a.check_in_time else None,
        "check_out_time": a.check_out_time.isoformat() if a.check_out_time else None,
        "status": a.status,
        "is_manual_override": a.is_manual_override,
        "override_by": a.override_by,
        "created_at": a.created_at.isoformat(),
        "updated_at": a.updated_at.isoformat(),
    }


# ── Pydantic schemas ───────────────────────────────────────────────────────────

class BulkUploadItem(BaseModel):
    employee_id: str | None = None
    employee_code: str | None = None
    attendance_date: str
    shift_code: str
    status: str
    check_in_time: str | None = None


class OverrideRequest(BaseModel):
    status: str
    override_by: str


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.post("/attendance/upload")
async def bulk_upload_attendance(
    items: list[BulkUploadItem],
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Bulk upload attendance records. Upserts by (employee_id, attendance_date, shift_code)."""
    created = 0
    updated = 0
    errors = []

    for item in items:
        try:
            att_date = date.fromisoformat(item.attendance_date)
        except ValueError:
            errors.append({"item": item.dict(), "error": "Invalid attendance_date format"})
            continue

        # Resolve employee
        employee = None
        if item.employee_id:
            try:
                eid = uuid.UUID(item.employee_id)
            except ValueError:
                errors.append({"item": item.dict(), "error": "Invalid employee_id format"})
                continue
            result = await session.execute(select(SandharEmployee).where(SandharEmployee.id == eid))
            employee = result.scalar_one_or_none()
        elif item.employee_code:
            result = await session.execute(
                select(SandharEmployee).where(SandharEmployee.employee_code == item.employee_code)
            )
            employee = result.scalar_one_or_none()

        if not employee:
            errors.append({"item": item.dict(), "error": "Employee not found"})
            continue

        # Check for existing record
        existing_result = await session.execute(
            select(SandharAttendance).where(
                and_(
                    SandharAttendance.employee_id == employee.id,
                    SandharAttendance.attendance_date == att_date,
                    SandharAttendance.shift_code == item.shift_code,
                )
            )
        )
        existing = existing_result.scalar_one_or_none()

        check_in = None
        if item.check_in_time:
            try:
                check_in = datetime.fromisoformat(item.check_in_time)
                if check_in.tzinfo is None:
                    check_in = check_in.replace(tzinfo=timezone.utc)
            except ValueError:
                pass

        if existing:
            existing.status = item.status
            if check_in:
                existing.check_in_time = check_in
            updated += 1
        else:
            att = SandharAttendance(
                employee_id=employee.id,
                attendance_date=att_date,
                shift_code=item.shift_code,
                status=item.status,
                check_in_time=check_in,
                is_manual_override=False,
            )
            session.add(att)
            created += 1

    await session.commit()
    return {"created": created, "updated": updated, "errors": errors}


@router.get("/attendance")
async def list_attendance(
    date: str | None = Query(None),
    shift_code: str | None = Query(None),
    status: str | None = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """List attendance records with optional filters."""
    q = select(SandharAttendance).order_by(
        SandharAttendance.attendance_date.desc(),
        SandharAttendance.shift_code,
    )
    if date:
        try:
            from datetime import date as _date
            att_date = _date.fromisoformat(date)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date format, use YYYY-MM-DD")
        q = q.where(SandharAttendance.attendance_date == att_date)
    if shift_code:
        q = q.where(SandharAttendance.shift_code == shift_code)
    if status:
        q = q.where(SandharAttendance.status == status)
    q = q.limit(limit)
    rows = await session.execute(q)
    return [_att_out(a) for a in rows.scalars().all()]


@router.put("/attendance/{attendance_id}/override")
async def override_attendance(
    attendance_id: str,
    req: OverrideRequest,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Manually override an attendance record."""
    try:
        aid = uuid.UUID(attendance_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid attendance ID format")

    result = await session.execute(select(SandharAttendance).where(SandharAttendance.id == aid))
    att = result.scalar_one_or_none()
    if not att:
        raise HTTPException(status_code=404, detail=f"Attendance record '{attendance_id}' not found")

    att.status = req.status
    att.override_by = req.override_by
    att.is_manual_override = True

    await session.commit()
    await session.refresh(att)
    return _att_out(att)


@router.get("/attendance/summary")
async def attendance_summary(
    date: str = Query(...),
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Return attendance summary grouped by shift for a given date."""
    try:
        from datetime import date as _date
        att_date = _date.fromisoformat(date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format, use YYYY-MM-DD")

    shifts_data = {}
    for shift_code in ["A", "B", "C"]:
        # Count by status
        status_q = select(
            SandharAttendance.status,
            func.count(SandharAttendance.id).label("cnt"),
        ).where(
            and_(
                SandharAttendance.attendance_date == att_date,
                SandharAttendance.shift_code == shift_code,
            )
        ).group_by(SandharAttendance.status)

        status_rows = await session.execute(status_q)
        status_counts = {row.status: row.cnt for row in status_rows}

        # Count by designation (join employees)
        desig_q = select(
            SandharEmployee.designation,
            func.count(SandharAttendance.id).label("cnt"),
        ).join(
            SandharEmployee, SandharAttendance.employee_id == SandharEmployee.id
        ).where(
            and_(
                SandharAttendance.attendance_date == att_date,
                SandharAttendance.shift_code == shift_code,
                SandharAttendance.status == "present",
            )
        ).group_by(SandharEmployee.designation)

        desig_rows = await session.execute(desig_q)
        operator_count = 0
        supervisor_count = 0
        for row in desig_rows:
            desig = (row.designation or "").lower()
            if "supervisor" in desig:
                supervisor_count += row.cnt
            else:
                operator_count += row.cnt

        shifts_data[shift_code] = {
            "present": status_counts.get("present", 0),
            "absent": status_counts.get("absent", 0),
            "late": status_counts.get("late", 0),
            "leave": status_counts.get("leave", 0),
            "operators": operator_count,
            "supervisors": supervisor_count,
        }

    return {"date": att_date.isoformat(), "shifts": shifts_data}

"""Sandhar master data CRUD endpoints."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agri_agent.api.dependencies import verify_api_key
from agri_agent.db.models import (
    SandharCustomer,
    SandharEmployee,
    SandharLine,
    SandharMachine,
    SandharMachineStatus,
    SandharProduct,
    SandharShift,
)
from agri_agent.db.session import get_session


async def _resolve(session: AsyncSession, value: str, model, code_col):
    """Return (uuid, orm_object) accepting either a UUID string or a code string.
    Raises 404 if nothing matches.
    """
    try:
        uid = uuid.UUID(value)
        row = await session.execute(select(model).where(model.id == uid))
    except ValueError:
        row = await session.execute(select(model).where(code_col == value))
    obj = row.scalar_one_or_none()
    if obj is None:
        raise HTTPException(status_code=404, detail=f"'{value}' not found")
    return obj.id, obj


router = APIRouter(prefix="/api/v1/sandhar", tags=["sandhar"])


# ── Serializers ────────────────────────────────────────────────────────────────

def _emp_out(e: SandharEmployee) -> dict[str, Any]:
    return {
        "id": str(e.id),
        "employee_code": e.employee_code,
        "name": e.name,
        "department": e.department,
        "designation": e.designation,
        "grade": e.grade,
        "shift_group": e.shift_group,
        "status": e.status,
        "joining_date": e.joining_date.isoformat() if e.joining_date else None,
        "created_at": e.created_at.isoformat(),
        "updated_at": e.updated_at.isoformat(),
    }


def _line_out(l: SandharLine) -> dict[str, Any]:
    return {
        "id": str(l.id),
        "line_code": l.line_code,
        "line_name": l.line_name,
        "area": l.area,
        "capacity_per_shift": l.capacity_per_shift,
        "status": l.status,
        "created_at": l.created_at.isoformat(),
        "updated_at": l.updated_at.isoformat(),
    }


def _machine_out(m: SandharMachine, operational_status: str | None = None) -> dict[str, Any]:
    return {
        "id": str(m.id),
        "machine_code": m.machine_code,
        "machine_name": m.machine_name,
        "line_id": str(m.line_id) if m.line_id else None,
        "machine_type": m.machine_type,
        "capacity_per_hour": m.capacity_per_hour,
        "status": m.status,
        "operational_status": operational_status,
        "created_at": m.created_at.isoformat(),
        "updated_at": m.updated_at.isoformat(),
    }


def _customer_out(c: SandharCustomer) -> dict[str, Any]:
    return {
        "id": str(c.id),
        "customer_code": c.customer_code,
        "customer_name": c.customer_name,
        "priority_level": c.priority_level,
        "created_at": c.created_at.isoformat(),
        "updated_at": c.updated_at.isoformat(),
    }


def _product_out(p: SandharProduct) -> dict[str, Any]:
    return {
        "id": str(p.id),
        "product_code": p.product_code,
        "product_name": p.product_name,
        "customer_id": str(p.customer_id) if p.customer_id else None,
        "standard_cycle_time": p.standard_cycle_time,
        "standard_manpower": p.standard_manpower,
        "line_id": str(p.line_id) if p.line_id else None,
        "created_at": p.created_at.isoformat(),
        "updated_at": p.updated_at.isoformat(),
    }


def _shift_out(s: SandharShift) -> dict[str, Any]:
    return {
        "id": str(s.id),
        "shift_code": s.shift_code,
        "shift_name": s.shift_name,
        "start_time": s.start_time,
        "end_time": s.end_time,
        "working_hours": s.working_hours,
        "created_at": s.created_at.isoformat(),
    }


# ── Pydantic schemas ───────────────────────────────────────────────────────────

class CreateEmployee(BaseModel):
    employee_code: str
    name: str
    department: str | None = None
    designation: str | None = None
    grade: str | None = None
    shift_group: str | None = None
    status: str = "active"
    joining_date: str | None = None  # YYYY-MM-DD


class UpdateEmployee(BaseModel):
    name: str | None = None
    department: str | None = None
    designation: str | None = None
    grade: str | None = None
    shift_group: str | None = None
    status: str | None = None


class CreateLine(BaseModel):
    line_code: str
    line_name: str
    area: str | None = None
    capacity_per_shift: int | None = None
    status: str = "active"


class CreateMachine(BaseModel):
    machine_code: str
    machine_name: str
    line_id: str | None = None
    machine_type: str | None = None
    capacity_per_hour: int | None = None
    status: str = "active"


class CreateCustomer(BaseModel):
    customer_code: str
    customer_name: str
    priority_level: str | None = None


class CreateProduct(BaseModel):
    product_code: str
    product_name: str
    customer_id: str | None = None
    standard_cycle_time: float | None = None
    standard_manpower: int | None = None
    line_id: str | None = None


class CreateShift(BaseModel):
    shift_code: str
    shift_name: str | None = None
    start_time: str | None = None
    end_time: str | None = None
    working_hours: float | None = None


# ── Employee endpoints ─────────────────────────────────────────────────────────

@router.get("/employees")
async def list_employees(
    status: str | None = Query(None),
    department: str | None = Query(None),
    shift_group: str | None = Query(None),
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """List employees with optional filters."""
    q = select(SandharEmployee).order_by(SandharEmployee.employee_code)
    if status:
        q = q.where(SandharEmployee.status == status)
    if department:
        q = q.where(SandharEmployee.department == department)
    if shift_group:
        q = q.where(SandharEmployee.shift_group == shift_group)
    rows = await session.execute(q)
    return [_emp_out(e) for e in rows.scalars().all()]


@router.post("/employees")
async def create_employee(
    req: CreateEmployee,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Create a new employee."""
    joining_date = date.fromisoformat(req.joining_date) if req.joining_date else None
    emp = SandharEmployee(
        employee_code=req.employee_code,
        name=req.name,
        department=req.department,
        designation=req.designation,
        grade=req.grade,
        shift_group=req.shift_group,
        status=req.status,
        joining_date=joining_date,
    )
    session.add(emp)
    await session.commit()
    await session.refresh(emp)
    return _emp_out(emp)


@router.put("/employees/{employee_id}")
async def update_employee(
    employee_id: str,
    req: UpdateEmployee,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Update non-null fields of an employee."""
    _, emp = await _resolve(session, employee_id, SandharEmployee, SandharEmployee.employee_code)

    if req.name is not None:
        emp.name = req.name
    if req.department is not None:
        emp.department = req.department
    if req.designation is not None:
        emp.designation = req.designation
    if req.grade is not None:
        emp.grade = req.grade
    if req.shift_group is not None:
        emp.shift_group = req.shift_group
    if req.status is not None:
        emp.status = req.status

    await session.commit()
    await session.refresh(emp)
    return _emp_out(emp)


# ── Line endpoints ─────────────────────────────────────────────────────────────

@router.get("/lines")
async def list_lines(
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """List all production lines."""
    rows = await session.execute(select(SandharLine).order_by(SandharLine.line_code))
    return [_line_out(l) for l in rows.scalars().all()]


@router.post("/lines")
async def create_line(
    req: CreateLine,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Create a new production line."""
    line = SandharLine(
        line_code=req.line_code,
        line_name=req.line_name,
        area=req.area,
        capacity_per_shift=req.capacity_per_shift,
        status=req.status,
    )
    session.add(line)
    await session.commit()
    await session.refresh(line)
    return _line_out(line)


# ── Machine endpoints ──────────────────────────────────────────────────────────

@router.get("/machines")
async def list_machines(
    line_id: str | None = Query(None),
    status: str | None = Query(None),
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """List machines with optional filters by line_id and status."""
    q = select(SandharMachine).order_by(SandharMachine.machine_code)
    if line_id:
        try:
            lid = uuid.UUID(line_id)
        except ValueError:
            row = await session.execute(select(SandharLine).where(SandharLine.line_code == line_id))
            line = row.scalar_one_or_none()
            lid = line.id if line else None
        if lid:
            q = q.where(SandharMachine.line_id == lid)
    if status:
        q = q.where(SandharMachine.status == status)
    rows = await session.execute(q)
    machines = rows.scalars().all()

    # Fetch latest operational status from the event log for each machine.
    # Uses a row_number window to pick the most-recent record per machine_id.
    inner = (
        select(
            SandharMachineStatus.machine_id,
            SandharMachineStatus.machine_status,
            func.row_number().over(
                partition_by=SandharMachineStatus.machine_id,
                order_by=SandharMachineStatus.status_datetime.desc(),
            ).label("rn"),
        ).subquery()
    )
    op_rows = await session.execute(
        select(inner.c.machine_id, inner.c.machine_status).where(inner.c.rn == 1)
    )
    op_map: dict[str, str] = {str(r.machine_id): r.machine_status for r in op_rows}

    return [_machine_out(m, op_map.get(str(m.id))) for m in machines]


@router.post("/machines")
async def create_machine(
    req: CreateMachine,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Create a new machine."""
    line_id = uuid.UUID(req.line_id) if req.line_id else None
    machine = SandharMachine(
        machine_code=req.machine_code,
        machine_name=req.machine_name,
        line_id=line_id,
        machine_type=req.machine_type,
        capacity_per_hour=req.capacity_per_hour,
        status=req.status,
    )
    session.add(machine)
    await session.commit()
    await session.refresh(machine)
    return _machine_out(machine)


# ── Product endpoints ──────────────────────────────────────────────────────────

@router.get("/products")
async def list_products(
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """List all products."""
    rows = await session.execute(select(SandharProduct).order_by(SandharProduct.product_code))
    return [_product_out(p) for p in rows.scalars().all()]


@router.get("/products/{product_id}")
async def get_product(
    product_id: str,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Get a single product by ID or product_code."""
    _, product = await _resolve(session, product_id, SandharProduct, SandharProduct.product_code)
    return _product_out(product)


@router.post("/products")
async def create_product(
    req: CreateProduct,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Create a new product."""
    customer_id = uuid.UUID(req.customer_id) if req.customer_id else None
    line_id = uuid.UUID(req.line_id) if req.line_id else None
    product = SandharProduct(
        product_code=req.product_code,
        product_name=req.product_name,
        customer_id=customer_id,
        standard_cycle_time=req.standard_cycle_time,
        standard_manpower=req.standard_manpower,
        line_id=line_id,
    )
    session.add(product)
    await session.commit()
    await session.refresh(product)
    return _product_out(product)


# ── Customer endpoints ─────────────────────────────────────────────────────────

@router.get("/customers")
async def list_customers(
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """List all customers."""
    rows = await session.execute(select(SandharCustomer).order_by(SandharCustomer.customer_code))
    return [_customer_out(c) for c in rows.scalars().all()]


@router.post("/customers")
async def create_customer(
    req: CreateCustomer,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Create a new customer."""
    customer = SandharCustomer(
        customer_code=req.customer_code,
        customer_name=req.customer_name,
        priority_level=req.priority_level,
    )
    session.add(customer)
    await session.commit()
    await session.refresh(customer)
    return _customer_out(customer)


# ── Shift endpoints ────────────────────────────────────────────────────────────

@router.get("/shifts")
async def list_shifts(
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """List all shifts."""
    rows = await session.execute(select(SandharShift).order_by(SandharShift.shift_code))
    return [_shift_out(s) for s in rows.scalars().all()]


@router.post("/shifts")
async def create_shift(
    req: CreateShift,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Create a new shift."""
    shift = SandharShift(
        shift_code=req.shift_code,
        shift_name=req.shift_name,
        start_time=req.start_time,
        end_time=req.end_time,
        working_hours=req.working_hours,
    )
    session.add(shift)
    await session.commit()
    await session.refresh(shift)
    return _shift_out(shift)

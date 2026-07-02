"""Sandhar simulation control endpoints (demo mode only)."""
from __future__ import annotations

import uuid
import random
from datetime import date, datetime, timezone, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel
from sqlalchemy import select, text, and_
from sqlalchemy.ext.asyncio import AsyncSession

from agri_agent.api.dependencies import verify_api_key
from agri_agent.db.models import (
    SandharAlert,
    SandharAttendance,
    SandharCustomer,
    SandharDailyKpi,
    SandharEmployee,
    SandharEmployeeSkill,
    SandharLine,
    SandharMachine,
    SandharMachineStatus,
    SandharMaterialAvailability,
    SandharPlanDetail,
    SandharPlanHeader,
    SandharProduct,
    SandharProductionActual,
    SandharQualityHold,
    SandharResourceAllocation,
    SandharShift,
    SandharWorkOrder,
    SandharWorkOrderOperation,
)
from agri_agent.db.session import get_session

router = APIRouter(prefix="/api/v1/sandhar", tags=["sandhar"])

# ── Seed data constants ────────────────────────────────────────────────────────

_SHIFTS = [
    {"shift_code": "A", "shift_name": "Morning Shift", "start_time": "06:00", "end_time": "14:00", "working_hours": 8.0},
    {"shift_code": "B", "shift_name": "Afternoon Shift", "start_time": "14:00", "end_time": "22:00", "working_hours": 8.0},
    {"shift_code": "C", "shift_name": "Night Shift", "start_time": "22:00", "end_time": "06:00", "working_hours": 8.0},
]

_CUSTOMERS = [
    {"customer_code": "CUST-OEM-A", "customer_name": "Maruti Suzuki", "priority_level": "critical"},
    {"customer_code": "CUST-OEM-B", "customer_name": "Hero MotoCorp", "priority_level": "high"},
    {"customer_code": "CUST-OEM-C", "customer_name": "TVS Motor", "priority_level": "high"},
    {"customer_code": "CUST-OEM-D", "customer_name": "Mahindra", "priority_level": "medium"},
]

_LINES = [
    {"line_code": "L001", "line_name": "Assembly Line-1", "capacity_per_shift": 900},
    {"line_code": "L002", "line_name": "Assembly Line-2", "capacity_per_shift": 600},
    {"line_code": "L003", "line_name": "Assembly Line-3", "capacity_per_shift": 500},
]

_MACHINES_BY_LINE = {
    "L001": [
        {"machine_code": "M001", "machine_name": "Press Machine 1", "capacity_per_hour": 120},
        {"machine_code": "M002", "machine_name": "Welding Robot A", "capacity_per_hour": 100},
    ],
    "L002": [
        {"machine_code": "M003", "machine_name": "Assembly Robot B", "capacity_per_hour": 80},
        {"machine_code": "M004", "machine_name": "CNC Machine 1", "capacity_per_hour": 75},
    ],
    "L003": [
        {"machine_code": "M005", "machine_name": "Hydraulic Press", "capacity_per_hour": 70},
        {"machine_code": "M006", "machine_name": "Quality Scanner", "capacity_per_hour": 90},
        {"machine_code": "M007", "machine_name": "Packaging Unit", "capacity_per_hour": 85},
    ],
}

_PRODUCTS_BY_LINE = {
    "L001": [
        {"product_code": "PROD-X", "product_name": "Mirror Bracket Assembly", "customer_code": "CUST-OEM-A", "standard_cycle_time": 2.5, "standard_manpower": 20},
        {"product_code": "PROD-A", "product_name": "Wiper Arm Set", "customer_code": "CUST-OEM-A", "standard_cycle_time": 2.0, "standard_manpower": 18},
    ],
    "L002": [
        {"product_code": "PROD-Z", "product_name": "Indicator Housing", "customer_code": "CUST-OEM-C", "standard_cycle_time": 1.8, "standard_manpower": 12},
        {"product_code": "PROD-B", "product_name": "Clutch Cable Set", "customer_code": "CUST-OEM-D", "standard_cycle_time": 4.0, "standard_manpower": 10},
    ],
    "L003": [
        {"product_code": "PROD-Y", "product_name": "Door Handle Assembly", "customer_code": "CUST-OEM-B", "standard_cycle_time": 3.0, "standard_manpower": 15},
    ],
}


# ── Pydantic schemas ───────────────────────────────────────────────────────────

class AttendanceInjectionRequest(BaseModel):
    plan_date: str
    shift_code: str
    absenteeism_pct: float = 0.0


# ── Seed helper ────────────────────────────────────────────────────────────────

async def _seed_master_data(session: AsyncSession) -> dict[str, Any]:
    """Seed all master data. Idempotent — returns 'already seeded' if data exists."""
    # Check if already seeded
    existing = await session.execute(
        select(SandharShift).where(SandharShift.shift_code == "A")
    )
    if existing.scalar_one_or_none():
        return {"status": "already seeded"}

    counts: dict[str, int] = {}

    # 1. Create shifts
    for s in _SHIFTS:
        session.add(SandharShift(**s))
    await session.flush()
    counts["shifts"] = len(_SHIFTS)

    # 2. Create customers — track by code
    customer_map: dict[str, uuid.UUID] = {}
    for c in _CUSTOMERS:
        cust = SandharCustomer(**c)
        session.add(cust)
        await session.flush()
        customer_map[c["customer_code"]] = cust.id
    counts["customers"] = len(_CUSTOMERS)

    # 3. Create lines — track by line_code
    line_map: dict[str, uuid.UUID] = {}
    for l in _LINES:
        line = SandharLine(
            line_code=l["line_code"],
            line_name=l["line_name"],
            capacity_per_shift=l["capacity_per_shift"],
            status="active",
        )
        session.add(line)
        await session.flush()
        line_map[l["line_code"]] = line.id
    counts["lines"] = len(_LINES)

    # 4. Create machines — track by machine_code
    machine_map: dict[str, uuid.UUID] = {}
    machine_count = 0
    for line_code, machines in _MACHINES_BY_LINE.items():
        line_id = line_map[line_code]
        for m in machines:
            machine = SandharMachine(
                machine_code=m["machine_code"],
                machine_name=m["machine_name"],
                line_id=line_id,
                capacity_per_hour=m["capacity_per_hour"],
                status="active",
            )
            session.add(machine)
            await session.flush()
            machine_map[m["machine_code"]] = machine.id
            machine_count += 1
    counts["machines"] = machine_count

    # 5. Create products
    product_count = 0
    product_map: dict[str, uuid.UUID] = {}
    for line_code, products in _PRODUCTS_BY_LINE.items():
        line_id = line_map[line_code]
        for p in products:
            customer_id = customer_map.get(p["customer_code"])
            product = SandharProduct(
                product_code=p["product_code"],
                product_name=p["product_name"],
                customer_id=customer_id,
                standard_cycle_time=p["standard_cycle_time"],
                standard_manpower=p["standard_manpower"],
                line_id=line_id,
            )
            session.add(product)
            await session.flush()
            product_map[p["product_code"]] = product.id
            product_count += 1
    counts["products"] = product_count

    # 6. Create employees
    # Shift A: 18 operators + 2 supervisors → L001
    # Shift B: 18 operators + 2 supervisors → L002
    # Shift C: 12 operators + 2 supervisors → L003
    employee_map: dict[str, list[uuid.UUID]] = {"A_ops": [], "A_sups": [], "B_ops": [], "B_sups": [], "C_ops": [], "C_sups": []}
    emp_counter = 1

    def _make_emp(shift: str, role: str, idx: int) -> SandharEmployee:
        nonlocal emp_counter
        code = f"EMP{emp_counter:04d}"
        emp_counter += 1
        designation = "Supervisor" if role == "sup" else "Operator"
        return SandharEmployee(
            employee_code=code,
            name=f"{designation} {shift}-{idx}",
            department="Production",
            designation=designation,
            grade="L3" if role == "sup" else "L1",
            shift_group=shift,
            status="active",
        )

    # Shift A operators
    for i in range(1, 19):
        emp = _make_emp("A", "op", i)
        session.add(emp)
        await session.flush()
        employee_map["A_ops"].append(emp.id)

    # Shift A supervisors
    for i in range(1, 3):
        emp = _make_emp("A", "sup", i)
        session.add(emp)
        await session.flush()
        employee_map["A_sups"].append(emp.id)

    # Shift B operators
    for i in range(1, 19):
        emp = _make_emp("B", "op", i)
        session.add(emp)
        await session.flush()
        employee_map["B_ops"].append(emp.id)

    # Shift B supervisors
    for i in range(1, 3):
        emp = _make_emp("B", "sup", i)
        session.add(emp)
        await session.flush()
        employee_map["B_sups"].append(emp.id)

    # Shift C operators
    for i in range(1, 13):
        emp = _make_emp("C", "op", i)
        session.add(emp)
        await session.flush()
        employee_map["C_ops"].append(emp.id)

    # Shift C supervisors
    for i in range(1, 3):
        emp = _make_emp("C", "sup", i)
        session.add(emp)
        await session.flush()
        employee_map["C_sups"].append(emp.id)

    counts["employees"] = sum(len(v) for v in employee_map.values())

    # 7. Create skill matrix
    skill_count = 0
    l001_id = line_map["L001"]
    l002_id = line_map["L002"]
    l003_id = line_map["L003"]

    # Shift A operators → primary L001, first 5 also cross-skilled to L002
    for i, emp_id in enumerate(employee_map["A_ops"]):
        session.add(SandharEmployeeSkill(employee_id=emp_id, line_id=l001_id, skill_level=2, active_flag=True))
        skill_count += 1
        if i < 5:
            session.add(SandharEmployeeSkill(employee_id=emp_id, line_id=l002_id, skill_level=1, active_flag=True))
            skill_count += 1

    # Shift A supervisors → L001 level 3
    for emp_id in employee_map["A_sups"]:
        session.add(SandharEmployeeSkill(employee_id=emp_id, line_id=l001_id, skill_level=3, active_flag=True))
        skill_count += 1

    # Shift B operators → primary L002, first 5 also cross-skilled to L001
    for i, emp_id in enumerate(employee_map["B_ops"]):
        session.add(SandharEmployeeSkill(employee_id=emp_id, line_id=l002_id, skill_level=2, active_flag=True))
        skill_count += 1
        if i < 5:
            session.add(SandharEmployeeSkill(employee_id=emp_id, line_id=l001_id, skill_level=1, active_flag=True))
            skill_count += 1

    # Shift B supervisors → L002 level 3
    for emp_id in employee_map["B_sups"]:
        session.add(SandharEmployeeSkill(employee_id=emp_id, line_id=l002_id, skill_level=3, active_flag=True))
        skill_count += 1

    # Shift C operators → primary L003
    for emp_id in employee_map["C_ops"]:
        session.add(SandharEmployeeSkill(employee_id=emp_id, line_id=l003_id, skill_level=2, active_flag=True))
        skill_count += 1

    # Shift C supervisors → L003 level 3
    for emp_id in employee_map["C_sups"]:
        session.add(SandharEmployeeSkill(employee_id=emp_id, line_id=l003_id, skill_level=3, active_flag=True))
        skill_count += 1

    counts["skills"] = skill_count

    # 8. Seed all machines as 'running'
    now = datetime.now(timezone.utc)
    for machine_code, machine_id in machine_map.items():
        session.add(SandharMachineStatus(
            machine_id=machine_id,
            status_datetime=now,
            machine_status="running",
            reason="Initial seeding",
            reported_by="system",
        ))
    counts["machine_statuses"] = len(machine_map)

    # 9. Create 20 open work orders spread across products
    today = date.today()
    product_ids = list(product_map.values())
    customer_ids = list(customer_map.values())
    priorities = ["high", "high", "medium", "medium", "low"]
    wo_count = 0
    for i in range(1, 21):
        prod_id = product_ids[i % len(product_ids)]
        cust_id = customer_ids[i % len(customer_ids)]
        due = today + timedelta(days=random.randint(2, 14))
        session.add(SandharWorkOrder(
            wo_number=f"WO-SEED-{i:04d}",
            product_id=prod_id,
            customer_id=cust_id,
            order_qty=random.randint(200, 1000),
            due_date=due,
            priority=priorities[i % len(priorities)],
            status="open",
        ))
        wo_count += 1
    await session.flush()
    counts["work_orders"] = wo_count

    await session.commit()
    counts["status"] = "seeded"
    return counts


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.post("/simulation/seed")
async def seed_data(
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Seed all Sandhar master data (idempotent)."""
    result = await _seed_master_data(session)
    return result


@router.post("/simulation/reset")
async def reset_data(
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Delete all Sandhar data and reseed from scratch."""
    # Delete in FK-safe order
    tables = [
        "sandhar_production_actual",
        "sandhar_resource_allocation",
        "sandhar_plan_detail",
        "sandhar_plan_header",
        "sandhar_daily_kpi",
        "sandhar_alert",
        "sandhar_quality_hold",
        "sandhar_material_availability",
        "sandhar_machine_status",
        "sandhar_work_order_operations",
        "sandhar_work_orders",
        "sandhar_attendance",
        "sandhar_employee_skill_matrix",
        "sandhar_employees",
        "sandhar_products",
        "sandhar_machines",
        "sandhar_customers",
        "sandhar_lines",
        "sandhar_shifts",
    ]
    for table in tables:
        await session.execute(text(f"DELETE FROM {table}"))
    await session.commit()

    result = await _seed_master_data(session)
    return {"reset": True, "seed": result}


@router.post("/simulation/attendance")
async def inject_attendance(
    req: AttendanceInjectionRequest,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Inject attendance records for employees in the given shift, with optional absenteeism."""
    try:
        att_date = date.fromisoformat(req.plan_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid plan_date format, use YYYY-MM-DD")

    # Get employees for this shift group
    emp_result = await session.execute(
        select(SandharEmployee).where(
            and_(
                SandharEmployee.shift_group == req.shift_code,
                SandharEmployee.status == "active",
            )
        )
    )
    employees = emp_result.scalars().all()

    if not employees:
        return {"created": 0, "present": 0, "absent": 0, "message": f"No active employees found for shift {req.shift_code}"}

    # Delete existing records for this date + shift
    await session.execute(
        text(
            "DELETE FROM sandhar_attendance WHERE attendance_date = :d AND shift_code = :s"
        ),
        {"d": att_date, "s": req.shift_code},
    )
    await session.flush()

    created = 0
    present_count = 0
    absent_count = 0
    absenteeism = max(0.0, min(1.0, req.absenteeism_pct / 100.0))

    for emp in employees:
        status = "absent" if random.random() < absenteeism else "present"
        att = SandharAttendance(
            employee_id=emp.id,
            attendance_date=att_date,
            shift_code=req.shift_code,
            status=status,
            is_manual_override=False,
        )
        session.add(att)
        created += 1
        if status == "present":
            present_count += 1
        else:
            absent_count += 1

    await session.commit()
    return {"created": created, "present": present_count, "absent": absent_count}


@router.get("/simulation/scenarios")
async def list_scenarios(
    _: str = Depends(verify_api_key),
):
    """Return hardcoded list of demo scenarios."""
    return [
        {
            "id": "s1-normal",
            "name": "Normal Day",
            "description": "Create 3 work orders and seed full attendance for all shifts. All machines running.",
        },
        {
            "id": "s2-absenteeism",
            "name": "High Absenteeism (Shift A)",
            "description": "Mark 20% of Shift A employees as absent for today.",
        },
        {
            "id": "s3-breakdown",
            "name": "Machine Breakdown",
            "description": "Simulate hydraulic press (M005) breakdown on L003.",
        },
        {
            "id": "s4-material-shortage",
            "name": "Material Shortage",
            "description": "Create material shortfall for PROD-Y (Door Handle Assembly).",
        },
        {
            "id": "s5-priority-conflict",
            "name": "Priority Conflict",
            "description": "Create two competing high-priority work orders for L001 due today.",
        },
        {
            "id": "s6-skill-gap",
            "name": "Skill Gap on L003",
            "description": "Deactivate all L003 operator skills, simulating a certification gap.",
        },
        {
            "id": "s7-underachievement",
            "name": "Underachievement",
            "description": "Submit actuals at 65% of planned qty for L003 (triggers alert).",
        },
        {
            "id": "s8-full-day",
            "name": "Full Day Simulation",
            "description": "Seed 5 work orders and attendance for all shifts.",
        },
    ]


@router.post("/simulation/scenario/{scenario_id}")
async def run_scenario(
    scenario_id: str = Path(...),
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Execute a named simulation scenario."""
    today = date.today()
    tomorrow = today + timedelta(days=1)
    now = datetime.now(timezone.utc)

    # ── s1-normal ──────────────────────────────────────────────────────────────
    if scenario_id == "s1-normal":
        # Get first 3 products
        prod_result = await session.execute(select(SandharProduct).limit(3))
        products = prod_result.scalars().all()

        wo_created = 0
        for i, product in enumerate(products):
            wo_num = f"WO-S1-{today.isoformat()}-{i+1:03d}"
            existing = await session.execute(
                select(SandharWorkOrder).where(SandharWorkOrder.wo_number == wo_num)
            )
            if not existing.scalar_one_or_none():
                wo = SandharWorkOrder(
                    wo_number=wo_num,
                    customer_id=product.customer_id,
                    product_id=product.id,
                    order_qty=random.randint(400, 800),
                    due_date=tomorrow,
                    priority="medium",
                    status="open",
                    quality_hold=False,
                )
                session.add(wo)
                wo_created += 1

        # Seed attendance for all shifts (all present)
        for shift_code in ["A", "B", "C"]:
            emp_result = await session.execute(
                select(SandharEmployee).where(
                    and_(SandharEmployee.shift_group == shift_code, SandharEmployee.status == "active")
                )
            )
            employees = emp_result.scalars().all()
            await session.execute(
                text("DELETE FROM sandhar_attendance WHERE attendance_date = :d AND shift_code = :s"),
                {"d": today.isoformat(), "s": shift_code},
            )
            for emp in employees:
                session.add(SandharAttendance(
                    employee_id=emp.id,
                    attendance_date=today,
                    shift_code=shift_code,
                    status="present",
                    is_manual_override=False,
                ))

        # Set all machines running
        machine_result = await session.execute(select(SandharMachine))
        machines = machine_result.scalars().all()
        for machine in machines:
            session.add(SandharMachineStatus(
                machine_id=machine.id,
                status_datetime=now,
                machine_status="running",
                reported_by="scenario-s1",
            ))

        await session.commit()
        return {"scenario": "s1-normal", "work_orders_created": wo_created, "shifts_seeded": 3}

    # ── s2-absenteeism ─────────────────────────────────────────────────────────
    elif scenario_id == "s2-absenteeism":
        emp_result = await session.execute(
            select(SandharEmployee).where(
                and_(SandharEmployee.shift_group == "A", SandharEmployee.status == "active")
            )
        )
        employees = emp_result.scalars().all()

        await session.execute(
            text("DELETE FROM sandhar_attendance WHERE attendance_date = :d AND shift_code = :s"),
            {"d": today.isoformat(), "s": "A"},
        )

        present_count = 0
        absent_count = 0
        for emp in employees:
            status = "absent" if random.random() < 0.20 else "present"
            session.add(SandharAttendance(
                employee_id=emp.id,
                attendance_date=today,
                shift_code="A",
                status=status,
                is_manual_override=False,
            ))
            if status == "present":
                present_count += 1
            else:
                absent_count += 1

        await session.commit()
        return {"scenario": "s2-absenteeism", "total": len(employees), "present": present_count, "absent": absent_count}

    # ── s3-breakdown ───────────────────────────────────────────────────────────
    elif scenario_id == "s3-breakdown":
        m005_result = await session.execute(
            select(SandharMachine).where(SandharMachine.machine_code == "M005")
        )
        m005 = m005_result.scalar_one_or_none()
        if not m005:
            raise HTTPException(status_code=404, detail="Machine M005 not found. Run /simulation/seed first.")

        status_record = SandharMachineStatus(
            machine_id=m005.id,
            status_datetime=now,
            machine_status="breakdown",
            reason="Hydraulic failure",
            estimated_restore_datetime=now + timedelta(hours=4),
            reported_by="scenario-s3",
        )
        session.add(status_record)
        await session.commit()
        return {"scenario": "s3-breakdown", "machine": "M005", "status": "breakdown", "reason": "Hydraulic failure"}

    # ── s4-material-shortage ───────────────────────────────────────────────────
    elif scenario_id == "s4-material-shortage":
        prod_y_result = await session.execute(
            select(SandharProduct).where(SandharProduct.product_code == "PROD-Y")
        )
        prod_y = prod_y_result.scalar_one_or_none()
        if not prod_y:
            raise HTTPException(status_code=404, detail="Product PROD-Y not found. Run /simulation/seed first.")

        # Upsert material availability
        existing_mat = await session.execute(
            select(SandharMaterialAvailability).where(
                and_(
                    SandharMaterialAvailability.product_id == prod_y.id,
                    SandharMaterialAvailability.plan_date == today,
                )
            )
        )
        mat = existing_mat.scalar_one_or_none()
        if mat:
            mat.available_qty = 600
            mat.required_qty = 800
            mat.shortfall_qty = 200
            mat.constraint_flag = True
        else:
            mat = SandharMaterialAvailability(
                product_id=prod_y.id,
                plan_date=today,
                available_qty=600,
                required_qty=800,
                shortfall_qty=200,
                constraint_flag=True,
            )
            session.add(mat)

        await session.commit()
        return {"scenario": "s4-material-shortage", "product": "PROD-Y", "available": 600, "required": 800, "shortfall": 200}

    # ── s5-priority-conflict ───────────────────────────────────────────────────
    elif scenario_id == "s5-priority-conflict":
        # Get L001 products
        l001_result = await session.execute(
            select(SandharLine).where(SandharLine.line_code == "L001")
        )
        l001 = l001_result.scalar_one_or_none()

        prod_result = await session.execute(
            select(SandharProduct).where(SandharProduct.line_id == l001.id if l001 else None).limit(2)
        )
        products = prod_result.scalars().all()

        wo_created = 0
        for i, product in enumerate(products[:2]):
            wo_num = f"WO-S5-HIGH-{today.isoformat()}-{i+1}"
            existing = await session.execute(
                select(SandharWorkOrder).where(SandharWorkOrder.wo_number == wo_num)
            )
            if not existing.scalar_one_or_none():
                wo = SandharWorkOrder(
                    wo_number=wo_num,
                    customer_id=product.customer_id,
                    product_id=product.id,
                    order_qty=900,
                    due_date=today,
                    priority="high",
                    status="open",
                    quality_hold=False,
                )
                session.add(wo)
                wo_created += 1

        await session.commit()
        return {"scenario": "s5-priority-conflict", "work_orders_created": wo_created, "priority": "high", "line": "L001"}

    # ── s6-skill-gap ───────────────────────────────────────────────────────────
    elif scenario_id == "s6-skill-gap":
        l003_result = await session.execute(
            select(SandharLine).where(SandharLine.line_code == "L003")
        )
        l003 = l003_result.scalar_one_or_none()
        if not l003:
            raise HTTPException(status_code=404, detail="Line L003 not found. Run /simulation/seed first.")

        skills_result = await session.execute(
            select(SandharEmployeeSkill).where(SandharEmployeeSkill.line_id == l003.id)
        )
        skills = skills_result.scalars().all()
        for skill in skills:
            skill.active_flag = False

        await session.commit()
        return {"scenario": "s6-skill-gap", "line": "L003", "skills_deactivated": len(skills)}

    # ── s7-underachievement ────────────────────────────────────────────────────
    elif scenario_id == "s7-underachievement":
        l003_result = await session.execute(
            select(SandharLine).where(SandharLine.line_code == "L003")
        )
        l003 = l003_result.scalar_one_or_none()
        if not l003:
            raise HTTPException(status_code=404, detail="Line L003 not found. Run /simulation/seed first.")

        # Find any plan detail for L003
        detail_result = await session.execute(
            select(SandharPlanDetail).where(SandharPlanDetail.line_id == l003.id).limit(1)
        )
        detail = detail_result.scalar_one_or_none()

        if not detail:
            return {
                "scenario": "s7-underachievement",
                "status": "skipped",
                "message": "No plan found — generate a plan first",
            }

        planned_qty = detail.planned_qty or 100
        produced_qty = int(planned_qty * 0.65)
        achievement_pct = round(produced_qty / planned_qty * 100, 2)

        actual = SandharProductionActual(
            plan_detail_id=detail.id,
            shift_code=None,
            produced_qty=produced_qty,
            rejected_qty=int(produced_qty * 0.05),
            rework_qty=int(produced_qty * 0.03),
            downtime_minutes=45,
            achievement_pct=achievement_pct,
            submitted_by="scenario-s7",
            submitted_at=now,
        )
        session.add(actual)

        # Create underachievement alert
        alert = SandharAlert(
            alert_type="production_delay",
            alert_message=f"L003 production at {achievement_pct}% — below 70% threshold (scenario s7)",
            severity="high",
            status="active",
            plan_date=today,
            related_line_id=l003.id,
            related_wo_id=detail.wo_id,
        )
        session.add(alert)

        await session.commit()
        return {
            "scenario": "s7-underachievement",
            "plan_detail_id": str(detail.id),
            "planned_qty": planned_qty,
            "produced_qty": produced_qty,
            "achievement_pct": achievement_pct,
            "alert_created": True,
        }

    # ── s8-full-day ────────────────────────────────────────────────────────────
    elif scenario_id == "s8-full-day":
        # Seed 5 work orders
        prod_result = await session.execute(select(SandharProduct).limit(5))
        products = prod_result.scalars().all()

        wo_created = 0
        for i, product in enumerate(products):
            wo_num = f"WO-S8-{today.isoformat()}-{i+1:03d}"
            existing = await session.execute(
                select(SandharWorkOrder).where(SandharWorkOrder.wo_number == wo_num)
            )
            if not existing.scalar_one_or_none():
                wo = SandharWorkOrder(
                    wo_number=wo_num,
                    customer_id=product.customer_id,
                    product_id=product.id,
                    order_qty=random.randint(300, 900),
                    due_date=tomorrow,
                    priority=random.choice(["high", "medium", "medium", "low"]),
                    status="open",
                    quality_hold=False,
                )
                session.add(wo)
                wo_created += 1

        # Seed attendance for all shifts
        total_att = 0
        for shift_code in ["A", "B", "C"]:
            emp_result = await session.execute(
                select(SandharEmployee).where(
                    and_(SandharEmployee.shift_group == shift_code, SandharEmployee.status == "active")
                )
            )
            employees = emp_result.scalars().all()
            await session.execute(
                text("DELETE FROM sandhar_attendance WHERE attendance_date = :d AND shift_code = :s"),
                {"d": today.isoformat(), "s": shift_code},
            )
            for emp in employees:
                session.add(SandharAttendance(
                    employee_id=emp.id,
                    attendance_date=today,
                    shift_code=shift_code,
                    status="present",
                    is_manual_override=False,
                ))
                total_att += 1

        await session.commit()
        return {
            "scenario": "s8-full-day",
            "work_orders_created": wo_created,
            "attendance_records_created": total_att,
            "shifts": ["A", "B", "C"],
        }

    else:
        raise HTTPException(status_code=404, detail=f"Unknown scenario '{scenario_id}'")

"""Sandhar work order endpoints."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, and_, or_, case
from sqlalchemy.ext.asyncio import AsyncSession

from fde_agent.api.dependencies import verify_api_key
from fde_agent.db.models import SandharCustomer, SandharLine, SandharProduct, SandharWorkOrder
from fde_agent.db.session import get_session

router = APIRouter(prefix="/api/v1/sandhar", tags=["sandhar"])


# ── Serializer ─────────────────────────────────────────────────────────────────

def _wo_out(w: SandharWorkOrder) -> dict[str, Any]:
    return {
        "id": str(w.id),
        "wo_number": w.wo_number,
        "customer_id": str(w.customer_id) if w.customer_id else None,
        "product_id": str(w.product_id) if w.product_id else None,
        "order_qty": w.order_qty,
        "due_date": w.due_date.isoformat(),
        "priority": w.priority,
        "status": w.status,
        "quality_hold": w.quality_hold,
        "created_at": w.created_at.isoformat(),
        "updated_at": w.updated_at.isoformat(),
    }


# ── Pydantic schemas ───────────────────────────────────────────────────────────

class CreateWorkOrder(BaseModel):
    wo_number: str
    customer_id: str | None = None
    product_id: str | None = None
    order_qty: int
    due_date: str  # YYYY-MM-DD
    priority: str = "medium"
    status: str = "open"


class UpdateWorkOrder(BaseModel):
    priority: str | None = None
    due_date: str | None = None
    status: str | None = None
    quality_hold: bool | None = None


# ── Endpoints — NOTE: /open must come before /{id} ────────────────────────────

@router.get("/work-orders/open")
async def list_open_work_orders(
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """List open/planned work orders not on quality hold, ordered by priority then due date."""
    priority_order = case(
        {"high": 1, "medium": 2, "low": 3},
        value=SandharWorkOrder.priority,
        else_=4,
    )
    q = (
        select(SandharWorkOrder)
        .where(
            and_(
                SandharWorkOrder.status.in_(["open", "planned"]),
                SandharWorkOrder.quality_hold == False,
            )
        )
        .order_by(priority_order, SandharWorkOrder.due_date.asc())
        .limit(50)
    )
    rows = await session.execute(q)
    work_orders = rows.scalars().all()

    # Enrich with product and line info
    result = []
    for wo in work_orders:
        entry = _wo_out(wo)
        if wo.product_id:
            prod_result = await session.execute(
                select(SandharProduct).where(SandharProduct.id == wo.product_id)
            )
            prod = prod_result.scalar_one_or_none()
            if prod:
                entry["product_code"] = prod.product_code
                entry["product_name"] = prod.product_name
                entry["standard_manpower"] = prod.standard_manpower
                entry["standard_cycle_time"] = prod.standard_cycle_time
                if prod.line_id:
                    line_result = await session.execute(
                        select(SandharLine).where(SandharLine.id == prod.line_id)
                    )
                    line = line_result.scalar_one_or_none()
                    if line:
                        entry["line_id"] = str(line.id)
                        entry["line_code"] = line.line_code
                        entry["line_name"] = line.line_name
        result.append(entry)
    return result


@router.get("/work-orders/{work_order_id}")
async def get_work_order(
    work_order_id: str,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Get full work order detail including product and customer info."""
    try:
        wid = uuid.UUID(work_order_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid work order ID format")

    result = await session.execute(select(SandharWorkOrder).where(SandharWorkOrder.id == wid))
    wo = result.scalar_one_or_none()
    if not wo:
        raise HTTPException(status_code=404, detail=f"Work order '{work_order_id}' not found")

    entry = _wo_out(wo)

    if wo.product_id:
        prod_result = await session.execute(
            select(SandharProduct).where(SandharProduct.id == wo.product_id)
        )
        prod = prod_result.scalar_one_or_none()
        if prod:
            entry["product_code"] = prod.product_code
            entry["product_name"] = prod.product_name
            entry["standard_manpower"] = prod.standard_manpower
            entry["standard_cycle_time"] = prod.standard_cycle_time
            if prod.line_id:
                line_result = await session.execute(
                    select(SandharLine).where(SandharLine.id == prod.line_id)
                )
                line = line_result.scalar_one_or_none()
                if line:
                    entry["line_id"] = str(line.id)
                    entry["line_code"] = line.line_code
                    entry["line_name"] = line.line_name

    if wo.customer_id:
        cust_result = await session.execute(
            select(SandharCustomer).where(SandharCustomer.id == wo.customer_id)
        )
        cust = cust_result.scalar_one_or_none()
        if cust:
            entry["customer_name"] = cust.customer_name
            entry["customer_code"] = cust.customer_code

    return entry


@router.get("/work-orders")
async def list_work_orders(
    status: str | None = Query(None),
    priority: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """List work orders with optional filters, ordered by due date ascending."""
    q = select(SandharWorkOrder).order_by(SandharWorkOrder.due_date.asc()).limit(limit)
    if status:
        q = q.where(SandharWorkOrder.status == status)
    if priority:
        q = q.where(SandharWorkOrder.priority == priority)
    rows = await session.execute(q)
    return [_wo_out(w) for w in rows.scalars().all()]


@router.post("/work-orders")
async def create_work_order(
    req: CreateWorkOrder,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Create a new work order."""
    try:
        due_date = date.fromisoformat(req.due_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid due_date format, use YYYY-MM-DD")

    customer_id = None
    if req.customer_id:
        try:
            customer_id = uuid.UUID(req.customer_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid customer_id format")

    product_id = None
    if req.product_id:
        try:
            product_id = uuid.UUID(req.product_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid product_id format")

    wo = SandharWorkOrder(
        wo_number=req.wo_number,
        customer_id=customer_id,
        product_id=product_id,
        order_qty=req.order_qty,
        due_date=due_date,
        priority=req.priority,
        status=req.status,
        quality_hold=False,
    )
    session.add(wo)
    await session.commit()
    await session.refresh(wo)
    return _wo_out(wo)


@router.put("/work-orders/{work_order_id}")
async def update_work_order(
    work_order_id: str,
    req: UpdateWorkOrder,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Update non-null fields of a work order."""
    try:
        wid = uuid.UUID(work_order_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid work order ID format")

    result = await session.execute(select(SandharWorkOrder).where(SandharWorkOrder.id == wid))
    wo = result.scalar_one_or_none()
    if not wo:
        raise HTTPException(status_code=404, detail=f"Work order '{work_order_id}' not found")

    if req.priority is not None:
        wo.priority = req.priority
    if req.due_date is not None:
        try:
            wo.due_date = date.fromisoformat(req.due_date)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid due_date format, use YYYY-MM-DD")
    if req.status is not None:
        wo.status = req.status
    if req.quality_hold is not None:
        wo.quality_hold = req.quality_hold

    await session.commit()
    await session.refresh(wo)
    return _wo_out(wo)

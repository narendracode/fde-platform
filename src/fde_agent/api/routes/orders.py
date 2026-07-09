"""Orders API — shipment dispatch workflow for pharma distributor demo."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fde_agent.api.dependencies import verify_api_key
from fde_agent.db.models import Order
from fde_agent.db.session import get_session

router = APIRouter(prefix="/api/v1/orders", tags=["orders"])


# ── Helpers ───────────────────────────────────────────────────────────────────

def _urgency_days(due: date) -> int:
    return (due - date.today()).days


def _order_out(o: Order) -> dict[str, Any]:
    return {
        "id": str(o.id),
        "order_ref": o.order_ref,
        "retailer_name": o.retailer_name,
        "medicine_name": o.medicine_name,
        "quantity": o.quantity,
        "unit_price_usd": o.unit_price_usd,
        "order_amount_usd": o.order_amount_usd,
        "margin_percent": o.margin_percent,
        "due_date": o.due_date.isoformat(),
        "urgency_days": _urgency_days(o.due_date),
        "status": o.status,
        "shipment_mode": o.shipment_mode,
        "decided_by": o.decided_by,
        "ai_recommended_mode": o.ai_recommended_mode,
        "ai_confidence": o.ai_confidence,
        "ai_reasoning": o.ai_reasoning,
        "agent_run_id": str(o.agent_run_id) if o.agent_run_id else None,
        "dispatched_at": o.dispatched_at.isoformat() if o.dispatched_at else None,
        "created_at": o.created_at.isoformat(),
        "updated_at": o.updated_at.isoformat(),
    }


async def _get_order_or_404(session: AsyncSession, order_id: str) -> Order:
    try:
        oid = uuid.UUID(order_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid order ID format")
    result = await session.execute(select(Order).where(Order.id == oid))
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail=f"Order '{order_id}' not found")
    return order


# ── Request schemas ───────────────────────────────────────────────────────────

class DispatchRequest(BaseModel):
    mode: str                       # air | train | road
    decided_by: str = "human"       # human | ai
    reasoning: str | None = None
    agent_run_id: str | None = None


class RecommendRequest(BaseModel):
    mode: str
    confidence: str = "medium"      # high | medium | low
    reasoning: str
    agent_run_id: str | None = None


class ApproveRequest(BaseModel):
    override_mode: str | None = None  # if set, human overrides the AI's recommendation


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("")
async def list_orders(
    status_filter: str | None = Query(None, alias="status"),
    limit: int | None = Query(None, ge=1, le=1000),
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """List orders. Optionally filter by status. Use limit to cap results."""
    q = select(Order).order_by(Order.due_date.asc(), Order.order_amount_usd.desc())
    if status_filter:
        q = q.where(Order.status == status_filter)
    if limit is not None:
        q = q.limit(limit)
    rows = await session.execute(q)
    return [_order_out(o) for o in rows.scalars().all()]


@router.get("/{order_id}")
async def get_order(
    order_id: str,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Get a single order by ID."""
    order = await _get_order_or_404(session, order_id)
    return _order_out(order)


@router.patch("/{order_id}/dispatch")
async def dispatch_order(
    order_id: str,
    req: DispatchRequest,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Set shipment mode and mark order as ready_to_dispatch.

    Called by both the human UI form and the AI dispatch tool — same endpoint,
    same behaviour. The `decided_by` field records the source.
    """
    if req.mode not in ("air", "train", "road"):
        raise HTTPException(status_code=400, detail="mode must be air, train, or road")
    if req.decided_by not in ("human", "ai"):
        raise HTTPException(status_code=400, detail="decided_by must be human or ai")

    order = await _get_order_or_404(session, order_id)
    if order.status not in ("pending", "pending_review"):
        raise HTTPException(
            status_code=409,
            detail=f"Order is '{order.status}' — only pending or pending_review orders can be dispatched",
        )

    order.shipment_mode = req.mode
    order.decided_by = req.decided_by
    order.status = "ready_to_dispatch"
    order.dispatched_at = datetime.now(timezone.utc)
    if req.agent_run_id:
        try:
            order.agent_run_id = uuid.UUID(req.agent_run_id)
        except ValueError:
            pass

    await session.commit()
    await session.refresh(order)
    return _order_out(order)


@router.patch("/{order_id}/recommend")
async def recommend_dispatch(
    order_id: str,
    req: RecommendRequest,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Store an AI shipment recommendation without dispatching.

    Moves the order to pending_review status. The human analyst then reviews
    and either approves or rejects the recommendation from the dashboard.
    Used by the AI agent when human_in_the_loop = true.
    """
    if req.mode not in ("air", "train", "road"):
        raise HTTPException(status_code=400, detail="mode must be air, train, or road")

    order = await _get_order_or_404(session, order_id)
    if order.status != "pending":
        raise HTTPException(
            status_code=409,
            detail=f"Order is '{order.status}' — only pending orders can receive a recommendation",
        )

    order.ai_recommended_mode = req.mode
    order.ai_confidence = req.confidence
    order.ai_reasoning = req.reasoning
    order.status = "pending_review"
    if req.agent_run_id:
        try:
            order.agent_run_id = uuid.UUID(req.agent_run_id)
        except ValueError:
            pass

    await session.commit()
    await session.refresh(order)
    return _order_out(order)


@router.post("/{order_id}/approve")
async def approve_recommendation(
    order_id: str,
    req: ApproveRequest,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Human approves the AI recommendation (optionally overriding the mode).

    Moves the order to ready_to_dispatch with decided_by=human.
    """
    order = await _get_order_or_404(session, order_id)
    if order.status != "pending_review":
        raise HTTPException(
            status_code=409,
            detail=f"Order is '{order.status}' — only pending_review orders can be approved",
        )

    final_mode = req.override_mode or order.ai_recommended_mode
    if not final_mode:
        raise HTTPException(status_code=400, detail="No mode to apply — no AI recommendation and no override_mode")

    order.shipment_mode = final_mode
    order.decided_by = "human"
    order.status = "ready_to_dispatch"
    order.dispatched_at = datetime.now(timezone.utc)

    await session.commit()
    await session.refresh(order)
    return _order_out(order)


@router.post("/{order_id}/reject")
async def reject_recommendation(
    order_id: str,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Human rejects the AI recommendation — order returns to pending for manual processing."""
    order = await _get_order_or_404(session, order_id)
    if order.status != "pending_review":
        raise HTTPException(
            status_code=409,
            detail=f"Order is '{order.status}' — only pending_review orders can be rejected",
        )

    order.status = "pending"
    order.ai_recommended_mode = None
    order.ai_confidence = None
    order.ai_reasoning = None
    order.agent_run_id = None

    await session.commit()
    await session.refresh(order)
    return _order_out(order)


@router.post("/seed", status_code=status.HTTP_201_CREATED)
async def seed_orders(
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Seed demo orders (idempotent — skips existing order_refs)."""
    from datetime import timedelta

    today = date.today()

    def d(days: int) -> date:
        return today + timedelta(days=days)

    SEED_DATA = [
        ("ORD-2026-001", "MedPlus Delhi",        "Amoxicillin 500mg",   500,  28.40, 18.0, d(1)),
        ("ORD-2026-002", "Apollo Pharmacy",       "Insulin Glargine",    200,  54.00, 20.0, d(1)),
        ("ORD-2026-003", "Sai Medical Stores",    "Paracetamol 650mg",  1000,   2.20, 12.0, d(1)),
        ("ORD-2026-004", "HealthFirst Retail",    "Cetirizine 10mg",     800,   1.80, 10.0, d(2)),
        ("ORD-2026-005", "Wellness Forever",      "Metformin 500mg",    1200,   8.50, 15.0, d(3)),
        ("ORD-2026-006", "Noble Chemists",        "Atorvastatin 20mg",   600,  18.00, 17.0, d(3)),
        ("ORD-2026-007", "Lifeline Pharmacy",     "Azithromycin 500mg",  400,  24.00, 22.0, d(4)),
        ("ORD-2026-008", "CureWell Medicals",     "Omeprazole 20mg",     900,   6.50, 28.0, d(4)),
        ("ORD-2026-009", "PharmaEase",            "Pantoprazole 40mg",   700,   9.00, 30.0, d(5)),
        ("ORD-2026-010", "City Chemist",          "Vitamin D3 1000IU", 2000,   4.00, 14.0, d(8)),
        ("ORD-2026-011", "Raj Medicals",          "Calcium Carbonate",  1500,   3.50, 11.0, d(9)),
        ("ORD-2026-012", "Sunrise Pharmacy",      "Iron Sucrose 100mg",  300,  12.00, 16.0, d(10)),
        ("ORD-2026-013", "BestCare Retail",       "Multivitamin Tab",   3000,   1.50,  9.0, d(12)),
        ("ORD-2026-014", "Greenleaf Medical",     "Zinc Sulphate 50mg", 2500,   2.00, 13.0, d(14)),
        ("ORD-2026-015", "Prime Pharmacy",        "Rosuvastatin 10mg",   500,  22.00, 32.0, d(7)),
        ("ORD-2026-016", "Excel Medicals",        "Dapagliflozin 10mg",  300,  45.00, 35.0, d(8)),
        ("ORD-2026-017", "National Pharma",       "Amlodipine 5mg",     1800,   5.50, 19.0, d(2)),
        ("ORD-2026-018", "Medicity Stores",       "Losartan 50mg",       800,  14.00, 24.0, d(5)),
        ("ORD-2026-019", "LifeCare Hub",          "Montelukast 10mg",    600,  11.00, 21.0, d(3)),
        ("ORD-2026-020", "Alpha Pharmaceuticals", "Clopidogrel 75mg",    400,  32.00, 26.0, d(6)),
    ]

    from sqlalchemy import text
    created = 0
    skipped = 0
    for ref, retailer, medicine, qty, unit_price, margin, due in SEED_DATA:
        exists = (await session.execute(
            text("SELECT 1 FROM orders WHERE order_ref = :ref"), {"ref": ref}
        )).first()
        if exists:
            skipped += 1
            continue
        session.add(Order(
            order_ref=ref,
            retailer_name=retailer,
            medicine_name=medicine,
            quantity=qty,
            unit_price_usd=unit_price,
            order_amount_usd=round(qty * unit_price, 2),
            margin_percent=margin,
            due_date=due,
            status="pending",
        ))
        created += 1

    await session.commit()
    return {"created": created, "skipped": skipped, "total": created + skipped}

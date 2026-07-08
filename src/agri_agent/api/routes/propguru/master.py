"""Propguru master data CRUD endpoints."""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agri_agent.api.dependencies import verify_api_key
from agri_agent.db.models import (
    PropguruChannelPartner,
    PropguruEvaluationCriteria,
    PropguruMarketComp,
    PropguruProperty,
)
from agri_agent.db.session import get_session

router = APIRouter(prefix="/api/v1/propguru", tags=["propguru"])


# ── Serializers ────────────────────────────────────────────────────────────────

def _cp_out(c: PropguruChannelPartner) -> dict[str, Any]:
    return {
        "id": str(c.id),
        "cp_code": c.cp_code,
        "name": c.name,
        "cp_type": c.cp_type,
        "phone": c.phone,
        "email": c.email,
        "city": c.city,
        "status": c.status,
        "commission_pct": c.commission_pct,
        "created_at": c.created_at.isoformat(),
        "updated_at": c.updated_at.isoformat(),
    }


def _criteria_out(c: PropguruEvaluationCriteria) -> dict[str, Any]:
    return {
        "id": str(c.id),
        "criterion_code": c.criterion_code,
        "name": c.name,
        "category": c.category,
        "weight": c.weight,
        "scoring_type": c.scoring_type,
        "description": c.description,
        "is_active": c.is_active,
        "sort_order": c.sort_order,
        "created_at": c.created_at.isoformat(),
    }


def _property_out(p: PropguruProperty) -> dict[str, Any]:
    return {
        "id": str(p.id),
        "property_code": p.property_code,
        "address_line1": p.address_line1,
        "city": p.city,
        "locality": p.locality,
        "pincode": p.pincode,
        "property_type": p.property_type,
        "carpet_area_sqft": p.carpet_area_sqft,
        "built_up_area_sqft": p.built_up_area_sqft,
        "bedrooms": p.bedrooms,
        "bathrooms": p.bathrooms,
        "floor_number": p.floor_number,
        "total_floors": p.total_floors,
        "building_age_years": p.building_age_years,
        "facing": p.facing,
        "latitude": p.latitude,
        "longitude": p.longitude,
        "created_at": p.created_at.isoformat(),
        "updated_at": p.updated_at.isoformat(),
    }


def _comp_out(c: PropguruMarketComp) -> dict[str, Any]:
    return {
        "id": str(c.id),
        "locality": c.locality,
        "property_type": c.property_type,
        "avg_price_per_sqft": c.avg_price_per_sqft,
        "min_price_per_sqft": c.min_price_per_sqft,
        "max_price_per_sqft": c.max_price_per_sqft,
        "price_trend_6m_pct": c.price_trend_6m_pct,
        "transaction_count_6m": c.transaction_count_6m,
        "data_source": c.data_source,
        "as_of_date": c.as_of_date.isoformat() if c.as_of_date else None,
        "created_at": c.created_at.isoformat(),
    }


# ── Pydantic schemas ───────────────────────────────────────────────────────────

class CreateChannelPartner(BaseModel):
    cp_code: str
    name: str
    cp_type: str | None = None
    phone: str | None = None
    email: str | None = None
    city: str | None = None
    status: str = "active"
    commission_pct: float | None = None


class UpdateChannelPartner(BaseModel):
    name: str | None = None
    cp_type: str | None = None
    phone: str | None = None
    email: str | None = None
    city: str | None = None
    status: str | None = None
    commission_pct: float | None = None


class UpdateCriterion(BaseModel):
    weight: float | None = None
    description: str | None = None
    is_active: bool | None = None


# ── Channel Partner endpoints ──────────────────────────────────────────────────

@router.get("/channel-partners")
async def list_channel_partners(
    cp_type: str | None = Query(None),
    status: str | None = Query(None),
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    q = select(PropguruChannelPartner).order_by(PropguruChannelPartner.cp_code)
    if cp_type:
        q = q.where(PropguruChannelPartner.cp_type == cp_type)
    if status:
        q = q.where(PropguruChannelPartner.status == status)
    rows = (await session.execute(q)).scalars().all()
    return [_cp_out(c) for c in rows]


@router.get("/channel-partners/{cp_id}")
async def get_channel_partner(
    cp_id: str,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    try:
        uid = uuid.UUID(cp_id)
        row = (await session.execute(select(PropguruChannelPartner).where(PropguruChannelPartner.id == uid))).scalar_one_or_none()
    except ValueError:
        row = (await session.execute(select(PropguruChannelPartner).where(PropguruChannelPartner.cp_code == cp_id))).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail=f"Channel partner '{cp_id}' not found")
    return _cp_out(row)


@router.post("/channel-partners")
async def create_channel_partner(
    req: CreateChannelPartner,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    cp = PropguruChannelPartner(**req.model_dump())
    session.add(cp)
    await session.commit()
    await session.refresh(cp)
    return _cp_out(cp)


@router.put("/channel-partners/{cp_id}")
async def update_channel_partner(
    cp_id: str,
    req: UpdateChannelPartner,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    try:
        uid = uuid.UUID(cp_id)
        cp = (await session.execute(select(PropguruChannelPartner).where(PropguruChannelPartner.id == uid))).scalar_one_or_none()
    except ValueError:
        cp = (await session.execute(select(PropguruChannelPartner).where(PropguruChannelPartner.cp_code == cp_id))).scalar_one_or_none()
    if not cp:
        raise HTTPException(status_code=404, detail=f"Channel partner '{cp_id}' not found")
    for field, value in req.model_dump(exclude_none=True).items():
        setattr(cp, field, value)
    await session.commit()
    await session.refresh(cp)
    return _cp_out(cp)


# ── Evaluation Criteria endpoints ──────────────────────────────────────────────

@router.get("/evaluation-criteria")
async def list_evaluation_criteria(
    category: str | None = Query(None),
    is_active: bool | None = Query(None),
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    q = select(PropguruEvaluationCriteria).order_by(
        PropguruEvaluationCriteria.sort_order, PropguruEvaluationCriteria.criterion_code
    )
    if category:
        q = q.where(PropguruEvaluationCriteria.category == category)
    if is_active is not None:
        q = q.where(PropguruEvaluationCriteria.is_active == is_active)
    rows = (await session.execute(q)).scalars().all()
    return [_criteria_out(c) for c in rows]


@router.put("/evaluation-criteria/{criterion_id}")
async def update_criterion(
    criterion_id: str,
    req: UpdateCriterion,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    try:
        uid = uuid.UUID(criterion_id)
        crit = (await session.execute(select(PropguruEvaluationCriteria).where(PropguruEvaluationCriteria.id == uid))).scalar_one_or_none()
    except ValueError:
        crit = (await session.execute(select(PropguruEvaluationCriteria).where(PropguruEvaluationCriteria.criterion_code == criterion_id))).scalar_one_or_none()
    if not crit:
        raise HTTPException(status_code=404, detail=f"Criterion '{criterion_id}' not found")
    for field, value in req.model_dump(exclude_none=True).items():
        setattr(crit, field, value)
    await session.commit()
    await session.refresh(crit)
    return _criteria_out(crit)


# ── Property endpoints ─────────────────────────────────────────────────────────

@router.get("/properties")
async def list_properties(
    city: str | None = Query(None),
    property_type: str | None = Query(None),
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    q = select(PropguruProperty).order_by(PropguruProperty.property_code)
    if city:
        q = q.where(PropguruProperty.city == city)
    if property_type:
        q = q.where(PropguruProperty.property_type == property_type)
    rows = (await session.execute(q)).scalars().all()
    return [_property_out(p) for p in rows]


@router.get("/properties/{property_id}")
async def get_property(
    property_id: str,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    try:
        uid = uuid.UUID(property_id)
        prop = (await session.execute(select(PropguruProperty).where(PropguruProperty.id == uid))).scalar_one_or_none()
    except ValueError:
        prop = (await session.execute(select(PropguruProperty).where(PropguruProperty.property_code == property_id))).scalar_one_or_none()
    if not prop:
        raise HTTPException(status_code=404, detail=f"Property '{property_id}' not found")
    return _property_out(prop)


# ── Market Comp endpoints ──────────────────────────────────────────────────────

@router.get("/market-comps")
async def list_market_comps(
    locality: str | None = Query(None),
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    q = select(PropguruMarketComp).order_by(PropguruMarketComp.locality)
    if locality:
        q = q.where(PropguruMarketComp.locality.ilike(f"%{locality}%"))
    rows = (await session.execute(q)).scalars().all()
    return [_comp_out(c) for c in rows]


# Deal list moved to deals.py (Phase 2)

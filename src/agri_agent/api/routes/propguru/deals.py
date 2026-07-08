"""Propguru deal CRUD + stage transition endpoints."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agri_agent.api.dependencies import verify_api_key
from agri_agent.db.models import (
    Agent,
    AgentRun,
    PropguruChannelPartner,
    PropguruDeal,
    PropguruProperty,
)
from agri_agent.db.session import get_session

router = APIRouter(prefix="/api/v1/propguru", tags=["propguru"])

VALID_STAGES = [
    "lead",
    "evaluation_pending",
    "evaluation_done",
    "agreement_signed",
    "listed",
    "sold",
    "lost",
]


# ── Serializers ────────────────────────────────────────────────────────────────

def _deal_out(d: PropguruDeal, prop: PropguruProperty | None = None, cp: PropguruChannelPartner | None = None) -> dict[str, Any]:
    entry: dict = {
        "id": str(d.id),
        "deal_code": d.deal_code,
        "property_id": str(d.property_id) if d.property_id else None,
        "sourcing_cp_id": str(d.sourcing_cp_id) if d.sourcing_cp_id else None,
        "sourcing_cp_commission_pct": d.sourcing_cp_commission_pct,
        "stage": d.stage,
        "lead_source": d.lead_source,
        "notes": d.notes,
        "target_acquisition_price": d.target_acquisition_price,
        "final_sale_price": d.final_sale_price,
        "created_at": d.created_at.isoformat(),
        "updated_at": d.updated_at.isoformat(),
    }
    if prop:
        entry["property"] = {
            "property_code": prop.property_code,
            "locality": prop.locality,
            "city": prop.city,
            "property_type": prop.property_type,
            "bedrooms": prop.bedrooms,
            "carpet_area_sqft": prop.carpet_area_sqft,
            "built_up_area_sqft": prop.built_up_area_sqft,
            "bathrooms": prop.bathrooms,
            "floor_number": prop.floor_number,
            "total_floors": prop.total_floors,
            "building_age_years": prop.building_age_years,
            "facing": prop.facing,
            "address_line1": prop.address_line1,
            "pincode": prop.pincode,
            "latitude": prop.latitude,
            "longitude": prop.longitude,
        }
    if cp:
        entry["sourcing_cp"] = {
            "cp_code": cp.cp_code,
            "name": cp.name,
            "city": cp.city,
            "phone": cp.phone,
            "email": cp.email,
            "commission_pct": cp.commission_pct,
        }
    return entry


async def _enrich_deal(d: PropguruDeal, session: AsyncSession) -> dict[str, Any]:
    prop = None
    cp = None
    if d.property_id:
        prop = (await session.execute(
            select(PropguruProperty).where(PropguruProperty.id == d.property_id)
        )).scalar_one_or_none()
    if d.sourcing_cp_id:
        cp = (await session.execute(
            select(PropguruChannelPartner).where(PropguruChannelPartner.id == d.sourcing_cp_id)
        )).scalar_one_or_none()
    return _deal_out(d, prop, cp)


# ── Pydantic schemas ───────────────────────────────────────────────────────────

class CreateDeal(BaseModel):
    property_id: str
    sourcing_cp_id: str
    sourcing_cp_commission_pct: float | None = None
    stage: str = "lead"
    lead_source: str | None = "channel_partner"
    notes: str | None = None


class UpdateDealStage(BaseModel):
    stage: str
    notes: str | None = None


class TriggerEvaluationRequest(BaseModel):
    override_context: dict = {}


# ── List / Get ─────────────────────────────────────────────────────────────────

@router.get("/deals")
async def list_deals(
    stage: str | None = Query(None),
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    q = select(PropguruDeal).order_by(PropguruDeal.created_at.desc())
    if stage:
        q = q.where(PropguruDeal.stage == stage)
    rows = (await session.execute(q)).scalars().all()
    return [await _enrich_deal(d, session) for d in rows]


@router.get("/deals/{deal_id}")
async def get_deal(
    deal_id: str,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    try:
        uid = uuid.UUID(deal_id)
        deal = (await session.execute(select(PropguruDeal).where(PropguruDeal.id == uid))).scalar_one_or_none()
    except ValueError:
        deal = (await session.execute(select(PropguruDeal).where(PropguruDeal.deal_code == deal_id))).scalar_one_or_none()
    if not deal:
        raise HTTPException(status_code=404, detail=f"Deal '{deal_id}' not found")
    return await _enrich_deal(deal, session)


# ── Create ─────────────────────────────────────────────────────────────────────

@router.post("/deals", status_code=201)
async def create_deal(
    req: CreateDeal,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    # Resolve property
    try:
        pid = uuid.UUID(req.property_id)
        prop = (await session.execute(select(PropguruProperty).where(PropguruProperty.id == pid))).scalar_one_or_none()
    except ValueError:
        prop = (await session.execute(select(PropguruProperty).where(PropguruProperty.property_code == req.property_id))).scalar_one_or_none()
    if not prop:
        raise HTTPException(status_code=404, detail=f"Property '{req.property_id}' not found")

    # Resolve CP
    try:
        cpid = uuid.UUID(req.sourcing_cp_id)
        cp = (await session.execute(select(PropguruChannelPartner).where(PropguruChannelPartner.id == cpid))).scalar_one_or_none()
    except ValueError:
        cp = (await session.execute(select(PropguruChannelPartner).where(PropguruChannelPartner.cp_code == req.sourcing_cp_id))).scalar_one_or_none()
    if not cp:
        raise HTTPException(status_code=404, detail=f"Channel Partner '{req.sourcing_cp_id}' not found")

    if req.stage not in VALID_STAGES:
        raise HTTPException(status_code=400, detail=f"Invalid stage '{req.stage}'. Must be one of: {VALID_STAGES}")

    # Auto-generate deal_code
    count_result = await session.execute(select(PropguruDeal))
    count = len(count_result.scalars().all())
    deal_code = f"DEAL-{count + 1:03d}"

    deal = PropguruDeal(
        deal_code=deal_code,
        property_id=prop.id,
        sourcing_cp_id=cp.id,
        sourcing_cp_commission_pct=req.sourcing_cp_commission_pct or cp.commission_pct,
        stage=req.stage,
        lead_source=req.lead_source,
        notes=req.notes,
    )
    session.add(deal)
    await session.commit()
    await session.refresh(deal)
    return await _enrich_deal(deal, session)


# ── Stage Transition ───────────────────────────────────────────────────────────

@router.patch("/deals/{deal_id}/stage")
async def update_deal_stage(
    deal_id: str,
    req: UpdateDealStage,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    try:
        uid = uuid.UUID(deal_id)
        deal = (await session.execute(select(PropguruDeal).where(PropguruDeal.id == uid))).scalar_one_or_none()
    except ValueError:
        deal = (await session.execute(select(PropguruDeal).where(PropguruDeal.deal_code == deal_id))).scalar_one_or_none()
    if not deal:
        raise HTTPException(status_code=404, detail=f"Deal '{deal_id}' not found")

    if req.stage not in VALID_STAGES:
        raise HTTPException(status_code=400, detail=f"Invalid stage '{req.stage}'")

    deal.stage = req.stage
    if req.notes:
        deal.notes = req.notes
    deal.updated_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(deal)
    return await _enrich_deal(deal, session)


# ── Evaluation Trigger ─────────────────────────────────────────────────────────

@router.post("/deals/{deal_id}/evaluate")
async def trigger_evaluation(
    deal_id: str,
    req: TriggerEvaluationRequest,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Trigger the evaluation agent pipeline for a deal."""
    try:
        uid = uuid.UUID(deal_id)
        deal = (await session.execute(select(PropguruDeal).where(PropguruDeal.id == uid))).scalar_one_or_none()
    except ValueError:
        deal = (await session.execute(select(PropguruDeal).where(PropguruDeal.deal_code == deal_id))).scalar_one_or_none()
    if not deal:
        raise HTTPException(status_code=404, detail=f"Deal '{deal_id}' not found")

    if deal.stage not in ("lead", "evaluation_pending"):
        raise HTTPException(
            status_code=422,
            detail=f"Deal is in stage '{deal.stage}'. Only leads or evaluation_pending deals can be evaluated.",
        )

    # Advance stage to evaluation_pending
    deal.stage = "evaluation_pending"
    deal.updated_at = datetime.now(timezone.utc)

    # Lookup the supervisor agent
    agent_result = await session.execute(
        select(Agent).where(Agent.name == "propguru-evaluation-supervisor")
    )
    agent = agent_result.scalar_one_or_none()
    if not agent:
        raise HTTPException(
            status_code=404,
            detail="Agent 'propguru-evaluation-supervisor' not found. Seed it first via /api/v1/agents.",
        )
    if not agent.is_active:
        raise HTTPException(
            status_code=403,
            detail="Agent 'propguru-evaluation-supervisor' is not active. Activate it from the Agents dashboard.",
        )

    # Check no active run already exists for this deal
    in_progress = (await session.execute(
        select(AgentRun)
        .where(AgentRun.status.in_(["pending", "running"]))
        .where(AgentRun.input["extra_context"]["deal_id"].astext == str(deal.id))
        .limit(1)
    )).scalar_one_or_none()
    if in_progress:
        raise HTTPException(
            status_code=409,
            detail=f"Evaluation already in progress for deal '{deal_id}'. Wait for it to complete.",
        )

    run = AgentRun(
        agent_id=agent.id,
        status="pending",
        input={
            "message": f"Evaluate property deal {deal.deal_code}",
            "extra_context": {
                "deal_id": str(deal.id),
                "deal_code": deal.deal_code,
            },
        },
    )
    session.add(run)
    await session.commit()
    await session.refresh(run)

    try:
        from agri_agent.queue.tasks import run_agent_task
        run_agent_task.delay(
            str(run.id),
            "propguru-evaluation-supervisor",
            f"Evaluate property deal {deal.deal_code}",
            {"deal_id": str(deal.id), "deal_code": deal.deal_code},
        )
    except Exception:
        pass

    return {
        "run_id": str(run.id),
        "deal_id": str(deal.id),
        "deal_code": deal.deal_code,
        "status": "queued",
        "message": f"Evaluation pipeline started for {deal.deal_code}",
    }

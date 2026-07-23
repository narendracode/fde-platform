"""Propguru evaluation report endpoints — get, scores, approve, reject."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from fde_agent.api.dependencies import verify_api_key
from fde_agent.db.models import (
    Agent,
    AgentAction,
    AgentRun,
    PropguruDeal,
    PropguruEvaluationCriteria,
    PropguruEvaluationReport,
    PropguruEvaluationScore,
    PropguruMarketComp,
    PropguruProperty,
)
from fde_agent.db.session import get_session

router = APIRouter(prefix="/api/v1/propguru", tags=["propguru"])

MAX_PREMIUM_PCT = 0.35  # 35% maximum premium


# ── Serializers ────────────────────────────────────────────────────────────────


def _report_out(r: PropguruEvaluationReport) -> dict[str, Any]:
    return {
        "id": str(r.id),
        "deal_id": str(r.deal_id),
        "version": r.version,
        "status": r.status,
        "market_rate_per_sqft": r.market_rate_per_sqft,
        "base_price": r.base_price,
        "score_factor": r.score_factor,
        "price_premium_pct": r.price_premium_pct,
        "recommended_price": r.recommended_price,
        "final_price": r.final_price,
        "confidence": r.confidence,
        "agent_reasoning": r.agent_reasoning,
        "analyst_notes": r.analyst_notes,
        "approved_by": r.approved_by,
        "approved_at": r.approved_at.isoformat() if r.approved_at else None,
        "verification_retries": r.verification_retries,
        "grader_flags": r.grader_flags or [],
        "model_grader_retries": r.model_grader_retries,
        "created_at": r.created_at.isoformat(),
        "updated_at": r.updated_at.isoformat(),
    }


def _score_out(
    s: PropguruEvaluationScore, criterion: PropguruEvaluationCriteria | None = None
) -> dict[str, Any]:
    entry: dict = {
        "id": str(s.id),
        "report_id": str(s.report_id),
        "criterion_id": str(s.criterion_id),
        "score": s.score,
        "raw_value": s.raw_value,
        "source": s.source,
        "notes": s.notes,
        "created_at": s.created_at.isoformat(),
        "updated_at": s.updated_at.isoformat(),
    }
    if criterion:
        entry["criterion"] = {
            "criterion_code": criterion.criterion_code,
            "name": criterion.name,
            "category": criterion.category,
            "weight": criterion.weight,
            "scoring_type": criterion.scoring_type,
        }
    return entry


# ── Score validation ───────────────────────────────────────────────────────────


def _validate_score(score: float, scoring_type: str, code: str = "") -> None:
    """Raise 422 if score is outside the valid range for the criterion type."""
    loc = f" for {code}" if code else ""
    if scoring_type == "boolean":
        if score not in (0.0, 1.0):
            raise HTTPException(
                status_code=422,
                detail=f"Boolean criterion{loc} requires 0 (absent) or 1 (present), got {score}. "
                f"Do not use a scale — this is a yes/no field.",
            )
    elif scoring_type in ("scale_1_5", "proximity_km"):
        if not (1.0 <= score <= 5.0):
            raise HTTPException(
                status_code=422,
                detail=f"Criterion{loc} ({scoring_type}) score must be between 1 and 5, got {score}.",
            )


# ── Pydantic schemas ───────────────────────────────────────────────────────────


class ApproveRequest(BaseModel):
    final_price: float | None = None
    analyst_notes: str | None = None
    approved_by: str = "analyst"


class RejectRequest(BaseModel):
    reason: str | None = None


class CreateReportRequest(BaseModel):
    deal_id: str
    version: int = 1


class SaveScoreRequest(BaseModel):
    criterion_id: str
    score: float
    raw_value: str | None = None
    source: str = "agent"
    notes: str | None = None


class UpdateScoreRequest(BaseModel):
    score: float
    raw_value: str | None = None
    source: str = "analyst"
    notes: str | None = None


class UpdateFinalPriceRequest(BaseModel):
    final_price: float
    analyst_notes: str | None = None


class RefineRequest(BaseModel):
    refinement_request: str
    requested_by: str = "analyst"


class UpdateStatusRequest(BaseModel):
    status: str


class SetBasePriceRequest(BaseModel):
    market_rate_per_sqft: float
    base_price: float


# ── Report endpoints ───────────────────────────────────────────────────────────


@router.post("/evaluations", status_code=201)
async def create_evaluation_report(
    req: CreateReportRequest,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Create a new draft evaluation report for a deal."""
    try:
        did = uuid.UUID(req.deal_id)
        deal = (
            await session.execute(select(PropguruDeal).where(PropguruDeal.id == did))
        ).scalar_one_or_none()
    except ValueError:
        deal = (
            await session.execute(select(PropguruDeal).where(PropguruDeal.deal_code == req.deal_id))
        ).scalar_one_or_none()
    if not deal:
        raise HTTPException(status_code=404, detail=f"Deal '{req.deal_id}' not found")

    report = PropguruEvaluationReport(
        deal_id=deal.id,
        version=req.version,
        status="draft",
    )
    session.add(report)
    await session.commit()
    await session.refresh(report)
    return _report_out(report)


@router.post("/deals/{deal_id}/pre-evaluate", status_code=201)
async def pre_evaluate(
    deal_id: str,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Pre-populate an evaluation report with deterministic scores before the agent pipeline runs.

    Creates the report, scores all criteria that can be derived from property data
    (PROPERTY, SOCIETY, AMENITY), and sets market_rate + base_price from the market comp DB.
    Returns report_id and context for the scorer agent (location, coordinates, market summary).
    """
    # ── Resolve deal + property ────────────────────────────────────────────────
    try:
        uid = uuid.UUID(deal_id)
        deal = (
            await session.execute(select(PropguruDeal).where(PropguruDeal.id == uid))
        ).scalar_one_or_none()
    except ValueError:
        deal = (
            await session.execute(select(PropguruDeal).where(PropguruDeal.deal_code == deal_id))
        ).scalar_one_or_none()
    if not deal:
        raise HTTPException(status_code=404, detail=f"Deal '{deal_id}' not found")

    prop = (
        await session.execute(
            select(PropguruProperty).where(PropguruProperty.id == deal.property_id)
        )
    ).scalar_one_or_none()
    if not prop:
        raise HTTPException(status_code=404, detail="Property not found for this deal")

    # ── Fetch all active criteria keyed by criterion_code ─────────────────────
    all_criteria = (
        (
            await session.execute(
                select(PropguruEvaluationCriteria).where(
                    PropguruEvaluationCriteria.is_active == True
                )
            )
        )
        .scalars()
        .all()
    )
    criteria_by_code = {c.criterion_code: c for c in all_criteria}

    # ── Market comp lookup ─────────────────────────────────────────────────────
    comp = (
        await session.execute(
            select(PropguruMarketComp).where(PropguruMarketComp.locality == prop.locality)
        )
    ).scalar_one_or_none()

    fallback_used = False
    if comp:
        market_rate = comp.avg_price_per_sqft
        market_source = comp.data_source or "db"
        market_trend = comp.price_trend_6m_pct
        market_txn_count = comp.transaction_count_6m
    else:
        market_rate = 10000.0
        market_source = "fallback"
        market_trend = None
        market_txn_count = None
        fallback_used = True

    base_price = round(market_rate * (prop.carpet_area_sqft or 0), 2)

    # ── Create draft report ────────────────────────────────────────────────────
    report = PropguruEvaluationReport(
        deal_id=deal.id,
        version=1,
        status="draft",
        market_rate_per_sqft=market_rate,
        base_price=base_price,
    )
    session.add(report)
    await session.flush()  # populate report.id before scoring

    # ── Deterministic scoring helpers ──────────────────────────────────────────
    is_house = prop.property_type == "independent_house"
    scored: list[dict] = []

    async def _save(code: str, score: float, raw: str, notes: str) -> None:
        crit = criteria_by_code.get(code)
        if not crit:
            return
        _validate_score(score, crit.scoring_type, code)
        session.add(
            PropguruEvaluationScore(
                report_id=report.id,
                criterion_id=crit.id,
                score=score,
                raw_value=raw,
                source="agent",
                notes=notes,
            )
        )
        scored.append({"code": code, "score": score})

    # ── PROPERTY criteria (CRIT-021 to CRIT-025) ──────────────────────────────
    # CRIT-021 Floor Level
    if is_house:
        await _save(
            "CRIT-021", 3.0, "independent_house", "N/A for independent house — neutral default"
        )
    else:
        fn = prop.floor_number or 0
        tf = prop.total_floors or 0
        if fn <= 1:
            floor_score = 2.0
        elif fn <= 5:
            floor_score = 3.0
        elif fn <= 10:
            floor_score = 4.0
        else:
            floor_score = 5.0
        if tf > 0 and fn == tf:
            floor_score = max(1.0, floor_score - 1.0)
        await _save("CRIT-021", floor_score, f"floor {fn} of {tf}", f"Rule-based: floor {fn}/{tf}")

    # CRIT-022 Facing
    facing_map = {
        "east": 5.0,
        "north": 4.0,
        "north_east": 3.0,
        "northeast": 3.0,
        "west": 3.0,
        "south": 2.0,
    }
    facing_score = facing_map.get((prop.facing or "").lower(), 3.0)
    await _save(
        "CRIT-022",
        facing_score,
        prop.facing or "unknown",
        f"Facing {prop.facing} → score {facing_score}",
    )

    # CRIT-023 Property Age
    age = prop.building_age_years or 0
    if age <= 2:
        age_score = 5.0
    elif age <= 5:
        age_score = 4.0
    elif age <= 10:
        age_score = 3.0
    elif age <= 20:
        age_score = 2.0
    else:
        age_score = 1.0
    await _save("CRIT-023", age_score, f"{age} years", f"Building age {age}yr → score {age_score}")

    # CRIT-024 Covered Parking — no parking data in property record
    await _save("CRIT-024", 3.0, "unknown", "No parking data in property record — neutral default")

    # CRIT-025 Power Backup — no power backup data in property record
    await _save(
        "CRIT-025",
        0.0,
        "unknown",
        "No power backup data in property record — conservative default 0",
    )

    # ── SOCIETY criteria (CRIT-026 to CRIT-030) ───────────────────────────────
    if is_house:
        await _save("CRIT-026", 0.0, "independent_house", "Not applicable for independent house")
        await _save("CRIT-027", 1.0, "independent_house", "Standalone independent — score 1")
        await _save("CRIT-028", 0.0, "independent_house", "No lift for independent house")
        await _save("CRIT-029", 3.0, "unknown", "Neutral default — analyst to verify")
        await _save("CRIT-030", 3.0, "unknown", "Neutral default — analyst to verify")
    else:
        for code in ("CRIT-026", "CRIT-028"):
            await _save(code, 0.0, "unknown", "Unknown — analyst to verify from site visit")
        for code in ("CRIT-027", "CRIT-029", "CRIT-030"):
            await _save(code, 3.0, "unknown", "Neutral default — analyst to verify")

    # ── AMENITY criteria (CRIT-001 to CRIT-010, all boolean) ──────────────────
    amenity_codes = [f"CRIT-{i:03d}" for i in range(1, 11)]
    if is_house:
        amenity_note = "No society amenities for independent house"
        amenity_score = 0.0
    else:
        amenity_note = "Unknown — verify from site visit data"
        amenity_score = 0.0
    for code in amenity_codes:
        await _save(code, amenity_score, "unknown", amenity_note)

    await session.commit()
    await session.refresh(report)

    # ── Build scorer context ───────────────────────────────────────────────────
    market_summary = {
        "locality": prop.locality,
        "market_rate_per_sqft": market_rate,
        "base_price": base_price,
        "base_price_lakhs": round(base_price / 100_000, 2),
        "price_trend_6m_pct": market_trend,
        "transaction_count_6m": market_txn_count,
        "data_source": market_source,
        "fallback_used": fallback_used,
    }

    return {
        "report_id": str(report.id),
        "deal_id": str(deal.id),
        "deal_code": deal.deal_code,
        "property_summary": f"{prop.bedrooms}BHK, {prop.carpet_area_sqft} sqft, {prop.locality}, {prop.city}",
        "locality": prop.locality,
        "city": prop.city,
        "latitude": prop.latitude,
        "longitude": prop.longitude,
        "property_type": prop.property_type,
        "pre_scored_count": len(scored),
        "pre_scored_criteria": [s["code"] for s in scored],
        "market_summary": market_summary,
        "message": f"Pre-evaluation complete: {len(scored)} criteria scored deterministically. "
        f"Scorer agent to handle CRIT-011 to CRIT-020 (location).",
    }


@router.get("/deals/{deal_id}/evaluation")
async def get_latest_evaluation(
    deal_id: str,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Get the most recent evaluation report for a deal."""
    try:
        uid = uuid.UUID(deal_id)
        deal = (
            await session.execute(select(PropguruDeal).where(PropguruDeal.id == uid))
        ).scalar_one_or_none()
    except ValueError:
        deal = (
            await session.execute(select(PropguruDeal).where(PropguruDeal.deal_code == deal_id))
        ).scalar_one_or_none()
    if not deal:
        raise HTTPException(status_code=404, detail=f"Deal '{deal_id}' not found")

    report = (
        await session.execute(
            select(PropguruEvaluationReport)
            .where(PropguruEvaluationReport.deal_id == deal.id)
            .order_by(desc(PropguruEvaluationReport.created_at))
            .limit(1)
        )
    ).scalar_one_or_none()
    if not report:
        raise HTTPException(status_code=404, detail="No evaluation report found for this deal")
    return _report_out(report)


@router.get("/evaluations/{report_id}")
async def get_evaluation_report(
    report_id: str,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Get full evaluation report."""
    try:
        rid = uuid.UUID(report_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid report_id")
    report = (
        await session.execute(
            select(PropguruEvaluationReport).where(PropguruEvaluationReport.id == rid)
        )
    ).scalar_one_or_none()
    if not report:
        raise HTTPException(status_code=404, detail=f"Evaluation report '{report_id}' not found")
    return _report_out(report)


@router.get("/evaluations/{report_id}/scores")
async def get_evaluation_scores(
    report_id: str,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Get all scores for a report, grouped by category."""
    try:
        rid = uuid.UUID(report_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid report_id")

    report = (
        await session.execute(
            select(PropguruEvaluationReport).where(PropguruEvaluationReport.id == rid)
        )
    ).scalar_one_or_none()
    if not report:
        raise HTTPException(status_code=404, detail=f"Report '{report_id}' not found")

    scores = (
        (
            await session.execute(
                select(PropguruEvaluationScore).where(PropguruEvaluationScore.report_id == rid)
            )
        )
        .scalars()
        .all()
    )

    # Enrich each score with criterion info
    enriched = []
    for s in scores:
        crit = (
            await session.execute(
                select(PropguruEvaluationCriteria).where(
                    PropguruEvaluationCriteria.id == s.criterion_id
                )
            )
        ).scalar_one_or_none()
        enriched.append(_score_out(s, crit))

    # Group by category — order determines display order in the UI
    categories = ["amenity", "location", "property", "society", "vastu"]
    grouped = {cat: [] for cat in categories}
    for item in enriched:
        cat = (item.get("criterion") or {}).get("category", "")
        if cat in grouped:
            grouped[cat].append(item)
        else:
            grouped.setdefault("other", []).append(item)

    return {
        "report_id": report_id,
        "total_scored": len(enriched),
        "score_factor": report.score_factor,
        "recommended_price": report.recommended_price,
        "final_price": report.final_price,
        "groups": grouped,
    }


# ── Score write endpoints (used by agents + refinement) ───────────────────────


@router.post("/evaluations/{report_id}/scores", status_code=201)
async def save_evaluation_score(
    report_id: str,
    req: SaveScoreRequest,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Save or update a single criterion score."""
    try:
        rid = uuid.UUID(report_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid report_id")

    report = (
        await session.execute(
            select(PropguruEvaluationReport).where(PropguruEvaluationReport.id == rid)
        )
    ).scalar_one_or_none()
    if not report:
        raise HTTPException(status_code=404, detail=f"Report '{report_id}' not found")

    try:
        cid = uuid.UUID(req.criterion_id)
    except ValueError:
        crit_lookup = (
            await session.execute(
                select(PropguruEvaluationCriteria).where(
                    PropguruEvaluationCriteria.criterion_code == req.criterion_id
                )
            )
        ).scalar_one_or_none()
        if not crit_lookup:
            raise HTTPException(status_code=404, detail=f"Criterion '{req.criterion_id}' not found")
        cid = crit_lookup.id

    # Validate score against criterion type before writing
    crit = (
        await session.execute(
            select(PropguruEvaluationCriteria).where(PropguruEvaluationCriteria.id == cid)
        )
    ).scalar_one_or_none()
    if crit:
        _validate_score(req.score, crit.scoring_type, crit.criterion_code)

    # Upsert: update existing score if present
    existing = (
        await session.execute(
            select(PropguruEvaluationScore).where(
                PropguruEvaluationScore.report_id == rid,
                PropguruEvaluationScore.criterion_id == cid,
            )
        )
    ).scalar_one_or_none()

    if existing:
        existing.score = req.score
        existing.raw_value = req.raw_value
        existing.source = req.source
        existing.notes = req.notes
        existing.updated_at = datetime.now(UTC)
        await session.commit()
        await session.refresh(existing)
        return _score_out(existing)
    else:
        score = PropguruEvaluationScore(
            report_id=rid,
            criterion_id=cid,
            score=req.score,
            raw_value=req.raw_value,
            source=req.source,
            notes=req.notes,
        )
        session.add(score)
        await session.commit()
        await session.refresh(score)
        return _score_out(score)


@router.patch("/evaluations/{report_id}/scores/{score_id}")
async def update_score(
    report_id: str,
    score_id: str,
    req: UpdateScoreRequest,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Update a specific score (used by refinement canvas)."""
    try:
        rid = uuid.UUID(report_id)
        sid = uuid.UUID(score_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid report_id or score_id")

    score = (
        await session.execute(
            select(PropguruEvaluationScore).where(
                PropguruEvaluationScore.id == sid,
                PropguruEvaluationScore.report_id == rid,
            )
        )
    ).scalar_one_or_none()
    if not score:
        raise HTTPException(status_code=404, detail="Score not found")

    # Validate before writing
    crit = (
        await session.execute(
            select(PropguruEvaluationCriteria).where(
                PropguruEvaluationCriteria.id == score.criterion_id
            )
        )
    ).scalar_one_or_none()
    if crit:
        _validate_score(req.score, crit.scoring_type, crit.criterion_code)

    score.score = req.score
    if req.raw_value is not None:
        score.raw_value = req.raw_value
    score.source = req.source
    if req.notes:
        score.notes = req.notes
    score.updated_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(score)
    return _score_out(score, crit)


# ── Price calculation ──────────────────────────────────────────────────────────


@router.post("/evaluations/{report_id}/calculate-price")
async def calculate_price(
    report_id: str,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Compute score_factor + recommended_price from current scores and save to report."""
    try:
        rid = uuid.UUID(report_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid report_id")

    report = (
        await session.execute(
            select(PropguruEvaluationReport).where(PropguruEvaluationReport.id == rid)
        )
    ).scalar_one_or_none()
    if not report:
        raise HTTPException(status_code=404, detail=f"Report '{report_id}' not found")

    scores = (
        (
            await session.execute(
                select(PropguruEvaluationScore).where(PropguruEvaluationScore.report_id == rid)
            )
        )
        .scalars()
        .all()
    )

    if not scores:
        raise HTTPException(
            status_code=422,
            detail="No scores saved yet. Save at least one score before calculating price.",
        )

    # Fetch criteria weights
    weighted_sum = 0.0
    total_weight = 0.0
    for s in scores:
        crit = (
            await session.execute(
                select(PropguruEvaluationCriteria).where(
                    PropguruEvaluationCriteria.id == s.criterion_id
                )
            )
        ).scalar_one_or_none()
        if crit:
            # Normalize score to 0-1 range; clamp defensively against out-of-range agent values
            if crit.scoring_type == "boolean":
                normalized = min(1.0, max(0.0, s.score))
            elif crit.scoring_type == "scale_1_5":
                normalized = min(1.0, max(0.0, (s.score - 1) / 4.0))
            elif crit.scoring_type == "proximity_km":
                normalized = min(1.0, max(0.0, s.score / 5.0))
            else:
                normalized = min(1.0, max(0.0, s.score))
            weighted_sum += crit.weight * normalized
            total_weight += crit.weight

    score_factor = weighted_sum / total_weight if total_weight > 0 else 0.0
    price_premium_pct = score_factor * MAX_PREMIUM_PCT

    base_price = report.base_price or 0.0
    recommended_price = base_price * (1 + price_premium_pct) if base_price > 0 else 0.0

    # Determine confidence based on coverage
    all_criteria_count = (
        (
            await session.execute(
                select(PropguruEvaluationCriteria).where(
                    PropguruEvaluationCriteria.is_active == True
                )
            )
        )
        .scalars()
        .all()
    )
    total_criteria = len(all_criteria_count)
    scored_count = len(scores)
    coverage = scored_count / total_criteria if total_criteria > 0 else 0.0

    if coverage >= 0.9 and score_factor >= 0.6:
        confidence = "high"
    elif coverage >= 0.7:
        confidence = "medium"
    else:
        confidence = "low"

    report.score_factor = round(score_factor, 4)
    report.price_premium_pct = round(price_premium_pct * 100, 2)
    report.recommended_price = round(recommended_price, 2)
    report.confidence = confidence
    report.updated_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(report)

    return {
        "report_id": report_id,
        "score_factor": report.score_factor,
        "price_premium_pct": report.price_premium_pct,
        "base_price": report.base_price,
        "recommended_price": report.recommended_price,
        "confidence": report.confidence,
        "scored_criteria": scored_count,
        "total_criteria": total_criteria,
        "coverage_pct": round(coverage * 100, 1),
    }


# ── Approve / Reject ───────────────────────────────────────────────────────────


@router.patch("/evaluations/{report_id}/approve")
async def approve_evaluation(
    report_id: str,
    req: ApproveRequest,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Analyst approves the evaluation. Sets deal stage to evaluation_done."""
    try:
        rid = uuid.UUID(report_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid report_id")

    report = (
        await session.execute(
            select(PropguruEvaluationReport).where(PropguruEvaluationReport.id == rid)
        )
    ).scalar_one_or_none()
    if not report:
        raise HTTPException(status_code=404, detail=f"Report '{report_id}' not found")

    if report.status == "approved":
        raise HTTPException(status_code=409, detail="Report is already approved")

    now = datetime.now(UTC)
    report.status = "approved"
    report.final_price = req.final_price or report.recommended_price
    report.analyst_notes = req.analyst_notes
    report.approved_by = req.approved_by
    report.approved_at = now
    report.updated_at = now

    # Advance deal stage and set acquisition price
    deal = (
        await session.execute(select(PropguruDeal).where(PropguruDeal.id == report.deal_id))
    ).scalar_one_or_none()
    if deal:
        deal.stage = "evaluation_done"
        deal.target_acquisition_price = report.final_price
        deal.updated_at = now

    # Mark any pending AgentAction pointing to this approval URL as approved
    action_url = f"/api/v1/propguru/evaluations/{report_id}/approve"
    pending_actions = (
        (
            await session.execute(
                select(AgentAction)
                .where(AgentAction.status == "pending_review")
                .where(AgentAction.approval_action["url"].astext == action_url)
            )
        )
        .scalars()
        .all()
    )
    for action in pending_actions:
        action.status = "approved"
        action.decided_at = now
        action.decided_by = req.approved_by
        action.decision_note = req.analyst_notes

    await session.commit()
    await session.refresh(report)
    return {
        "report_id": report_id,
        "status": report.status,
        "final_price": report.final_price,
        "deal_stage": deal.stage if deal else None,
        "approved_by": report.approved_by,
        "approved_at": report.approved_at.isoformat(),
    }


@router.patch("/evaluations/{report_id}/reject")
async def reject_evaluation(
    report_id: str,
    req: RejectRequest,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Analyst rejects the evaluation — deal stays at evaluation_pending for re-run."""
    try:
        rid = uuid.UUID(report_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid report_id")

    report = (
        await session.execute(
            select(PropguruEvaluationReport).where(PropguruEvaluationReport.id == rid)
        )
    ).scalar_one_or_none()
    if not report:
        raise HTTPException(status_code=404, detail=f"Report '{report_id}' not found")

    now = datetime.now(UTC)
    report.status = "rejected"
    report.analyst_notes = req.reason
    report.updated_at = now

    # Reset deal to lead so the analyst can trigger a fresh evaluation
    deal = (
        await session.execute(select(PropguruDeal).where(PropguruDeal.id == report.deal_id))
    ).scalar_one_or_none()
    if deal and deal.stage == "evaluation_pending":
        deal.stage = "lead"
        deal.updated_at = now

    await session.commit()
    return {
        "report_id": report_id,
        "status": "rejected",
        "deal_stage": deal.stage if deal else None,
        "reason": req.reason,
    }


# ── Final price override ───────────────────────────────────────────────────────


@router.patch("/evaluations/{report_id}/base-price")
async def set_base_price(
    report_id: str,
    req: SetBasePriceRequest,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Set market_rate_per_sqft and base_price on the report."""
    try:
        rid = uuid.UUID(report_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid report_id")

    report = (
        await session.execute(
            select(PropguruEvaluationReport).where(PropguruEvaluationReport.id == rid)
        )
    ).scalar_one_or_none()
    if not report:
        raise HTTPException(status_code=404, detail=f"Report '{report_id}' not found")

    report.market_rate_per_sqft = req.market_rate_per_sqft
    report.base_price = req.base_price
    report.updated_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(report)
    return _report_out(report)


@router.patch("/evaluations/{report_id}/final-price")
async def update_final_price(
    report_id: str,
    req: UpdateFinalPriceRequest,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Set a manual final price override on the report (used by refinement canvas)."""
    try:
        rid = uuid.UUID(report_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid report_id")

    report = (
        await session.execute(
            select(PropguruEvaluationReport).where(PropguruEvaluationReport.id == rid)
        )
    ).scalar_one_or_none()
    if not report:
        raise HTTPException(status_code=404, detail=f"Report '{report_id}' not found")

    report.final_price = req.final_price
    if req.analyst_notes:
        report.analyst_notes = req.analyst_notes
    report.updated_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(report)
    return _report_out(report)


_ALLOWED_STATUS_TRANSITIONS = {
    "draft": {"pending_review"},
    "pending_review": {"draft", "approved", "rejected"},
}


@router.patch("/evaluations/{report_id}/status")
async def update_report_status(
    report_id: str,
    req: UpdateStatusRequest,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Internal: update evaluation report status (used by the evaluator agent after creating the HITL action)."""
    try:
        rid = uuid.UUID(report_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid report_id")

    report = (
        await session.execute(
            select(PropguruEvaluationReport).where(PropguruEvaluationReport.id == rid)
        )
    ).scalar_one_or_none()
    if not report:
        raise HTTPException(status_code=404, detail=f"Report '{report_id}' not found")

    allowed = _ALLOWED_STATUS_TRANSITIONS.get(report.status, set())
    if req.status not in allowed:
        raise HTTPException(
            status_code=422,
            detail=f"Cannot transition report from '{report.status}' to '{req.status}'",
        )

    report.status = req.status
    await session.commit()
    await session.refresh(report)
    return _report_out(report)


@router.post("/evaluations/{report_id}/refine", status_code=202)
async def trigger_refinement(
    report_id: str,
    req: RefineRequest,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Trigger the AI refinement agent to update specific scores based on analyst feedback."""
    try:
        rid = uuid.UUID(report_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid report_id")

    report = (
        await session.execute(
            select(PropguruEvaluationReport).where(PropguruEvaluationReport.id == rid)
        )
    ).scalar_one_or_none()
    if not report:
        raise HTTPException(status_code=404, detail=f"Report '{report_id}' not found")

    if report.status == "approved":
        raise HTTPException(status_code=422, detail="Cannot refine an approved evaluation")

    agent_result = await session.execute(
        select(Agent).where(Agent.name == "propguru-evaluation-refiner")
    )
    agent = agent_result.scalar_one_or_none()
    if not agent:
        raise HTTPException(
            status_code=404,
            detail="Agent 'propguru-evaluation-refiner' not found. Seed it first via /api/v1/agents.",
        )
    if not agent.is_active:
        raise HTTPException(
            status_code=403,
            detail="Agent 'propguru-evaluation-refiner' is not active. Activate it from the Agents dashboard.",
        )

    extra_context = {
        "report_id": str(report.id),
        "deal_id": str(report.deal_id),
        "refinement_request": req.refinement_request,
    }
    run = AgentRun(
        agent_id=agent.id,
        status="pending",
        input={
            "message": f"Refine evaluation report {report_id}: {req.refinement_request[:100]}",
            "extra_context": extra_context,
        },
    )
    session.add(run)
    await session.commit()
    await session.refresh(run)

    try:
        from fde_agent.queue.tasks import run_agent_task

        run_agent_task.delay(
            str(run.id),
            "propguru-evaluation-refiner",
            req.refinement_request,
            extra_context,
        )
    except Exception:
        pass

    return {
        "run_id": str(run.id),
        "report_id": str(report.id),
        "status": "queued",
        "message": "Refinement agent started — poll /api/v1/runs/{run_id} for status",
    }


@router.get("/evaluations/{report_id}/action")
async def get_evaluation_action(
    report_id: str,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Return the most recent pending AgentAction for this evaluation report.

    Used by the evaluation UI to obtain the action_id before opening the
    conversational refine canvas (which operates on actions, not reports).
    Returns 404 if no pending_review action exists for this report.
    """
    try:
        rid = uuid.UUID(report_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid report_id")

    report = (
        await session.execute(
            select(PropguruEvaluationReport).where(PropguruEvaluationReport.id == rid)
        )
    ).scalar_one_or_none()
    if not report:
        raise HTTPException(status_code=404, detail=f"Report '{report_id}' not found")

    action = (
        await session.execute(
            select(AgentAction)
            .where(AgentAction.approval_action["url_params"]["report_id"].as_string() == report_id)
            .where(AgentAction.status == "pending_review")
            .order_by(desc(AgentAction.created_at))
            .limit(1)
        )
    ).scalar_one_or_none()

    if not action:
        raise HTTPException(
            status_code=404,
            detail="No pending approval action found for this report. The evaluation may not have completed or was already decided.",
        )

    return {
        "action_id": str(action.id),
        "status": action.status,
        "title": action.title,
        "enable_refinement": True,
    }


# ── Verification loop ─────────────────────────────────────────────────────────


class GraderResultRequest(BaseModel):
    verification_retries: int
    grader_flags: list[str] = []
    model_grader_retries: int = 0


@router.post("/evaluations/{report_id}/grader-result")
async def save_grader_result(
    report_id: str,
    req: GraderResultRequest,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Persist grader verdicts to the evaluation report (called by the verifier node)."""
    try:
        rid = uuid.UUID(report_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid report_id")

    report = (
        await session.execute(
            select(PropguruEvaluationReport).where(PropguruEvaluationReport.id == rid)
        )
    ).scalar_one_or_none()
    if not report:
        raise HTTPException(status_code=404, detail=f"Report '{report_id}' not found")

    report.verification_retries = req.verification_retries
    report.grader_flags = req.grader_flags or []
    report.model_grader_retries = req.model_grader_retries
    await session.commit()
    await session.refresh(report)
    return {
        "report_id": report_id,
        "verification_retries": report.verification_retries,
        "grader_flags": report.grader_flags,
        "model_grader_retries": report.model_grader_retries,
    }

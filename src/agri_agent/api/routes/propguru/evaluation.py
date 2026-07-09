"""Propguru evaluation report endpoints — get, scores, approve, reject."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from agri_agent.api.dependencies import verify_api_key
from agri_agent.db.models import (
    Agent,
    AgentAction,
    AgentRun,
    PropguruDeal,
    PropguruEvaluationCriteria,
    PropguruEvaluationReport,
    PropguruEvaluationScore,
)
from agri_agent.db.session import get_session

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
        "created_at": r.created_at.isoformat(),
        "updated_at": r.updated_at.isoformat(),
    }


def _score_out(s: PropguruEvaluationScore, criterion: PropguruEvaluationCriteria | None = None) -> dict[str, Any]:
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
        deal = (await session.execute(select(PropguruDeal).where(PropguruDeal.id == did))).scalar_one_or_none()
    except ValueError:
        deal = (await session.execute(select(PropguruDeal).where(PropguruDeal.deal_code == req.deal_id))).scalar_one_or_none()
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


@router.get("/deals/{deal_id}/evaluation")
async def get_latest_evaluation(
    deal_id: str,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Get the most recent evaluation report for a deal."""
    try:
        uid = uuid.UUID(deal_id)
        deal = (await session.execute(select(PropguruDeal).where(PropguruDeal.id == uid))).scalar_one_or_none()
    except ValueError:
        deal = (await session.execute(select(PropguruDeal).where(PropguruDeal.deal_code == deal_id))).scalar_one_or_none()
    if not deal:
        raise HTTPException(status_code=404, detail=f"Deal '{deal_id}' not found")

    report = (await session.execute(
        select(PropguruEvaluationReport)
        .where(PropguruEvaluationReport.deal_id == deal.id)
        .order_by(desc(PropguruEvaluationReport.created_at))
        .limit(1)
    )).scalar_one_or_none()
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
    report = (await session.execute(
        select(PropguruEvaluationReport).where(PropguruEvaluationReport.id == rid)
    )).scalar_one_or_none()
    if not report:
        raise HTTPException(status_code=404, detail=f"Evaluation report '{report_id}' not found")
    return _report_out(report)


@router.get("/evaluations/{report_id}/scores")
async def get_evaluation_scores(
    report_id: str,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Get all 30 scores for a report, grouped by category."""
    try:
        rid = uuid.UUID(report_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid report_id")

    report = (await session.execute(
        select(PropguruEvaluationReport).where(PropguruEvaluationReport.id == rid)
    )).scalar_one_or_none()
    if not report:
        raise HTTPException(status_code=404, detail=f"Report '{report_id}' not found")

    scores = (await session.execute(
        select(PropguruEvaluationScore).where(PropguruEvaluationScore.report_id == rid)
    )).scalars().all()

    # Enrich each score with criterion info
    enriched = []
    for s in scores:
        crit = (await session.execute(
            select(PropguruEvaluationCriteria).where(PropguruEvaluationCriteria.id == s.criterion_id)
        )).scalar_one_or_none()
        enriched.append(_score_out(s, crit))

    # Group by category
    categories = ["amenity", "location", "property", "society"]
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

    report = (await session.execute(
        select(PropguruEvaluationReport).where(PropguruEvaluationReport.id == rid)
    )).scalar_one_or_none()
    if not report:
        raise HTTPException(status_code=404, detail=f"Report '{report_id}' not found")

    try:
        cid = uuid.UUID(req.criterion_id)
    except ValueError:
        crit_lookup = (await session.execute(
            select(PropguruEvaluationCriteria).where(PropguruEvaluationCriteria.criterion_code == req.criterion_id)
        )).scalar_one_or_none()
        if not crit_lookup:
            raise HTTPException(status_code=404, detail=f"Criterion '{req.criterion_id}' not found")
        cid = crit_lookup.id

    # Validate score against criterion type before writing
    crit = (await session.execute(
        select(PropguruEvaluationCriteria).where(PropguruEvaluationCriteria.id == cid)
    )).scalar_one_or_none()
    if crit:
        _validate_score(req.score, crit.scoring_type, crit.criterion_code)

    # Upsert: update existing score if present
    existing = (await session.execute(
        select(PropguruEvaluationScore).where(
            PropguruEvaluationScore.report_id == rid,
            PropguruEvaluationScore.criterion_id == cid,
        )
    )).scalar_one_or_none()

    if existing:
        existing.score = req.score
        existing.raw_value = req.raw_value
        existing.source = req.source
        existing.notes = req.notes
        existing.updated_at = datetime.now(timezone.utc)
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

    score = (await session.execute(
        select(PropguruEvaluationScore).where(
            PropguruEvaluationScore.id == sid,
            PropguruEvaluationScore.report_id == rid,
        )
    )).scalar_one_or_none()
    if not score:
        raise HTTPException(status_code=404, detail="Score not found")

    # Validate before writing
    crit = (await session.execute(
        select(PropguruEvaluationCriteria).where(PropguruEvaluationCriteria.id == score.criterion_id)
    )).scalar_one_or_none()
    if crit:
        _validate_score(req.score, crit.scoring_type, crit.criterion_code)

    score.score = req.score
    if req.raw_value is not None:
        score.raw_value = req.raw_value
    score.source = req.source
    if req.notes:
        score.notes = req.notes
    score.updated_at = datetime.now(timezone.utc)
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

    report = (await session.execute(
        select(PropguruEvaluationReport).where(PropguruEvaluationReport.id == rid)
    )).scalar_one_or_none()
    if not report:
        raise HTTPException(status_code=404, detail=f"Report '{report_id}' not found")

    scores = (await session.execute(
        select(PropguruEvaluationScore).where(PropguruEvaluationScore.report_id == rid)
    )).scalars().all()

    if not scores:
        raise HTTPException(status_code=422, detail="No scores saved yet. Save at least one score before calculating price.")

    # Fetch criteria weights
    weighted_sum = 0.0
    total_weight = 0.0
    for s in scores:
        crit = (await session.execute(
            select(PropguruEvaluationCriteria).where(PropguruEvaluationCriteria.id == s.criterion_id)
        )).scalar_one_or_none()
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
    all_criteria_count = (await session.execute(
        select(PropguruEvaluationCriteria).where(PropguruEvaluationCriteria.is_active == True)
    )).scalars().all()
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
    report.updated_at = datetime.now(timezone.utc)
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

    report = (await session.execute(
        select(PropguruEvaluationReport).where(PropguruEvaluationReport.id == rid)
    )).scalar_one_or_none()
    if not report:
        raise HTTPException(status_code=404, detail=f"Report '{report_id}' not found")

    if report.status == "approved":
        raise HTTPException(status_code=409, detail="Report is already approved")

    now = datetime.now(timezone.utc)
    report.status = "approved"
    report.final_price = req.final_price or report.recommended_price
    report.analyst_notes = req.analyst_notes
    report.approved_by = req.approved_by
    report.approved_at = now
    report.updated_at = now

    # Advance deal stage and set acquisition price
    deal = (await session.execute(
        select(PropguruDeal).where(PropguruDeal.id == report.deal_id)
    )).scalar_one_or_none()
    if deal:
        deal.stage = "evaluation_done"
        deal.target_acquisition_price = report.final_price
        deal.updated_at = now

    # Mark any pending AgentAction pointing to this approval URL as approved
    action_url = f"/api/v1/propguru/evaluations/{report_id}/approve"
    pending_actions = (await session.execute(
        select(AgentAction)
        .where(AgentAction.status == "pending_review")
        .where(AgentAction.approval_action["url"].astext == action_url)
    )).scalars().all()
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

    report = (await session.execute(
        select(PropguruEvaluationReport).where(PropguruEvaluationReport.id == rid)
    )).scalar_one_or_none()
    if not report:
        raise HTTPException(status_code=404, detail=f"Report '{report_id}' not found")

    report.status = "rejected"
    report.analyst_notes = req.reason
    report.updated_at = datetime.now(timezone.utc)
    await session.commit()
    return {"report_id": report_id, "status": "rejected", "reason": req.reason}


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

    report = (await session.execute(
        select(PropguruEvaluationReport).where(PropguruEvaluationReport.id == rid)
    )).scalar_one_or_none()
    if not report:
        raise HTTPException(status_code=404, detail=f"Report '{report_id}' not found")

    report.market_rate_per_sqft = req.market_rate_per_sqft
    report.base_price = req.base_price
    report.updated_at = datetime.now(timezone.utc)
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

    report = (await session.execute(
        select(PropguruEvaluationReport).where(PropguruEvaluationReport.id == rid)
    )).scalar_one_or_none()
    if not report:
        raise HTTPException(status_code=404, detail=f"Report '{report_id}' not found")

    report.final_price = req.final_price
    if req.analyst_notes:
        report.analyst_notes = req.analyst_notes
    report.updated_at = datetime.now(timezone.utc)
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

    report = (await session.execute(
        select(PropguruEvaluationReport).where(PropguruEvaluationReport.id == rid)
    )).scalar_one_or_none()
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

    report = (await session.execute(
        select(PropguruEvaluationReport).where(PropguruEvaluationReport.id == rid)
    )).scalar_one_or_none()
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
        from agri_agent.queue.tasks import run_agent_task
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

    report = (await session.execute(
        select(PropguruEvaluationReport).where(PropguruEvaluationReport.id == rid)
    )).scalar_one_or_none()
    if not report:
        raise HTTPException(status_code=404, detail=f"Report '{report_id}' not found")

    action = (await session.execute(
        select(AgentAction)
        .where(AgentAction.approval_action["url_params"]["report_id"].as_string() == report_id)
        .where(AgentAction.status == "pending_review")
        .order_by(desc(AgentAction.created_at))
        .limit(1)
    )).scalar_one_or_none()

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

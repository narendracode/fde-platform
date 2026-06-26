"""Generic agent-action inbox API.

Agents write AgentAction records via POST /api/v1/actions (through the
propose_action tool).  Humans review them at GET /approvals and approve/reject
via these endpoints.  On approval the platform executes the stored
approval_action HTTP call — no domain-specific code needed here.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agri_agent.api.dependencies import verify_api_key
from agri_agent.config.settings import settings
from agri_agent.db.models import Agent, AgentAction
from agri_agent.db.session import get_session

router = APIRouter(prefix="/api/v1/actions", tags=["actions"])

_VALID_STATUSES = {"pending_review", "approved", "rejected", "approval_failed", "expired", "stale", "drifted", "dismissed"}
_VALID_CONFIDENCE = {"high", "medium", "low"}
_HISTORY_STATUSES = {"approved", "rejected", "approval_failed", "expired", "stale", "drifted", "dismissed"}


# ── Request / Response schemas ─────────────────────────────────────────────────

class CreateActionRequest(BaseModel):
    agent_name: str
    agent_run_id: str | None = None
    title: str
    summary: str = ""
    reasoning: str | None = None
    confidence: str | None = None
    display_data: list[dict[str, Any]] = []
    tags: list[str] = []
    approval_action: dict[str, Any]
    rejection_action: dict[str, Any] | None = None
    expires_at: str | None = None          # ISO datetime string
    expected_state: dict[str, Any] | None = None  # resource state snapshot for drift detection


class ApproveRequest(BaseModel):
    note: str | None = None
    override_drift: bool = False   # skip drift check; used when analyst chooses "Approve Anyway"


class RejectRequest(BaseModel):
    note: str | None = None


class DismissRequest(BaseModel):
    note: str | None = None


class MarkDriftedRequest(BaseModel):
    drift_details: dict[str, Any]
    note: str | None = None


# ── Serialiser ────────────────────────────────────────────────────────────────

def _action_out(a: AgentAction) -> dict[str, Any]:
    return {
        "id": str(a.id),
        "agent_name": a.agent_name,
        "agent_run_id": str(a.agent_run_id) if a.agent_run_id else None,
        "title": a.title,
        "summary": a.summary,
        "reasoning": a.reasoning,
        "confidence": a.confidence,
        "display_data": a.display_data,
        "tags": a.tags,
        "approval_action": a.approval_action,
        "status": a.status,
        "decided_by": a.decided_by,
        "decided_at": a.decided_at.isoformat() if a.decided_at else None,
        "decision_note": a.decision_note,
        "override_body": a.override_body,
        "approval_error": a.approval_error,
        "expires_at": a.expires_at.isoformat() if a.expires_at else None,
        "expected_state": a.expected_state,
        "stale_after_seconds": a.stale_after_seconds,
        "stale_marked_at": a.stale_marked_at.isoformat() if a.stale_marked_at else None,
        "drift_detected_at": a.drift_detected_at.isoformat() if a.drift_detected_at else None,
        "drift_details": a.drift_details,
        "drift_override": a.drift_override,
        "created_at": a.created_at.isoformat(),
        "updated_at": a.updated_at.isoformat(),
    }


# ── Staleness utilities ───────────────────────────────────────────────────────

def _parse_duration_seconds(s: str | None) -> int | None:
    """Parse a duration string like '4h', '2d', '30m' to seconds."""
    if not s:
        return None
    s = s.strip().lower()
    if s.endswith("d"):
        return int(s[:-1]) * 86400
    if s.endswith("h"):
        return int(s[:-1]) * 3600
    if s.endswith("m"):
        return int(s[:-1]) * 60
    if s.endswith("s"):
        return int(s[:-1])
    return int(s)


async def auto_mark_stale_actions(session: AsyncSession) -> int:
    """Auto-mark overdue pending_review actions as stale.

    Called at inbox load so the inbox never shows actions the analyst can no
    longer act on.  Returns the count of newly staled actions.
    """
    now = datetime.now(UTC)
    rows = await session.execute(
        select(AgentAction).where(
            AgentAction.status == "pending_review",
            AgentAction.stale_after_seconds.isnot(None),
        )
    )
    staled = 0
    for action in rows.scalars().all():
        age_seconds = (now - action.created_at.replace(tzinfo=UTC)).total_seconds()
        if age_seconds > action.stale_after_seconds:
            action.status = "stale"
            action.stale_marked_at = now
            staled += 1
    if staled:
        await session.commit()
    return staled


# ── Drift detection ───────────────────────────────────────────────────────────

async def _check_drift(action: AgentAction, session: AsyncSession) -> dict | None:
    """Compare current resource state against the expected_state snapshot.

    Returns a dict of {field: {"expected": val, "actual": val}} for changed
    fields, or None if no drift (or if drift checking is not configured).
    """
    expected = action.expected_state
    if not expected:
        return None

    resource_id = expected.get("resource_id")
    if not resource_id:
        return None

    # Load agent config to get track_resource_state.check_url
    row = await session.execute(select(Agent).where(Agent.name == action.agent_name))
    agent = row.scalar_one_or_none()
    if not agent:
        return None

    flags = agent.config.get("feature_flags", {})
    track = flags.get("track_resource_state")
    if not track:
        return None

    check_url: str = track.get("check_url", "")
    fields: list[str] = track.get("fields", [])
    if not check_url or not fields:
        return None

    full_url = settings.api_base_url.rstrip("/") + check_url.replace("{resource_id}", str(resource_id))
    headers = {"X-API-Key": settings.api_key, "Content-Type": "application/json"}

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(full_url, headers=headers)
            if resp.status_code != 200:
                return None  # can't verify — don't block the approval
            current = resp.json()
    except Exception:
        return None  # network error — don't block the approval

    drift: dict = {}
    for field in fields:
        expected_val = expected.get(field)
        current_val = current.get(field)
        if str(expected_val) != str(current_val):
            drift[field] = {"expected": expected_val, "actual": current_val}

    return drift if drift else None


# ── Approval execution engine ─────────────────────────────────────────────────

def _build_url(approval_action: dict) -> str:
    """Resolve url_params into the URL template and prepend base URL."""
    url: str = approval_action["url"]
    url_params: dict = approval_action.get("url_params", {})
    for key, val in url_params.items():
        url = url.replace(f"{{{key}}}", str(val))
    remaining = re.findall(r"\{(\w+)\}", url)
    if remaining:
        raise ValueError(f"Unresolved URL params: {remaining}")
    base = settings.api_base_url.rstrip("/")
    return f"{base}{url}"


async def _execute_approval_action(
    approval_action: dict,
    override_body: dict | None,
) -> tuple[bool, str | None]:
    """Call the stored approval_action HTTP endpoint.

    Returns (success: bool, error_message: str | None).
    """
    method: str = approval_action.get("method", "POST").upper()
    try:
        full_url = _build_url(approval_action)
    except ValueError as exc:
        return False, str(exc)

    body: dict = dict(approval_action.get("body", {}))
    if override_body:
        body.update(override_body)

    headers = {
        "Content-Type": "application/json",
        "X-API-Key": settings.api_key,
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.request(method, full_url, json=body, headers=headers)
            if resp.status_code >= 400:
                return False, f"HTTP {resp.status_code}: {resp.text[:300]}"
            return True, None
    except httpx.ConnectError as exc:
        return False, f"Connection failed: {exc}"
    except Exception as exc:
        return False, str(exc)


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("", status_code=status.HTTP_201_CREATED)
async def create_action(
    req: CreateActionRequest,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Create an AgentAction record (called by the propose_action tool)."""
    if req.confidence and req.confidence not in _VALID_CONFIDENCE:
        raise HTTPException(400, detail=f"confidence must be one of {_VALID_CONFIDENCE}")

    expires_at = None
    if req.expires_at:
        try:
            expires_at = datetime.fromisoformat(req.expires_at)
        except ValueError:
            raise HTTPException(400, detail="expires_at must be ISO 8601 datetime")

    run_id = None
    if req.agent_run_id:
        try:
            run_id = uuid.UUID(req.agent_run_id)
        except ValueError:
            raise HTTPException(400, detail="agent_run_id must be a valid UUID")

    # Look up stale_after from the agent's YAML config
    stale_after_seconds = None
    row = await session.execute(select(Agent).where(Agent.name == req.agent_name))
    agent = row.scalar_one_or_none()
    if agent:
        flags = agent.config.get("feature_flags", {})
        stale_after_seconds = _parse_duration_seconds(flags.get("stale_after"))

    action = AgentAction(
        agent_name=req.agent_name,
        agent_run_id=run_id,
        title=req.title,
        summary=req.summary,
        reasoning=req.reasoning,
        confidence=req.confidence,
        display_data=req.display_data,
        tags=req.tags,
        approval_action=req.approval_action,
        rejection_action=req.rejection_action,
        expires_at=expires_at,
        expected_state=req.expected_state,
        stale_after_seconds=stale_after_seconds,
    )
    session.add(action)
    await session.commit()
    await session.refresh(action)
    return _action_out(action)


@router.get("/counts")
async def action_counts(
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Return counts of pending_review actions grouped by agent_name.

    Used by the dashboard badge to show how many actions await review.
    """
    rows = await session.execute(
        select(AgentAction.agent_name, func.count(AgentAction.id))
        .where(AgentAction.status == "pending_review")
        .group_by(AgentAction.agent_name)
    )
    counts = {name: cnt for name, cnt in rows.all()}
    total = sum(counts.values())
    return {"total": total, "by_agent": counts}


@router.get("")
async def list_actions(
    action_status: str | None = Query(None, alias="status"),
    agent_name: str | None = Query(None),
    confidence: str | None = Query(None),
    include_history: bool = Query(False),
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """List agent actions with optional filters.

    By default returns only active (pending_review) actions, auto-marking stale
    ones before returning so the caller always sees a fresh inbox.
    Pass include_history=true to include terminated statuses.
    """
    auto_staled = await auto_mark_stale_actions(session)

    q = select(AgentAction).order_by(AgentAction.created_at.desc())
    if action_status:
        q = q.where(AgentAction.status == action_status)
    elif not include_history:
        q = q.where(AgentAction.status == "pending_review")
    if agent_name:
        q = q.where(AgentAction.agent_name == agent_name)
    if confidence:
        q = q.where(AgentAction.confidence == confidence)
    rows = await session.execute(q)
    actions = [_action_out(a) for a in rows.scalars().all()]
    return {"actions": actions, "auto_staled_count": auto_staled}


@router.get("/{action_id}")
async def get_action(
    action_id: str,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Get a single action by ID."""
    return _action_out(await _get_or_404(session, action_id))


@router.post("/{action_id}/approve")
async def approve_action(
    action_id: str,
    req: ApproveRequest,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Approve an action: execute its approval_action HTTP call, then mark approved.

    If the action has an expected_state snapshot and the agent is configured with
    track_resource_state, the platform compares current resource state before
    executing.  If drift is detected and override_drift is False, returns HTTP 409
    with drift details so the UI can present the three-choice drift panel.
    Set override_drift=true to proceed anyway (recorded with drift_override=true).
    """
    action = await _get_or_404(session, action_id)
    if action.status != "pending_review":
        raise HTTPException(409, detail=f"Action is '{action.status}' — only pending_review can be approved")

    now = datetime.now(UTC)

    # Drift check
    if action.expected_state and not req.override_drift:
        drift = await _check_drift(action, session)
        if drift:
            action.drift_detected_at = now
            action.drift_details = drift
            action.updated_at = now
            await session.commit()
            raise HTTPException(
                409,
                detail={
                    "conflict": "state_drift",
                    "drift_details": drift,
                    "action_id": action_id,
                    "message": "Resource state has changed since this action was proposed.",
                },
            )

    # If overriding drift, record it
    if req.override_drift and action.expected_state:
        drift = await _check_drift(action, session)
        if drift:
            action.drift_detected_at = now
            action.drift_details = drift
            action.drift_override = True

    success, error = await _execute_approval_action(action.approval_action, None)

    if success:
        action.status = "approved"
        action.approval_error = None
    else:
        action.status = "approval_failed"
        action.approval_error = error

    action.decided_by = "human"
    action.decided_at = now
    action.decision_note = req.note
    action.updated_at = now

    await session.commit()
    await session.refresh(action)

    if not success:
        raise HTTPException(
            502,
            detail={
                "message": "Approval action execution failed",
                "error": error,
                "action_id": action_id,
            },
        )
    return _action_out(action)


@router.post("/{action_id}/mark-drifted")
async def mark_drifted(
    action_id: str,
    req: MarkDriftedRequest,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Mark an action as drifted — resource state changed, action should not proceed."""
    action = await _get_or_404(session, action_id)
    if action.status != "pending_review":
        raise HTTPException(409, detail=f"Action is '{action.status}' — only pending_review can be marked drifted")

    now = datetime.now(UTC)
    action.status = "drifted"
    action.drift_detected_at = now
    action.drift_details = req.drift_details
    action.decided_by = "human"
    action.decided_at = now
    action.decision_note = req.note
    action.updated_at = now

    await session.commit()
    await session.refresh(action)
    return _action_out(action)


@router.post("/{action_id}/reject")
async def reject_action(
    action_id: str,
    req: RejectRequest,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Reject an action — marks it rejected and optionally calls rejection_action."""
    action = await _get_or_404(session, action_id)
    if action.status != "pending_review":
        raise HTTPException(409, detail=f"Action is '{action.status}' — only pending_review can be rejected")

    if action.rejection_action:
        await _execute_approval_action(action.rejection_action, None)

    now = datetime.now(UTC)
    action.status = "rejected"
    action.decided_by = "human"
    action.decided_at = now
    action.decision_note = req.note
    action.updated_at = now

    await session.commit()
    await session.refresh(action)
    return _action_out(action)


@router.post("/{action_id}/dismiss")
async def dismiss_action(
    action_id: str,
    req: DismissRequest,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Dismiss an action — analyst could not decide at this moment.

    Unlike reject (a deliberate No), dismiss signals "I've seen this but cannot
    act on it right now."  No rejection_action is called.  The action moves to
    History with status 'dismissed' and can be re-proposed by the agent.
    """
    action = await _get_or_404(session, action_id)
    if action.status != "pending_review":
        raise HTTPException(409, detail=f"Action is '{action.status}' — only pending_review can be dismissed")

    now = datetime.now(UTC)
    action.status = "dismissed"
    action.decided_by = "human"
    action.decided_at = now
    action.decision_note = req.note
    action.updated_at = now

    await session.commit()
    await session.refresh(action)
    return _action_out(action)


@router.post("/{action_id}/retry")
async def retry_action(
    action_id: str,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Retry execution of a failed approval_action."""
    action = await _get_or_404(session, action_id)
    if action.status != "approval_failed":
        raise HTTPException(409, detail=f"Action is '{action.status}' — only approval_failed can be retried")

    success, error = await _execute_approval_action(action.approval_action, None)

    now = datetime.now(UTC)
    action.status = "approved" if success else "approval_failed"
    action.approval_error = None if success else error
    action.decided_at = now
    action.updated_at = now

    await session.commit()
    await session.refresh(action)

    if not success:
        raise HTTPException(502, detail={"message": "Retry failed", "error": error})
    return _action_out(action)


# ── Helper ────────────────────────────────────────────────────────────────────

async def _get_or_404(session: AsyncSession, action_id: str) -> AgentAction:
    try:
        aid = uuid.UUID(action_id)
    except ValueError:
        raise HTTPException(400, detail="Invalid action ID")
    row = await session.execute(select(AgentAction).where(AgentAction.id == aid))
    action = row.scalar_one_or_none()
    if not action:
        raise HTTPException(404, detail=f"Action '{action_id}' not found")
    return action

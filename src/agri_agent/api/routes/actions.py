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
from agri_agent.db.models import AgentAction
from agri_agent.db.session import get_session

router = APIRouter(prefix="/api/v1/actions", tags=["actions"])

_VALID_STATUSES = {"pending_review", "approved", "rejected", "approval_failed", "expired"}
_VALID_CONFIDENCE = {"high", "medium", "low"}


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


class ApproveRequest(BaseModel):
    override_body: dict[str, Any] | None = None   # merged on top of approval_action.body
    note: str | None = None
    decided_by: str = "human"


class RejectRequest(BaseModel):
    note: str | None = None
    decided_by: str = "human"


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
        "created_at": a.created_at.isoformat(),
        "updated_at": a.updated_at.isoformat(),
    }


# ── Approval execution engine ─────────────────────────────────────────────────

def _build_url(approval_action: dict) -> str:
    """Resolve url_params into the URL template and prepend base URL."""
    url: str = approval_action["url"]
    url_params: dict = approval_action.get("url_params", {})
    for key, val in url_params.items():
        url = url.replace(f"{{{key}}}", str(val))
    # If any {placeholder} remains it means a param was not supplied
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
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """List agent actions with optional filters."""
    q = select(AgentAction).order_by(AgentAction.created_at.desc())
    if action_status:
        q = q.where(AgentAction.status == action_status)
    if agent_name:
        q = q.where(AgentAction.agent_name == agent_name)
    if confidence:
        q = q.where(AgentAction.confidence == confidence)
    rows = await session.execute(q)
    return [_action_out(a) for a in rows.scalars().all()]


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

    The platform resolves url_params, merges override_body, and makes the call
    server-side.  The domain API (e.g. PATCH /orders/{id}/dispatch) does not
    know it's being called via an approval — it's the same endpoint the human
    or agent would call directly.
    """
    action = await _get_or_404(session, action_id)
    if action.status != "pending_review":
        raise HTTPException(409, detail=f"Action is '{action.status}' — only pending_review can be approved")

    success, error = await _execute_approval_action(action.approval_action, req.override_body)

    now = datetime.now(UTC)
    if success:
        action.status = "approved"
        action.approval_error = None
    else:
        action.status = "approval_failed"
        action.approval_error = error

    action.decided_by = req.decided_by
    action.decided_at = now
    action.decision_note = req.note
    action.override_body = req.override_body
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

    # Fire rejection_action if defined (best-effort, don't fail on error)
    if action.rejection_action:
        await _execute_approval_action(action.rejection_action, None)

    now = datetime.now(UTC)
    action.status = "rejected"
    action.decided_by = req.decided_by
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

    success, error = await _execute_approval_action(action.approval_action, action.override_body)

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

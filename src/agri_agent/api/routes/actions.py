"""Generic agent-action inbox API.

Agents write AgentAction records via POST /api/v1/actions (through the
propose_action tool).  Humans review them at GET /approvals and approve/reject
via these endpoints.  On approval the platform executes the stored
approval_action HTTP call — no domain-specific code needed here.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import UTC, datetime
from typing import Any, AsyncGenerator

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agri_agent.api.dependencies import verify_api_key
from agri_agent.config.loader import load_agent_config
from agri_agent.config.settings import settings
from agri_agent.db.models import Agent, AgentAction, AgentRefineMessage, AgentRefineSession
from agri_agent.db.session import AsyncSessionLocal, get_session

_log = logging.getLogger(__name__)

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


class RefineMessageRequest(BaseModel):
    content: str


# ── Serialiser ────────────────────────────────────────────────────────────────

def _action_out(a: AgentAction, flags: dict | None = None) -> dict[str, Any]:
    f = flags or {}
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
        # Refinement capability — sourced from the agent's feature_flags
        "enable_refinement": bool(f.get("enable_refinement", False)),
        "refinement_agent": f.get("refinement_agent", ""),
        "refinement_preview": f.get("refinement_preview", ""),
    }


def _session_out(s: AgentRefineSession, *, is_new: bool = False) -> dict[str, Any]:
    return {
        "session_id": str(s.id),
        "action_id": str(s.action_id),
        "refinement_agent": s.refinement_agent,
        "status": s.status,
        "opened_by": s.opened_by,
        "is_new": is_new,
        "created_at": s.created_at.isoformat(),
        "closed_at": s.closed_at.isoformat() if s.closed_at else None,
    }


def _message_out(m: AgentRefineMessage) -> dict[str, Any]:
    return {
        "id": str(m.id),
        "session_id": str(m.session_id),
        "role": m.role,
        "content": m.content,
        "tool_calls": m.tool_calls,
        "input_tokens": m.input_tokens,
        "output_tokens": m.output_tokens,
        "langsmith_trace_url": m.langsmith_trace_url,
        "created_at": m.created_at.isoformat(),
    }


# ── Agent feature-flag helpers ────────────────────────────────────────────────

async def _agent_flags(session: AsyncSession, agent_name: str) -> dict:
    """Return feature_flags for a named agent.

    Prefers the YAML (source of truth) so that flag changes take effect without
    a DB record update. Falls back to the DB snapshot when the YAML is absent.
    """
    try:
        cfg = load_agent_config(agent_name)
        if cfg.feature_flags:
            return cfg.feature_flags
    except Exception:
        pass
    row = await session.execute(select(Agent).where(Agent.name == agent_name))
    agent = row.scalar_one_or_none()
    return (agent.config.get("feature_flags", {}) if agent else {})


async def _agents_flags(session: AsyncSession, agent_names: set[str]) -> dict[str, dict]:
    """Batch-fetch feature_flags for a set of agent names.

    Merges YAML flags (source of truth) over DB snapshots so that flag changes
    take effect immediately without a DB record update.
    """
    if not agent_names:
        return {}
    rows = await session.execute(select(Agent).where(Agent.name.in_(agent_names)))
    db_map = {a.name: a.config.get("feature_flags", {}) for a in rows.scalars().all()}
    result: dict[str, dict] = {}
    for name in agent_names:
        try:
            cfg = load_agent_config(name)
            result[name] = cfg.feature_flags if cfg.feature_flags else db_map.get(name, {})
        except Exception:
            result[name] = db_map.get(name, {})
    return result


async def _ensure_agent_active(session: AsyncSession, agent_name: str) -> None:
    """Auto-register and activate a refinement agent if it is not already active.

    Silently skips if the agent YAML does not yet exist — allows the session
    to be created in Phase 1 before the agent YAML is added in Phase 2.
    """
    from agri_agent.config.loader import load_agent_config

    row = await session.execute(select(Agent).where(Agent.name == agent_name))
    agent = row.scalar_one_or_none()
    if agent is None:
        try:
            cfg = load_agent_config(agent_name)
        except FileNotFoundError:
            return  # YAML not yet present; session creation still proceeds
        agent = Agent(
            name=cfg.name,
            description=cfg.description,
            version=cfg.version,
            config=cfg.model_dump(),
            is_active=True,
        )
        session.add(agent)
    elif not agent.is_active:
        agent.is_active = True
    await session.flush()


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
    action_list = rows.scalars().all()
    # Batch-load agent flags so the UI knows which actions have refinement enabled
    names = {a.agent_name for a in action_list}
    flags_map = await _agents_flags(session, names)
    actions = [_action_out(a, flags_map.get(a.agent_name)) for a in action_list]
    return {"actions": actions, "auto_staled_count": auto_staled}


@router.get("/{action_id}")
async def get_action(
    action_id: str,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Get a single action by ID."""
    action = await _get_or_404(session, action_id)
    flags = await _agent_flags(session, action.agent_name)
    return _action_out(action, flags)


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


# ── Refinement endpoints ──────────────────────────────────────────────────────

@router.post("/{action_id}/refine/start")
async def start_refine_session(
    action_id: str,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Open a 'Refine with AI' session on a pending action.

    Returns the existing active session if one already exists (idempotent).
    Auto-registers and auto-activates the configured refinement_agent.
    Fails with 422 if the action is not pending_review, or 403 if the agent
    does not have enable_refinement: true in its feature_flags.
    """
    action = await _get_or_404(session, action_id)
    if action.status != "pending_review":
        raise HTTPException(
            422,
            detail=f"Refinement requires a pending_review action; current status is '{action.status}'",
        )

    flags = await _agent_flags(session, action.agent_name)
    if not flags.get("enable_refinement"):
        raise HTTPException(
            403,
            detail=f"Agent '{action.agent_name}' does not have enable_refinement enabled",
        )

    refinement_agent = flags.get("refinement_agent", "").strip()
    if not refinement_agent:
        raise HTTPException(
            422,
            detail=f"Agent '{action.agent_name}' has enable_refinement=true but no refinement_agent configured",
        )

    # Return existing active session (idempotent — single-user system for v1)
    existing = await session.execute(
        select(AgentRefineSession)
        .where(AgentRefineSession.action_id == action.id)
        .where(AgentRefineSession.status == "active")
    )
    existing_session = existing.scalar_one_or_none()
    if existing_session:
        return _session_out(existing_session, is_new=False)

    # Ensure the refinement agent is registered and active
    await _ensure_agent_active(session, refinement_agent)

    refine_session = AgentRefineSession(
        action_id=action.id,
        refinement_agent=refinement_agent,
        status="active",
        opened_by="anonymous",
    )
    session.add(refine_session)
    await session.commit()
    await session.refresh(refine_session)
    return _session_out(refine_session, is_new=True)


@router.get("/{action_id}/refine/messages")
async def get_refine_messages(
    action_id: str,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Return the most recent refinement session and its messages for an action.

    Returns {"session": null, "messages": []} if no session has been started.
    Used on page reload to restore an in-progress session.
    """
    action = await _get_or_404(session, action_id)

    # Most recent session regardless of status (for page-reload restore)
    sess_row = await session.execute(
        select(AgentRefineSession)
        .where(AgentRefineSession.action_id == action.id)
        .order_by(AgentRefineSession.created_at.desc())
        .limit(1)
    )
    refine_session = sess_row.scalar_one_or_none()
    if not refine_session:
        return {"session": None, "messages": []}

    msg_rows = await session.execute(
        select(AgentRefineMessage)
        .where(AgentRefineMessage.session_id == refine_session.id)
        .order_by(AgentRefineMessage.created_at)
    )
    return {
        "session": _session_out(refine_session),
        "messages": [_message_out(m) for m in msg_rows.scalars().all()],
    }


@router.post("/{action_id}/refine/message")
async def refine_message(
    action_id: str,
    body: RefineMessageRequest,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Send a user message and receive a streaming AI response.

    Response is `text/event-stream`. Three event types:
      data: {"type": "token",    "content": "..."}
      data: {"type": "tool_use", "tool": "...", "args": {...}}
      data: {"type": "done",     "session_id": "...", "message_id": "..."}

    Validates that an active refinement session exists.
    Persists both the user message and the assistant reply to agent_refine_message.
    """
    action = await _get_or_404(session, action_id)
    if action.status != "pending_review":
        raise HTTPException(422, detail="Action is not in pending_review status")

    sess_row = await session.execute(
        select(AgentRefineSession)
        .where(AgentRefineSession.action_id == action.id)
        .where(AgentRefineSession.status == "active")
    )
    refine_session = sess_row.scalar_one_or_none()
    if not refine_session:
        raise HTTPException(404, detail="No active refinement session. Call /refine/start first.")

    # Persist user message
    user_msg = AgentRefineMessage(
        session_id=refine_session.id,
        role="user",
        content=body.content,
    )
    session.add(user_msg)
    await session.commit()
    await session.refresh(user_msg)

    # Load previous messages for history
    history_rows = await session.execute(
        select(AgentRefineMessage)
        .where(AgentRefineMessage.session_id == refine_session.id)
        .where(AgentRefineMessage.id != user_msg.id)
        .order_by(AgentRefineMessage.created_at)
    )
    prior_messages = history_rows.scalars().all()
    turn_index = len([m for m in prior_messages if m.role == "user"]) + 1

    # Load agent config
    try:
        agent_config = load_agent_config(refine_session.refinement_agent)
    except FileNotFoundError:
        raise HTTPException(500, detail=f"Refinement agent config '{refine_session.refinement_agent}' not found")

    # Extract plan_header_id from action
    plan_header_id = (action.approval_action or {}).get("url_params", {}).get("plan_header_id", "")

    # Build conversation history text
    history_parts = []
    for m in prior_messages:
        role_label = "Planner" if m.role == "user" else "Assistant"
        history_parts.append(f"{role_label}: {m.content}")
    history_text = "\n\n".join(history_parts) if history_parts else "(none — this is the first message)"

    # Build full message with context injection
    context_lines = f"  plan_header_id: {plan_header_id}" if plan_header_id else ""
    full_user_message = (
        f"[Runtime context]\n{context_lines}\n\n"
        f"[Conversation history so far]\n{history_text}\n\n"
        f"[Task]\n{body.content}"
    )

    # Capture these for the generator closure
    session_id = refine_session.id
    refinement_agent_name = refine_session.refinement_agent
    user_msg_id = user_msg.id

    async def event_generator() -> AsyncGenerator[str, None]:
        from agri_agent.agent import build_agent
        from agri_agent.agent.react_agent import _langsmith_url

        ls_run_id = uuid.uuid4()
        runnable_config = RunnableConfig(
            run_id=ls_run_id,
            recursion_limit=agent_config.guardrails.max_iterations,
            tags=[
                "feature:refine",
                f"refinement_agent:{refinement_agent_name}",
                f"session_id:{str(session_id)}",
                f"turn_index:{turn_index}",
            ],
            metadata={
                "session_id": str(session_id),
                "turn_index": turn_index,
                "plan_header_id": plan_header_id,
            },
        )

        agent = build_agent(agent_config)
        collected = ""
        tool_calls: list[dict] = []
        in_tok = out_tok = 0

        try:
            async for ev in agent.astream_events(
                {"messages": [HumanMessage(content=full_user_message)]},
                config=runnable_config,
                version="v2",
            ):
                kind = ev["event"]

                if kind == "on_chat_model_stream":
                    chunk = ev["data"].get("chunk")
                    if chunk:
                        content = chunk.content
                        token = ""
                        if isinstance(content, str):
                            token = content
                        elif isinstance(content, list):
                            for block in content:
                                if isinstance(block, dict) and block.get("type") == "text":
                                    token += block.get("text", "")
                        if token:
                            collected += token
                            yield f"data: {json.dumps({'type': 'token', 'content': token})}\n\n"

                elif kind == "on_tool_start":
                    tool_name = ev.get("name", "")
                    tool_input = ev["data"].get("input", {})
                    tool_calls.append({"tool": tool_name, "args": tool_input})
                    yield f"data: {json.dumps({'type': 'tool_use', 'tool': tool_name, 'args': tool_input})}\n\n"

                elif kind == "on_tool_end":
                    tool_name = ev.get("name", "")
                    output = ev["data"].get("output", "")
                    for tc in reversed(tool_calls):
                        if tc.get("tool") == tool_name and "result" not in tc:
                            tc["result"] = output if isinstance(output, str) else str(output)
                            break

                elif kind == "on_chat_model_end":
                    out = ev["data"].get("output")
                    if out and hasattr(out, "usage_metadata") and out.usage_metadata:
                        meta = out.usage_metadata
                        in_tok += meta.get("input_tokens", 0)
                        out_tok += meta.get("output_tokens", 0)

        except Exception as exc:
            _log.exception("Error during refine agent streaming for session %s", session_id)
            yield f"data: {json.dumps({'type': 'error', 'detail': str(exc)})}\n\n"
            return

        # Persist assistant message with plan snapshot
        ls_url: str | None = None
        try:
            ls_url = _langsmith_url(str(ls_run_id))
        except Exception:
            pass

        async with AsyncSessionLocal() as new_session:
            # Capture current plan state as context_snapshot for LLMOps
            context_snapshot: dict | None = None
            if plan_header_id:
                try:
                    import httpx as _httpx
                    base = settings.api_base_url if hasattr(settings, "api_base_url") else "http://localhost:8000"
                    async with _httpx.AsyncClient(
                        base_url=base,
                        headers={"X-API-Key": settings.api_key},
                        timeout=10.0,
                    ) as hc:
                        r = await hc.get(f"/api/v1/sandhar/plan/{plan_header_id}")
                        if r.status_code == 200:
                            context_snapshot = r.json()
                except Exception:
                    pass

            asst = AgentRefineMessage(
                session_id=session_id,
                role="assistant",
                content=collected,
                tool_calls=tool_calls or None,
                context_snapshot=context_snapshot,
                input_tokens=in_tok or None,
                output_tokens=out_tok or None,
                langsmith_run_id=str(ls_run_id),
                langsmith_trace_url=ls_url,
            )
            new_session.add(asst)
            await new_session.commit()
            await new_session.refresh(asst)
            asst_id = asst.id

        yield f"data: {json.dumps({'type': 'done', 'session_id': str(session_id), 'message_id': str(asst_id)})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.post("/{action_id}/refine/close")
async def close_refine_session(
    action_id: str,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Close the active refinement session without approving.

    Detects reversed turns (planner undid an AI change) by diffing consecutive
    context_snapshots and retroactively tags affected LangSmith runs with
    outcome=reversed. Sessions closed without any assistant message get outcome=abandoned.
    The action remains pending_review — Approve and Reject still available.
    """
    action = await _get_or_404(session, action_id)

    sess_row = await session.execute(
        select(AgentRefineSession)
        .where(AgentRefineSession.action_id == action.id)
        .where(AgentRefineSession.status == "active")
    )
    refine_session = sess_row.scalar_one_or_none()
    if not refine_session:
        raise HTTPException(404, detail="No active refinement session for this action")

    # Fetch all assistant messages with snapshots for reversal detection
    msg_rows = await session.execute(
        select(AgentRefineMessage)
        .where(AgentRefineMessage.session_id == refine_session.id)
        .where(AgentRefineMessage.role == "assistant")
        .order_by(AgentRefineMessage.created_at)
    )
    asst_messages = msg_rows.scalars().all()

    # Outcome: approved if action is approved, abandoned if no AI messages, else closed
    if action.status == "approved":
        outcome = "approved"
    elif not asst_messages:
        outcome = "abandoned"
    else:
        outcome = "closed"

    # Reversal detection: compare consecutive context_snapshots
    # A reversal is detected when the total planned_qty drops between consecutive turns
    # (simplest proxy for "planner undid an AI change")
    reversed_run_ids: list[str] = []
    if len(asst_messages) >= 2:
        for i in range(1, len(asst_messages)):
            prev = asst_messages[i - 1]
            curr = asst_messages[i]
            if prev.context_snapshot and curr.context_snapshot:
                prev_qty = _total_planned_qty(prev.context_snapshot)
                curr_qty = _total_planned_qty(curr.context_snapshot)
                if curr_qty < prev_qty and prev.langsmith_run_id:
                    reversed_run_ids.append(prev.langsmith_run_id)

    # Tag LangSmith runs retroactively (fire-and-forget — don't fail close on errors)
    _tag_langsmith_runs_async(
        session_run_ids=[m.langsmith_run_id for m in asst_messages if m.langsmith_run_id],
        reversed_run_ids=reversed_run_ids,
        outcome=outcome,
    )

    now = datetime.now(UTC)
    refine_session.status = "closed"
    refine_session.closed_at = now
    await session.commit()
    await session.refresh(refine_session)
    return {
        **_session_out(refine_session),
        "outcome": outcome,
        "reversed_turns": len(reversed_run_ids),
    }


def _total_planned_qty(snapshot: dict) -> int:
    """Extract total planned qty from a plan context_snapshot dict."""
    if not snapshot:
        return 0
    if "total_planned_qty" in snapshot:
        return int(snapshot["total_planned_qty"] or 0)
    details = snapshot.get("details", [])
    return sum(int(d.get("planned_qty") or 0) for d in details)


def _tag_langsmith_runs_async(
    session_run_ids: list[str],
    reversed_run_ids: list[str],
    outcome: str,
) -> None:
    """Fire-and-forget: tag LangSmith runs with outcome metadata.

    Uses a background thread to avoid blocking the close endpoint.
    Silently skips if LangSmith is not configured.
    """
    if not settings.langchain_api_key or not session_run_ids:
        return
    import threading

    def _tag():
        try:
            from langsmith import Client
            client = Client(api_key=settings.langchain_api_key)
            reversed_set = set(reversed_run_ids)
            for run_id_str in session_run_ids:
                run_outcome = "reversed" if run_id_str in reversed_set else outcome
                client.update_run(
                    run_id_str,
                    extra={"metadata": {"outcome": run_outcome, "feature": "refine"}},
                )
        except Exception as exc:
            _log.warning("LangSmith outcome tagging failed: %s", exc)

    threading.Thread(target=_tag, daemon=True).start()


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

"""Platform-level tools available to all agents.

propose_action  — writes a self-describing AgentAction record for human review.
                  Replaces all domain-specific recommend_* tools.
                  When human_in_the_loop=false, agents call the direct action
                  tool instead (e.g. dispatch_order).
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx
from langchain_core.tools import tool

_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")
_API_KEY = os.getenv("API_KEY", "dev-secret-key-change-in-prod")
_HEADERS = {"X-API-Key": _API_KEY, "Content-Type": "application/json"}


def _client() -> httpx.Client:
    return httpx.Client(base_url=_BASE_URL, headers=_HEADERS, timeout=30.0)


@tool
def propose_action(
    agent_name: str,
    title: str,
    summary: str,
    reasoning: str,
    confidence: str,
    display_data: str,
    approval_action: str,
    tags: str = "[]",
) -> str:
    """Propose an action for human review (human_in_the_loop = true).

    Creates a pending review record in the Action Inbox. A human analyst will
    see it at /approvals, inspect the details, and approve or reject it.
    On approval, the platform automatically executes the approval_action API call.

    USE THIS TOOL when the [Feature flags] block shows human_in_the_loop: true.
    Do NOT use this tool when human_in_the_loop: false — call the direct action
    tool (e.g. dispatch_order) instead.

    Args:
        agent_name:       Your agent name, e.g. "order-dispatch-review"
        title:            Short title shown in inbox, e.g. "Dispatch ORD-001 via AIR"
        summary:          One-line context, e.g. "MedCorp · $14,200 · due in 2 days"
        reasoning:        Your full justification referencing specific rules/data
        confidence:       "high", "medium", or "low"
        display_data:     JSON string — list of {"label": "...", "value": "..."} pairs
                          shown to the analyst in the review UI.
                          Example: '[{"label":"Order","value":"ORD-001"},{"label":"Mode","value":"AIR"}]'
        approval_action:  JSON string — HTTP call to execute on approval:
                          {"method":"PATCH","url":"/api/v1/orders/{order_id}/dispatch",
                           "url_params":{"order_id":"uuid"},"body":{"mode":"air","decided_by":"ai"},
                           "body_schema":{"mode":{"type":"enum","options":["air","train","road"],"label":"Override mode"}}}
        tags:             JSON string — optional list of tag strings, e.g. '["dispatch","urgent"]'

    Returns JSON with the created action's id and status.
    """
    # Parse JSON string arguments
    try:
        display_data_parsed: list[dict[str, Any]] = json.loads(display_data)
    except (json.JSONDecodeError, TypeError):
        return json.dumps({"error": "display_data must be a valid JSON array string"})

    try:
        approval_action_parsed: dict[str, Any] = json.loads(approval_action)
    except (json.JSONDecodeError, TypeError):
        return json.dumps({"error": "approval_action must be a valid JSON object string"})

    try:
        tags_parsed: list[str] = json.loads(tags)
    except (json.JSONDecodeError, TypeError):
        tags_parsed = []

    if confidence not in ("high", "medium", "low"):
        confidence = "medium"

    payload = {
        "agent_name": agent_name,
        "title": title,
        "summary": summary,
        "reasoning": reasoning,
        "confidence": confidence,
        "display_data": display_data_parsed,
        "tags": tags_parsed,
        "approval_action": approval_action_parsed,
    }

    with _client() as c:
        resp = c.post("/api/v1/actions", json=payload)
        resp.raise_for_status()
        data = resp.json()

    return json.dumps({
        "success": True,
        "action_id": data["id"],
        "status": data["status"],
        "title": data["title"],
        "message": "Action queued for human review at /approvals",
    })

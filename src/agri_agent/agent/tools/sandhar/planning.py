"""Sandhar planning tools."""
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
def sandhar_calculate_planned_qty(
    line_id: str,
    product_id: str,
    available_manpower: int,
    shift_code: str,
) -> str:
    """Calculate the planned production quantity for a line based on available manpower.
    Uses product standard cycle time and manpower to compute achievable output for the shift.
    Args:
        line_id: UUID of the production line
        product_id: UUID of the product to be produced
        available_manpower: Number of operators available for this line+shift
        shift_code: Shift code (A, B, or C) - each shift has 7 productive hours
    """
    with _client() as c:
        resp = c.get(f"/api/v1/sandhar/products/{product_id}")
        if resp.status_code != 200:
            return json.dumps({"error": f"Product {product_id} not found"})
        product = resp.json()

    standard_cycle_time = product.get("standard_cycle_time") or 0
    standard_manpower = product.get("standard_manpower") or 1
    shift_hours = 7  # 7 productive hours per shift (8h - 1h break)

    if standard_cycle_time <= 0 or standard_manpower <= 0:
        return json.dumps({"planned_qty": 0, "basis": "Missing product cycle time or manpower data"})

    manpower_ratio = available_manpower / standard_manpower
    units_per_hour = 60.0 / standard_cycle_time
    planned_qty = int(manpower_ratio * shift_hours * units_per_hour)

    return json.dumps({
        "planned_qty": planned_qty,
        "product_code": product.get("product_code"),
        "available_manpower": available_manpower,
        "standard_manpower": standard_manpower,
        "shift_hours": shift_hours,
        "basis": f"{available_manpower}/{standard_manpower} operators × {shift_hours}h × {units_per_hour:.1f} units/hr = {planned_qty} units",
    }, indent=2)


@tool
def sandhar_allocate_line(
    line_id: str,
    wo_id: str,
    shift_code: str,
    plan_date: str,
    operator_ids: str,
    supervisor_id: str,
    plan_header_id: str,
    planned_qty: int = 0,
) -> str:
    """Create resource allocation records for a production line assignment.
    Creates one allocation per operator and one plan detail record for the line.
    Always call sandhar_calculate_planned_qty first and pass the result as planned_qty.
    Args:
        line_id: UUID or line_code (e.g. 'L001') of the production line
        wo_id: UUID of the work order to produce
        shift_code: Shift code (A, B, or C)
        plan_date: Planning date in YYYY-MM-DD format
        operator_ids: Comma-separated employee UUIDs for operators
        supervisor_id: UUID of the supervisor for this line
        plan_header_id: UUID of the plan header to attach this allocation to
        planned_qty: Units planned for this shift (from sandhar_calculate_planned_qty)
    """
    operator_list = [oid.strip() for oid in operator_ids.split(",") if oid.strip()]

    payload = {
        "line_id": line_id,
        "wo_id": wo_id,
        "shift_code": shift_code,
        "plan_date": plan_date,
        "operator_ids": operator_list,
        "supervisor_id": supervisor_id,
        "planned_qty": planned_qty,
    }

    with _client() as c:
        resp = c.post(f"/api/v1/sandhar/plan/{plan_header_id}/allocate-line", json=payload)
        if resp.status_code not in (200, 201):
            return json.dumps({"error": f"Failed to allocate line: {resp.text}"})
        return json.dumps(resp.json(), indent=2)


@tool
def sandhar_save_plan_header(plan_date: str, shift_code: str, confidence: str) -> str:
    """Create a plan header record for a date and shift combination.
    Returns the plan_header_id needed by other planning tools like sandhar_allocate_line.
    Call this before allocating lines for each shift.
    Args:
        plan_date: Planning date in YYYY-MM-DD format
        shift_code: Shift code (A, B, or C)
        confidence: Plan confidence level - 'high', 'medium', or 'low'
    """
    with _client() as c:
        resp = c.post("/api/v1/sandhar/plan/header", json={
            "plan_date": plan_date,
            "shift_code": shift_code,
            "confidence": confidence,
        })
        if resp.status_code not in (200, 201):
            return json.dumps({"error": f"Failed to save plan header: {resp.text}"})
        return json.dumps(resp.json(), indent=2)


@tool
def sandhar_create_alert(
    alert_type: str,
    alert_message: str,
    severity: str,
    plan_date: str,
    shift_code: str,
    related_line_id: str = "",
    related_wo_id: str = "",
    related_employee_id: str = "",
    related_machine_id: str = "",
) -> str:
    """Create an alert record for a production planning issue.
    Use this to flag manpower shortages, skill gaps, machine breakdowns, material shortages, etc.
    Args:
        alert_type: Type of alert - manpower_shortage | skill_gap | machine_breakdown | material_shortage | quality_hold | production_delay | certification_expiry | excess_capacity
        alert_message: Human-readable description of the issue
        severity: Severity level - critical | high | medium | low | info
        plan_date: Planning date this alert relates to (YYYY-MM-DD)
        shift_code: Shift code (A, B, C, or empty string for non-shift-specific alerts)
        related_line_id: UUID of the affected production line (optional)
        related_wo_id: UUID of the affected work order (optional)
        related_employee_id: UUID of the affected employee (optional)
        related_machine_id: UUID of the affected machine (optional)
    """
    payload: dict = {
        "alert_type": alert_type,
        "alert_message": alert_message,
        "severity": severity,
        "plan_date": plan_date if plan_date else None,
        "shift_code": shift_code if shift_code else None,
    }
    if related_line_id:
        payload["related_line_id"] = related_line_id
    if related_wo_id:
        payload["related_wo_id"] = related_wo_id
    if related_employee_id:
        payload["related_employee_id"] = related_employee_id
    if related_machine_id:
        payload["related_machine_id"] = related_machine_id

    with _client() as c:
        resp = c.post("/api/v1/sandhar/alerts", json=payload)
        if resp.status_code not in (200, 201):
            return json.dumps({"error": f"Failed to create alert: {resp.text}"})
        return json.dumps(resp.json(), indent=2)


@tool
def sandhar_propose_plan_for_review(
    plan_header_id: str,
    plan_date: str,
    shift_code: str,
    summary: str,
    confidence: str,
    display_data: str,
    reasoning: str = "",
) -> str:
    """Propose the generated production plan for human (planner) review via the HITL inbox.
    The planner will see this proposal at /approvals or /sandhar/plan.
    On approval, the plan status will be set to 'approved'.
    Args:
        plan_header_id: UUID of the plan header to propose for review
        plan_date: Planning date (YYYY-MM-DD)
        shift_code: Shift code (A, B, or C)
        summary: One-line summary, e.g. "2,500 units across 3 lines, 2 alerts"
        confidence: Plan confidence - 'high', 'medium', or 'low'
        display_data: JSON array string of {"label": "...", "value": "..."} pairs for display
        reasoning: Optional explanation of how the plan was generated
    """
    try:
        display_data_parsed = json.loads(display_data)
    except (json.JSONDecodeError, TypeError):
        display_data_parsed = [{"label": "Summary", "value": summary}]

    payload = {
        "agent_name": "sandhar-plan-generator",
        "title": f"Production Plan {plan_date} — Shift {shift_code}",
        "summary": summary,
        "reasoning": reasoning or f"Production plan generated for {plan_date} Shift {shift_code}.",
        "confidence": confidence,
        "display_data": display_data_parsed,
        "tags": ["sandhar", "production-plan", f"shift-{shift_code}"],
        "approval_action": {
            "method": "POST",
            "url": f"/api/v1/sandhar/plan/{plan_header_id}/approve",
            "url_params": {"plan_header_id": plan_header_id},
            "body": {"planner_id": "human"},
        },
    }

    with _client() as c:
        resp = c.post("/api/v1/actions", json=payload)
        if resp.status_code not in (200, 201):
            return json.dumps({"error": f"Failed to propose plan for review: {resp.text}"})
        data = resp.json()
        return json.dumps({
            "success": True,
            "action_id": data.get("id"),
            "message": f"Production plan for {plan_date} Shift {shift_code} proposed for human review at /approvals",
        }, indent=2)

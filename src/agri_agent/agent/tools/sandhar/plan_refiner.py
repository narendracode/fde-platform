"""Sandhar plan refinement tools.

Called exclusively from the sandhar-plan-refiner agent during a 'Refine with AI' session.
All mutations go through the existing planning API endpoints.
"""
from __future__ import annotations

import json
import os

import httpx
from langchain_core.tools import tool

_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")
_API_KEY = os.getenv("API_KEY", "dev-secret-key-change-in-prod")
_HEADERS = {"X-API-Key": _API_KEY, "Content-Type": "application/json"}


def _client() -> httpx.Client:
    return httpx.Client(base_url=_BASE_URL, headers=_HEADERS, timeout=30.0)


@tool
def sandhar_refine_get_plan(plan_header_id: str) -> str:
    """Read the full current plan for a given plan header.

    Returns all plan detail rows with line assignments, WO numbers, planned quantities,
    manpower figures, and supervisor info. Use this first to understand the current state
    before making any changes. Also returns resource allocations.

    Args:
        plan_header_id: UUID of the SandharPlanHeader being refined
    """
    with _client() as c:
        resp = c.get(f"/api/v1/sandhar/plan/{plan_header_id}")
        if resp.status_code == 404:
            return json.dumps({"error": f"Plan header '{plan_header_id}' not found"})
        if resp.status_code != 200:
            return json.dumps({"error": f"Failed to fetch plan: {resp.text}"})
        data = resp.json()
        # Return a condensed summary to keep context manageable
        details = data.get("details", [])
        summary_lines = []
        total_qty = 0
        for d in details:
            qty = d.get("planned_qty") or 0
            total_qty += qty
            summary_lines.append({
                "detail_id": d.get("id"),
                "line_code": d.get("line_code") or d.get("line_id"),
                "wo_number": d.get("wo_number") or d.get("wo_id"),
                "product_name": d.get("product_name") or d.get("product_id"),
                "planned_qty": qty,
                "planned_manpower": d.get("planned_manpower"),
                "available_manpower": d.get("available_manpower"),
                "manpower_gap": d.get("manpower_gap"),
                "status": d.get("status"),
            })
        return json.dumps({
            "plan_header_id": plan_header_id,
            "shift_code": data.get("shift_code"),
            "plan_date": data.get("plan_date"),
            "status": data.get("status"),
            "confidence": data.get("confidence"),
            "total_planned_qty": total_qty,
            "details": summary_lines,
        }, indent=2)


@tool
def sandhar_refine_update_qty(
    plan_header_id: str,
    detail_id: str,
    new_qty: int | None = None,
    new_planned_manpower: int | None = None,
) -> str:
    """Update the planned quantity and/or planned operator count for a plan detail row.

    Use this when the planner wants to change how many units are planned on a line,
    or how many operators are allocated to it. At least one of new_qty or
    new_planned_manpower must be provided.

    Args:
        plan_header_id: UUID of the SandharPlanHeader being refined
        detail_id: UUID of the SandharPlanDetail row to update (from sandhar_refine_get_plan)
        new_qty: New planned quantity (must be > 0 if provided)
        new_planned_manpower: New number of planned operators (must be >= 0 if provided)
    """
    if new_qty is None and new_planned_manpower is None:
        return json.dumps({"error": "Provide at least one of new_qty or new_planned_manpower"})
    if new_qty is not None and new_qty <= 0:
        return json.dumps({"error": "planned_qty must be greater than 0"})
    if new_planned_manpower is not None and new_planned_manpower < 0:
        return json.dumps({"error": "planned_manpower must be >= 0"})

    payload: dict = {}
    if new_qty is not None:
        payload["planned_qty"] = new_qty
    if new_planned_manpower is not None:
        payload["planned_manpower"] = new_planned_manpower

    with _client() as c:
        resp = c.patch(
            f"/api/v1/sandhar/plan/{plan_header_id}/details/{detail_id}",
            json=payload,
        )
        if resp.status_code == 404:
            return json.dumps({"error": f"Plan detail '{detail_id}' not found in plan '{plan_header_id}'"})
        if resp.status_code != 200:
            return json.dumps({"error": f"Failed to update detail: {resp.text}"})
        d = resp.json()
        changes = []
        if new_qty is not None:
            changes.append(f"planned_qty → {new_qty}")
        if new_planned_manpower is not None:
            changes.append(f"planned_manpower → {new_planned_manpower}")
        return json.dumps({
            "success": True,
            "detail_id": d.get("id"),
            "line_id": d.get("line_id"),
            "planned_qty": d.get("planned_qty"),
            "planned_manpower": d.get("planned_manpower"),
            "message": "Updated: " + ", ".join(changes),
        }, indent=2)


@tool
def sandhar_refine_move_wo(plan_header_id: str, detail_id: str, new_line_id: str) -> str:
    """Reassign a work order to a different production line.

    Use this when the planner wants to move a WO from one line to another.
    Pass the line_code (e.g. 'L002') or the line UUID.
    Note: the existing detail row is updated in place — manpower figures may need
    manual adjustment afterwards if the new line has different staffing.

    Args:
        plan_header_id: UUID of the SandharPlanHeader being refined
        detail_id: UUID of the SandharPlanDetail row to move (from sandhar_refine_get_plan)
        new_line_id: Line code (e.g. 'L002') or UUID of the destination line
    """
    with _client() as c:
        resp = c.patch(
            f"/api/v1/sandhar/plan/{plan_header_id}/details/{detail_id}",
            json={"line_id": new_line_id},
        )
        if resp.status_code == 404:
            return json.dumps({"error": f"Plan detail or line not found"})
        if resp.status_code != 200:
            return json.dumps({"error": f"Failed to move WO: {resp.text}"})
        d = resp.json()
        return json.dumps({
            "success": True,
            "detail_id": d.get("id"),
            "new_line_id": d.get("line_id"),
            "wo_id": d.get("wo_id"),
            "planned_qty": d.get("planned_qty"),
            "message": f"WO moved to line '{new_line_id}' successfully",
        }, indent=2)


@tool
def sandhar_refine_add_wo(
    plan_header_id: str,
    wo_id: str,
    line_id: str,
    planned_qty: int,
    planned_manpower: int | None = None,
) -> str:
    """Add an open work order to the plan on a specified production line.

    Use this when the planner wants to include a WO that was not originally planned.
    The WO must be open (not quality-held). Pass the WO UUID.
    Get open WO UUIDs by calling sandhar_refine_get_plan — open WOs are not in the current details.
    Use sandhar_get_open_work_orders (available separately) to list all open WOs if needed.

    Args:
        plan_header_id: UUID of the SandharPlanHeader being refined
        wo_id: UUID of the open SandharWorkOrder to add
        line_id: Line code (e.g. 'L001') or UUID of the line to run the WO on
        planned_qty: Planned quantity for this WO (must be > 0)
        planned_manpower: Number of operators allocated (optional)
    """
    if planned_qty <= 0:
        return json.dumps({"error": "planned_qty must be greater than 0"})
    payload: dict = {"wo_id": wo_id, "line_id": line_id, "planned_qty": planned_qty}
    if planned_manpower is not None:
        payload["planned_manpower"] = planned_manpower
    with _client() as c:
        resp = c.post(f"/api/v1/sandhar/plan/{plan_header_id}/details", json=payload)
        if resp.status_code == 404:
            return json.dumps({"error": "Plan header, WO, or line not found"})
        if resp.status_code not in (200, 201):
            return json.dumps({"error": f"Failed to add WO to plan: {resp.text}"})
        d = resp.json()
        return json.dumps({
            "success": True,
            "detail_id": d.get("id"),
            "wo_id": d.get("wo_id"),
            "line_id": d.get("line_id"),
            "planned_qty": d.get("planned_qty"),
            "message": f"WO added to plan on line '{line_id}' with {planned_qty} units",
        }, indent=2)


@tool
def sandhar_refine_remove_wo(plan_header_id: str, detail_id: str) -> str:
    """Remove a work order from the plan. The WO returns to unplanned status.

    Use this when the planner decides a WO should not be produced in this shift.
    The WO remains in the system — it can be added back or planned in a future shift.

    Args:
        plan_header_id: UUID of the SandharPlanHeader being refined
        detail_id: UUID of the SandharPlanDetail row to remove (from sandhar_refine_get_plan)
    """
    with _client() as c:
        resp = c.delete(f"/api/v1/sandhar/plan/{plan_header_id}/details/{detail_id}")
        if resp.status_code == 404:
            return json.dumps({"error": f"Plan detail '{detail_id}' not found in plan '{plan_header_id}'"})
        if resp.status_code not in (200, 204):
            return json.dumps({"error": f"Failed to remove WO from plan: {resp.text}"})
        return json.dumps({
            "success": True,
            "detail_id": detail_id,
            "message": "WO removed from plan. It is now unplanned and can be re-added to a future shift.",
        }, indent=2)


@tool
def sandhar_refine_explain_constraint(plan_header_id: str, constraint_description: str) -> str:
    """Explain a specific manpower gap, alert, or planning constraint in plain language.

    Use this when the planner asks 'why is L001 short?' or 'what does the alert mean?'
    Fetches the current plan state and formats a clear explanation.

    Args:
        plan_header_id: UUID of the SandharPlanHeader being refined
        constraint_description: Description of the constraint to explain
          (e.g. 'L001 manpower gap', 'high severity alert on L003', 'low confidence')
    """
    with _client() as c:
        resp = c.get(f"/api/v1/sandhar/plan/{plan_header_id}")
        if resp.status_code != 200:
            return json.dumps({"error": f"Could not fetch plan: {resp.text}"})
        data = resp.json()
    details = data.get("details", [])
    gaps = [
        {
            "line": d.get("line_code") or d.get("line_id"),
            "wo": d.get("wo_number") or d.get("wo_id"),
            "manpower_gap": d.get("manpower_gap"),
            "planned_manpower": d.get("planned_manpower"),
            "available_manpower": d.get("available_manpower"),
            "planned_qty": d.get("planned_qty"),
        }
        for d in details
        if (d.get("manpower_gap") or 0) != 0
    ]
    return json.dumps({
        "constraint_asked_about": constraint_description,
        "plan_confidence": data.get("confidence"),
        "shift": data.get("shift_code"),
        "lines_with_gaps": gaps,
        "note": (
            "Lines with manpower_gap < 0 are understaffed. "
            "The gap is (available_manpower - planned_manpower). "
            "Use sandhar_refine_update_qty to reduce planned_qty proportionally, "
            "or accept the gap if the planner is comfortable with reduced output."
        ),
    }, indent=2)

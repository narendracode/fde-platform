"""Sandhar attendance tools."""
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
def sandhar_get_attendance_summary(plan_date: str, shift_code: str) -> str:
    """Get shift-wise attendance summary for a planning date.
    Returns present/absent/late/leave counts and operator/supervisor breakdown per shift.
    Args:
        plan_date: Date in YYYY-MM-DD format
        shift_code: Shift code (A, B, or C) - used for context but returns all shifts
    """
    with _client() as c:
        resp = c.get("/api/v1/sandhar/attendance/summary", params={"date": plan_date})
        if resp.status_code != 200:
            return json.dumps({"error": f"Failed to get attendance summary: {resp.text}"})
        return json.dumps(resp.json(), indent=2)


@tool
def sandhar_get_present_operators(plan_date: str, shift_code: str) -> str:
    """Get list of employees present for a specific shift and date.
    Returns employee details including employee_id, name, designation, and employee_code.
    Args:
        plan_date: Date in YYYY-MM-DD format
        shift_code: Shift code (A, B, or C)
    """
    with _client() as c:
        resp = c.get("/api/v1/sandhar/attendance", params={
            "date": plan_date, "shift_code": shift_code, "status": "present", "limit": 200
        })
        if resp.status_code != 200:
            return json.dumps({"error": f"Failed to get present operators: {resp.text}"})
        return json.dumps(resp.json(), indent=2)


@tool
def sandhar_check_certification_expiry(plan_date: str) -> str:
    """Get employee skill certifications expiring within 30 days of the plan date.
    Returns list of {employee_id, employee_code, name, skill_level, expiry_date, line_id, machine_id}.
    Args:
        plan_date: Reference date in YYYY-MM-DD format
    """
    with _client() as c:
        resp = c.get("/api/v1/sandhar/skills/expiring", params={"plan_date": plan_date})
        if resp.status_code != 200:
            return json.dumps({"error": f"Failed to check certification expiry: {resp.text}"})
        return json.dumps(resp.json(), indent=2)


@tool
def sandhar_find_qualified_operators(
    line_id: str,
    plan_date: str,
    shift_code: str,
    min_skill_level: int = 2,
    machine_id: str = "",
) -> str:
    """Find operators who are present AND qualified to work on a specific line or machine.
    Checks both skill matrix (active certifications) and attendance (present on this shift/date).
    Returns list of {employee_id, employee_code, name, designation, skill_level}.
    Args:
        line_id: Line UUID or line_code (e.g. 'L001', 'L002', 'L003'). Get from sandhar_get_open_work_orders or sandhar_list_lines.
        plan_date: Date in YYYY-MM-DD format
        shift_code: Shift code (A, B, or C)
        min_skill_level: Minimum skill level required (1=Trainee, 2=Basic, 3=Skilled, 4=Expert). Default 2.
        machine_id: Machine UUID or machine_code (e.g. 'M001'). Optional, for machine-specific skills.
    """
    params = {
        "line_id": line_id,
        "plan_date": plan_date,
        "shift_code": shift_code,
        "min_skill_level": min_skill_level,
    }
    if machine_id:
        params["machine_id"] = machine_id
    with _client() as c:
        resp = c.get("/api/v1/sandhar/skills/qualified-operators", params=params)
        if resp.status_code != 200:
            return json.dumps({"error": f"Failed to find qualified operators: {resp.text}"})
        return json.dumps(resp.json(), indent=2)


@tool
def sandhar_get_operator_skills(employee_id: str) -> str:
    """Get all skill assignments for a specific employee.
    Returns list of skills with line_id, machine_id, skill_level, certification dates.
    Args:
        employee_id: UUID of the employee
    """
    with _client() as c:
        resp = c.get("/api/v1/sandhar/skills", params={"employee_id": employee_id})
        if resp.status_code != 200:
            return json.dumps({"error": f"Failed to get operator skills: {resp.text}"})
        return json.dumps(resp.json(), indent=2)


@tool
def sandhar_get_crossskill_candidates(
    line_id: str,
    plan_date: str,
    shift_code: str,
    count_needed: int = 1,
) -> str:
    """Find cross-skilled operators from the same shift who are qualified for a line but not yet allocated.
    Used to fill manpower gaps by pulling in operators from lower-priority lines.
    Args:
        line_id: Line UUID or line_code (e.g. 'L001', 'L002', 'L003'). Get from sandhar_get_open_work_orders or sandhar_list_lines.
        plan_date: Date in YYYY-MM-DD format
        shift_code: Shift code (A, B, or C)
        count_needed: Number of additional operators needed
    """
    with _client() as c:
        resp = c.get("/api/v1/sandhar/skills/qualified-operators", params={
            "line_id": line_id,
            "plan_date": plan_date,
            "shift_code": shift_code,
            "min_skill_level": 2,
        })
        if resp.status_code != 200:
            return json.dumps({"error": f"Failed to find cross-skill candidates: {resp.text}"})
        all_qualified = resp.json()
        candidates = all_qualified[:count_needed] if count_needed > 0 else all_qualified
        return json.dumps({
            "candidates": candidates,
            "total_qualified": len(all_qualified),
            "returning": len(candidates),
        }, indent=2)

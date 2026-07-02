"""Sandhar constraints tools."""
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
def sandhar_get_machine_status(plan_date: str = "") -> str:
    """Get current operational status of all production machines.
    Returns list of machines with their current status (running/breakdown/maintenance/idle).
    Args:
        plan_date: Planning date for context (YYYY-MM-DD, optional)
    """
    with _client() as c:
        resp = c.get("/api/v1/sandhar/machines/status")
        if resp.status_code != 200:
            return json.dumps({"error": f"Failed to get machine status: {resp.text}"})
        return json.dumps(resp.json(), indent=2)


@tool
def sandhar_check_material_availability(plan_date: str) -> str:
    """Check material availability for a planning date.
    Returns only products with material shortages (shortfall_qty > 0).
    Args:
        plan_date: Date to check in YYYY-MM-DD format
    """
    with _client() as c:
        resp = c.get("/api/v1/sandhar/material", params={"date": plan_date, "constraint_flag": "true"})
        if resp.status_code != 200:
            return json.dumps({"error": f"Failed to check material availability: {resp.text}"})
        return json.dumps(resp.json(), indent=2)


@tool
def sandhar_get_quality_holds(plan_date: str = "") -> str:
    """Get active quality holds on work orders or products.
    Returns list of quality holds with WO number, reason, and hold status.
    Args:
        plan_date: Date for context (YYYY-MM-DD, optional)
    """
    with _client() as c:
        resp = c.get("/api/v1/sandhar/quality-hold")
        if resp.status_code != 200:
            return json.dumps({"error": f"Failed to get quality holds: {resp.text}"})
        return json.dumps(resp.json(), indent=2)


@tool
def sandhar_get_constraint_summary(plan_date: str) -> str:
    """Get consolidated summary of all production constraints for a planning date.
    Includes machine breakdowns, material shortages, and quality holds in one call.
    Args:
        plan_date: Date to check in YYYY-MM-DD format
    """
    with _client() as c:
        resp = c.get("/api/v1/sandhar/constraints/summary", params={"plan_date": plan_date})
        if resp.status_code != 200:
            return json.dumps({"error": f"Failed to get constraint summary: {resp.text}"})
        return json.dumps(resp.json(), indent=2)

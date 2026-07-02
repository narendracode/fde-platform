"""Sandhar work orders tools."""
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
def sandhar_list_lines() -> str:
    """Get all assembly lines with their IDs, codes, names, and capacity.
    Always call this first to get valid line_id values before calling
    sandhar_find_qualified_operators or sandhar_allocate_line.
    Returns list of {id, line_code, line_name, capacity_per_shift, status}.
    """
    with _client() as c:
        resp = c.get("/api/v1/sandhar/lines")
        if resp.status_code != 200:
            return json.dumps({"error": f"Failed to list lines: {resp.text}"})
        return json.dumps(resp.json(), indent=2)


@tool
def sandhar_get_open_work_orders(plan_date: str) -> str:
    """Get all open work orders eligible for production planning.
    Returns WOs sorted by priority (high first) then due date (soonest first).
    Excludes WOs on quality hold. Each WO includes:
      - line_id, line_code: use for sandhar_find_qualified_operators
      - standard_manpower: pass directly to sandhar_calculate_planned_qty as standard_manpower
      - standard_cycle_time: pass directly to sandhar_calculate_planned_qty as cycle_time_minutes
    Do NOT pass wo_number or id to sandhar_calculate_planned_qty — pass the numeric fields above.
    Args:
        plan_date: Planning date in YYYY-MM-DD format (used for context)
    """
    with _client() as c:
        resp = c.get("/api/v1/sandhar/work-orders/open")
        if resp.status_code != 200:
            return json.dumps({"error": f"Failed to get open work orders: {resp.text}"})
        return json.dumps(resp.json(), indent=2)


@tool
def sandhar_get_work_order_detail(wo_id: str) -> str:
    """Get complete details for a specific work order.
    Returns WO with product information, customer, quantities, and current status.
    Args:
        wo_id: UUID of the work order
    """
    with _client() as c:
        resp = c.get(f"/api/v1/sandhar/work-orders/{wo_id}")
        if resp.status_code != 200:
            return json.dumps({"error": f"Work order {wo_id} not found: {resp.text}"})
        return json.dumps(resp.json(), indent=2)


@tool
def sandhar_rank_work_orders(wo_ids: str, plan_date: str) -> str:
    """Rank a set of work orders by production priority.
    Priority order: customer criticality (critical > high > medium > low), then due date proximity.
    Args:
        wo_ids: Comma-separated list of work order UUIDs to rank
        plan_date: Planning date for context (YYYY-MM-DD)
    """
    with _client() as c:
        resp = c.get("/api/v1/sandhar/work-orders/open")
        if resp.status_code != 200:
            return json.dumps({"error": f"Failed to rank work orders: {resp.text}"})
        all_open = resp.json()

    requested_ids = set(id.strip() for id in wo_ids.split(",") if id.strip())
    ranked = [wo for wo in all_open if wo.get("id") in requested_ids]
    return json.dumps({"ranked_work_orders": ranked, "total": len(ranked)}, indent=2)

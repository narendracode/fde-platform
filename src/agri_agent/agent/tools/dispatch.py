"""Order dispatch tools for the pharma distributor demo.

All tools make HTTP calls to the platform's Orders API — the same endpoints
the human dashboard uses.  This means the AI and the human are calling
identical code paths.

Tools:
    get_pending_orders     — list all pending orders
    get_order_details      — full detail for one order
    get_dispatch_rules     — business rules for mode selection
    dispatch_order         — set mode and mark ready_to_dispatch (Mode 3)
    recommend_dispatch     — store AI recommendation for human review (Mode 2)
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


# ── Tools ──────────────────────────────────────────────────────────────────────

@tool
def get_pending_orders(limit: int = 50) -> str:
    """Retrieve pending orders up to the specified limit.

    Returns a JSON list of orders sorted by urgency (soonest due date first).
    Each order includes: order_ref, retailer_name, medicine_name, quantity,
    order_amount_usd, margin_percent, urgency_days (days until due date),
    and id (needed to dispatch or recommend).

    Call this first to discover what needs to be processed.

    Args:
        limit: Maximum number of orders to return. Pass the batch_size value
               from the runtime context. Defaults to 50.
    """
    with _client() as c:
        resp = c.get("/api/v1/orders", params={"status": "pending", "limit": limit})
        resp.raise_for_status()
        orders = resp.json()
    if not orders:
        return json.dumps({"message": "No pending orders found.", "orders": []})
    return json.dumps({"total": len(orders), "orders": orders}, indent=2)


@tool
def get_order_details(order_id: str) -> str:
    """Get complete details for a single order by its ID.

    Args:
        order_id: The UUID of the order (from get_pending_orders results).

    Returns full order detail including urgency_days, margin_percent,
    order_amount_usd, and current status.
    """
    with _client() as c:
        resp = c.get(f"/api/v1/orders/{order_id}")
        resp.raise_for_status()
    return json.dumps(resp.json(), indent=2)


@tool
def get_dispatch_rules() -> str:
    """Return the shipment mode decision rules used by the distributor.

    Call this once before processing orders to understand the business logic.
    Apply these rules consistently to every order you process.
    """
    rules = {
        "rules": [
            {
                "priority": 1,
                "condition": "urgency_days <= 2 AND order_amount_usd > 5000",
                "mode": "air",
                "rationale": "Very urgent and high-value — fastest mode to protect revenue and relationship",
            },
            {
                "priority": 2,
                "condition": "urgency_days <= 2 AND order_amount_usd <= 5000",
                "mode": "train",
                "rationale": "Very urgent but low-value — air cost not justified, train still fast",
            },
            {
                "priority": 3,
                "condition": "urgency_days BETWEEN 3 AND 5 AND order_amount_usd > 8000",
                "mode": "train",
                "rationale": "Near deadline and high value — train balances speed and cost",
            },
            {
                "priority": 4,
                "condition": "urgency_days BETWEEN 3 AND 5 AND order_amount_usd <= 8000",
                "mode": "road",
                "rationale": "Near deadline but lower value — road is sufficient if booked immediately",
            },
            {
                "priority": 5,
                "condition": "urgency_days > 5",
                "mode": "road",
                "rationale": "Comfortable timeline — road is most cost-effective",
            },
        ],
        "margin_upgrade_rule": {
            "condition": "margin_percent >= 25",
            "effect": "Upgrade one tier: road → train, train → air",
            "rationale": "High-margin orders justify faster shipment to maintain retailer satisfaction",
        },
        "confidence_guide": {
            "high": "Order clearly matches a single rule with no ambiguity",
            "medium": "Order is near a boundary (e.g. urgency_days = 3, amount near threshold)",
            "low": "Multiple rules apply or significant uncertainty in urgency/amount tradeoff",
        },
    }
    return json.dumps(rules, indent=2)


@tool
def dispatch_order(order_id: str, mode: str, reasoning: str) -> str:
    """Set the shipment mode for an order and mark it as ready to dispatch.

    USE THIS TOOL when human_in_the_loop is false (full automation mode).
    The order is dispatched immediately — no human review step.

    Args:
        order_id: UUID of the order to dispatch.
        mode: Shipment mode — must be exactly 'air', 'train', or 'road'.
        reasoning: Brief explanation of why this mode was chosen (stored for audit).

    Returns the updated order record with status=ready_to_dispatch.
    """
    if mode not in ("air", "train", "road"):
        return json.dumps({"error": f"Invalid mode '{mode}'. Must be air, train, or road."})

    with _client() as c:
        resp = c.patch(
            f"/api/v1/orders/{order_id}/dispatch",
            json={"mode": mode, "decided_by": "ai", "reasoning": reasoning},
        )
        if resp.status_code == 409:
            data = resp.json()
            return json.dumps({"skipped": True, "reason": data.get("detail"), "order_id": order_id})
        resp.raise_for_status()
    return json.dumps({"success": True, "order_id": order_id, "mode": mode, **resp.json()})


@tool
def recommend_dispatch(order_id: str, mode: str, confidence: str, reasoning: str) -> str:
    """Store an AI shipment recommendation for human review.

    USE THIS TOOL when human_in_the_loop is true (human review mode).
    The order moves to 'pending_review' — a human analyst will see this
    recommendation on the dashboard and approve or reject it.

    Args:
        order_id:   UUID of the order.
        mode:       Recommended shipment mode — 'air', 'train', or 'road'.
        confidence: Your confidence level — 'high', 'medium', or 'low'.
        reasoning:  Clear explanation of why you chose this mode, referencing
                    the specific rule(s) that apply (shown to the analyst).

    Returns the updated order record with status=pending_review.
    """
    if mode not in ("air", "train", "road"):
        return json.dumps({"error": f"Invalid mode '{mode}'. Must be air, train, or road."})
    if confidence not in ("high", "medium", "low"):
        confidence = "medium"

    with _client() as c:
        resp = c.patch(
            f"/api/v1/orders/{order_id}/recommend",
            json={"mode": mode, "confidence": confidence, "reasoning": reasoning},
        )
        if resp.status_code == 409:
            data = resp.json()
            return json.dumps({"skipped": True, "reason": data.get("detail"), "order_id": order_id})
        resp.raise_for_status()
    return json.dumps({"success": True, "order_id": order_id, "mode": mode,
                       "confidence": confidence, **resp.json()})

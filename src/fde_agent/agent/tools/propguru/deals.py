"""Propguru deal + property read tools."""
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
def propguru_get_deal(deal_id: str) -> str:
    """Get full deal details including property and channel partner info.

    Returns deal stage, target price, property details (area, bedrooms, floor,
    facing, locality, coordinates) and sourcing CP info.
    Call this first at the start of every evaluation pipeline.

    Args:
        deal_id: Deal UUID or deal_code (e.g. 'DEAL-001')
    """
    with _client() as c:
        resp = c.get(f"/api/v1/propguru/deals/{deal_id}")
        if resp.status_code == 404:
            return json.dumps({"error": f"Deal '{deal_id}' not found"})
        if resp.status_code != 200:
            return json.dumps({"error": f"Failed to fetch deal: {resp.text}"})
        return json.dumps(resp.json(), indent=2)


@tool
def propguru_get_property_details(property_id: str) -> str:
    """Get full property record — all columns including area, floor, age, facing, coordinates.

    Use this when you need granular property data beyond what propguru_get_deal returns.

    Args:
        property_id: Property UUID or property_code (e.g. 'PROP-001')
    """
    with _client() as c:
        resp = c.get(f"/api/v1/propguru/properties/{property_id}")
        if resp.status_code == 404:
            return json.dumps({"error": f"Property '{property_id}' not found"})
        if resp.status_code != 200:
            return json.dumps({"error": f"Failed to fetch property: {resp.text}"})
        return json.dumps(resp.json(), indent=2)


@tool
def propguru_list_deals(stage: str = "") -> str:
    """List deals, optionally filtered by stage.

    Args:
        stage: Optional stage filter — one of: lead, evaluation_pending, evaluation_done,
               agreement_signed, listed, sold, lost. Pass empty string for all.
    """
    with _client() as c:
        params = {"stage": stage} if stage else {}
        resp = c.get("/api/v1/propguru/deals", params=params)
        if resp.status_code != 200:
            return json.dumps({"error": f"Failed to list deals: {resp.text}"})
        data = resp.json()
        summary = [
            {
                "deal_id": d.get("id"),
                "deal_code": d.get("deal_code"),
                "stage": d.get("stage"),
                "locality": d.get("property", {}).get("locality") if d.get("property") else None,
                "bedrooms": d.get("property", {}).get("bedrooms") if d.get("property") else None,
                "cp_name": d.get("sourcing_cp", {}).get("name") if d.get("sourcing_cp") else None,
                "target_acquisition_price": d.get("target_acquisition_price"),
            }
            for d in data
        ]
        return json.dumps({"total": len(summary), "deals": summary}, indent=2)

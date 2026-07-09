"""Propguru evaluation tools — scoring, pricing, HITL proposal."""
from __future__ import annotations

import json
import os

import httpx
from langchain_core.tools import tool

_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")
_API_KEY = os.getenv("API_KEY", "dev-secret-key-change-in-prod")
_HEADERS = {"X-API-Key": _API_KEY, "Content-Type": "application/json"}

# Proximity scoring bands: closer = better
_PROXIMITY_BANDS = [
    (0.5, 5.0),
    (1.0, 4.0),
    (2.0, 3.0),
    (4.0, 2.0),
]  # (max_km, score) — if > 4 km, score = 1.0


def _client() -> httpx.Client:
    return httpx.Client(base_url=_BASE_URL, headers=_HEADERS, timeout=30.0)


def _proximity_score(distance_km: float) -> float:
    """Convert a distance in km to a 1–5 proximity score."""
    for max_km, score in _PROXIMITY_BANDS:
        if distance_km <= max_km:
            return score
    return 1.0


@tool
def propguru_get_criteria() -> str:
    """Fetch all 30 active evaluation criteria with weights and scoring types.

    Returns criterion_code, name, category, weight, scoring_type, description.
    Call this first so you know what to score and how each criterion is measured.
    Categories: amenity (10), location (10), property (5), society (5).
    Scoring types: boolean (0/1), scale_1_5 (1-5), proximity_km (distance in km → scored 1-5).
    """
    with _client() as c:
        resp = c.get("/api/v1/propguru/evaluation-criteria", params={"is_active": "true"})
        if resp.status_code != 200:
            return json.dumps({"error": f"Failed to fetch criteria: {resp.text}"})
        data = resp.json()
        return json.dumps({
            "total": len(data),
            "criteria": [
                {
                    "id": c["id"],
                    "criterion_code": c["criterion_code"],
                    "name": c["name"],
                    "category": c["category"],
                    "weight": c["weight"],
                    "scoring_type": c["scoring_type"],
                    "description": c.get("description", ""),
                }
                for c in data
            ],
        }, indent=2)


@tool
def propguru_get_market_comp(locality: str) -> str:
    """Fetch market comp data for a locality — avg/min/max price per sqft and 6-month trend.

    Use the locality name from the property record (e.g. 'Whitefield', 'Andheri East').
    Returns avg_price_per_sqft, price_trend_6m_pct, transaction_count_6m, data_source.
    The avg_price_per_sqft is the basis for computing base_price = market_rate × carpet_area.

    Args:
        locality: Locality name from the property record
    """
    with _client() as c:
        resp = c.get("/api/v1/propguru/market-comps", params={"locality": locality})
        if resp.status_code != 200:
            return json.dumps({"error": f"Failed to fetch market comps: {resp.text}"})
        data = resp.json()
        if not data:
            return json.dumps({
                "locality": locality,
                "found": False,
                "message": "No market comp data found for this locality. Use citywide average or mark confidence as low.",
            })
        comp = data[0]
        return json.dumps({
            "locality": comp["locality"],
            "found": True,
            "avg_price_per_sqft": comp["avg_price_per_sqft"],
            "min_price_per_sqft": comp["min_price_per_sqft"],
            "max_price_per_sqft": comp["max_price_per_sqft"],
            "price_trend_6m_pct": comp["price_trend_6m_pct"],
            "transaction_count_6m": comp["transaction_count_6m"],
            "data_source": comp["data_source"],
            "as_of_date": comp["as_of_date"],
        }, indent=2)


@tool
def propguru_create_evaluation_report(deal_id: str, version: int = 1) -> str:
    """Create a new draft evaluation report for a deal.

    Returns the report_id needed by all subsequent scoring and pricing tools.
    Always call this before propguru_save_evaluation_score.

    Args:
        deal_id: Deal UUID or deal_code (e.g. 'DEAL-001')
        version: Version number (default 1, increment on re-evaluation)
    """
    with _client() as c:
        resp = c.post("/api/v1/propguru/evaluations", json={"deal_id": deal_id, "version": version})
        if resp.status_code not in (200, 201):
            return json.dumps({"error": f"Failed to create report: {resp.text}"})
        data = resp.json()
        return json.dumps({
            "report_id": data["id"],
            "deal_id": data["deal_id"],
            "version": data["version"],
            "status": data["status"],
            "message": "Draft report created. Use report_id in all subsequent scoring calls.",
        }, indent=2)


@tool
def propguru_save_evaluation_score(
    report_id: str,
    criterion_id: str,
    score: float,
    raw_value: str = "",
    notes: str = "",
) -> str:
    """Save one criterion score to the evaluation report.

    For boolean criteria: score = 1.0 (present) or 0.0 (absent).
    For scale_1_5 criteria: score = raw value 1.0–5.0.
    For proximity_km criteria: pass the distance in km as raw_value; compute score using
      the proximity bands (< 0.5 km = 5, 0.5-1 = 4, 1-2 = 3, 2-4 = 2, > 4 = 1).

    Call this once per criterion. Upserts if called twice for the same criterion.

    Args:
        report_id: UUID of the evaluation report (from propguru_create_evaluation_report)
        criterion_id: UUID or criterion_code (e.g. 'CRIT-001') of the criterion to score
        score: Numeric score (0.0–5.0 for scale/proximity; 0.0 or 1.0 for boolean)
        raw_value: The raw collected value, e.g. '0.8 km', 'yes', '3' (for audit trail)
        notes: Optional explanation of how this score was determined
    """
    with _client() as c:
        resp = c.post(f"/api/v1/propguru/evaluations/{report_id}/scores", json={
            "criterion_id": criterion_id,
            "score": score,
            "raw_value": raw_value or None,
            "source": "agent",
            "notes": notes or None,
        })
        if resp.status_code not in (200, 201):
            return json.dumps({"error": f"Failed to save score: {resp.text}"})
        data = resp.json()
        return json.dumps({
            "saved": True,
            "score_id": data["id"],
            "criterion_id": criterion_id,
            "score": data["score"],
            "raw_value": data["raw_value"],
        }, indent=2)


@tool
def propguru_calculate_price(report_id: str) -> str:
    """Compute the weighted score_factor and recommended_price from all saved scores.

    Formula: score_factor = Σ(weight_i × normalized_score_i) / Σ(weight_i)
             recommended_price = base_price × (1 + score_factor × 35%)
    Saves results to the report and returns the full pricing breakdown.
    Call this after saving all criterion scores and after setting base_price via
    propguru_set_base_price.

    Args:
        report_id: UUID of the evaluation report
    """
    with _client() as c:
        resp = c.post(f"/api/v1/propguru/evaluations/{report_id}/calculate-price")
        if resp.status_code != 200:
            return json.dumps({"error": f"Failed to calculate price: {resp.text}"})
        data = resp.json()
        base = data.get("base_price") or 0
        rec = data.get("recommended_price") or 0
        return json.dumps({
            "report_id": report_id,
            "score_factor": data["score_factor"],
            "price_premium_pct": data["price_premium_pct"],
            "base_price": base,
            "base_price_lakhs": round(base / 100000, 2) if base else None,
            "recommended_price": rec,
            "recommended_price_lakhs": round(rec / 100000, 2) if rec else None,
            "confidence": data["confidence"],
            "scored_criteria": data["scored_criteria"],
            "total_criteria": data["total_criteria"],
            "coverage_pct": data["coverage_pct"],
        }, indent=2)


@tool
def propguru_set_base_price(report_id: str, market_rate_per_sqft: float, carpet_area_sqft: float) -> str:
    """Set the base price on the evaluation report.

    base_price = market_rate_per_sqft × carpet_area_sqft
    Call this before propguru_calculate_price. Uses market comp data as the rate.

    Args:
        report_id: UUID of the evaluation report
        market_rate_per_sqft: Average market rate from propguru_get_market_comp
        carpet_area_sqft: Carpet area from the property record
    """
    base_price = market_rate_per_sqft * carpet_area_sqft
    with _client() as c:
        resp = c.patch(f"/api/v1/propguru/evaluations/{report_id}/base-price", json={
            "market_rate_per_sqft": market_rate_per_sqft,
            "base_price": base_price,
        })
        if resp.status_code != 200:
            return json.dumps({"error": f"Failed to set base price: {resp.text}"})
        data = resp.json()
        return json.dumps({
            "report_id": report_id,
            "market_rate_per_sqft": market_rate_per_sqft,
            "carpet_area_sqft": carpet_area_sqft,
            "base_price": data.get("base_price"),
            "base_price_lakhs": round(base_price / 100000, 2),
            "message": "Base price set. Now call propguru_calculate_price after scoring all criteria.",
        }, indent=2)


@tool
def propguru_score_proximity(
    report_id: str,
    criterion_id: str,
    distance_km: float,
    landmark_name: str = "",
) -> str:
    """Score a proximity criterion by converting a distance in km to a 1–5 score.

    Scoring bands: < 0.5 km → 5, 0.5-1 km → 4, 1-2 km → 3, 2-4 km → 2, > 4 km → 1.
    Saves the score automatically. Use this for all CRIT-011 through CRIT-018, CRIT-020.

    Args:
        report_id: UUID of the evaluation report
        criterion_id: Criterion UUID or criterion_code
        distance_km: Distance in kilometres to the nearest landmark
        landmark_name: Name of the landmark (for the raw_value audit trail)
    """
    score = _proximity_score(distance_km)
    raw = f"{distance_km} km" + (f" to {landmark_name}" if landmark_name else "")
    with _client() as c:
        resp = c.post(f"/api/v1/propguru/evaluations/{report_id}/scores", json={
            "criterion_id": criterion_id,
            "score": score,
            "raw_value": raw,
            "source": "agent",
            "notes": f"Proximity band: {distance_km} km → score {score}/5",
        })
        if resp.status_code not in (200, 201):
            return json.dumps({"error": f"Failed to save proximity score: {resp.text}"})
        return json.dumps({
            "criterion_id": criterion_id,
            "distance_km": distance_km,
            "score": score,
            "raw_value": raw,
            "message": f"Distance {distance_km} km → proximity score {score}/5",
        }, indent=2)


@tool
def propguru_propose_evaluation(
    report_id: str,
    deal_code: str,
    property_summary: str,
    recommended_price: float,
    confidence: str,
    score_factor: float,
    scored_count: int,
    total_count: int,
    reasoning: str = "",
) -> str:
    """Propose the completed evaluation for analyst review via the HITL inbox.

    Creates an AgentAction visible at /approvals and /propguru/evaluation.
    On approval, the deal stage advances to evaluation_done.
    Call this as the FINAL step after propguru_calculate_price.

    Args:
        report_id: UUID of the evaluation report
        deal_code: Deal code for display (e.g. 'DEAL-001')
        property_summary: One-line property description (e.g. '3BHK, 1250 sqft, Whitefield')
        recommended_price: Recommended acquisition price in INR
        confidence: 'high', 'medium', or 'low'
        score_factor: Weighted score factor 0-1
        scored_count: Number of criteria scored by agent
        total_count: Total criteria (30)
        reasoning: Agent's explanation of the evaluation
    """
    price_lakhs = round(recommended_price / 100_000, 2) if recommended_price else 0
    price_premium = round(score_factor * 35, 1)
    auto_scored = scored_count
    analyst_needed = total_count - scored_count

    display_data = [
        {"label": "Property", "value": property_summary},
        {"label": "Score Factor", "value": f"{round(score_factor * 100, 1)}% ({confidence} confidence)"},
        {"label": "Price Premium", "value": f"+{price_premium}%"},
        {"label": "Recommended Price", "value": f"₹{price_lakhs} L (₹{round(recommended_price/10_000_000, 2)} Cr)"},
        {"label": "Criteria Coverage", "value": f"{auto_scored}/{total_count} auto-scored" + (f", {analyst_needed} need review" if analyst_needed else "")},
    ]

    payload = {
        "agent_name": "propguru-evaluator",
        "title": f"Property Evaluation — {deal_code} ({property_summary})",
        "summary": f"Recommended ₹{price_lakhs}L — confidence: {confidence} — {auto_scored}/{total_count} criteria scored",
        "reasoning": reasoning or f"Evaluation completed for {deal_code}. Score factor: {round(score_factor * 100, 1)}%.",
        "confidence": confidence,
        "display_data": display_data,
        "tags": ["propguru", "evaluation", f"{confidence}_confidence"],
        "approval_action": {
            "method": "PATCH",
            "url": f"/api/v1/propguru/evaluations/{report_id}/approve",
            "url_params": {"report_id": report_id},
            "body": {"approved_by": "analyst"},
        },
    }

    with _client() as c:
        resp = c.post("/api/v1/actions", json=payload)
        if resp.status_code not in (200, 201):
            return json.dumps({"error": f"Failed to create HITL action: {resp.text}"})
        data = resp.json()

        # Transition report to pending_review so the analyst UI shows the correct state
        c.patch(f"/api/v1/propguru/evaluations/{report_id}/status",
                json={"status": "pending_review"})

        return json.dumps({
            "success": True,
            "action_id": data.get("id"),
            "report_id": report_id,
            "message": f"Evaluation for {deal_code} proposed for analyst review at /approvals",
        }, indent=2)

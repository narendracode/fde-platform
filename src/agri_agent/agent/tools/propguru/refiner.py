"""Propguru evaluation refinement tools — read and update individual scores."""
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
def propguru_get_report_scores(report_id: str) -> str:
    """Get all saved scores for an evaluation report with criterion details.

    Returns each score with score_id (needed for updates), criterion_code, name,
    category, weight, scoring_type, current score, raw_value, source, and notes.

    Args:
        report_id: UUID of the evaluation report
    """
    with _client() as c:
        resp = c.get(f"/api/v1/propguru/evaluations/{report_id}/scores")
        if resp.status_code != 200:
            return json.dumps({"error": f"Failed to fetch scores: {resp.text}"})
        data = resp.json()
        groups = data.get("groups", {})
        all_scores = []
        for cat, items in groups.items():
            for s in items:
                crit = s.get("criterion") or {}
                all_scores.append({
                    "score_id": s["id"],
                    "criterion_code": crit.get("criterion_code", "?"),
                    "name": crit.get("name", "?"),
                    "category": crit.get("category", cat),
                    "weight": crit.get("weight", 0),
                    "scoring_type": crit.get("scoring_type", ""),
                    "current_score": s["score"],
                    "raw_value": s.get("raw_value", ""),
                    "source": s.get("source", "agent"),
                    "notes": s.get("notes", ""),
                })
        all_scores.sort(key=lambda x: x["criterion_code"])
        return json.dumps({
            "report_id": report_id,
            "total_scored": data.get("total_scored", 0),
            "score_factor": data.get("score_factor"),
            "recommended_price": data.get("recommended_price"),
            "scores": all_scores,
        }, indent=2)


@tool
def propguru_update_score(
    report_id: str,
    score_id: str,
    new_score: float,
    notes: str = "",
    raw_value: str = "",
) -> str:
    """Update a single criterion score on an evaluation report.

    Use this when the analyst has provided a correction or you have better data.
    Source is automatically set to 'analyst' to indicate a human override.
    Call propguru_calculate_price after all updates to recompute the recommended price.

    Args:
        report_id: UUID of the evaluation report
        score_id: UUID of the score to update (from propguru_get_report_scores)
        new_score: Updated numeric score (0-5 for scale/proximity; 0 or 1 for boolean)
        notes: Explanation of why this score was changed
        raw_value: Updated raw collected value (optional, for audit trail)
    """
    body: dict = {"score": new_score, "source": "analyst"}
    if notes:
        body["notes"] = notes
    if raw_value:
        body["raw_value"] = raw_value

    with _client() as c:
        resp = c.patch(
            f"/api/v1/propguru/evaluations/{report_id}/scores/{score_id}",
            json=body,
        )
        if resp.status_code != 200:
            return json.dumps({"error": f"Failed to update score: {resp.text}"})
        data = resp.json()
        return json.dumps({
            "updated": True,
            "score_id": score_id,
            "new_score": data.get("score"),
            "source": data.get("source"),
            "notes": data.get("notes"),
        }, indent=2)

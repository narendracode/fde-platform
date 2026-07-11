"""Unit tests for Propguru code grader (Phase 1 — no network calls).

Tests exercise the five grader checks in isolation using mocked API data.
Run with:  docker compose exec api uv run pytest tests/test_propguru_verifier.py -v
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from fde_agent.agent.propguru_verifier import (
    GraderResult,
    MIN_COVERAGE,
    HIGH_CONF_MIN_COVERAGE,
    HIGH_CONF_MIN_FACTOR,
    MAX_PRICE_DEVIATION,
    _check_boolean_validity,
    _check_category_zeros,
    _check_confidence,
    _check_coverage,
    _check_price_sanity,
    extract_action_id,
    extract_report_id,
    run_propguru_code_grader,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_criteria(n: int = 30) -> list[dict]:
    """Generate n fake criteria — first 10 amenity boolean, rest scale_1_5."""
    cats = ["amenity"] * 10 + ["location"] * 10 + ["property"] * 5 + ["society"] * 5
    types = ["boolean"] * 10 + ["scale_1_5"] * 20
    return [
        {
            "id": f"crit-{i:04d}",
            "criterion_code": f"CRIT-{i:03d}",
            "category": cats[i],
            "weight": 1.0,
            "scoring_type": types[i],
        }
        for i in range(n)
    ]


def _make_groups(criteria: list[dict], score_override: dict | None = None) -> dict:
    """Build score groups where every criterion has score=3.0 (or override)."""
    groups: dict[str, list] = {"amenity": [], "location": [], "property": [], "society": []}
    for c in criteria:
        s = (score_override or {}).get(c["criterion_code"], (1.0 if c["scoring_type"] == "boolean" else 3.0))
        groups[c["category"]].append({
            "criterion_id": c["id"],
            "criterion_code": c["criterion_code"],
            "score": s,
            "criterion": {
                "criterion_code": c["criterion_code"],
                "category": c["category"],
                "weight": c["weight"],
                "scoring_type": c["scoring_type"],
            },
        })
    return groups


def _make_report(
    base_price: float = 1_000_000,
    recommended_price: float = 1_100_000,
    confidence: str = "medium",
    score_factor: float = 0.55,
) -> dict:
    return {
        "base_price": base_price,
        "recommended_price": recommended_price,
        "confidence": confidence,
        "score_factor": score_factor,
    }


# ── Coverage check ────────────────────────────────────────────────────────────

class TestCheckCoverage:
    def test_all_scored_passes(self):
        criteria = _make_criteria(30)
        groups = _make_groups(criteria)
        count, missing = _check_coverage(groups, criteria)
        assert count == 30
        assert missing == []

    def test_missing_two_returns_them(self):
        criteria = _make_criteria(30)
        groups = _make_groups(criteria)
        # Remove 2 scores from amenity
        groups["amenity"] = groups["amenity"][:-2]
        count, missing = _check_coverage(groups, criteria)
        assert count == 28
        assert len(missing) == 2

    def test_empty_groups_returns_all_missing(self):
        criteria = _make_criteria(30)
        groups = {"amenity": [], "location": [], "property": [], "society": []}
        count, missing = _check_coverage(groups, criteria)
        assert count == 0
        assert len(missing) == 30

    def test_threshold(self):
        criteria = _make_criteria(30)
        groups = _make_groups(criteria)
        groups["society"] = groups["society"][:-3]   # 27 scored
        count, _ = _check_coverage(groups, criteria)
        assert count < MIN_COVERAGE


# ── Boolean validity check ────────────────────────────────────────────────────

class TestCheckBooleanValidity:
    def test_valid_boolean_scores_pass(self):
        criteria = _make_criteria(10)  # all boolean (amenity)
        groups = {"amenity": [], "location": [], "property": [], "society": []}
        for c in criteria:
            groups["amenity"].append({"criterion_id": c["id"], "score": 1.0, "criterion": c})
        errors = _check_boolean_validity(groups, criteria)
        assert errors == []

    def test_invalid_boolean_caught(self):
        criteria = _make_criteria(10)
        groups = {"amenity": [], "location": [], "property": [], "society": []}
        for i, c in enumerate(criteria):
            # One criterion has score=3 (invalid for boolean)
            s = 3.0 if i == 5 else 1.0
            groups["amenity"].append({"criterion_id": c["id"], "score": s, "criterion": c})
        errors = _check_boolean_validity(groups, criteria)
        assert len(errors) == 1
        assert "CRIT-005" in errors[0] or "3.0" in errors[0]

    def test_scale_scores_ignored(self):
        criteria = _make_criteria(30)
        groups = _make_groups(criteria, {"CRIT-010": 3.5})  # scale criterion — should not flag
        # CRIT-010 is scale_1_5 — won't appear in bool check
        errors = _check_boolean_validity(groups, criteria)
        assert errors == []


# ── Price sanity check ────────────────────────────────────────────────────────

class TestCheckPriceSanity:
    def test_within_range_passes(self):
        report = _make_report(base_price=1_000_000, recommended_price=1_200_000)
        assert _check_price_sanity(report) is None

    def test_50pct_above_fails(self):
        report = _make_report(base_price=1_000_000, recommended_price=1_600_000)
        result = _check_price_sanity(report)
        assert result is not None
        assert "above" in result

    def test_50pct_below_fails(self):
        report = _make_report(base_price=1_000_000, recommended_price=400_000)
        result = _check_price_sanity(report)
        assert result is not None
        assert "below" in result

    def test_missing_prices_passes(self):
        report = {"base_price": None, "recommended_price": None}
        assert _check_price_sanity(report) is None

    def test_zero_base_price_skipped(self):
        report = {"base_price": 0, "recommended_price": 1_000_000}
        assert _check_price_sanity(report) is None


# ── Confidence calibration check ──────────────────────────────────────────────

class TestCheckConfidence:
    def test_medium_confidence_always_passes(self):
        report = _make_report(confidence="medium", score_factor=0.3)
        assert _check_confidence(report, 20) is None

    def test_high_confidence_with_good_coverage_passes(self):
        report = _make_report(confidence="high", score_factor=0.7)
        assert _check_confidence(report, 28) is None

    def test_high_confidence_low_coverage_fails(self):
        report = _make_report(confidence="high", score_factor=0.7)
        result = _check_confidence(report, HIGH_CONF_MIN_COVERAGE - 1)
        assert result is not None
        assert "coverage" in result

    def test_high_confidence_low_factor_fails(self):
        report = _make_report(confidence="high", score_factor=HIGH_CONF_MIN_FACTOR - 0.1)
        result = _check_confidence(report, 29)
        assert result is not None
        assert "score_factor" in result

    def test_high_confidence_both_low_fails(self):
        report = _make_report(confidence="high", score_factor=0.3)
        result = _check_confidence(report, 20)
        assert result is not None


# ── Category zero-out check ───────────────────────────────────────────────────

class TestCheckCategoryZeros:
    def test_all_categories_nonzero_passes(self):
        criteria = _make_criteria(30)
        groups = _make_groups(criteria)
        assert _check_category_zeros(groups, criteria) == []

    def test_empty_category_flagged(self):
        criteria = _make_criteria(30)
        groups = _make_groups(criteria)
        groups["society"] = []   # no society scores
        zeroed = _check_category_zeros(groups, criteria)
        assert "society" in zeroed

    def test_all_zeros_in_category_flagged(self):
        criteria = _make_criteria(30)
        # score_override sets amenity CRIT-000..009 to 0.0
        overrides = {f"CRIT-{i:03d}": 0.0 for i in range(10)}
        groups = _make_groups(criteria, overrides)
        zeroed = _check_category_zeros(groups, criteria)
        assert "amenity" in zeroed


# ── Message parsing utilities ─────────────────────────────────────────────────

class TestExtractIds:
    def _msg(self, content: str):
        m = MagicMock()
        m.content = content
        return m

    def test_extract_report_id_from_content(self):
        msgs = [self._msg("report_id: 3691e6c0-699e-432e-83de-92b92823d64f")]
        assert extract_report_id(msgs) == "3691e6c0-699e-432e-83de-92b92823d64f"

    def test_extract_report_id_from_json_content(self):
        msgs = [self._msg('{"report_id": "abc12345-0000-0000-0000-000000000001"}')]
        assert extract_report_id(msgs) == "abc12345-0000-0000-0000-000000000001"

    def test_extract_report_id_returns_most_recent(self):
        msgs = [
            self._msg("report_id: 00000000-0000-0000-0000-000000000001"),
            self._msg("report_id: 00000000-0000-0000-0000-000000000002"),
        ]
        # reversed() search → most recent message wins
        assert extract_report_id(msgs) == "00000000-0000-0000-0000-000000000002"

    def test_extract_action_id(self):
        msgs = [self._msg('action_id: "dddddddd-0000-0000-0000-000000000009"')]
        assert extract_action_id(msgs) == "dddddddd-0000-0000-0000-000000000009"

    def test_no_match_returns_none(self):
        msgs = [self._msg("nothing relevant here")]
        assert extract_report_id(msgs) is None
        assert extract_action_id(msgs) is None


# ── Integration: run_propguru_code_grader with mocked API ────────────────────

FAKE_CRITERIA = _make_criteria(30)
FAKE_GROUPS_FULL = _make_groups(FAKE_CRITERIA)
FAKE_REPORT_GOOD = _make_report(
    base_price=1_000_000, recommended_price=1_200_000,
    confidence="medium", score_factor=0.55
)

FAKE_SCORES_RESP_FULL = {
    "groups": FAKE_GROUPS_FULL,
    "total_scored": 30,
}


def _mock_fetch(report, scores_resp, criteria):
    groups = scores_resp.get("groups", {})
    return report, groups, criteria


class TestRunCodeGrader:
    @patch("fde_agent.agent.propguru_verifier._fetch_data")
    def test_clean_report_passes(self, mock_fetch):
        mock_fetch.return_value = (FAKE_REPORT_GOOD, FAKE_GROUPS_FULL, FAKE_CRITERIA)
        result = run_propguru_code_grader("any-report-id", "http://localhost:8000", "key")
        assert result.passed is True
        assert result.flags == []

    @patch("fde_agent.agent.propguru_verifier._fetch_data")
    def test_low_coverage_fails(self, mock_fetch):
        groups = _make_groups(FAKE_CRITERIA)
        groups["society"] = groups["society"][:1]   # only 1 of 5 society scored → 26/30
        mock_fetch.return_value = (FAKE_REPORT_GOOD, groups, FAKE_CRITERIA)
        result = run_propguru_code_grader("any", "http://localhost:8000", "key")
        assert not result.passed
        assert "COVERAGE" in result.flags
        assert "COVERAGE" in result.feedback

    @patch("fde_agent.agent.propguru_verifier._fetch_data")
    def test_price_anomaly_fails(self, mock_fetch):
        report = _make_report(base_price=1_000_000, recommended_price=2_000_000)
        mock_fetch.return_value = (report, FAKE_GROUPS_FULL, FAKE_CRITERIA)
        result = run_propguru_code_grader("any", "http://localhost:8000", "key")
        assert not result.passed
        assert "PRICE_SANITY" in result.flags

    @patch("fde_agent.agent.propguru_verifier._fetch_data")
    def test_confidence_mismatch_fails(self, mock_fetch):
        report = _make_report(confidence="high", score_factor=0.3)
        mock_fetch.return_value = (report, FAKE_GROUPS_FULL, FAKE_CRITERIA)
        result = run_propguru_code_grader("any", "http://localhost:8000", "key")
        assert not result.passed
        assert "CONFIDENCE_MISMATCH" in result.flags

    @patch("fde_agent.agent.propguru_verifier._fetch_data")
    def test_network_error_passes_through(self, mock_fetch):
        mock_fetch.side_effect = Exception("connection refused")
        result = run_propguru_code_grader("any", "http://localhost:8000", "key")
        assert result.passed is True
        assert "GRADER_DATA_ERROR" in result.flags

    @patch("fde_agent.agent.propguru_verifier._fetch_data")
    def test_multiple_issues_all_flagged(self, mock_fetch):
        bad_report = _make_report(
            base_price=1_000_000, recommended_price=2_500_000,
            confidence="high", score_factor=0.2
        )
        groups = _make_groups(FAKE_CRITERIA)
        groups["location"] = []   # missing location → low coverage + zero category
        mock_fetch.return_value = (bad_report, groups, FAKE_CRITERIA)
        result = run_propguru_code_grader("any", "http://localhost:8000", "key")
        assert not result.passed
        assert len(result.flags) >= 3   # COVERAGE, PRICE_SANITY, CONFIDENCE_MISMATCH, CATEGORY_ZEROED
        assert result.feedback != ""

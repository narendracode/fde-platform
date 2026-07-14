"""Unit tests for Propguru Phase 2 — Model Grader.

Tests cover context fetching, prompt building, LLM response parsing,
score computation, pass/fail thresholds, and error handling.

Run with:  docker compose exec api uv run pytest tests/test_propguru_model_grader.py -v
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from fde_agent.agent.propguru_verifier import (
    MODEL_GRADER_PASS_THRESHOLD,
    MODEL_GRADER_WEIGHTS,
    ModelGraderResult,
    _build_model_grader_feedback,
    _build_model_grader_prompt,
    run_propguru_model_grader,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

GOOD_REPORT = {
    "base_price": 5_000_000,
    "recommended_price": 5_500_000,
    "price_premium_pct": 10.0,
    "score_factor": 0.68,
    "confidence": "medium",
}

GOOD_CONTEXT = {
    "report": GOOD_REPORT,
    "category_summary": {
        "amenity": "0.75 avg over 10 criteria",
        "location": "0.82 avg over 10 criteria",
        "property": "3.20 avg over 5 criteria",
        "society": "2.90 avg over 5 criteria",
    },
    "reasoning": (
        "The property at Koramangala scores well on location (0.82) and amenity (0.75), "
        "reflecting proximity to tech parks and good social infrastructure. "
        "The 10% premium over base market rate is justified by the above-average location score "
        "and strong amenity coverage. Confidence is medium because two society criteria — "
        "security and maintenance — could not be verified. The analyst should confirm these "
        "society scores before approval, as they could shift the premium by ±3%."
    ),
}

MINIMAL_CONTEXT = {
    "report": GOOD_REPORT,
    "category_summary": {},
    "reasoning": "Looks fine.",  # too short — will be flagged as NO_REASONING
}

PASSING_RAW = {
    "reasoning_coherence": {"score": 8, "rationale": "Clear chain from data to conclusion."},
    "price_justification": {"score": 7, "rationale": "Premium explained by location score."},
    "market_alignment": {"score": 7, "rationale": "References tech park proximity."},
    "analyst_guidance": {"score": 8, "rationale": "Highlights unverified society criteria."},
}

FAILING_RAW = {
    "reasoning_coherence": {"score": 3, "rationale": "Disjointed, no clear narrative."},
    "price_justification": {"score": 2, "rationale": "Price not connected to scores."},
    "market_alignment": {"score": 4, "rationale": "No market comparables mentioned."},
    "analyst_guidance": {"score": 3, "rationale": "No actionable guidance for analyst."},
}

# Compute expected weighted scores for assertions
def _weighted(raw: dict) -> float:
    return sum(
        float(raw[c]["score"]) * w
        for c, w in MODEL_GRADER_WEIGHTS.items()
    )


# ── Prompt builder ─────────────────────────────────────────────────────────────

class TestBuildModelGraderPrompt:
    def test_includes_report_figures(self):
        prompt = _build_model_grader_prompt(GOOD_CONTEXT)
        assert "5,000,000" in prompt
        assert "5,500,000" in prompt
        assert "+10.0%" in prompt

    def test_includes_reasoning_text(self):
        prompt = _build_model_grader_prompt(GOOD_CONTEXT)
        assert "Koramangala scores well" in prompt

    def test_includes_rubric_criteria(self):
        prompt = _build_model_grader_prompt(GOOD_CONTEXT)
        for criterion in MODEL_GRADER_WEIGHTS:
            assert criterion in prompt

    def test_missing_reasoning_shows_placeholder(self):
        ctx = {**GOOD_CONTEXT, "reasoning": ""}
        prompt = _build_model_grader_prompt(ctx)
        assert "(no reasoning provided)" in prompt

    def test_includes_category_summary(self):
        prompt = _build_model_grader_prompt(GOOD_CONTEXT)
        assert "amenity" in prompt
        assert "location" in prompt


# ── Feedback builder ──────────────────────────────────────────────────────────

class TestBuildModelGraderFeedback:
    def test_feedback_includes_overall_score(self):
        scores = {c: float(FAILING_RAW[c]["score"]) for c in MODEL_GRADER_WEIGHTS}
        rationales = {c: FAILING_RAW[c]["rationale"] for c in MODEL_GRADER_WEIGHTS}
        overall = _weighted(FAILING_RAW)
        feedback = _build_model_grader_feedback(scores, rationales, overall)
        assert f"{overall:.1f}/10" in feedback

    def test_feedback_lists_all_criteria(self):
        scores = {c: float(FAILING_RAW[c]["score"]) for c in MODEL_GRADER_WEIGHTS}
        rationales = {c: FAILING_RAW[c]["rationale"] for c in MODEL_GRADER_WEIGHTS}
        feedback = _build_model_grader_feedback(scores, rationales, _weighted(FAILING_RAW))
        for criterion in MODEL_GRADER_WEIGHTS:
            label = criterion.replace("_", " ").title()
            assert label in feedback

    def test_feedback_includes_required_actions(self):
        scores = {c: 0.0 for c in MODEL_GRADER_WEIGHTS}
        rationales = {c: "" for c in MODEL_GRADER_WEIGHTS}
        feedback = _build_model_grader_feedback(scores, rationales, 0.0)
        assert "propguru_propose_evaluation" in feedback


# ── run_propguru_model_grader integration ─────────────────────────────────────

class TestRunModelGrader:
    @patch("fde_agent.agent.propguru_verifier._fetch_model_grader_context")
    @patch("fde_agent.agent.propguru_verifier._call_model_grader")
    def test_passing_scores_return_pass(self, mock_llm, mock_fetch):
        mock_fetch.return_value = GOOD_CONTEXT
        mock_llm.return_value = PASSING_RAW
        result = run_propguru_model_grader("rep-id", "act-id", "http://localhost:8000", "key")
        assert result.passed is True
        assert result.overall_score >= MODEL_GRADER_PASS_THRESHOLD
        assert result.flags == []
        assert result.feedback == ""

    @patch("fde_agent.agent.propguru_verifier._fetch_model_grader_context")
    @patch("fde_agent.agent.propguru_verifier._call_model_grader")
    def test_failing_scores_return_fail(self, mock_llm, mock_fetch):
        mock_fetch.return_value = GOOD_CONTEXT
        mock_llm.return_value = FAILING_RAW
        result = run_propguru_model_grader("rep-id", "act-id", "http://localhost:8000", "key")
        assert result.passed is False
        assert result.overall_score < MODEL_GRADER_PASS_THRESHOLD
        assert "REASONING_QUALITY" in result.flags
        assert "Model Grader Feedback" in result.feedback

    @patch("fde_agent.agent.propguru_verifier._fetch_model_grader_context")
    def test_infra_error_passes_through(self, mock_fetch):
        mock_fetch.return_value = None  # infra failure
        result = run_propguru_model_grader("rep-id", "act-id", "http://localhost:8000", "key")
        assert result.passed is True
        assert "MODEL_GRADER_INFRA_ERROR" in result.flags

    @patch("fde_agent.agent.propguru_verifier._fetch_model_grader_context")
    def test_short_reasoning_skips_grader(self, mock_fetch):
        mock_fetch.return_value = MINIMAL_CONTEXT
        result = run_propguru_model_grader("rep-id", "act-id", "http://localhost:8000", "key")
        assert result.passed is True
        assert "MODEL_GRADER_NO_REASONING" in result.flags

    @patch("fde_agent.agent.propguru_verifier._fetch_model_grader_context")
    @patch("fde_agent.agent.propguru_verifier._call_model_grader")
    def test_json_parse_error_passes_through(self, mock_llm, mock_fetch):
        import json
        mock_fetch.return_value = GOOD_CONTEXT
        mock_llm.side_effect = json.JSONDecodeError("bad json", "", 0)
        result = run_propguru_model_grader("rep-id", "act-id", "http://localhost:8000", "key")
        assert result.passed is True
        assert "MODEL_GRADER_PARSE_ERROR" in result.flags

    @patch("fde_agent.agent.propguru_verifier._fetch_model_grader_context")
    @patch("fde_agent.agent.propguru_verifier._call_model_grader")
    def test_llm_api_error_passes_through(self, mock_llm, mock_fetch):
        mock_fetch.return_value = GOOD_CONTEXT
        mock_llm.side_effect = Exception("connection refused")
        result = run_propguru_model_grader("rep-id", "act-id", "http://localhost:8000", "key")
        assert result.passed is True
        assert "MODEL_GRADER_INFRA_ERROR" in result.flags

    @patch("fde_agent.agent.propguru_verifier._fetch_model_grader_context")
    @patch("fde_agent.agent.propguru_verifier._call_model_grader")
    def test_weighted_score_computed_correctly(self, mock_llm, mock_fetch):
        mock_fetch.return_value = GOOD_CONTEXT
        mock_llm.return_value = PASSING_RAW
        result = run_propguru_model_grader("rep-id", "act-id", "http://localhost:8000", "key")
        expected = _weighted(PASSING_RAW)
        assert abs(result.overall_score - expected) < 0.01

    @patch("fde_agent.agent.propguru_verifier._fetch_model_grader_context")
    @patch("fde_agent.agent.propguru_verifier._call_model_grader")
    def test_criteria_scores_extracted(self, mock_llm, mock_fetch):
        mock_fetch.return_value = GOOD_CONTEXT
        mock_llm.return_value = PASSING_RAW
        result = run_propguru_model_grader("rep-id", "act-id", "http://localhost:8000", "key")
        assert result.criteria_scores["reasoning_coherence"] == 8.0
        assert result.criteria_scores["price_justification"] == 7.0
        assert result.criteria_scores["market_alignment"] == 7.0
        assert result.criteria_scores["analyst_guidance"] == 8.0

    @patch("fde_agent.agent.propguru_verifier._fetch_model_grader_context")
    @patch("fde_agent.agent.propguru_verifier._call_model_grader")
    def test_borderline_pass_at_exactly_threshold(self, mock_llm, mock_fetch):
        """A score that rounds to exactly MODEL_GRADER_PASS_THRESHOLD must pass."""
        # Engineer scores so weighted average == 6.0 exactly
        # weights: coherence=0.35, price=0.35, market=0.20, guidance=0.10
        # 6/0.35 + 6/0.35 + 6/0.20 + 6/0.10 → just use all 6s: 6*1.0 = 6.0
        borderline_raw = {
            "reasoning_coherence": {"score": 6, "rationale": "ok"},
            "price_justification": {"score": 6, "rationale": "ok"},
            "market_alignment": {"score": 6, "rationale": "ok"},
            "analyst_guidance": {"score": 6, "rationale": "ok"},
        }
        mock_fetch.return_value = GOOD_CONTEXT
        mock_llm.return_value = borderline_raw
        result = run_propguru_model_grader("rep-id", "act-id", "http://localhost:8000", "key")
        assert result.passed is True
        assert abs(result.overall_score - 6.0) < 0.01

    @patch("fde_agent.agent.propguru_verifier._fetch_model_grader_context")
    @patch("fde_agent.agent.propguru_verifier._call_model_grader")
    def test_borderline_fail_just_below_threshold(self, mock_llm, mock_fetch):
        """A score just below MODEL_GRADER_PASS_THRESHOLD must fail."""
        just_below_raw = {
            "reasoning_coherence": {"score": 5, "rationale": "ok"},
            "price_justification": {"score": 5, "rationale": "ok"},
            "market_alignment": {"score": 6, "rationale": "ok"},
            "analyst_guidance": {"score": 7, "rationale": "ok"},
        }
        # weighted: 5*0.35 + 5*0.35 + 6*0.20 + 7*0.10 = 1.75+1.75+1.20+0.70 = 5.40
        mock_fetch.return_value = GOOD_CONTEXT
        mock_llm.return_value = just_below_raw
        result = run_propguru_model_grader("rep-id", "act-id", "http://localhost:8000", "key")
        assert result.passed is False
        assert result.overall_score < MODEL_GRADER_PASS_THRESHOLD


# ── Constant sanity checks ────────────────────────────────────────────────────

class TestConstants:
    def test_weights_sum_to_one(self):
        total = sum(MODEL_GRADER_WEIGHTS.values())
        assert abs(total - 1.0) < 1e-9

    def test_pass_threshold_in_valid_range(self):
        assert 0.0 < MODEL_GRADER_PASS_THRESHOLD < 10.0

"""Propguru quality gates on evaluator output.

Phase 1 — Code Grader (zero LLM cost):
  Deterministic checks on coverage, boolean validity, price sanity, confidence,
  and category zero-out.

Phase 2 — Model Grader (LLM-as-judge):
  Grades the evaluator's reasoning text against a 4-criterion rubric using
  Claude Haiku.  Runs only after the code grader passes.

Checks (Phase 1)
----------------
1. Coverage      — at least MIN_COVERAGE of 30 criteria must be scored
2. Boolean       — boolean criteria must be exactly 0.0 or 1.0
3. Price sanity  — recommended_price within ±MAX_PRICE_DEVIATION of base_price
4. Confidence    — "high" confidence requires ≥ HIGH_CONF_MIN_COVERAGE and
                   score_factor ≥ HIGH_CONF_MIN_FACTOR
5. Category zero — no full category (amenity/location/property/society) may have
                   every score missing or at zero

Rubric (Phase 2)                        Weight
-----------------------------------------------
reasoning_coherence                      0.35
price_justification                      0.35
market_alignment                         0.20
analyst_guidance                         0.10
Pass threshold: weighted average ≥ 6.0 / 10
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

_log = logging.getLogger(__name__)

# ── Thresholds ────────────────────────────────────────────────────────────────

MIN_COVERAGE = 28               # out of 30 — FAIL if fewer than this are scored
MAX_PRICE_DEVIATION = 0.50      # ±50% from base_price
HIGH_CONF_MIN_COVERAGE = 27     # 90% of 30
HIGH_CONF_MIN_FACTOR = 0.60     # minimum score_factor for "high" confidence

# Phase 2 — Model Grader
MODEL_GRADER_PASS_THRESHOLD = 6.0   # weighted average out of 10.0
MODEL_GRADER_WEIGHTS: dict[str, float] = {
    "reasoning_coherence": 0.35,
    "price_justification": 0.35,
    "market_alignment": 0.20,
    "analyst_guidance": 0.10,
}
_DEFAULT_MODEL_GRADER_MODEL = "claude-haiku-4-5-20251001"


# ── Result types ──────────────────────────────────────────────────────────────

@dataclass
class GraderResult:
    passed: bool
    flags: list[str] = field(default_factory=list)
    feedback: str = ""          # injected into evaluator's conversation on FAIL


@dataclass
class ModelGraderResult:
    passed: bool
    overall_score: float                    # weighted average 0.0–10.0
    criteria_scores: dict[str, float]       # per-criterion score 0–10
    criteria_rationales: dict[str, str]     # LLM explanation per criterion
    feedback: str                           # narrative feedback for evaluator on FAIL
    flags: list[str] = field(default_factory=list)


# ── Internal helpers ──────────────────────────────────────────────────────────

def _http(base_url: str, api_key: str) -> httpx.Client:
    return httpx.Client(
        base_url=base_url.rstrip("/"),
        headers={"X-API-Key": api_key, "Content-Type": "application/json"},
        timeout=20.0,
    )


def _fetch_data(
    report_id: str, base_url: str, api_key: str
) -> tuple[dict, dict[str, list], list[dict]]:
    """Fetch report, scores-by-category, and all active criteria."""
    with _http(base_url, api_key) as c:
        r_report = c.get(f"/api/v1/propguru/evaluations/{report_id}")
        r_report.raise_for_status()
        report = r_report.json()

        r_scores = c.get(f"/api/v1/propguru/evaluations/{report_id}/scores")
        r_scores.raise_for_status()
        scores_payload = r_scores.json()

        r_crit = c.get("/api/v1/propguru/evaluation-criteria", params={"is_active": "true"})
        r_crit.raise_for_status()
        criteria: list[dict] = r_crit.json()

    return report, scores_payload.get("groups", {}), criteria


# ── Individual checks ─────────────────────────────────────────────────────────

def _check_coverage(
    groups: dict[str, list], criteria: list[dict]
) -> tuple[int, list[str]]:
    """Return (scored_count, list_of_missing_criterion_codes)."""
    scored_ids = {
        s["criterion_id"]
        for scores in groups.values()
        for s in scores
        if s.get("score") is not None
    }
    missing = [
        c["criterion_code"]
        for c in criteria
        if str(c["id"]) not in scored_ids
    ]
    return len(criteria) - len(missing), missing


def _check_boolean_validity(
    groups: dict[str, list], criteria: list[dict]
) -> list[str]:
    """Return a list of error strings for boolean criteria with invalid scores."""
    bool_ids = {
        str(c["id"]): c["criterion_code"]
        for c in criteria
        if c["scoring_type"] == "boolean"
    }
    errors: list[str] = []
    for scores in groups.values():
        for s in scores:
            cid = s.get("criterion_id", "")
            if cid in bool_ids and s.get("score") is not None:
                score = float(s["score"])
                if score not in (0.0, 1.0):
                    errors.append(f"{bool_ids[cid]}={score}")
    return errors


def _check_price_sanity(report: dict) -> str | None:
    """Return an error string if recommended_price deviates too far from base_price."""
    base = report.get("base_price") or 0.0
    rec = report.get("recommended_price") or 0.0
    if base <= 0 or rec <= 0:
        return None  # can't check without both values
    ratio = rec / base
    if ratio > (1 + MAX_PRICE_DEVIATION):
        return (
            f"recommended_price ₹{rec:,.0f} is {(ratio-1)*100:.0f}% above base_price ₹{base:,.0f} "
            f"(max allowed: {int(MAX_PRICE_DEVIATION*100)}%)"
        )
    if ratio < (1 - MAX_PRICE_DEVIATION):
        return (
            f"recommended_price ₹{rec:,.0f} is {(1-ratio)*100:.0f}% below base_price ₹{base:,.0f} "
            f"(max allowed: {int(MAX_PRICE_DEVIATION*100)}%)"
        )
    return None


def _check_confidence(report: dict, scored_count: int) -> str | None:
    """Return an error string if confidence='high' is not justified."""
    confidence = (report.get("confidence") or "").lower()
    if confidence != "high":
        return None
    score_factor = report.get("score_factor") or 0.0
    issues: list[str] = []
    if scored_count < HIGH_CONF_MIN_COVERAGE:
        issues.append(
            f"coverage is {scored_count}/30 (need ≥ {HIGH_CONF_MIN_COVERAGE} for 'high')"
        )
    if score_factor < HIGH_CONF_MIN_FACTOR:
        issues.append(
            f"score_factor is {score_factor:.2f} (need ≥ {HIGH_CONF_MIN_FACTOR} for 'high')"
        )
    if issues:
        return "confidence='high' but " + " AND ".join(issues) + ". Lower to 'medium' or fix the underlying issues."
    return None


def _check_category_zeros(
    groups: dict[str, list], criteria: list[dict]
) -> list[str]:
    """Return categories where every scored criterion is 0 or the category has no scores."""
    crit_by_cat: dict[str, list[str]] = {}
    for c in criteria:
        crit_by_cat.setdefault(c["category"], []).append(str(c["id"]))

    zeroed: list[str] = []
    for cat, expected_ids in crit_by_cat.items():
        cat_scores = [s for s in groups.get(cat, []) if s.get("score") is not None]
        if not cat_scores:
            zeroed.append(cat)
            continue
        nonzero = [s for s in cat_scores if float(s["score"]) > 0]
        if not nonzero:
            zeroed.append(cat)
    return zeroed


# ── Feedback builder ──────────────────────────────────────────────────────────

def _build_feedback(issues: list[str], report: dict) -> str:
    """Format structured grader feedback to inject into the evaluator's conversation."""
    lines = [
        "[Code Grader Feedback]",
        "Your evaluation output did not pass the automated quality checks. "
        "Please fix the issues below and re-submit the evaluation proposal.\n",
        "FAIL — issues found:",
    ]
    for issue in issues:
        lines.append(f"  • {issue}")

    lines.append("")
    lines.append("Required actions:")
    flag_codes = {i.split(":")[0] for i in issues}

    if "COVERAGE" in flag_codes:
        lines.append(
            "  1. Re-run propguru_get_criteria to identify missing criterion IDs, "
            "then call propguru_save_evaluation_score for each missing criterion."
        )
    if "BOOLEAN_INVALID" in flag_codes:
        lines.append(
            "  2. Boolean criteria (scoring_type='boolean') must be exactly 0.0 (absent) "
            "or 1.0 (present). Update the invalid scores."
        )
    if "PRICE_SANITY" in flag_codes:
        lines.append(
            "  3. Call propguru_calculate_price and verify the recommended_price is reasonable "
            f"relative to the base_price (within ±{int(MAX_PRICE_DEVIATION*100)}%)."
        )
    if "CONFIDENCE_MISMATCH" in flag_codes:
        lines.append(
            "  4. Adjust the confidence level to match actual coverage and score_factor."
        )
    if "CATEGORY_ZEROED" in flag_codes:
        lines.append(
            "  5. At least one criterion per category must have a non-zero score. "
            "Score the missing/zero categories before re-proposing."
        )

    lines.append("")
    lines.append(
        "After fixing all issues, call propguru_calculate_price to recompute the price, "
        "then call propguru_propose_evaluation to re-submit for analyst review."
    )
    return "\n".join(lines)


# ── Public API ────────────────────────────────────────────────────────────────

def run_propguru_code_grader(
    report_id: str, base_url: str, api_key: str
) -> GraderResult:
    """Run all five code-based checks on a Propguru evaluation report.

    Returns GraderResult(passed=True) on success, or a result with flags and
    structured feedback the evaluator can use to self-correct.

    On infrastructure errors (API down, network timeout), returns passed=True
    with a GRADER_DATA_ERROR flag so the pipeline is never blocked by infra.
    """
    try:
        report, groups, criteria = _fetch_data(report_id, base_url, api_key)
    except Exception as exc:
        _log.warning("propguru_verifier: could not fetch data for %s: %s", report_id, exc)
        return GraderResult(passed=True, flags=["GRADER_DATA_ERROR"])

    issues: list[str] = []

    # 1. Coverage
    scored_count, missing = _check_coverage(groups, criteria)
    if scored_count < MIN_COVERAGE:
        top_missing = missing[:12]
        more = f" (and {len(missing)-12} more)" if len(missing) > 12 else ""
        issues.append(
            f"COVERAGE: {scored_count}/{len(criteria)} criteria scored. "
            f"Missing: {', '.join(top_missing)}{more}"
        )

    # 2. Boolean validity
    bool_errors = _check_boolean_validity(groups, criteria)
    if bool_errors:
        issues.append(f"BOOLEAN_INVALID: {'; '.join(bool_errors)}")

    # 3. Price sanity
    price_issue = _check_price_sanity(report)
    if price_issue:
        issues.append(f"PRICE_SANITY: {price_issue}")

    # 4. Confidence calibration
    conf_issue = _check_confidence(report, scored_count)
    if conf_issue:
        issues.append(f"CONFIDENCE_MISMATCH: {conf_issue}")

    # 5. Category zero-out
    zero_cats = _check_category_zeros(groups, criteria)
    if zero_cats:
        issues.append(
            f"CATEGORY_ZEROED: {', '.join(zero_cats)} — "
            "all scores in this/these category/categories are 0 or missing"
        )

    if not issues:
        _log.info("propguru_verifier: PASS — report %s", report_id)
        return GraderResult(passed=True)

    flag_codes = [i.split(":")[0] for i in issues]
    _log.info("propguru_verifier: FAIL — report %s flags=%s", report_id, flag_codes)
    return GraderResult(passed=False, flags=flag_codes, feedback=_build_feedback(issues, report))


def save_grader_result(
    report_id: str,
    retries: int,
    flags: list[str],
    base_url: str,
    api_key: str,
    model_grader_retries: int = 0,
) -> None:
    """Persist grader result to the evaluation report (best-effort, non-blocking)."""
    try:
        with _http(base_url, api_key) as c:
            c.post(
                f"/api/v1/propguru/evaluations/{report_id}/grader-result",
                json={
                    "verification_retries": retries,
                    "grader_flags": flags,
                    "model_grader_retries": model_grader_retries,
                },
            )
    except Exception as exc:
        _log.warning("propguru_verifier: could not persist grader result: %s", exc)


def dismiss_stale_action(action_id: str, note: str, base_url: str, api_key: str) -> None:
    """Mark the evaluator's HITL proposal as dismissed so a retry can create a fresh one."""
    try:
        with _http(base_url, api_key) as c:
            c.post(f"/api/v1/actions/{action_id}/dismiss", json={"note": note})
    except Exception as exc:
        _log.warning("propguru_verifier: could not dismiss action %s: %s", action_id, exc)


def reset_report_to_draft(report_id: str, base_url: str, api_key: str) -> None:
    """Reset report status to 'draft' so the evaluator can re-propose after a grader failure."""
    try:
        with _http(base_url, api_key) as c:
            c.patch(
                f"/api/v1/propguru/evaluations/{report_id}/status",
                json={"status": "draft"},
            )
    except Exception as exc:
        _log.warning("propguru_verifier: could not reset report status: %s", exc)


# ── Phase 2: Model Grader ─────────────────────────────────────────────────────

def _fetch_model_grader_context(
    report_id: str,
    action_id: str | None,
    base_url: str,
    api_key: str,
) -> dict | None:
    """Fetch the evaluation context and reasoning needed for the model grader.

    Returns a dict with keys: report, category_summary, reasoning.
    Returns None on any infrastructure error (model grader will pass through).
    """
    try:
        with _http(base_url, api_key) as c:
            r_rep = c.get(f"/api/v1/propguru/evaluations/{report_id}")
            r_rep.raise_for_status()
            report = r_rep.json()

            r_scores = c.get(f"/api/v1/propguru/evaluations/{report_id}/scores")
            r_scores.raise_for_status()
            scores_payload = r_scores.json()

            reasoning = ""
            if action_id:
                r_act = c.get(f"/api/v1/actions/{action_id}")
                if r_act.status_code == 200:
                    reasoning = r_act.json().get("reasoning") or ""

        # Build per-category score average (normalized 0–1) for prompt context
        groups = scores_payload.get("groups", {})
        category_summary: dict[str, str] = {}
        for cat, scores in groups.items():
            scored = [float(s["score"]) for s in scores if s.get("score") is not None]
            if scored:
                avg = sum(scored) / len(scored)
                category_summary[cat] = f"{avg:.2f} avg over {len(scored)} criteria"
            else:
                category_summary[cat] = "no scores"

        return {"report": report, "category_summary": category_summary, "reasoning": reasoning}
    except Exception as exc:
        _log.warning("model_grader: could not fetch context for %s: %s", report_id, exc)
        return None


def _build_model_grader_prompt(context: dict) -> str:
    report = context["report"]
    cat_summary = context["category_summary"]
    reasoning = context.get("reasoning", "")

    base_price = report.get("base_price") or 0
    rec_price = report.get("recommended_price") or 0
    premium_pct = report.get("price_premium_pct") or 0
    score_factor = report.get("score_factor") or 0
    confidence = report.get("confidence") or "unknown"

    cat_lines = "\n".join(f"  {cat}: {summary}" for cat, summary in cat_summary.items())

    return f"""You are a real-estate evaluation quality reviewer. Your task is to grade the reasoning
provided by an AI evaluator for a property deal evaluation.

--- EVALUATION SUMMARY ---
Score factor (overall quality): {score_factor:.2f} / 1.0
Confidence: {confidence}
Base market price: ₹{base_price:,.0f}
Recommended price: ₹{rec_price:,.0f}  ({premium_pct:+.1f}% premium/discount)
Category score averages (0 = absent/false, 5 = max for scale_1_5):
{cat_lines}

--- EVALUATOR REASONING TEXT ---
{reasoning or "(no reasoning provided)"}

--- YOUR TASK ---
Grade the reasoning text on exactly these 4 criteria, each scored 0–10:

1. reasoning_coherence (weight 0.35)
   Does the reasoning chain logically from property data → category analysis → final evaluation?
   Is there a clear narrative thread rather than isolated facts?

2. price_justification (weight 0.35)
   Does the reasoning explain WHY the recommended price is appropriate relative to the base price?
   Does it reference score_factor, market conditions, or specific property strengths/weaknesses?

3. market_alignment (weight 0.20)
   Does the reasoning mention market comparables, location factors, or demand/supply dynamics
   that support the pricing decision?

4. analyst_guidance (weight 0.10)
   Does the reasoning give the HITL analyst actionable context — what to verify, what risks
   to look out for, or what would change the recommendation?

Score 0–10 for each criterion (0 = completely missing, 5 = partial, 10 = excellent).

Respond with ONLY valid JSON in exactly this format — no prose, no markdown:
{{
  "reasoning_coherence": {{"score": <int 0-10>, "rationale": "<1-2 sentences>"}},
  "price_justification": {{"score": <int 0-10>, "rationale": "<1-2 sentences>"}},
  "market_alignment": {{"score": <int 0-10>, "rationale": "<1-2 sentences>"}},
  "analyst_guidance": {{"score": <int 0-10>, "rationale": "<1-2 sentences>"}}
}}"""


def _call_model_grader(context: dict, model_name: str) -> dict:
    """Call the LLM and return the raw parsed JSON dict from the grader prompt.

    Raises on API error or JSON parse failure — caller handles these cases.
    """
    import anthropic  # lazy import — only needed when model grader is enabled

    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    prompt = _build_model_grader_prompt(context)
    response = client.messages.create(
        model=model_name,
        max_tokens=512,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = response.content[0].text.strip()
    # Strip markdown code fences if the model wraps the JSON
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-z]*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)
    return json.loads(raw)


def _build_model_grader_feedback(
    criteria_scores: dict[str, float],
    criteria_rationales: dict[str, str],
    overall_score: float,
) -> str:
    lines = [
        "[Model Grader Feedback]",
        f"Your evaluation reasoning scored {overall_score:.1f}/10 (threshold: {MODEL_GRADER_PASS_THRESHOLD:.1f}).",
        "The reasoning did not meet the minimum quality bar. Please revise and re-submit.\n",
        "Per-criterion feedback:",
    ]
    for criterion, weight in MODEL_GRADER_WEIGHTS.items():
        score = criteria_scores.get(criterion, 0.0)
        rationale = criteria_rationales.get(criterion, "")
        label = criterion.replace("_", " ").title()
        lines.append(f"  • {label} (weight {weight:.0%}): {score:.0f}/10 — {rationale}")

    lines.extend([
        "",
        "Required actions:",
        "  1. Expand your reasoning to explain the price recommendation relative to "
           "base_price and market conditions.",
        "  2. Reference specific category scores or property features that drive your conclusion.",
        "  3. Include actionable guidance for the analyst reviewing this proposal.",
        "",
        "After revising your reasoning, call propguru_propose_evaluation again with the "
        "improved reasoning text.",
    ])
    return "\n".join(lines)


def run_propguru_model_grader(
    report_id: str,
    action_id: str | None,
    base_url: str,
    api_key: str,
    model_name: str = _DEFAULT_MODEL_GRADER_MODEL,
) -> ModelGraderResult:
    """Run the LLM-as-judge model grader on the evaluator's reasoning text.

    Called only after the code grader passes.  Uses Claude Haiku to score the
    reasoning on 4 rubric criteria.  Weighted average ≥ MODEL_GRADER_PASS_THRESHOLD
    required to pass.

    On infrastructure errors (API unavailable, JSON parse failure) the grader
    returns passed=True with an infra-error flag to avoid blocking the pipeline.
    """
    context = _fetch_model_grader_context(report_id, action_id, base_url, api_key)
    if context is None:
        return ModelGraderResult(
            passed=True,
            overall_score=0.0,
            criteria_scores={},
            criteria_rationales={},
            feedback="",
            flags=["MODEL_GRADER_INFRA_ERROR"],
        )

    reasoning = context.get("reasoning", "")
    if not reasoning or len(reasoning.strip()) < 30:
        _log.info("model_grader: no substantive reasoning for %s — skipping", report_id)
        return ModelGraderResult(
            passed=True,
            overall_score=0.0,
            criteria_scores={},
            criteria_rationales={},
            feedback="",
            flags=["MODEL_GRADER_NO_REASONING"],
        )

    try:
        raw = _call_model_grader(context, model_name)
    except json.JSONDecodeError as exc:
        _log.warning("model_grader: JSON parse error for %s: %s", report_id, exc)
        return ModelGraderResult(
            passed=True,
            overall_score=0.0,
            criteria_scores={},
            criteria_rationales={},
            feedback="",
            flags=["MODEL_GRADER_PARSE_ERROR"],
        )
    except Exception as exc:
        _log.warning("model_grader: LLM API error for %s: %s", report_id, exc)
        return ModelGraderResult(
            passed=True,
            overall_score=0.0,
            criteria_scores={},
            criteria_rationales={},
            feedback="",
            flags=["MODEL_GRADER_INFRA_ERROR"],
        )

    # Extract scores and rationales from the parsed dict
    criteria_scores: dict[str, float] = {}
    criteria_rationales: dict[str, str] = {}
    for criterion in MODEL_GRADER_WEIGHTS:
        entry = raw.get(criterion, {})
        criteria_scores[criterion] = float(entry.get("score", 0))
        criteria_rationales[criterion] = str(entry.get("rationale", ""))

    # Compute weighted average
    overall_score = sum(
        criteria_scores.get(c, 0.0) * w
        for c, w in MODEL_GRADER_WEIGHTS.items()
    )

    passed = overall_score >= MODEL_GRADER_PASS_THRESHOLD
    _log.info(
        "model_grader: report %s overall=%.2f/%s verdict=%s",
        report_id, overall_score, MODEL_GRADER_PASS_THRESHOLD,
        "PASS" if passed else "FAIL",
    )

    if passed:
        return ModelGraderResult(
            passed=True,
            overall_score=overall_score,
            criteria_scores=criteria_scores,
            criteria_rationales=criteria_rationales,
            feedback="",
        )

    return ModelGraderResult(
        passed=False,
        overall_score=overall_score,
        criteria_scores=criteria_scores,
        criteria_rationales=criteria_rationales,
        feedback=_build_model_grader_feedback(criteria_scores, criteria_rationales, overall_score),
        flags=["REASONING_QUALITY"],
    )


# ── Message-parsing utilities (used by supervisor_agent.py) ──────────────────

_UUID_RE = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"

_REPORT_ID_PAT = re.compile(
    rf'report[_\s-]?id["\s:=]+({_UUID_RE})', re.IGNORECASE
)
_ACTION_ID_PAT = re.compile(
    rf'action[_\s-]?id["\s:=]+({_UUID_RE})', re.IGNORECASE
)


def extract_report_id(messages: list[Any]) -> str | None:
    """Scan supervisor message history for the most recent report_id UUID."""
    for msg in reversed(messages):
        content = str(getattr(msg, "content", "") or "")
        m = _REPORT_ID_PAT.search(content)
        if m:
            return m.group(1)
    return None


def extract_action_id(messages: list[Any]) -> str | None:
    """Scan supervisor message history for the most recent action_id UUID."""
    for msg in reversed(messages):
        content = str(getattr(msg, "content", "") or "")
        m = _ACTION_ID_PAT.search(content)
        if m:
            return m.group(1)
    return None

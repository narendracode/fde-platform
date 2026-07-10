# Propguru — Verification Loop Design

## What Problem This Solves

The current Propguru pipeline has a **React loop** inside each agent (the model picks a tool, runs it, observes the result, picks next tool — until done). That loop ensures the agent finishes its individual task.

But there is no quality gate between "agent finished" and "human sees the result." Today, if the scorer only scores 20 of 30 criteria, or if the evaluator sets `confidence = high` on a half-scored property, or if the recommended price is 80% above market — the evaluation still reaches the HITL inbox. The human has to catch it.

A **verification loop** adds an automated quality gate *between* the agent completing its run and the output being accepted. If the output fails the quality check, structured feedback goes back to the agent so it can correct and retry — without human involvement. Only a passing output proceeds to the HITL inbox.

---

## Current Architecture

```
Trigger (POST /deals/{id}/evaluate)
    │
    ▼
Supervisor (propguru-evaluation-supervisor)
    │
    ├──► data-collector   (ReAct loop)  → property facts
    ├──► market-analyst   (ReAct loop)  → comps, base price/sqft
    ├──► scorer           (ReAct loop)  → 30 criteria scored
    └──► evaluator        (ReAct loop)  → price, confidence, reasoning
                                             │
                                             ▼
                                      HITL inbox (human reviews)
                                             │
                              ┌──────────────┼──────────────┐
                              ▼              ▼              ▼
                           Approve        Reject       Refine with AI
```

The human is the only quality gate today. The "Refine with AI" feature is essentially a **human-triggered grader with free-form rubric** — but it only runs after the human has already read the output and decided it needs work.

---

## What a Verification Loop Adds

```
Supervisor
    │
    └──► ... scorer → evaluator (ReAct loop) ...
                          │
                          ▼
                    ┌─ VERIFIER ─┐
                    │  (grader)  │
                    └────────────┘
                      │       │
                   PASS     FAIL + structured feedback
                      │       │
                      │       └──► back to evaluator (retry, max 2×)
                      │
                      ▼
               HITL inbox (human reviews)
```

The verifier sits between the evaluator and the HITL proposal. It is not another ReAct agent — it is a **deterministic or model-based check** that produces a pass/fail verdict with a reason list. The loop runs at most 2–3 times before escalating to the human with a "low confidence — needs review" flag.

---

## Grader Types and Their Role in Propguru

### 1. Code-Based Graders (deterministic)

These run instantly, have zero LLM cost, and are always correct.

| Check | What it catches |
|---|---|
| **Coverage** | Fewer than 30 criteria scored. Identifies which criteria codes are missing. |
| **Boolean validity** | A boolean criterion has a score that is not exactly 0.0 or 1.0. (Caught at the API layer now, but the grader can detect it in the agent's reasoning before the API call.) |
| **Scale range** | A `scale_1_5` criterion was scored outside 1–5, or a `proximity_km` outside 0–5. |
| **Price sanity** | `recommended_price` is more than 50% above or below `base_price`. Catches wildly miscalibrated scores. |
| **Confidence calibration** | `confidence = high` but fewer than 90% of criteria scored, or `score_factor < 0.6`. |
| **Category zero-out** | Any full category (amenity / location / property / society) has a weighted score of 0%, which likely means all its criteria were skipped. |

**Verdict format example:**
```
FAIL
- Coverage: 26/30 criteria scored. Missing: CRIT-027, CRIT-028, CRIT-029, CRIT-030 (society)
- Confidence: Set to "high" but only 87% coverage. Must be "medium" or lower.

Required: Re-score the missing society criteria, then recalculate price and confidence.
```

This feedback is injected as a new message into the agent's conversation. The agent reads it and corrects.

---

### 2. Model-Based Grader (LLM-as-judge)

An LLM evaluates the agent's qualitative output against a rubric. Costs one small LLM call. Used for things code cannot check.

**Rubric for Propguru evaluator output:**

| Criterion | Weight | What is checked |
|---|---|---|
| Reasoning coherence | High | Does the reasoning explain which specific criteria drove the score up/down, or is it generic boilerplate? |
| Price justification | High | Is the price premium (above/below base) linked to specific high/low-scoring criteria? |
| Market alignment | Medium | Does the confidence level match the data coverage and market trend direction? |
| Analyst guidance | Low | Does the reasoning flag which scores are estimates that the analyst should verify? |

The model grader returns a score (0–10) per criterion and an overall verdict. Threshold below 6/10 overall = FAIL.

**When to use model grader vs code grader:**
- Code grader runs first on every evaluation. Fast, cheap, catches structural issues.
- Model grader runs only if code grader passes. It checks quality, not correctness.
- Running both in sequence is the recommended starting point.

---

### 3. Human Grader (already exists)

The existing **HITL approve/reject/refine flow is a human grader**. The verification loop does not replace it — it makes it more effective:

- Today: human sees every output including low-quality ones, spends time rejecting or refining structural issues.
- With verification loop: structural and quality issues are auto-corrected before the human sees anything. The human focuses only on business judgment (is this deal worth pursuing at this price?), not on data completeness problems.

The "Refine with AI" feature remains as the human's tool for cases where the verified output is technically correct but the human has additional business context the agents lacked.

---

## Proposed Architecture for Propguru (Phased)

### Phase 1 — Code Grader on Evaluator Output (Start Here)

Scope: one verifier node, checking the evaluator's final output only.

- Grader type: code-based only
- Checks: coverage, boolean validity, price sanity, confidence calibration
- Max retries: 2 (after 2 failures, escalate to HITL with a "GRADER_FLAGGED" marker)
- Implementation: new LangGraph node added to the supervisor graph, conditional edge from evaluator → verifier → [pass: HITL proposal | fail: evaluator retry]
- No new agents, no new YAML configs needed

**Product effect:** Human reviewers stop seeing evaluations with obvious data gaps. The HITL inbox becomes higher signal.

---

### Phase 2 — Model Grader on Evaluator Reasoning

Scope: add LLM-as-judge after code grader passes.

- Grader type: rubric-based LLM call (cheap, fast model — Haiku or Sonnet with low token budget)
- Checks: reasoning coherence, price justification, analyst guidance flags
- Max retries: 1 (model grader failure → evaluator retries once with specific rubric feedback)
- If still failing after retry → HITL with "REASONING_FLAGGED" marker

**Product effect:** Reports reaching the human have clearly articulated reasoning. Analysts spend less time decoding cryptic AI rationale.

---

### Phase 3 — Per-Agent Verification (Scorer)

Scope: add a verifier after the scorer, before the evaluator runs.

- Grader type: code-based (coverage + validity checks on the 30 scores)
- Rationale: catching scorer failures early is cheaper than letting a bad score set flow all the way through the evaluator and then retrying both.
- If scorer fails verification → scorer retries (max 2×) before evaluator is even invoked

**Product effect:** Evaluator always starts from a complete, valid score set. Evaluator output quality improves without changing the evaluator at all.

---

## Where This Lives in the LangGraph Architecture

The supervisor graph in `supervisor_agent.py` already uses the pattern:

```
START → supervisor node → [conditional edge] → worker → [report back] → supervisor
```

The verifier fits as an additional node between a worker's completion and the supervisor's acceptance:

```
evaluator reports back
        │
        ▼
   verifier node      ← new node (stateless, pure function)
        │
    PASS / FAIL
        │          │
        ▼          ▼
  supervisor    evaluator (with feedback message injected)
  accepts       retries
```

The verifier node receives the supervisor's `State`, reads the evaluator's output from it, runs the grader logic, and either:
- **PASS**: sets a `verification_status = "passed"` field on state, continues to HITL proposal
- **FAIL**: appends a feedback `HumanMessage` to `messages`, increments a `verification_retries` counter, routes back to evaluator

The `verification_retries` counter is checked by a conditional edge — if it exceeds `max_retries`, the graph routes to HITL with a `grader_flags` list attached to the proposal.

No changes to existing YAML configs, agent prompts, or tool implementations are needed for Phase 1.

---

## Key Implementation Concepts (No Code Yet)

**State extension:** The supervisor's `SupervisorState` gets two new fields:
- `verification_retries: int` — tracks how many times the grader has rejected
- `grader_flags: list[str]` — issues found on the final (accepted or escalated) run

**Verifier node:** A plain Python function, not an LLM. Reads the evaluator's output from state, runs checks, returns a structured verdict.

**Feedback injection:** On FAIL, the verifier appends a structured message to `messages`. When the evaluator re-runs, it sees this message in its conversation history as context for what to fix.

**LangGraph pattern name:** This is the **Reflection pattern** (also called self-critique or evaluation loop in LangGraph documentation). It is distinct from:
- The ReAct loop (inner, per-agent tool use)
- The supervisor routing loop (outer, cross-agent coordination)
The verifier sits at the same level as supervisor routing but on a quality axis.

**Note on `RubricMiddleware` and `after_agent` hook:** These are not real LangChain/LangGraph APIs. The correct mechanism is a verifier node with a conditional edge, which is what gives you the equivalent of an "after agent" hook within a `StateGraph`. LangSmith has online evaluators that run against traces post-hoc, but those are observability tools — they do not feed back into the running agent.

---

## Observability and Auditability

Every verification run should be recorded:

- Which checks ran
- Which passed / failed
- What feedback was sent back
- How many retries occurred
- Final verdict (passed / escalated)

This data belongs in the `agent_actions` table (already exists) or a new `propguru_verification_logs` table. It gives analysts visibility into why an evaluation took longer than usual, and feeds into measuring grader effectiveness over time.

**Metrics to track:**
- Grader trigger rate (what % of evaluations fail at least one check)
- Issue type distribution (coverage failures vs price anomalies vs reasoning quality)
- Retry success rate (how often the agent self-corrects on the second attempt)
- Human escalation rate after grader (what reaches HITL with a FLAGGED marker)

---

## Benefits Summary

| Benefit | Who feels it | When |
|---|---|---|
| Fewer half-scored evaluations reaching analysts | Analysts | Phase 1 |
| Boolean/scale violations caught before HITL | System reliability | Phase 1 |
| Clearer, well-reasoned reports | Analysts | Phase 2 |
| Evaluator always works from complete score set | Agent quality | Phase 3 |
| Audit trail of grader decisions | Dev / QA | Phase 1 |
| Reduced analyst refinement workload | Analysts | Phase 1+2 |
| Trust in AI recommendations increases | Product | Phase 2+ |

---

## Risks and Mitigations

| Risk | Mitigation |
|---|---|
| Verification loop causes evaluation to time out | Hard cap of 2 retries; each retry resets only the evaluator, not the full pipeline |
| Grader is too strict and always fails | Start with lenient thresholds (e.g., coverage ≥ 80% not 100%); tune after observing false-fail rates |
| Agent ignores feedback and repeats the same mistake | Log as `stuck` after second retry; escalate to HITL with `GRADER_FLAGGED` — do not loop forever |
| Model grader is inconsistent across runs | Pin model and temperature to 0; use structured output (JSON pass/fail verdict) not free-form |
| Adds latency to evaluation pipeline | Code grader: <100ms. Model grader: 2–5 seconds. Retries: only triggered on failure, not on every run |

# Propguru Evaluation — Pre-Evaluate Refactor Plan

## Problem

The 4-agent evaluation pipeline consumes excessive tokens because agents 1 and 2
are doing purely mechanical, deterministic work that does not require an LLM:

| Agent | What it does | Needs LLM? |
|---|---|---|
| `propguru-data-collector` | Creates report; applies scoring rules (floor/facing/age → score) | No — pure rules |
| `propguru-market-analyst` | DB lookup for market comp; multiplication (rate × sqft) | No — pure lookup |
| `propguru-scorer` | Scores 20 criteria (10 amenity + 10 location) | Partially — location needs city knowledge |
| `propguru-evaluator` | Reads scores, calculates price, writes reasoning, proposes HITL | Yes — reasoning + proposal |

Additionally, the scorer's amenity scoring is low-quality: it guesses amenities from
locality labels ("premium" vs "standard"), which is unreliable and produces inconsistent
scores that the analyst must correct anyway.

---

## Solution

Introduce a **pre-evaluation service** that runs synchronously when the analyst clicks
"Evaluate". This service fills in everything that can be computed deterministically
before any agent is invoked. Agents then only run for what genuinely requires judgment.

### Pre-Evaluate Service (New)

`POST /api/v1/propguru/deals/{deal_id}/pre-evaluate`

Runs synchronously inside the `trigger_evaluation` API handler (not via Celery). Does:

1. Resolves deal → property record
2. Creates `PropguruEvaluationReport` (status = "draft")
3. Scores **PROPERTY criteria** (CRIT-021 to CRIT-025) with deterministic rules:
   - CRIT-021 Floor Level (scale_1_5): Ground/1st=2, 2–5=3, 6–10=4, 11+=5; top floor −1
   - CRIT-022 Facing (scale_1_5): east=5, north=4, north_east/west=3, south=2
   - CRIT-023 Property Age (scale_1_5): 0–2yr=5, 3–5=4, 6–10=3, 11–20=2, 20+=1
   - CRIT-024 Covered Parking (scale_1_5): 3 (neutral — not in property DB record)
   - CRIT-025 Power Backup (boolean): 0 (conservative — not in property DB record)
4. Scores **SOCIETY criteria** (CRIT-026 to CRIT-030) with property_type-aware defaults:
   - Apartments: all 3/5 neutral (analyst to verify)
   - Independent houses: CRIT-026 (gated) = 0, CRIT-027 (society type) = 1, CRIT-028 (lift) = 0, others = 3
5. Scores **AMENITY criteria** (CRIT-001 to CRIT-010, boolean):
   - Independent houses: all 0 (no society amenities — structural)
   - Apartments: all 0 with note "unknown — verify from site visit" (conservative; removes unreliable LLM guessing)
6. Fetches market comp by locality; falls back to ₹10,000/sqft if not found
7. Sets `market_rate_per_sqft` and `base_price` on the report
8. Returns `{report_id, property_summary, locality, city, latitude, longitude, market_summary}`

### Updated Pipeline (2 agents instead of 4)

```
trigger_evaluation API:
  1. pre_evaluate(deal_id) → {report_id, locality, lat, lon, ...}   [synchronous, no LLM]
  2. queue Celery task with {deal_id, report_id, locality, city, lat, lon}

Celery → propguru-evaluation-supervisor:
  Worker 1: propguru-scorer    (was workers 3, now only location — CRIT-011 to 020)
  Worker 2: propguru-evaluator (unchanged)
```

### What each agent does after refactor

**`propguru-scorer`** (simplified — location only):
- Receives `report_id`, `locality`, `city`, `latitude`, `longitude` from `extra_context`
- Scores only CRIT-011 to CRIT-020 (10 location proximity criteria)
- Uses `propguru_score_proximity` for most
- Does NOT need `propguru_get_deal` or `propguru_get_criteria` — context already provided
- Saves 10 scores, reports done

**`propguru-evaluator`** (unchanged):
- Calls `propguru_calculate_price` to compute score_factor + recommended_price
- Composes reasoning (can reference market_summary from extra_context)
- Calls `propguru_propose_evaluation` for HITL

---

## What Changes

### Files Modified

| File | Change |
|---|---|
| `src/fde_agent/api/routes/propguru/evaluation.py` | Add `POST /deals/{deal_id}/pre-evaluate` endpoint with scoring logic |
| `src/fde_agent/api/routes/propguru/deals.py` | `trigger_evaluation` calls pre-evaluate before queuing; passes report_id in extra_context |
| `agents/configs/propguru-evaluation-supervisor.yaml` | Remove data-collector + market-analyst from workers; update routing description |
| `agents/configs/propguru-scorer.yaml` | Simplify prompt to location-only (10 criteria); remove get_deal/get_criteria tool calls |
| `agents/configs/propguru-data-collector.yaml` | Retired (kept as file; no longer in supervisor workers) |
| `agents/configs/propguru-market-analyst.yaml` | Retired (kept as file; no longer in supervisor workers) |

### Files Unchanged

- `propguru-evaluator.yaml` — same workflow, same tools
- `propguru-evaluation-refiner.yaml` — not affected
- `src/fde_agent/agent/tools/propguru/evaluation.py` — all tools remain; no deletions
- `src/fde_agent/api/routes/propguru/simulation.py` — unchanged
- `src/fde_agent/agent/propguru_verifier.py` — unchanged

---

## Token Reduction Estimate

| Stage | Before | After |
|---|---|---|
| data-collector | ~1,500–2,000 tokens | Eliminated |
| market-analyst | ~800–1,200 tokens | Eliminated |
| scorer (20 criteria) | ~2,500–3,500 tokens | ~800–1,200 (10 criteria only) |
| evaluator | ~1,500–2,000 tokens | ~1,500–2,000 (unchanged) |
| supervisor routing | ~600–1,000 tokens (4 rounds) | ~300–500 (2 rounds) |
| **Total** | **~7,000–10,000 tokens** | **~2,500–3,500 tokens** |

Estimated **60–65% reduction** in tokens per evaluation.

---

## Criteria Pre-Scoring Rules (Reference)

### PROPERTY criteria

| Code | Name | Type | Rule |
|---|---|---|---|
| CRIT-021 | Floor Level | scale_1_5 | Independent house → 3. Apartment: ground/1st=2, 2–5=3, 6–10=4, 11+=5; top floor gets −1 |
| CRIT-022 | Facing | scale_1_5 | east=5, north=4, north_east/west=3, south=2, default=3 |
| CRIT-023 | Property Age | scale_1_5 | 0–2yr=5, 3–5=4, 6–10=3, 11–20=2, 20+=1 |
| CRIT-024 | Covered Parking | scale_1_5 | 3 (neutral default — no parking data in property record) |
| CRIT-025 | Power Backup | boolean | 0 (conservative default — not in property record) |

### SOCIETY criteria

| Code | Name | Type | Apartment default | Independent house default |
|---|---|---|---|---|
| CRIT-026 | Gated Community | boolean | 0 (unknown) | 0 (not applicable) |
| CRIT-027 | Society Type | scale_1_5 | 3 (neutral) | 1 (standalone) |
| CRIT-028 | Lift Availability | boolean | 0 (unknown) | 0 (not applicable) |
| CRIT-029 | Water Supply | scale_1_5 | 3 (neutral) | 3 (neutral) |
| CRIT-030 | Maintenance Quality | scale_1_5 | 3 (neutral) | 3 (neutral) |

### AMENITY criteria (CRIT-001 to CRIT-010, all boolean)

| Property type | Default score | Note |
|---|---|---|
| `independent_house` | 0 | No society amenities — structural fact |
| `apartment` | 0 | Unknown — analyst to verify from site visit |

---

## Trade-offs

**Gains:**
- 60–65% fewer tokens per evaluation
- Deterministic scoring is never wrong in the way LLM scoring can be
- Amenity defaults are more honest (0 = unknown) vs current LLM guessing

**Losses / limitations:**
- CRIT-024 (Parking) and CRIT-025 (Power Backup) are defaulted conservatively — if these
  fields are added to the property DB schema in future, the pre-evaluate logic should be
  updated to read them directly
- Amenity pre-scores are always 0 for apartments, which is conservative; analyst must
  verify and update via refinement canvas if amenities are present

**Not affected:**
- HITL workflow — unchanged
- Quality verification loop — unchanged
- Refinement canvas — unchanged
- Analyst's ability to correct any pre-scored criterion via the refine flow

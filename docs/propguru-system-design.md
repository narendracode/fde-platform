# Propguru — Property Evaluation System Design
**Status:** Design / Pre-implementation
**Domain:** Real estate — residential property buy/sell
**Platform pattern:** Follows Sandhar implementation (supervisor-worker agents, HITL canvas, simulations)

---

## 1. Business Context

Propguru buys and sells residential properties (houses and apartments). It does not deal with buyers or sellers directly — all business flows through **Channel Partners (CPs)**.

```
Seller → CP-1 (deal sourcing) → Propguru → CP-2 (distribution) → Buyer
```

Business economics: Propguru acquires a property at price **X**, refurbishes, and sells at **X + Y** (profit).

The only use case being implemented is **Evaluation** — the process by which Propguru determines the acquisition price X for a property brought in by a Channel Partner.

---

## 2. Evaluation Domain: How It Works Today

A dedicated back-office analyst team evaluates each incoming deal. The evaluation involves:

1. Going through **30 structured data points** covering amenities, location, property attributes, and society/building quality.
2. Scoring each data point.
3. Looking up **market data** for the locality — historical transactions, price trends, comparable sales — sourced from partners like housing.com.
4. Applying an established **pricing algorithm** (trade secret) to compute the final offer price.
5. Locking the price and communicating it to the Channel Partner.

**Problems today:**
- Manual → slow (days per deal)
- Analyst bottleneck → capacity constraint, opportunity loss
- Inconsistent scoring across analysts → price variance on similar properties
- No audit trail on why a price was set

**What AI adds:**
- Automated data collection and scoring for the 30 criteria
- Consistent, algorithm-driven price calculation
- HITL for analyst oversight and override
- Full audit trail of every score, rationale, and override
- Path to full automation when confidence is high

---

## 3. Domain Model

### 3.1 Entity Overview

```
PropguruChannelPartner
    │  1
    ├──── PropguruDeal (1 CP per deal — the sourcing CP)
    │           │ 1
    │           ├──── PropguruProperty (1 property per deal)
    │           │
    │           └──── PropguruEvaluationReport  ←──┐
    │                       │ 1                     │
    │                       ├─── PropguruEvalScore  │  (30 rows — one per criterion)
    │                       │        │               │
    │                       │        └──── PropguruEvaluationCriteria (master)
    │                       │
    │                       └─── PropguruMarketComp (comps pulled for the locality)
    │
    └──── PropguruDeal (0-N on distribution side — assigned buyer CPs)
```

### 3.2 DB Tables

#### `propguru_channel_partners`
| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `cp_code` | String(20) UNIQUE | e.g., `CP-001` |
| `name` | String(200) | |
| `cp_type` | String(20) | `sourcing` / `distribution` / `both` |
| `phone` | String(20) | |
| `email` | String(100) | |
| `city` | String(100) | |
| `status` | String(20) | `active` / `inactive` |
| `commission_pct` | Float | default commission % |
| `created_at` | Timestamp | |
| `updated_at` | Timestamp | |

#### `propguru_deals`
| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `deal_code` | String(30) UNIQUE | e.g., `DEAL-001` |
| `property_id` | UUID FK → `propguru_properties` | |
| `sourcing_cp_id` | UUID FK → `propguru_channel_partners` | CP who brought the deal |
| `sourcing_cp_commission_pct` | Float | agreed commission for sourcing CP |
| `stage` | String(30) | `lead` / `evaluation_pending` / `evaluation_done` / `agreement_signed` / `listed` / `sold` / `lost` |
| `lead_source` | String(50) | `channel_partner` / `referral` |
| `notes` | Text | |
| `target_acquisition_price` | Float (nullable) | set after evaluation approved |
| `final_sale_price` | Float (nullable) | set on deal close |
| `created_at` | Timestamp | |
| `updated_at` | Timestamp | |

#### `propguru_properties`
| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `property_code` | String(30) UNIQUE | e.g., `PROP-001` |
| `address_line1` | String(300) | |
| `city` | String(100) | |
| `locality` | String(150) | e.g., "Whitefield, Bengaluru" |
| `pincode` | String(10) | |
| `property_type` | String(30) | `apartment` / `independent_house` |
| `carpet_area_sqft` | Float | |
| `built_up_area_sqft` | Float (nullable) | |
| `bedrooms` | Integer | |
| `bathrooms` | Integer | |
| `floor_number` | Integer (nullable) | null for independent house |
| `total_floors` | Integer (nullable) | total floors in building |
| `building_age_years` | Integer (nullable) | |
| `facing` | String(20) | `east` / `west` / `north` / `south` |
| `latitude` | Float (nullable) | for distance calculations |
| `longitude` | Float (nullable) | |
| `created_at` | Timestamp | |
| `updated_at` | Timestamp | |

#### `propguru_evaluation_criteria` (master — 30 data points)
| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `criterion_code` | String(20) UNIQUE | e.g., `CRIT-001` |
| `name` | String(200) | e.g., "Swimming Pool" |
| `category` | String(30) | `amenity` / `location` / `property` / `society` |
| `weight` | Float | 1.0–10.0, higher = more price impact |
| `scoring_type` | String(20) | `boolean` (yes/no) / `scale_1_5` / `proximity_km` |
| `description` | Text | what the analyst checks |
| `is_active` | Boolean | |
| `sort_order` | Integer | |
| `created_at` | Timestamp | |

#### `propguru_evaluation_reports`
| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `deal_id` | UUID FK → `propguru_deals` | |
| `version` | Integer | auto-increments on re-evaluation |
| `status` | String(20) | `draft` / `pending_review` / `approved` / `rejected` |
| `market_rate_per_sqft` | Float | from market comp analysis |
| `base_price` | Float | `market_rate_per_sqft × carpet_area_sqft` |
| `score_factor` | Float | weighted score percentage 0–1 |
| `price_premium_pct` | Float | score_factor × premium_multiplier |
| `recommended_price` | Float | `base_price × (1 + price_premium_pct)` |
| `final_price` | Float (nullable) | may differ from recommended after analyst override |
| `confidence` | String(20) | `high` / `medium` / `low` |
| `agent_reasoning` | Text | agent's explanation |
| `analyst_notes` | Text (nullable) | analyst override rationale |
| `approved_by` | String(100) (nullable) | |
| `approved_at` | Timestamp (nullable) | |
| `created_at` | Timestamp | |
| `updated_at` | Timestamp | |

#### `propguru_evaluation_scores`
| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `report_id` | UUID FK → `propguru_evaluation_reports` | |
| `criterion_id` | UUID FK → `propguru_evaluation_criteria` | |
| `score` | Float | 0–5 normalized score (or 0/1 for boolean) |
| `raw_value` | String(200) | actual collected value e.g., "0.8 km", "yes", "3" |
| `source` | String(20) | `agent` / `analyst` |
| `notes` | Text (nullable) | why this score was given |
| `created_at` | Timestamp | |
| `updated_at` | Timestamp | |

#### `propguru_market_comps`
| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `locality` | String(150) | |
| `property_type` | String(30) | |
| `avg_price_per_sqft` | Float | |
| `min_price_per_sqft` | Float | |
| `max_price_per_sqft` | Float | |
| `price_trend_6m_pct` | Float | % change in last 6 months (+ = rising) |
| `transaction_count_6m` | Integer | number of comparable transactions |
| `data_source` | String(100) | e.g., "housing.com" |
| `as_of_date` | Date | |
| `created_at` | Timestamp | |

---

## 4. The 30 Evaluation Criteria (Seed Data)

Grouped by category, with indicative weights and scoring types.

### A. Amenities (10 criteria)
| Code | Name | Weight | Scoring Type |
|---|---|---|---|
| CRIT-001 | Swimming Pool | 6 | boolean |
| CRIT-002 | Clubhouse | 5 | boolean |
| CRIT-003 | Gymnasium | 5 | boolean |
| CRIT-004 | Children's Playground | 4 | boolean |
| CRIT-005 | Jogging / Cycling Track | 3 | boolean |
| CRIT-006 | Indoor Games Room | 2 | boolean |
| CRIT-007 | Tennis / Badminton Court | 3 | boolean |
| CRIT-008 | Landscaped Garden | 4 | boolean |
| CRIT-009 | Multipurpose Hall | 2 | boolean |
| CRIT-010 | Rooftop / Terrace Access | 3 | boolean |

### B. Location Connectivity (10 criteria)
| Code | Name | Weight | Scoring Type |
|---|---|---|---|
| CRIT-011 | Proximity — Metro / Railway Station | 8 | proximity_km |
| CRIT-012 | Proximity — Highway / Expressway | 5 | proximity_km |
| CRIT-013 | Proximity — Airport | 5 | proximity_km |
| CRIT-014 | Proximity — School (Top-rated) | 7 | proximity_km |
| CRIT-015 | Proximity — Hospital | 7 | proximity_km |
| CRIT-016 | Proximity — Mall / Shopping Centre | 6 | proximity_km |
| CRIT-017 | Proximity — Park / Greenery | 5 | proximity_km |
| CRIT-018 | Proximity — IT / Business Park | 7 | proximity_km |
| CRIT-019 | Public Bus Connectivity | 4 | scale_1_5 |
| CRIT-020 | Daily Essentials / Market Access | 5 | proximity_km |

### C. Property Attributes (5 criteria)
| Code | Name | Weight | Scoring Type |
|---|---|---|---|
| CRIT-021 | Floor Level | 4 | scale_1_5 |
| CRIT-022 | Facing Direction | 4 | scale_1_5 |
| CRIT-023 | Property Age | 6 | scale_1_5 |
| CRIT-024 | Covered Parking | 5 | scale_1_5 |
| CRIT-025 | Power Backup | 3 | boolean |

### D. Building / Society (5 criteria)
| Code | Name | Weight | Scoring Type |
|---|---|---|---|
| CRIT-026 | Gated Community with Security | 6 | boolean |
| CRIT-027 | Society Type (premium/standard/standalone) | 5 | scale_1_5 |
| CRIT-028 | Lift Availability | 4 | boolean |
| CRIT-029 | Water Supply Quality | 4 | scale_1_5 |
| CRIT-030 | Society Maintenance Quality | 5 | scale_1_5 |

**Scoring logic:**
- `boolean`: 1.0 = present, 0.0 = absent
- `scale_1_5`: raw value 1–5 → normalized as `(value - 1) / 4`
- `proximity_km`: closer is better — scored by bands (e.g., < 0.5 km = 5, 0.5–1 = 4, 1–2 = 3, 2–4 = 2, > 4 = 1)

**Price formula:**
```
score_factor      = Σ(weight_i × score_i) / Σ(weight_i)   [0.0–1.0]
price_premium_pct = score_factor × MAX_PREMIUM_PCT          [MAX = 35% in POC]
recommended_price = base_price × (1 + price_premium_pct)
```
`base_price = market_rate_per_sqft × carpet_area_sqft`

---

## 5. Agent Architecture

### 5.1 Agent Tree

```
propguru-evaluation-supervisor  (supervisor, orchestrates 4 workers)
    │
    ├── propguru-data-collector
    │       Reads property record, scores boolean and scale criteria
    │       from property attributes. Flags criteria it cannot auto-score.
    │
    ├── propguru-market-analyst
    │       Queries propguru_market_comps for the property's locality.
    │       Computes base price (market_rate × carpet_area).
    │       Returns 6-month trend, transaction count, price band.
    │
    ├── propguru-scorer
    │       For each of the 30 criteria:
    │         - Uses data-collector output for property/amenity criteria
    │         - Applies proximity scoring for location criteria
    │         - Saves scores to propguru_evaluation_scores
    │
    └── propguru-evaluator
            Reads scores from DB, computes score_factor and recommended_price.
            Determines confidence: high / medium / low.
            Proposes evaluation for analyst review via propguru_propose_evaluation (HITL).
            Feature flags: enable_refinement: true, refinement_agent: propguru-evaluation-refiner

propguru-evaluation-refiner  (standalone — one turn per chat message in canvas)
    Conversational refinement of the evaluation.
    Analyst can: adjust a specific score, ask why a score was given,
    change the final price, add notes. Each change re-computes the price.
```

### 5.2 Model Selection
| Agent | Model | Rationale |
|---|---|---|
| `propguru-evaluation-supervisor` | claude-haiku-4-5 | Lightweight routing only |
| `propguru-data-collector` | claude-sonnet-4-6 | Interprets property data, handles missing fields |
| `propguru-market-analyst` | claude-sonnet-4-6 | Market data reasoning |
| `propguru-scorer` | claude-sonnet-4-6 | 30 scores in one pass, needs consistency |
| `propguru-evaluator` | claude-sonnet-4-6 | Price reasoning + HITL proposal |
| `propguru-evaluation-refiner` | claude-sonnet-4-6 | Conversational, needs domain reasoning |

### 5.3 YAML Agent Files
```
agents/configs/
  propguru-evaluation-supervisor.yaml
  propguru-data-collector.yaml
  propguru-market-analyst.yaml
  propguru-scorer.yaml
  propguru-evaluator.yaml
  propguru-evaluation-refiner.yaml
```

---

## 6. Tool Catalogue

Parallel to `src/agri_agent/agent/tools/sandhar/`, create:
```
src/agri_agent/agent/tools/propguru/
    __init__.py
    deals.py        # deal + property read tools
    evaluation.py   # scoring, pricing, proposal tools
    evaluation_refiner.py   # refinement canvas tools
```

### 6.1 Data Tools (`deals.py`)
| Tool | Description |
|---|---|
| `propguru_get_deal` | Returns deal + property details for a given deal_id |
| `propguru_get_property_details` | Returns full property record (all columns) |
| `propguru_list_deals` | Lists deals by stage, optionally filtered |

### 6.2 Evaluation Tools (`evaluation.py`)
| Tool | Description |
|---|---|
| `propguru_get_criteria` | Returns all 30 active evaluation criteria with weights |
| `propguru_get_market_comp` | Returns market comp data for a locality + property_type |
| `propguru_save_evaluation_score` | Saves one criterion score (criterion_id, score, raw_value, notes) |
| `propguru_calculate_price` | Given report_id, reads all scores and computes score_factor + recommended_price; saves to report |
| `propguru_propose_evaluation` | Creates AgentAction in platform HITL inbox with full evaluation display |
| `propguru_create_evaluation_report` | Creates a new draft report for a deal |

### 6.3 Refinement Tools (`evaluation_refiner.py`)
| Tool | Description |
|---|---|
| `propguru_refine_get_evaluation` | Returns full evaluation report with all 30 scores |
| `propguru_refine_update_score` | Updates a specific criterion score; re-calculates price |
| `propguru_refine_explain_score` | Returns criteria details + current score + scoring bands for explanation |
| `propguru_refine_update_final_price` | Sets final_price to an analyst-specified value with override note |

---

## 7. API Design

All routes under `/api/v1/propguru/` prefix. Follows Sandhar route pattern.

```
src/agri_agent/api/routes/propguru/
    __init__.py
    pages.py          # HTML pages
    master.py         # CRUD for CPs, criteria, properties
    deals.py          # Deal CRUD + stage transitions
    evaluation.py     # Evaluation trigger + approval
    simulation.py     # Seed + scenario endpoints
```

### 7.1 Master Data Endpoints (`master.py`)
| Method | Path | Description |
|---|---|---|
| GET | `/api/v1/propguru/channel-partners` | List all CPs |
| POST | `/api/v1/propguru/channel-partners` | Create CP |
| GET | `/api/v1/propguru/channel-partners/{id}` | Get CP |
| GET | `/api/v1/propguru/evaluation-criteria` | List all 30 criteria |
| GET | `/api/v1/propguru/properties` | List properties |
| GET | `/api/v1/propguru/properties/{id}` | Property detail |

### 7.2 Deal Endpoints (`deals.py`)
| Method | Path | Description |
|---|---|---|
| GET | `/api/v1/propguru/deals` | List deals, filter by stage |
| POST | `/api/v1/propguru/deals` | Create deal (registers CP + property) |
| GET | `/api/v1/propguru/deals/{id}` | Deal detail with property + CP |
| PATCH | `/api/v1/propguru/deals/{id}/stage` | Advance deal stage |

### 7.3 Evaluation Endpoints (`evaluation.py`)
| Method | Path | Description |
|---|---|---|
| POST | `/api/v1/propguru/deals/{id}/evaluate` | Trigger evaluation agent pipeline |
| GET | `/api/v1/propguru/deals/{id}/evaluation` | Latest evaluation report |
| GET | `/api/v1/propguru/evaluations/{report_id}` | Full report with all 30 scores |
| GET | `/api/v1/propguru/evaluations/{report_id}/scores` | Scores grouped by category |
| PATCH | `/api/v1/propguru/evaluations/{report_id}/approve` | Analyst approval (sets status → approved, target_acquisition_price on deal) |
| PATCH | `/api/v1/propguru/evaluations/{report_id}/reject` | Reject → triggers re-evaluation |

### 7.4 Simulation Endpoints (`simulation.py`)
| Method | Path | Description |
|---|---|---|
| POST | `/api/v1/propguru/simulation/seed` | Seed all master data (idempotent) |
| POST | `/api/v1/propguru/simulation/reset` | Delete all data and reseed |
| GET | `/api/v1/propguru/simulation/scenarios` | List demo scenarios |
| POST | `/api/v1/propguru/simulation/scenario/{id}` | Run a specific scenario |

---

## 8. UI Pages

```
src/agri_agent/templates/propguru/
    dashboard.html       # Pipeline overview: deal count by stage, recent evaluations
    deals.html           # Deal list with stage badges + "Start Evaluation" button
    evaluation.html      # Evaluation workspace: trigger + review + Refine with AI canvas
    master.html          # CRUD tabs: Channel Partners, Evaluation Criteria, Properties
    simulation.html      # Scenario cards + seed/reset controls
    _refine_preview_propguru-evaluation.html   # Server-rendered partial for refinement canvas
```

### Page Routes (`pages.py`)
| Path | Template | Description |
|---|---|---|
| `/propguru` | `propguru/dashboard.html` | Command centre — deal pipeline funnel, recent evaluations |
| `/propguru/deals` | `propguru/deals.html` | Deal list, stage filter, create deal form |
| `/propguru/evaluation` | `propguru/evaluation.html` | Evaluation trigger + review workspace |
| `/propguru/master` | `propguru/master.html` | CPs, criteria, properties CRUD |
| `/propguru/simulation` | `propguru/simulation.html` | Seed + scenario panel |
| `/propguru/evaluation/{report_id}/refine-preview` | partial | SSE canvas preview refresh |

### Navigation
Propguru pages appear as a separate nav section in the sidebar, parallel to Sandhar, following the same active_page pattern.

---

## 9. HITL + Refinement Design

### 9.1 Evaluation Proposal (AgentAction)

When `propguru-evaluator` completes its calculation, it calls `propguru_propose_evaluation` which creates an `AgentAction` with:

```json
{
  "title": "Property Evaluation — PROP-001 (Whitefield 3BHK)",
  "summary": "Recommended price ₹1.42 Cr — confidence: high — 26/30 criteria scored",
  "confidence": "high",
  "display_data": [
    {"label": "Property", "value": "3BHK, 1250 sqft, Whitefield, Bengaluru"},
    {"label": "Market Rate", "value": "₹9,200/sqft"},
    {"label": "Base Price", "value": "₹1.15 Cr"},
    {"label": "Score Factor", "value": "83% (strong amenities, good connectivity)"},
    {"label": "Price Premium", "value": "+23.4%"},
    {"label": "Recommended Price", "value": "₹1.42 Cr"},
    {"label": "Criteria Scored", "value": "26/30 auto-scored, 4 need analyst review"}
  ],
  "tags": ["propguru", "evaluation", "high_confidence"],
  "approval_action": {
    "method": "PATCH",
    "url": "/api/v1/propguru/evaluations/{report_id}/approve"
  }
}
```

The action shows in `/approvals` (generic inbox) and on `/propguru/evaluation`.

### 9.2 Refinement Canvas (propguru-evaluation-refiner)

Feature flag on `propguru-evaluator.yaml`:
```yaml
feature_flags:
  enable_refinement: true
  refinement_agent: "propguru-evaluation-refiner"
  refinement_preview: "propguru-evaluation"
```

The preview partial (`_refine_preview_propguru-evaluation.html`) shows:
- Recommended price prominently
- Score factor bar
- Top-5 criteria by weight with their scores and raw values
- Category subtotals (Amenities / Location / Property / Society)
- Final price (if analyst has set an override)

**Example refinement turns:**
```
Analyst: "Why did swimming pool score so low?"
Agent: [calls propguru_refine_explain_score] → "Swimming pool is present (score: 1.0/1.0, boolean type).
        With weight 6, it contributed +0.33% to the premium. It is fully scored."

Analyst: "The proximity to metro is wrong — it's actually 0.6 km not 2 km."
Agent: [calls propguru_refine_update_score(criterion=CRIT-011, raw_value="0.6 km", score=4.0)]
        → "Updated. Metro proximity now scores 4.0/5.0 (was 2.0). Recommended price updated:
           ₹1.42 Cr → ₹1.47 Cr (+₹5 lakhs). Score factor: 83% → 86%."

Analyst: "Looks good. Set the final price at ₹1.45 Cr."
Agent: [calls propguru_refine_update_final_price(1_45_00_000)]
        → "Final price set to ₹1.45 Cr. Reason recorded: analyst override — price positioned
           between recommended and market ceiling."
```

Deep-link pattern identical to Sandhar: `?refine=<action_id>` in URL. "← Back to Evaluation" preserves the active refinement session.

### 9.3 Approval Flow

```
Deal created → Stage: evaluation_pending
    │
    ▼
Agent pipeline triggered → propguru_propose_evaluation → AgentAction in inbox
    │
    ▼
Analyst opens /propguru/evaluation → reviews report + scores
    │
    ├── Option A: Approve directly → PATCH /evaluations/{id}/approve
    │             → report status: approved
    │             → deal stage: evaluation_done
    │             → deal.target_acquisition_price = report.final_price
    │
    ├── Option B: Refine with AI → open canvas → adjust scores / final price → approve from canvas
    │
    └── Option C: Reject → deal stays at evaluation_pending → can re-trigger agent
```

### 9.4 Path to Full Automation

The `human_in_the_loop` feature flag on `propguru-evaluator.yaml` controls the HITL step:

```yaml
feature_flags:
  human_in_the_loop: true    # set to false for full automation
```

When `false`, `propguru_propose_evaluation` auto-approves the evaluation without creating an `AgentAction`. The deal stage advances automatically. This is a configuration change, not a code change.

---

## 10. Simulation Scenarios

### Seed Data
**Channel Partners:** 8 CPs (4 sourcing, 4 distribution, 2 both)
**Properties:** 10 pre-configured properties across 3 cities, 2 property types
**Market Comps:** 5 localities with simulated market rates and 6-month trend data
**Deals:** 5 pre-created deals in various stages for demo

### Scenario Definitions
| ID | Name | What It Sets Up |
|---|---|---|
| `s1-normal` | Normal Evaluation | Clean 3BHK apartment, all 30 data points available, good market data → high-confidence evaluation → agent proposes → analyst approves |
| `s2-luxury` | Luxury Property | All 10 amenities present, metro < 0.5 km, IT park < 1 km → score factor ~95% → premium pricing → demonstrates high-end deal |
| `s3-missing-data` | Incomplete Data | 8 criteria cannot be auto-scored (e.g., indoor games, water quality unknown) → medium confidence → analyst must fill gaps in canvas |
| `s4-analyst-override` | Price Override | Agent recommends ₹1.2 Cr but analyst knows the seller expects ₹1.3 Cr; uses refinement canvas to document rationale and set final price |
| `s5-market-drop` | Market Downturn | Market comp shows -8% price trend in last 6 months → base price lower → agent flags in reasoning → demonstrates constraint-aware pricing |

---

## 11. DB Migration Plan

Following the Sandhar numbering convention (which ends at 015):

| Migration | File | Tables Created |
|---|---|---|
| 016 | `016_propguru_master.py` | `propguru_channel_partners`, `propguru_evaluation_criteria` |
| 017 | `017_propguru_deals.py` | `propguru_properties`, `propguru_deals` |
| 018 | `018_propguru_evaluation.py` | `propguru_evaluation_reports`, `propguru_evaluation_scores`, `propguru_market_comps` |

---

## 12. File Structure

```
alembic/versions/
  016_propguru_master.py
  017_propguru_deals.py
  018_propguru_evaluation.py

agents/configs/
  propguru-evaluation-supervisor.yaml
  propguru-data-collector.yaml
  propguru-market-analyst.yaml
  propguru-scorer.yaml
  propguru-evaluator.yaml
  propguru-evaluation-refiner.yaml

src/agri_agent/
  db/models.py                               ← append Propguru models
  agent/tools/propguru/
    __init__.py
    deals.py
    evaluation.py
    evaluation_refiner.py
  agent/tools/__init__.py                    ← register propguru tools
  api/routes/propguru/
    __init__.py
    pages.py
    master.py
    deals.py
    evaluation.py
    simulation.py
  api/app.py                                 ← include propguru routers
  templates/propguru/
    dashboard.html
    deals.html
    evaluation.html
    master.html
    simulation.html
    _refine_preview_propguru-evaluation.html

tests/
  test_propguru_evaluation.py
  test_propguru_simulation.py
```

---

## 13. Tests

### `test_propguru_simulation.py`
- Seed creates all channel partners, evaluation criteria (30), properties, market comps
- Reset wipes and reseeds correctly
- Each scenario endpoint returns 200 and expected payload fields
- `s3-missing-data` scenario creates a deal with known-missing criteria

### `test_propguru_evaluation.py`
- Price formula: score_factor calculation with known inputs
- `propguru_calculate_price` tool returns correct recommended_price
- Proximity scoring bands produce correct score values
- Boolean criteria scoring (present=1.0, absent=0.0)
- PATCH `/evaluations/{id}/approve` sets deal.target_acquisition_price and advances stage
- Agent action created by `propguru_propose_evaluation` has required display_data fields

---

## 14. Navigation / Company Visibility

The platform gates company-specific sidebar sections via the `COMPANIES_TO_SHOW` environment variable (already implemented in `settings.py` and `_templates.py`).

### How it works (existing pattern)
1. `settings.py` — `companies_to_show: str = "sandhar,fundly"` (default, from `.env`)
2. `_templates.py` — parses the string into a list and injects it as a Jinja2 global: `templates.env.globals["companies"] = [...]`
3. `base.html` — wraps each company nav section in a guard:
   ```jinja2
   {% if 'sandhar' in companies %}
     <div class="sidebar-section"> ... Sandhar links ... </div>
   {% endif %}
   ```

### What Propguru adds

**`base.html`** — add a new guarded section after the Sandhar block:
```jinja2
{% if 'propguru' in companies %}
<div class="sidebar-divider"></div>
<div class="sidebar-section">
  <div class="sidebar-section-label">Propguru</div>
  <a href="/propguru" class="sidebar-link {% if active_page == 'propguru_dashboard' %}active{% endif %}">
    <span class="icon">🏠</span> Dashboard
  </a>
  <a href="/propguru/deals" class="sidebar-link {% if active_page == 'propguru_deals' %}active{% endif %}">
    <span class="icon">📋</span> Deals
  </a>
  <a href="/propguru/evaluation" class="sidebar-link {% if active_page == 'propguru_evaluation' %}active{% endif %}">
    <span class="icon">🔍</span> Evaluation
  </a>
  <a href="/propguru/master" class="sidebar-link {% if active_page == 'propguru_master' %}active{% endif %}">
    <span class="icon">🗂</span> Master Data
  </a>
  <a href="/propguru/simulation" class="sidebar-link {% if active_page == 'propguru_simulation' %}active{% endif %}">
    <span class="icon">🎮</span> Simulation
  </a>
</div>
{% endif %}
```

**`.env` / `settings.py` default** — update the default to include `propguru`:
```
COMPANIES_TO_SHOW=sandhar,fundly,propguru
```

No code change required — toggling Propguru's nav on/off is purely a config value.

Each Propguru page route passes the correct `active_page` key to the template (e.g., `"propguru_dashboard"`, `"propguru_deals"`, `"propguru_evaluation"`, `"propguru_master"`, `"propguru_simulation"`), following the same pattern as `sandhar_pages.py`.

---

## 15. Implementation Phases

Dependencies drive the order: database models must exist before tools can query them; tools must be registered before YAML agents can use them; agents must work before the UI has anything meaningful to show; the refinement canvas depends on all of the above being stable.

---

### Phase 1 — Data Foundation
**Goal:** The database exists, master data is browsable, seed/reset works. No agents yet.

**Why first:** Every subsequent piece — tools, agents, UI — depends on the DB schema and seed data being in place. Phase 1 can be independently verified by browsing the master data page and running the seed endpoint.

| Task | Files |
|---|---|
| DB migrations 016, 017, 018 | `alembic/versions/016_propguru_master.py` `017_propguru_deals.py` `018_propguru_evaluation.py` |
| ORM models | Append all 7 Propguru models to `src/agri_agent/db/models.py` |
| Simulation seed + reset endpoints | `src/agri_agent/api/routes/propguru/simulation.py` |
| Master data API routes | `src/agri_agent/api/routes/propguru/master.py` (CPs, criteria, properties) |
| Navigation — `base.html` Propguru block | `src/agri_agent/templates/base.html` |
| `settings.py` default update | `companies_to_show` default → `"sandhar,fundly,propguru"` |
| Master Data UI page | `src/agri_agent/templates/propguru/master.html` |
| Simulation UI page | `src/agri_agent/templates/propguru/simulation.html` |
| Page routes for master + simulation | `src/agri_agent/api/routes/propguru/pages.py` |
| Register propguru routers in app | `src/agri_agent/api/app.py` |

**Verification:** Run `POST /api/v1/propguru/simulation/seed` → visit `/propguru/master` → all 30 criteria, 8 channel partners, 10 properties are visible. Propguru section appears in sidebar when `COMPANIES_TO_SHOW` includes `propguru`.

---

### Phase 2 — Agent Pipeline + Deal Flow
**Goal:** Create a deal, trigger the evaluation agent pipeline, review the output, approve it. Full HITL flow end-to-end. No refinement canvas yet.

**Why second:** Agent tools call the deal and evaluation API endpoints, so those routes must exist. The supervisor + 4 worker agents then use those tools. The UI surfaces the results.

| Task | Files |
|---|---|
| Deal API routes (CRUD + stage transitions) | `src/agri_agent/api/routes/propguru/deals.py` |
| Evaluation API routes (trigger, get report, scores, approve, reject) | `src/agri_agent/api/routes/propguru/evaluation.py` |
| Agent tools — deals | `src/agri_agent/agent/tools/propguru/deals.py` (`propguru_get_deal`, `propguru_get_property_details`, `propguru_list_deals`) |
| Agent tools — evaluation | `src/agri_agent/agent/tools/propguru/evaluation.py` (`propguru_get_criteria`, `propguru_get_market_comp`, `propguru_save_evaluation_score`, `propguru_calculate_price`, `propguru_propose_evaluation`, `propguru_create_evaluation_report`) |
| Register propguru tools in tool registry | `src/agri_agent/agent/tools/__init__.py` |
| Agent YAML configs — 5 agents | `propguru-evaluation-supervisor.yaml` `propguru-data-collector.yaml` `propguru-market-analyst.yaml` `propguru-scorer.yaml` `propguru-evaluator.yaml` |
| Deals UI page | `src/agri_agent/templates/propguru/deals.html` |
| Evaluation UI page (trigger + review + approve/reject, no canvas) | `src/agri_agent/templates/propguru/evaluation.html` |
| Dashboard UI page | `src/agri_agent/templates/propguru/dashboard.html` |
| Page routes for dashboard, deals, evaluation | `src/agri_agent/api/routes/propguru/pages.py` (extend from Phase 1) |
| Scenarios s1-normal, s2-luxury, s3-missing-data, s5-market-drop | `src/agri_agent/api/routes/propguru/simulation.py` (extend from Phase 1) |

**Verification:** Trigger scenario s1-normal → seed a deal → click "Start Evaluation" → watch agent pipeline progress → evaluation report appears with 30 scores and a recommended price → click Approve → deal stage advances to `evaluation_done` → `target_acquisition_price` set on the deal.

---

### Phase 3 — Refinement Canvas + Tests
**Goal:** Analyst can open a conversational canvas on any pending evaluation, adjust individual scores through chat, set a final price override, and approve from within the canvas. Full test coverage.

**Why last:** The refinement canvas depends on the evaluation agent and HITL action both being stable (Phase 2). The refiner tools call the same evaluation endpoints added in Phase 2 — no new API routes needed.

| Task | Files |
|---|---|
| Refinement tools | `src/agri_agent/agent/tools/propguru/evaluation_refiner.py` (`propguru_refine_get_evaluation`, `propguru_refine_update_score`, `propguru_refine_explain_score`, `propguru_refine_update_final_price`) |
| Register refinement tools | `src/agri_agent/agent/tools/__init__.py` |
| Refinement agent YAML | `agents/configs/propguru-evaluation-refiner.yaml` |
| Feature flags on evaluator YAML | `enable_refinement: true`, `refinement_agent: propguru-evaluation-refiner`, `refinement_preview: propguru-evaluation` |
| Preview partial template | `src/agri_agent/templates/propguru/_refine_preview_propguru-evaluation.html` |
| Preview server route | `src/agri_agent/api/routes/propguru/pages.py` — `GET /propguru/evaluation/{report_id}/refine-preview` |
| Refinement canvas UI (SSE chat, deep-link, Back button) | `src/agri_agent/templates/propguru/evaluation.html` (extend from Phase 2) |
| Scenario s4-analyst-override | `src/agri_agent/api/routes/propguru/simulation.py` |
| Test suite | `tests/test_propguru_evaluation.py` `tests/test_propguru_simulation.py` |

**Verification:** Open any `pending_review` evaluation → click "✦ Refine with AI" → canvas opens with score breakdown on the left and chat on the right → type "Metro station is only 0.6 km" → agent updates CRIT-011 score → recommended price updates in preview → click Approve → session saved, deal approved. URL contains `?refine=<action_id>`. Browser Back closes the canvas without ending the session.

---

### Dependency Summary

```
Phase 1 ──► Phase 2 ──► Phase 3
  │              │            │
  DB models      Tools        Refiner tools
  Migrations     Agents       Refiner YAML
  Seed data      Routes       Canvas UI
  Master UI      Deal UI      Preview partial
  Nav change     Eval UI      Tests
                 Dashboard
```

Nothing in Phase 2 can start until migrations and the seed function are working (Phase 1). Nothing in Phase 3 can start until the base evaluation pipeline produces a valid `AgentAction` in the inbox (Phase 2). Each phase is independently demonstrable before the next begins.

---

## 16. Implementation Considerations

1. **No custom scoring algorithm in code.** The formula (`score_factor × max_premium`) is stored in constants. Weights are in the DB (editable per criteria record). This keeps the "trade secret" aspect configurable without code changes.

2. **Market data is simulated.** In production, this would call housing.com or similar APIs. For the POC, `propguru_market_comps` is seeded with realistic but fabricated data. The tool `propguru_get_market_comp` queries this table — the integration layer is a future concern.

3. **Criteria are soft-coded.** The 30 data points are DB records, not code. Propguru can add/remove/reweight criteria from the master UI without a deployment.

4. **Follows Sandhar patterns exactly.** No new platform patterns introduced. The HITL, refinement canvas, AgentAction mechanics, Celery execution, YAML config loading, and `companies_to_show` nav gating all reuse existing platform code.

5. **`companies` field in YAML.** All propguru YAMLs declare `companies: [propguru]` to allow future company-scoped filtering on the agents page.

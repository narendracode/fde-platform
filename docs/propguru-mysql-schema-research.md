# Propguru CRM — MySQL Schema Research
## Property Inventory & Valuation Data Points

**Database**: `fde_dev` (MySQL 8.4)
**Scope**: Understanding what data is captured per property unit and what signals are available for AI-based valuation
**Status**: Research / Pre-build analysis

---

## 1. The Core Mental Model

Every property deal is one row in the `inventories` table. That single row is the **unit of evaluation**. Everything else — society, locality, visits, offers, features — links back to it via foreign keys.

```
cities
  └── micro_markets          (13 sub-markets: Gaur City, Greater Noida West, Central Noida...)
        └── localities        (132 areas)
              └── inventory_societies     (873 housing complexes)
                    └── inventory_society_towers   (1,166 towers)
                          └── inventories           (684 units) ← unit of evaluation
                                ├── inventory_offer_negotiations
                                ├── inventory_customer_visits
                                ├── inventory_status_history
                                ├── inventory_feature_map
                                ├── inventory_view_map
                                ├── inventory_observation_map
                                └── inventory_extra_room_map
```

---

## 2. What Describes a Unit — Input Signals by Category

### 2.1 Physical Unit Attributes

The most consistently populated fields. Present on 95%+ of units.

| Data Point | Table / Field | Population | Values |
|---|---|---|---|
| BHK type | `inventories.bhk_id` → `inventory_bhk_types` | ~100% | 1, 1.5, 2, 2.5, 3, 3.5, 4, 4.5, 5 BHK |
| Carpet area (sqft) | `inventories.unit_size_id` → `inventory_society_unit_sizes.size` | 89% (609/684) | Varies by society |
| Floor number | `inventories.floor_number` | 99% (677/684) | Integer |
| Total floors | `inventories.total_floor` | 60% (412/684) | Integer |
| Furnishing status | `inventories.furnished_status_id` → `inventory_furnishing_types` | 96% (654/684) | Unfurnished / Basic Semi-Furnished / Semi-Furnished / Good Semi-Furnished / Fully-Furnished |
| Balcony facing | `inventories.balcony_facing_id` → `inventory_facing_types` | 65% (446/684) | North / South / East / West / North-East / North-West / South-East / South-West |
| Exit facing | `inventories.exit_facing_id` → `inventory_facing_types` | Lower than balcony | Same 8 directions |
| Covered parking | `inventories.covered_parking` | 23% non-zero (160/684) | Integer count |
| Open parking | `inventories.open_parking` | Similar | Integer count |
| Registry status | `inventories.registry_status_id` → `inventory_registry_status_types` | 98% (670/684) | Registered / Unregistered |
| Occupancy status | `inventories.occupancy_status_id` → `inventory_occupancy_status_types` | 44% (299/684) | Owner Occupied / Tenant Occupied / Vacant |
| Tower | `inventories.society_tower_id` → `inventory_society_towers` | 76% (523/684) | Tower name within society |
| POC type | `inventories.poc_type_id` → `inventory_poc_types` | 61% (420/684) | Channel Partner / Customer |

**Many-to-many unit attributes** — low coverage but high quality when present:

| Attribute Category | Junction Table | Lookup Table | Units Covered | Values |
|---|---|---|---|---|
| Interior features | `inventory_feature_map` | `inventory_feature_types` | 33 units | Modular Kitchen, False Ceiling, Wood Flooring, Spacious Unit, No Seepage, New Interiors |
| Views | `inventory_view_map` | `inventory_view_types` | 147 units | Society / Garden / Park / Pool / Road / Lake / Open View |
| Extra rooms | `inventory_extra_room_map` | `inventory_extra_room_types` | 29 units | Puja Room / Study Room / Store Room |
| Condition observations | `inventory_observation_map` | `inventory_observation_types` | 16 units | Minor/Extensive Seepage, Damaged Flooring, Damaged Walls, Needs Painting, Needs Repairs, Old/New Interiors |

---

### 2.2 Price Data on the Unit

This is where evaluation lives. Multiple price fields exist on the `inventories` row — each with a different meaning and reliability.

| Field | Meaning | Population | Notes |
|---|---|---|---|
| `customer_asking_price` | What the seller is asking | ~100% | Always the first known data point. Starting anchor for negotiation. |
| `initial_customer_asking_price` | Snapshot of original ask before negotiations | ~100% | Difference vs. `customer_asking_price` shows negotiation movement |
| `competition_pickup_price` | Price a competing buyer offered | 11% (73/684) | Strong signal — reveals real demand from other market participants |
| `proho_tool_valuation` | Propguru's internal pricing tool output (hand-entered by RM) | 47% (324/684) | Human benchmark — the most consistently available valuation reference |
| `acre_99_listing_range` | Price range from 99Acres, stored as free text | 50% (342/684) | Format: "75-82L", "90-1.05CR" — needs parsing to numeric |
| `housing_listing_range` | Price range from Housing.com, stored as free text | 3% (21/684) | Same format, very sparse |
| `registry_maxima` | Maximum declared value in last registry transaction | 11% (72/684) | Last known government-registered transaction value |
| `last_registry_month` | Month of last registry | 10% (70/684) | e.g., "January", "February" |
| `last_registry_year` | Year of last registry | 10% | Integer year |
| `sourcing_price` | What Propguru actually paid — ground truth acquisition price | 3% (22/684) | **Most valuable label** — grows with deal volume |
| `sourcing_brokerage` | Brokerage paid to sourcing CP | On sourced deals | Additional cost on top of sourcing_price |
| `cost_to_company` | Total cost including all expenses | Populated, often 0 | |
| `listing_price_to_sell` | Price Propguru lists the unit at for resale | Low | Propguru's asking price on exit side |
| `selling_price` | Final sale price | 1% (9/684) | **Ground truth exit label** — very sparse today |
| `selling_brokerage` | Brokerage paid to selling CP | Low | |

---

### 2.3 Geographic and Society Context

| Data Point | Source | What It Tells You |
|---|---|---|
| Micro market | `micro_markets.name` | Biggest price differentiator — Gaur City vs. Greater Noida West vs. Central Noida each have distinct price bands |
| Locality | `localities.name` | Sub-area (e.g., Sector 16C Greater Noida West) |
| Society name | `inventory_societies.name` | Housing complex — strongest comparable anchor |
| Builder | `inventory_societies.builder` | Brand quality (Gaur, Mahagun, Eros, Supertech, Paramount, etc.) |
| Society age | `inventory_societies.age` | Building age — sparse (NULL on most societies currently) |
| 99Acres listing URL | `inventory_societies.acres_99_listing_url` | Direct market listing link per society |

The geo chain is well-populated and clean. It is the primary grouping for comparable analysis.

---

### 2.4 Deal Activity Signals

These reveal how the market is responding to a specific unit.

| Signal | Table | Coverage | What It Tells You |
|---|---|---|---|
| Visit count | `inventory_customer_visits` | 886 visits across 26 units | Demand intensity — high visits = market interest |
| Visit type | `visit_type` field | First Visit (838) / Revisit (35) | Revisits signal stronger buyer intent |
| Visit status | `visit_status` field | Completed (873) / Upcoming / Cancelled | |
| CP on visit | `inventory_visit_cp_map` | 791 rows | Which CPs are bringing buyers to this unit |
| Offer — company | `inventory_offer_negotiations` (offer_from='Company') | 121 rows | What Propguru is willing to pay |
| Offer — customer/CP | `inventory_offer_negotiations` (offer_from='Customer') | 91 rows | What seller/CP counter-offers |
| Accepted offers | `offer_status = 'Accepted'` | 6 rows | Final agreed price — negotiation ground truth |
| Sanctioned amount | `inventory_offer_negotiations.sanctioned_amount` | Sparse | Bank sanction on the unit |
| Deal notes | `inventory_deal_notes` | 4 rows | Freetext RM observations — qualitative |

---

### 2.5 Sourcing Score (Pre-Existing)

`inventories.sourcing_score` is a pre-computed integer score on 68 units. Values observed: 68, 72, 76, 80, 84. The scoring logic is not in the database — it is applied externally before unit entry. It appears to represent lead quality. Most high-scoring units (84) have garbled price data suggesting they are test records. For real sourced deals this field is mostly absent, so it cannot be relied on as an input today.

---

## 3. The Deal Lifecycle — Where Evaluation Fits

The `inventory_status_types` table defines two sequential pipelines with 24 statuses total.

### Sourcing Pipeline (buying from seller)

```
Submitted (220 units reached)
    ↓
Owner Onboarding (35 units)
    ↓
Evaluation (53 units) ← AI EVALUATION OPPORTUNITY
    ↓
Negotiations Ongoing (81 units) ← offer_negotiations rows created here
    ↓
Token Initiated (4)
    ↓
TA Executed (30) → Documents Received (9) → DD Passed (9) → AMA Executed (27) → Key Received (21)
    ↓
[sourcing_price set on inventories row]
```

Terminal statuses: `Lost` (13 units), `Rejected` (52 units), `Waitlist` (9), `Awaiting Details` (5)

### Selling Pipeline (after acquisition)

```
Refurbishment in Progress
    ↓
Ready for Visits ← inventory_customer_visits created here
    ↓
Buyer Token Received (13)
    ↓
ATS Executed (9) → Bank NOC Applied → Builder NOC Applied → TM Applied → Registry Done (8)
    ↓
[selling_price set on inventories row]
```

**Funnel reality**: 220 submitted → 53 reached Evaluation → 27 AMA executed → 9 final Registry Done. Roughly 25% of submitted leads convert to acquisition; ~4% complete the full buy-refurbish-sell cycle.

Every status transition is logged in `inventory_status_history` with timestamp, changed_by, and remarks (707 rows total).

---

## 4. Reliable vs. Sparse — Practical Data Availability

### Tier 1 — Always Available (build valuation on these)

| Signal | Field | Reliability |
|---|---|---|
| BHK type | `bhk_id` → bhk name | ✅ ~100% |
| Carpet sqft | `unit_size_id` → size | ✅ 89% |
| Floor number | `floor_number` | ✅ 99% |
| Furnishing | `furnished_status_id` | ✅ 96% |
| Registry status | `registry_status_id` | ✅ 98% |
| Society | `society_name_id` | ✅ 100% |
| Locality + Micro market | via society | ✅ 100% |
| Seller asking price | `customer_asking_price` | ✅ ~100% |

### Tier 2 — Available on ~Half the Units (use as supporting signals)

| Signal | Field | Reliability |
|---|---|---|
| Balcony facing | `balcony_facing_id` | ⚠️ 65% |
| Tower | `society_tower_id` | ⚠️ 76% |
| Occupancy status | `occupancy_status_id` | ⚠️ 44% |
| Propguru internal valuation | `proho_tool_valuation` | ⚠️ 47% |
| 99Acres listing range | `acre_99_listing_range` | ⚠️ 50% (text, needs parsing) |
| Competitor offer price | `competition_pickup_price` | ⚠️ 11% (high signal when present) |
| Registry transaction data | `registry_maxima` + month/year | ⚠️ 10% |

### Tier 3 — Sparse Today, Critical as Volume Grows (ground truth labels)

| Signal | Field | Current Coverage | Role |
|---|---|---|---|
| Sourcing price (buy) | `sourcing_price` | 3% (22 units) | Training label: what Propguru paid |
| Selling price (exit) | `selling_price` | 1% (9 units) | Training label: what Propguru sold for |
| Accepted offer price | `offer_negotiations` where `offer_status='Accepted'` | 6 units | Negotiation ground truth |
| Views, features, observations | many-to-many maps | 16–147 units | Condition/quality signals |

---

## 5. Societies with Most Data — Best Starting Point

Top societies by unit volume with price data:

| Society | Micro Market | Units | Avg Asking Price | Sourced Units | Sold Units |
|---|---|---|---|---|---|
| Gaur 14th Avenue | Gaur City | 70 | ₹1.04 Cr | 1 | 1 |
| Gaur 11th Avenue | Gaur City | 34 | — | 0 | 0 |
| Panchsheel Greens II | Greater Noida West | 29 | ₹84L | 2 | 2 |
| Eros Sampoornam | Greater Noida West | 24 | ₹91L | 2 | 1 |
| Mahagun Mywoods Phase 1 | Gaur City | 23 | ₹1.53 Cr | 0 | 0 |
| Galaxy Vega | Greater Noida West | — | — | 1 | 1 |
| Wall Rock Aishwaryam | Greater Noida West | 13 | ₹87L | 2 | 2 |
| Galaxy Royale | Gaur City | 12 | ₹77L | 1 | 1 |
| Nirala Aspire | Greater Noida West | 17 | ₹1.01 Cr | 1 | 0 |

---

## 6. The Key Data Gap

There is **no standalone market transactions table** in MySQL. The closest signals are `registry_maxima`, `last_registry_month`, and `last_registry_year` — all hand-entered by RMs on the `inventories` row. The system has no systematic feed of comparable sales from 99Acres, Housing.com, or the government registry.

This means the AI evaluation will need to:
1. Use `proho_tool_valuation` + `acre_99_listing_range` as the primary external market anchors (available on ~50% of units)
2. Use internal comparables — `sourcing_price` of similar units in the same society — as the transaction anchor (requires cross-unit querying within the same `society_name_id`)
3. Use `offer_negotiations` history as a negotiation signal (price trajectory from ask to accepted)

Closing this gap by adding a `market_transactions` table per society (or ingesting from an external API) would be the highest-leverage schema addition before building AI valuation.

---

## 7. Recommended Evaluation Data Model for AI

Based on all of the above, here is the minimal data extraction needed per unit to evaluate it:

```
Unit Context
├── inventory_id, inventory_name, flat_number
├── bhk (name), sqft (size), floor_number, total_floor
├── furnishing (name), facing (name), parking (covered + open)
├── registry_status (name), occupancy_status (name)
├── society (name, builder, age, 99acres_url)
├── locality (name), micro_market (name), city (name)
└── tower (name)

Price Signals
├── customer_asking_price          ← always available
├── proho_tool_valuation           ← 47% — internal benchmark
├── acre_99_listing_range          ← 50% — external market range (parse text)
├── competition_pickup_price       ← 11% — competitor signal
├── registry_maxima + month/year   ← 10% — last transaction anchor
└── sourcing_price of comparable units in same society ← cross-unit query

Deal Context
├── current status in sourcing pipeline
├── offer_negotiations: all offers, directions, accepted price if any
├── visit count and revisit count
└── deal_notes (freetext)

Qualitative Signals (when present)
├── views (society / garden / park / pool / open)
├── features (modular kitchen / false ceiling / new interiors)
├── observations (seepage / damaged flooring / needs painting)
└── extra rooms (puja / study / store)
```

This is the complete input surface available from the current MySQL schema for AI-based property valuation.

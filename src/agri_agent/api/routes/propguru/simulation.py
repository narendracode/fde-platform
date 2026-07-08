"""Propguru simulation control endpoints (demo mode only)."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from agri_agent.api.dependencies import verify_api_key
from agri_agent.db.models import (
    PropguruChannelPartner,
    PropguruDeal,
    PropguruEvaluationCriteria,
    PropguruMarketComp,
    PropguruProperty,
)
from agri_agent.db.session import get_session

router = APIRouter(prefix="/api/v1/propguru", tags=["propguru"])

# ── Seed constants ─────────────────────────────────────────────────────────────

_CHANNEL_PARTNERS = [
    {"cp_code": "CP-001", "name": "Rahul Properties", "cp_type": "sourcing", "phone": "+91-98201-11001", "email": "rahul@rahulproperties.in", "city": "Mumbai", "commission_pct": 2.0},
    {"cp_code": "CP-002", "name": "Sharma Realty", "cp_type": "distribution", "phone": "+91-98201-22002", "email": "info@sharmarealty.in", "city": "Mumbai", "commission_pct": 1.5},
    {"cp_code": "CP-003", "name": "Bangalore Home Finders", "cp_type": "both", "phone": "+91-98451-33003", "email": "contact@bhfindia.com", "city": "Bengaluru", "commission_pct": 2.0},
    {"cp_code": "CP-004", "name": "South Delhi Estates", "cp_type": "sourcing", "phone": "+91-98110-44004", "email": "deals@sdestates.co.in", "city": "Delhi", "commission_pct": 2.5},
    {"cp_code": "CP-005", "name": "NCR Properties Hub", "cp_type": "distribution", "phone": "+91-98110-55005", "email": "ncr@propertyhub.in", "city": "Delhi", "commission_pct": 1.75},
    {"cp_code": "CP-006", "name": "Pune Realty Group", "cp_type": "sourcing", "phone": "+91-98901-66006", "email": "leads@punerealtygroup.com", "city": "Pune", "commission_pct": 2.0},
    {"cp_code": "CP-007", "name": "City Square Realty", "cp_type": "both", "phone": "+91-98451-77007", "email": "hello@citysquare.in", "city": "Bengaluru", "commission_pct": 2.25},
    {"cp_code": "CP-008", "name": "Prime Homes India", "cp_type": "distribution", "phone": "+91-98201-88008", "email": "sales@primehomes.in", "city": "Mumbai", "commission_pct": 1.5},
]

_CRITERIA = [
    # Amenities (10)
    {"criterion_code": "CRIT-001", "name": "Swimming Pool", "category": "amenity", "weight": 6.0, "scoring_type": "boolean", "description": "Dedicated swimming pool within the residential complex.", "sort_order": 1},
    {"criterion_code": "CRIT-002", "name": "Clubhouse", "category": "amenity", "weight": 5.0, "scoring_type": "boolean", "description": "Clubhouse facility with indoor amenities.", "sort_order": 2},
    {"criterion_code": "CRIT-003", "name": "Gymnasium", "category": "amenity", "weight": 5.0, "scoring_type": "boolean", "description": "Well-equipped gymnasium within the complex.", "sort_order": 3},
    {"criterion_code": "CRIT-004", "name": "Children's Playground", "category": "amenity", "weight": 4.0, "scoring_type": "boolean", "description": "Dedicated children's play area.", "sort_order": 4},
    {"criterion_code": "CRIT-005", "name": "Jogging / Cycling Track", "category": "amenity", "weight": 3.0, "scoring_type": "boolean", "description": "Jogging or cycling track within the complex.", "sort_order": 5},
    {"criterion_code": "CRIT-006", "name": "Indoor Games Room", "category": "amenity", "weight": 2.0, "scoring_type": "boolean", "description": "Indoor games room (table tennis, billiards, etc.).", "sort_order": 6},
    {"criterion_code": "CRIT-007", "name": "Tennis / Badminton Court", "category": "amenity", "weight": 3.0, "scoring_type": "boolean", "description": "Outdoor or indoor sports court.", "sort_order": 7},
    {"criterion_code": "CRIT-008", "name": "Landscaped Garden", "category": "amenity", "weight": 4.0, "scoring_type": "boolean", "description": "Maintained landscaped garden or green zone.", "sort_order": 8},
    {"criterion_code": "CRIT-009", "name": "Multipurpose Hall", "category": "amenity", "weight": 2.0, "scoring_type": "boolean", "description": "Hall available for events, meetings, and gatherings.", "sort_order": 9},
    {"criterion_code": "CRIT-010", "name": "Rooftop / Terrace Access", "category": "amenity", "weight": 3.0, "scoring_type": "boolean", "description": "Usable rooftop or terrace common area.", "sort_order": 10},
    # Location (10)
    {"criterion_code": "CRIT-011", "name": "Proximity — Metro / Railway Station", "category": "location", "weight": 8.0, "scoring_type": "proximity_km", "description": "Walking/driving distance to nearest metro or railway station. Score: <0.5km=5, 0.5-1km=4, 1-2km=3, 2-4km=2, >4km=1.", "sort_order": 11},
    {"criterion_code": "CRIT-012", "name": "Proximity — Highway / Expressway", "category": "location", "weight": 5.0, "scoring_type": "proximity_km", "description": "Distance to major highway or expressway. Score: <1km=5, 1-3km=4, 3-5km=3, 5-10km=2, >10km=1.", "sort_order": 12},
    {"criterion_code": "CRIT-013", "name": "Proximity — Airport", "category": "location", "weight": 5.0, "scoring_type": "proximity_km", "description": "Distance to nearest international airport. Score: <10km=5, 10-20km=4, 20-35km=3, 35-50km=2, >50km=1.", "sort_order": 13},
    {"criterion_code": "CRIT-014", "name": "Proximity — Top-rated School", "category": "location", "weight": 7.0, "scoring_type": "proximity_km", "description": "Distance to nearest reputed school (CBSE/ICSE/IB). Score: <0.5km=5, 0.5-1km=4, 1-2km=3, 2-4km=2, >4km=1.", "sort_order": 14},
    {"criterion_code": "CRIT-015", "name": "Proximity — Hospital", "category": "location", "weight": 7.0, "scoring_type": "proximity_km", "description": "Distance to nearest multi-specialty hospital. Score: <0.5km=5, 0.5-1.5km=4, 1.5-3km=3, 3-5km=2, >5km=1.", "sort_order": 15},
    {"criterion_code": "CRIT-016", "name": "Proximity — Mall / Shopping Centre", "category": "location", "weight": 6.0, "scoring_type": "proximity_km", "description": "Distance to nearest major mall or shopping centre. Score: <0.5km=5, 0.5-1.5km=4, 1.5-3km=3, 3-5km=2, >5km=1.", "sort_order": 16},
    {"criterion_code": "CRIT-017", "name": "Proximity — Park / Greenery", "category": "location", "weight": 5.0, "scoring_type": "proximity_km", "description": "Distance to public park or significant green area. Score: <0.3km=5, 0.3-0.8km=4, 0.8-2km=3, 2-4km=2, >4km=1.", "sort_order": 17},
    {"criterion_code": "CRIT-018", "name": "Proximity — IT / Business Park", "category": "location", "weight": 7.0, "scoring_type": "proximity_km", "description": "Distance to major IT or business park (employment hub). Score: <1km=5, 1-3km=4, 3-6km=3, 6-10km=2, >10km=1.", "sort_order": 18},
    {"criterion_code": "CRIT-019", "name": "Public Bus Connectivity", "category": "location", "weight": 4.0, "scoring_type": "scale_1_5", "description": "Quality of bus connectivity. 5=excellent (multiple routes, frequent), 1=none.", "sort_order": 19},
    {"criterion_code": "CRIT-020", "name": "Daily Essentials / Market Access", "category": "location", "weight": 5.0, "scoring_type": "proximity_km", "description": "Distance to nearest supermarket or daily essentials market. Score: <0.3km=5, 0.3-0.8km=4, 0.8-1.5km=3, 1.5-3km=2, >3km=1.", "sort_order": 20},
    # Property (5)
    {"criterion_code": "CRIT-021", "name": "Floor Level", "category": "property", "weight": 4.0, "scoring_type": "scale_1_5", "description": "Floor position. 5=high floor with views (8+), 4=mid-high (5-7), 3=mid (3-4), 2=low (2), 1=ground floor.", "sort_order": 21},
    {"criterion_code": "CRIT-022", "name": "Facing Direction", "category": "property", "weight": 4.0, "scoring_type": "scale_1_5", "description": "Property facing direction. 5=East, 4=North, 3=North-East, 2=West, 1=South.", "sort_order": 22},
    {"criterion_code": "CRIT-023", "name": "Property Age", "category": "property", "weight": 6.0, "scoring_type": "scale_1_5", "description": "Age of the building. 5=0-3 years, 4=3-7 years, 3=7-12 years, 2=12-20 years, 1=20+ years.", "sort_order": 23},
    {"criterion_code": "CRIT-024", "name": "Covered Parking", "category": "property", "weight": 5.0, "scoring_type": "scale_1_5", "description": "Parking availability. 5=2+ covered slots, 4=1 covered slot, 3=1 open slot, 2=visitor parking only, 1=none.", "sort_order": 24},
    {"criterion_code": "CRIT-025", "name": "Power Backup", "category": "property", "weight": 3.0, "scoring_type": "boolean", "description": "100% power backup available in the unit and common areas.", "sort_order": 25},
    # Society (5)
    {"criterion_code": "CRIT-026", "name": "Gated Community with Security", "category": "society", "weight": 6.0, "scoring_type": "boolean", "description": "Fully gated complex with 24/7 security personnel and CCTV.", "sort_order": 26},
    {"criterion_code": "CRIT-027", "name": "Society Type", "category": "society", "weight": 5.0, "scoring_type": "scale_1_5", "description": "Society classification. 5=premium integrated township, 4=branded developer society, 3=standard society, 2=old co-operative, 1=standalone independent.", "sort_order": 27},
    {"criterion_code": "CRIT-028", "name": "Lift Availability", "category": "society", "weight": 4.0, "scoring_type": "boolean", "description": "Functional passenger lift in the building.", "sort_order": 28},
    {"criterion_code": "CRIT-029", "name": "Water Supply Quality", "category": "society", "weight": 4.0, "scoring_type": "scale_1_5", "description": "Water availability and quality. 5=24/7 municipal + backup, 4=regular municipal with backup, 3=timed supply with tank, 2=partial supply, 1=borewell only.", "sort_order": 29},
    {"criterion_code": "CRIT-030", "name": "Society Maintenance Quality", "category": "society", "weight": 5.0, "scoring_type": "scale_1_5", "description": "Overall maintenance of common areas and building. 5=excellent, 1=poor.", "sort_order": 30},
]

_PROPERTIES = [
    {"property_code": "PROP-001", "address_line1": "Prestige Tech Park Road", "city": "Bengaluru", "locality": "Whitefield", "pincode": "560066", "property_type": "apartment", "carpet_area_sqft": 1250.0, "built_up_area_sqft": 1480.0, "bedrooms": 3, "bathrooms": 2, "floor_number": 8, "total_floors": 15, "building_age_years": 5, "facing": "east", "latitude": 12.9698, "longitude": 77.7500},
    {"property_code": "PROP-002", "address_line1": "80 Feet Road, 5th Block", "city": "Bengaluru", "locality": "Koramangala", "pincode": "560034", "property_type": "apartment", "carpet_area_sqft": 950.0, "built_up_area_sqft": 1100.0, "bedrooms": 2, "bathrooms": 2, "floor_number": 3, "total_floors": 10, "building_age_years": 8, "facing": "north", "latitude": 12.9352, "longitude": 77.6245},
    {"property_code": "PROP-003", "address_line1": "Hill Road, Bandra", "city": "Mumbai", "locality": "Bandra West", "pincode": "400050", "property_type": "apartment", "carpet_area_sqft": 1800.0, "built_up_area_sqft": 2100.0, "bedrooms": 4, "bathrooms": 3, "floor_number": 12, "total_floors": 20, "building_age_years": 3, "facing": "west", "latitude": 19.0544, "longitude": 72.8272},
    {"property_code": "PROP-004", "address_line1": "Andheri Kurla Road", "city": "Mumbai", "locality": "Andheri East", "pincode": "400069", "property_type": "apartment", "carpet_area_sqft": 1100.0, "built_up_area_sqft": 1300.0, "bedrooms": 3, "bathrooms": 2, "floor_number": 6, "total_floors": 18, "building_age_years": 7, "facing": "south", "latitude": 19.1136, "longitude": 72.8697},
    {"property_code": "PROP-005", "address_line1": "Sohna Road, Sector 49", "city": "Gurgaon", "locality": "Gurgaon Sector 49", "pincode": "122018", "property_type": "apartment", "carpet_area_sqft": 850.0, "built_up_area_sqft": 1010.0, "bedrooms": 2, "bathrooms": 2, "floor_number": 4, "total_floors": 12, "building_age_years": 10, "facing": "east", "latitude": 28.4089, "longitude": 77.0420},
    {"property_code": "PROP-006", "address_line1": "ITPL Main Road", "city": "Bengaluru", "locality": "Whitefield", "pincode": "560048", "property_type": "independent_house", "carpet_area_sqft": 2400.0, "built_up_area_sqft": 2800.0, "bedrooms": 4, "bathrooms": 4, "floor_number": None, "total_floors": None, "building_age_years": 15, "facing": "east", "latitude": 12.9780, "longitude": 77.7480},
    {"property_code": "PROP-007", "address_line1": "Sarjapur Road, 7th Block", "city": "Bengaluru", "locality": "Koramangala", "pincode": "560034", "property_type": "apartment", "carpet_area_sqft": 1400.0, "built_up_area_sqft": 1650.0, "bedrooms": 3, "bathrooms": 3, "floor_number": 10, "total_floors": 12, "building_age_years": 2, "facing": "north", "latitude": 12.9328, "longitude": 77.6227},
    {"property_code": "PROP-008", "address_line1": "Versova Road", "city": "Mumbai", "locality": "Andheri East", "pincode": "400053", "property_type": "apartment", "carpet_area_sqft": 780.0, "built_up_area_sqft": 920.0, "bedrooms": 2, "bathrooms": 1, "floor_number": 2, "total_floors": 8, "building_age_years": 12, "facing": "west", "latitude": 19.1200, "longitude": 72.8380},
    {"property_code": "PROP-009", "address_line1": "Golf Course Extension Road", "city": "Gurgaon", "locality": "Gurgaon Sector 49", "pincode": "122018", "property_type": "apartment", "carpet_area_sqft": 2100.0, "built_up_area_sqft": 2450.0, "bedrooms": 4, "bathrooms": 4, "floor_number": 15, "total_floors": 22, "building_age_years": 1, "facing": "east", "latitude": 28.4150, "longitude": 77.0560},
    {"property_code": "PROP-010", "address_line1": "Kothrud Main Road", "city": "Pune", "locality": "Kothrud", "pincode": "411038", "property_type": "independent_house", "carpet_area_sqft": 1800.0, "built_up_area_sqft": 2100.0, "bedrooms": 3, "bathrooms": 3, "floor_number": None, "total_floors": None, "building_age_years": 20, "facing": "north", "latitude": 18.5074, "longitude": 73.8077},
]

_MARKET_COMPS = [
    {"locality": "Whitefield", "property_type": "apartment", "avg_price_per_sqft": 6800.0, "min_price_per_sqft": 5500.0, "max_price_per_sqft": 8500.0, "price_trend_6m_pct": 4.2, "transaction_count_6m": 45, "data_source": "housing.com"},
    {"locality": "Koramangala", "property_type": "apartment", "avg_price_per_sqft": 12500.0, "min_price_per_sqft": 10000.0, "max_price_per_sqft": 16000.0, "price_trend_6m_pct": 2.8, "transaction_count_6m": 28, "data_source": "housing.com"},
    {"locality": "Bandra West", "property_type": "apartment", "avg_price_per_sqft": 35000.0, "min_price_per_sqft": 28000.0, "max_price_per_sqft": 45000.0, "price_trend_6m_pct": 1.5, "transaction_count_6m": 15, "data_source": "housing.com"},
    {"locality": "Andheri East", "property_type": "apartment", "avg_price_per_sqft": 18500.0, "min_price_per_sqft": 15000.0, "max_price_per_sqft": 23000.0, "price_trend_6m_pct": 3.1, "transaction_count_6m": 32, "data_source": "housing.com"},
    {"locality": "Gurgaon Sector 49", "property_type": "apartment", "avg_price_per_sqft": 9200.0, "min_price_per_sqft": 7500.0, "max_price_per_sqft": 12000.0, "price_trend_6m_pct": 5.6, "transaction_count_6m": 38, "data_source": "housing.com"},
]


# ── Seed helper ────────────────────────────────────────────────────────────────

async def _seed_master_data(session: AsyncSession) -> dict[str, Any]:
    """Seed all Propguru master data. Idempotent — skips if already seeded."""
    existing = (await session.execute(
        select(PropguruChannelPartner).where(PropguruChannelPartner.cp_code == "CP-001")
    )).scalar_one_or_none()
    if existing:
        return {"status": "already seeded"}

    counts: dict[str, int] = {}

    # 1. Channel partners
    cp_map: dict[str, uuid.UUID] = {}
    for c in _CHANNEL_PARTNERS:
        cp = PropguruChannelPartner(**c, status="active")
        session.add(cp)
        await session.flush()
        cp_map[c["cp_code"]] = cp.id
    counts["channel_partners"] = len(_CHANNEL_PARTNERS)

    # 2. Evaluation criteria
    criteria_count = 0
    for c in _CRITERIA:
        session.add(PropguruEvaluationCriteria(**c, is_active=True))
        criteria_count += 1
    await session.flush()
    counts["evaluation_criteria"] = criteria_count

    # 3. Properties
    prop_map: dict[str, uuid.UUID] = {}
    for p in _PROPERTIES:
        prop = PropguruProperty(**p)
        session.add(prop)
        await session.flush()
        prop_map[p["property_code"]] = prop.id
    counts["properties"] = len(_PROPERTIES)

    # 4. Market comps
    today = date.today()
    for m in _MARKET_COMPS:
        session.add(PropguruMarketComp(**m, as_of_date=today))
    await session.flush()
    counts["market_comps"] = len(_MARKET_COMPS)

    # 5. Deals (5 across various stages for demo variety)
    _deals = [
        {"deal_code": "DEAL-001", "property_code": "PROP-001", "cp_code": "CP-001", "stage": "evaluation_pending", "sourcing_cp_commission_pct": 2.0, "lead_source": "channel_partner", "notes": "3BHK Whitefield — ready for evaluation"},
        {"deal_code": "DEAL-002", "property_code": "PROP-002", "cp_code": "CP-003", "stage": "lead", "sourcing_cp_commission_pct": 2.0, "lead_source": "channel_partner", "notes": "2BHK Koramangala — fresh lead"},
        {"deal_code": "DEAL-003", "property_code": "PROP-003", "cp_code": "CP-004", "stage": "evaluation_done", "sourcing_cp_commission_pct": 2.5, "lead_source": "channel_partner", "target_acquisition_price": 6_48_00_000.0, "notes": "4BHK Bandra — evaluation approved"},
        {"deal_code": "DEAL-004", "property_code": "PROP-007", "cp_code": "CP-007", "stage": "listed", "sourcing_cp_commission_pct": 2.25, "lead_source": "channel_partner", "target_acquisition_price": 1_82_00_000.0, "notes": "3BHK Koramangala — listed with distribution CPs"},
        {"deal_code": "DEAL-005", "property_code": "PROP-005", "cp_code": "CP-001", "stage": "lead", "sourcing_cp_commission_pct": 2.0, "lead_source": "channel_partner", "notes": "2BHK Gurgaon — awaiting basic info"},
    ]
    for d in _deals:
        prop_id = prop_map.get(d.pop("property_code"))
        cp_id = cp_map.get(d.pop("cp_code"))
        session.add(PropguruDeal(property_id=prop_id, sourcing_cp_id=cp_id, **d))
    await session.flush()
    counts["deals"] = len(_deals)

    await session.commit()
    counts["status"] = "seeded"
    return counts


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.post("/simulation/seed")
async def seed_data(
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Seed all Propguru master data (idempotent)."""
    return await _seed_master_data(session)


@router.post("/simulation/reset")
async def reset_data(
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Delete all Propguru data and reseed from scratch."""
    tables = [
        "propguru_evaluation_scores",
        "propguru_evaluation_reports",
        "propguru_deals",
        "propguru_market_comps",
        "propguru_properties",
        "propguru_evaluation_criteria",
        "propguru_channel_partners",
    ]
    for table in tables:
        await session.execute(text(f"DELETE FROM {table}"))
    await session.commit()
    result = await _seed_master_data(session)
    return {"reset": True, "seed": result}


@router.get("/simulation/scenarios")
async def list_scenarios(
    _: str = Depends(verify_api_key),
):
    """Return available demo scenarios."""
    return [
        {
            "id": "s1-normal",
            "name": "Normal Evaluation",
            "description": "Create a clean evaluation-pending deal for PROP-001 (3BHK Whitefield). Standard property, full market data, good connectivity. High-confidence evaluation expected.",
        },
        {
            "id": "s2-luxury",
            "name": "Luxury Property",
            "description": "Create an evaluation-pending deal for PROP-003 (4BHK Bandra West). All amenities present, premium locality. Premium pricing expected.",
        },
        {
            "id": "s3-missing-data",
            "name": "Incomplete Data",
            "description": "Create an evaluation-pending deal for PROP-010 (Independent House, Kothrud). Older property, independent house type — several criteria will require analyst input.",
        },
    ]


@router.post("/simulation/scenario/{scenario_id}")
async def run_scenario(
    scenario_id: str = Path(...),
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Set up a named demo scenario by creating the appropriate deal."""
    scenario_map = {
        "s1-normal": ("PROP-001", "CP-001", "DEAL-S1", "3BHK Whitefield — s1-normal scenario"),
        "s2-luxury":  ("PROP-003", "CP-004", "DEAL-S2", "4BHK Bandra West — s2-luxury scenario"),
        "s3-missing-data": ("PROP-010", "CP-006", "DEAL-S3", "Independent House Kothrud — s3-missing-data scenario"),
    }
    if scenario_id not in scenario_map:
        raise HTTPException(status_code=404, detail=f"Unknown scenario '{scenario_id}'")

    prop_code, cp_code, deal_code, notes = scenario_map[scenario_id]

    prop = (await session.execute(
        select(PropguruProperty).where(PropguruProperty.property_code == prop_code)
    )).scalar_one_or_none()
    if not prop:
        raise HTTPException(status_code=404, detail=f"Property {prop_code} not found — run /simulation/seed first")

    cp = (await session.execute(
        select(PropguruChannelPartner).where(PropguruChannelPartner.cp_code == cp_code)
    )).scalar_one_or_none()
    if not cp:
        raise HTTPException(status_code=404, detail=f"Channel partner {cp_code} not found — run /simulation/seed first")

    # Idempotent — reuse existing deal if already created for this scenario
    existing = (await session.execute(
        select(PropguruDeal).where(PropguruDeal.deal_code == deal_code)
    )).scalar_one_or_none()
    if existing:
        return {
            "scenario": scenario_id,
            "status": "already_exists",
            "deal_code": deal_code,
            "deal_id": str(existing.id),
        }

    deal = PropguruDeal(
        deal_code=deal_code,
        property_id=prop.id,
        sourcing_cp_id=cp.id,
        sourcing_cp_commission_pct=cp.commission_pct,
        stage="evaluation_pending",
        lead_source="channel_partner",
        notes=notes,
    )
    session.add(deal)
    await session.commit()
    await session.refresh(deal)

    return {
        "scenario": scenario_id,
        "status": "created",
        "deal_code": deal_code,
        "deal_id": str(deal.id),
        "property": prop_code,
        "channel_partner": cp_code,
    }

"""Fundly email marketing outreach tools — pharma retailer prospecting.

All three tools are mock implementations for local development and agent testing.
No real APIs or email infrastructure are used.
"""

from __future__ import annotations

import json
import random

from langchain_core.tools import tool

# ── Mock data ──────────────────────────────────────────────────────────────────

# Each region has a fixed pool of pharmacy names.  Using a seeded RNG keyed on
# the region name keeps results deterministic across runs for the same region.

_PHARMACY_SUFFIXES = [
    "Pharma", "Medicals", "Drug Store", "Pharmacy", "Health Hub",
    "MediCare", "LifeCare", "Wellness", "Health Plus", "Chemists",
]

_REGION_SEEDS: dict[str, list[dict]] = {
    "mumbai": [
        {"name": "Kohinoor Pharma",        "location": "Dadar, Mumbai"},
        {"name": "Shree Medicals",          "location": "Andheri West, Mumbai"},
        {"name": "Lifeline Drug Store",     "location": "Bandra, Mumbai"},
        {"name": "Apollo Health Hub",       "location": "Borivali, Mumbai"},
        {"name": "Sunrise MediCare",        "location": "Thane, Mumbai"},
        {"name": "Sai LifeCare",            "location": "Navi Mumbai"},
        {"name": "City Wellness Centre",    "location": "Malad, Mumbai"},
        {"name": "Max Health Plus",         "location": "Powai, Mumbai"},
        {"name": "Reliable Chemists",       "location": "Kurla, Mumbai"},
        {"name": "Central Pharmacy",        "location": "Fort, Mumbai"},
    ],
    "delhi": [
        {"name": "Capital Pharma",          "location": "Connaught Place, Delhi"},
        {"name": "Delhi Medicals",          "location": "Lajpat Nagar, Delhi"},
        {"name": "GreenLeaf Drug Store",    "location": "Karol Bagh, Delhi"},
        {"name": "North Star Pharmacy",     "location": "Rohini, Delhi"},
        {"name": "Medicity Health Hub",     "location": "Dwarka, Delhi"},
        {"name": "Prime MediCare",          "location": "Janakpuri, Delhi"},
        {"name": "Ashoka LifeCare",         "location": "Pitampura, Delhi"},
        {"name": "Wellness First",          "location": "Saket, Delhi"},
        {"name": "Heritage Chemists",       "location": "Old Delhi"},
        {"name": "Bharat Health Plus",      "location": "Noida Extension, Delhi NCR"},
    ],
    "bangalore": [
        {"name": "Silicon Pharma",          "location": "Koramangala, Bangalore"},
        {"name": "Garden City Medicals",    "location": "Indiranagar, Bangalore"},
        {"name": "Tech Park Drug Store",    "location": "Whitefield, Bangalore"},
        {"name": "South Block Pharmacy",    "location": "Jayanagar, Bangalore"},
        {"name": "Metro Health Hub",        "location": "Marathahalli, Bangalore"},
        {"name": "Ulsoor MediCare",         "location": "Ulsoor, Bangalore"},
        {"name": "Brigade LifeCare",        "location": "Brigade Road, Bangalore"},
        {"name": "HSR Wellness",            "location": "HSR Layout, Bangalore"},
        {"name": "Electronic City Chemists","location": "Electronic City, Bangalore"},
        {"name": "Rajaji Health Plus",      "location": "Rajajinagar, Bangalore"},
    ],
    "hyderabad": [
        {"name": "Deccan Pharma",           "location": "Banjara Hills, Hyderabad"},
        {"name": "Hitech Medicals",         "location": "HITEC City, Hyderabad"},
        {"name": "Nizami Drug Store",       "location": "Secunderabad, Hyderabad"},
        {"name": "Pearl Pharmacy",          "location": "Jubilee Hills, Hyderabad"},
        {"name": "Charminar Health Hub",    "location": "Old City, Hyderabad"},
        {"name": "Kondapur MediCare",       "location": "Kondapur, Hyderabad"},
        {"name": "Gachibowli LifeCare",     "location": "Gachibowli, Hyderabad"},
        {"name": "Ameerpet Wellness",       "location": "Ameerpet, Hyderabad"},
        {"name": "LB Nagar Chemists",       "location": "LB Nagar, Hyderabad"},
        {"name": "Kukatpally Health Plus",  "location": "Kukatpally, Hyderabad"},
    ],
    "chennai": [
        {"name": "Marina Pharma",           "location": "T. Nagar, Chennai"},
        {"name": "Kovalam Medicals",        "location": "Anna Nagar, Chennai"},
        {"name": "Besant Drug Store",       "location": "Besant Nagar, Chennai"},
        {"name": "Santhome Pharmacy",       "location": "Santhome, Chennai"},
        {"name": "Velachery Health Hub",    "location": "Velachery, Chennai"},
        {"name": "OMR MediCare",            "location": "OMR, Chennai"},
        {"name": "Tambaram LifeCare",       "location": "Tambaram, Chennai"},
        {"name": "Adyar Wellness",          "location": "Adyar, Chennai"},
        {"name": "Chromepet Chemists",      "location": "Chromepet, Chennai"},
        {"name": "Porur Health Plus",       "location": "Porur, Chennai"},
    ],
    "pune": [
        {"name": "Deccan Pharma Hub",       "location": "Deccan, Pune"},
        {"name": "Koregaon Medicals",       "location": "Koregaon Park, Pune"},
        {"name": "Wakad Drug Store",        "location": "Wakad, Pune"},
        {"name": "Hinjewadi Pharmacy",      "location": "Hinjewadi, Pune"},
        {"name": "Kothrud Health Hub",      "location": "Kothrud, Pune"},
        {"name": "Baner MediCare",          "location": "Baner, Pune"},
        {"name": "Hadapsar LifeCare",       "location": "Hadapsar, Pune"},
        {"name": "Viman Nagar Wellness",    "location": "Viman Nagar, Pune"},
        {"name": "Kharadi Chemists",        "location": "Kharadi, Pune"},
        {"name": "Pimpri Health Plus",      "location": "Pimpri, Pune"},
    ],
}

# Revenues are bucketed so the mock data contains a realistic mix of
# sub-threshold and above-threshold retailers for filter_prospects to work on.
_REVENUE_POOL = [
    82_000, 88_500, 94_000, 99_500,          # below threshold (< 100k)
    101_000, 115_000, 128_500, 145_000,       # just above threshold
    175_000, 210_000, 255_000, 310_000,       # mid range
    380_000, 425_000, 465_000, 498_000,       # high range
]


def _retailers_for_region(region: str) -> list[dict]:
    """Return a deterministic mock retailer list for the given region."""
    key = region.strip().lower()

    # Exact match first; fall back to fuzzy prefix match
    pool = _REGION_SEEDS.get(key)
    if pool is None:
        for k, v in _REGION_SEEDS.items():
            if key.startswith(k) or k.startswith(key):
                pool = v
                break

    if pool is None:
        # Unknown region — generate generic entries so the agent still works
        rng = random.Random(hash(key) & 0xFFFFFFFF)
        pool = [
            {"name": f"{region.title()} {sfx}", "location": f"{region.title()}"}
            for sfx in _PHARMACY_SUFFIXES
        ]

    rng = random.Random(hash(key) & 0xFFFFFFFF)
    revenues = rng.choices(_REVENUE_POOL, k=len(pool))

    return [
        {
            "name": entry["name"],
            "location": entry["location"],
            "yearly_revenue_usd": rev,
        }
        for entry, rev in zip(pool, revenues)
    ]


# ── Tools ──────────────────────────────────────────────────────────────────────

@tool
def list_retailers(region: str) -> str:
    """List pharma retailers in a given region.

    Returns a JSON array where each object has:
      - name              : retailer name
      - location          : city / area
      - yearly_revenue_usd: annual revenue in USD

    Args:
        region: City or region name to search (e.g. "Mumbai", "Delhi", "Bangalore").
    """
    retailers = _retailers_for_region(region)
    return json.dumps({"region": region, "retailers": retailers}, indent=2)


@tool
def filter_prospects(retailers_json: str) -> str:
    """Filter a list of retailers to those with yearly revenue >= 100,000 USD.

    Accepts the JSON string returned by list_retailers (or any JSON string
    containing a 'retailers' array with 'yearly_revenue_usd' fields).
    Returns a JSON object with the filtered list and a count.

    Args:
        retailers_json: JSON string — either the full list_retailers response
                        or a bare JSON array of retailer objects.
    """
    try:
        data = json.loads(retailers_json)
    except json.JSONDecodeError as exc:
        return json.dumps({"error": f"Invalid JSON: {exc}"})

    # Accept both {"retailers": [...]} wrapper and a bare [...]
    if isinstance(data, dict):
        raw = data.get("retailers", [])
    elif isinstance(data, list):
        raw = data
    else:
        return json.dumps({"error": "Unexpected input format"})

    THRESHOLD = 100_000
    prospects = [r for r in raw if r.get("yearly_revenue_usd", 0) >= THRESHOLD]

    return json.dumps(
        {
            "threshold_usd": THRESHOLD,
            "total_evaluated": len(raw),
            "prospects_found": len(prospects),
            "prospects": prospects,
        },
        indent=2,
    )


@tool
def send_email(to: str, subject: str, body: str) -> str:
    """Send a marketing email to a pharma retailer.

    This is a mock tool — it prints the email to the console instead of
    delivering it. Use for local testing and agent development.

    Args:
        to     : Recipient email address.
        subject: Email subject line.
        body   : Full email body (plain text or HTML).
    """
    border = "─" * 60
    output = (
        f"\n{'═' * 60}\n"
        f"  📧  MOCK EMAIL SENT\n"
        f"{'═' * 60}\n"
        f"  To      : {to}\n"
        f"  Subject : {subject}\n"
        f"{border}\n"
        f"{body}\n"
        f"{'═' * 60}\n"
    )
    print(output)
    return json.dumps({"status": "sent", "to": to, "subject": subject})

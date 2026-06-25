"""Fundly domain tools — example agriculture — crop recommendations, pest alerts, fertiliser, weather."""

from langchain_core.tools import tool

# ── Crop recommendation ───────────────────────────────────────────────────────

_CROP_DB: dict[str, dict[str, list[str]]] = {
    "rabi": {
        "loamy": ["wheat", "mustard", "chickpea", "lentil"],
        "clay": ["wheat", "barley", "peas"],
        "sandy": ["mustard", "groundnut", "sorghum"],
    },
    "kharif": {
        "loamy": ["rice", "maize", "cotton", "soybean"],
        "clay": ["rice", "sugarcane", "jute"],
        "sandy": ["millet", "groundnut", "watermelon"],
    },
    "zaid": {
        "loamy": ["cucumber", "watermelon", "muskmelon"],
        "clay": ["bitter gourd", "bottle gourd"],
        "sandy": ["watermelon", "pumpkin"],
    },
}


@tool
def get_crop_recommendation(season: str, soil_type: str, region: str = "general") -> str:
    """Get crop recommendations for a given season, soil type, and region.

    Args:
        season: Cropping season — rabi (Oct-Mar), kharif (Jun-Oct), or zaid (Mar-Jun).
        soil_type: Soil type — loamy, clay, or sandy.
        region: Geographic region (currently used for context only).
    """
    season = season.lower().strip()
    soil_type = soil_type.lower().strip()
    crops = _CROP_DB.get(season, {}).get(soil_type)
    if not crops:
        return (
            f"No data for season='{season}' + soil='{soil_type}'. "
            "Valid seasons: rabi, kharif, zaid. Valid soils: loamy, clay, sandy."
        )
    return (
        f"Recommended crops for {season.title()} season, {soil_type} soil "
        f"(region: {region}):\n  • " + "\n  • ".join(crops)
    )


# ── Pest alert ────────────────────────────────────────────────────────────────

_PEST_DB: dict[str, list[dict[str, str]]] = {
    "wheat": [
        {"pest": "Aphids", "severity": "medium", "ipm": "Neem-based spray, encourage ladybirds"},
        {"pest": "Rust (yellow/brown)", "severity": "high", "ipm": "Fungicide application, resistant varieties"},
    ],
    "rice": [
        {"pest": "Brown planthopper", "severity": "high", "ipm": "Avoid excess nitrogen, use light traps"},
        {"pest": "Stem borer", "severity": "medium", "ipm": "Pheromone traps, Trichogramma parasitoids"},
    ],
    "cotton": [
        {"pest": "Bollworm", "severity": "high", "ipm": "Bt cotton, pheromone traps"},
        {"pest": "Whitefly", "severity": "medium", "ipm": "Yellow sticky traps, imidacloprid (last resort)"},
    ],
}


@tool
def get_pest_alert(crop: str, region: str = "general") -> str:
    """Get current pest alerts and IPM recommendations for a crop.

    Args:
        crop: The crop name (e.g. wheat, rice, cotton).
        region: Region for contextual advice (currently informational).
    """
    crop = crop.lower().strip()
    alerts = _PEST_DB.get(crop)
    if not alerts:
        return (
            f"No pest data for '{crop}'. Available: {', '.join(_PEST_DB.keys())}. "
            "Use web_search for other crops."
        )
    lines = [f"Pest alerts for {crop.title()} (region: {region}):"]
    for a in alerts:
        lines.append(
            f"\n  🐛 {a['pest']}  [severity: {a['severity'].upper()}]\n"
            f"     IPM: {a['ipm']}"
        )
    return "\n".join(lines)


# ── Fertiliser calculator ─────────────────────────────────────────────────────

_NPK_REQUIREMENTS: dict[str, dict[str, float]] = {
    "wheat":  {"N": 120, "P": 60,  "K": 40},
    "rice":   {"N": 100, "P": 50,  "K": 50},
    "maize":  {"N": 150, "P": 75,  "K": 60},
    "cotton": {"N": 120, "P": 60,  "K": 60},
    "sugarcane": {"N": 250, "P": 100, "K": 120},
}

_PH_CORRECTION: dict[str, float] = {
    "acidic": 1.15,    # apply 15% more
    "neutral": 1.0,
    "alkaline": 0.90,  # reduce by 10%
}


@tool
def calculate_fertilizer(crop: str, area_hectares: float, soil_ph: str = "neutral") -> str:
    """Calculate fertiliser requirements (NPK) for a crop.

    Args:
        crop: The crop name (e.g. wheat, rice, maize, cotton, sugarcane).
        area_hectares: Field area in hectares.
        soil_ph: Soil pH category — acidic, neutral, or alkaline.
    """
    crop = crop.lower().strip()
    soil_ph = soil_ph.lower().strip()
    base = _NPK_REQUIREMENTS.get(crop)
    if not base:
        return f"No NPK data for '{crop}'. Available: {', '.join(_NPK_REQUIREMENTS.keys())}."
    factor = _PH_CORRECTION.get(soil_ph, 1.0)
    result = {k: round(v * factor * area_hectares, 1) for k, v in base.items()}
    return (
        f"Fertiliser plan for {area_hectares} ha of {crop.title()} "
        f"({soil_ph} soil, pH-factor {factor}):\n"
        f"  Nitrogen (N): {result['N']} kg\n"
        f"  Phosphorus (P): {result['P']} kg\n"
        f"  Potassium (K): {result['K']} kg\n"
        f"  → Split N into 3 applications; apply P+K as basal dose."
    )


# ── Weather data ──────────────────────────────────────────────────────────────

_MOCK_WEATHER: dict[str, dict[str, str]] = {
    "punjab":       {"temp": "28°C", "humidity": "65%", "condition": "Partly cloudy", "rainfall_7d": "12mm"},
    "maharashtra":  {"temp": "32°C", "humidity": "70%", "condition": "Sunny", "rainfall_7d": "5mm"},
    "tamil nadu":   {"temp": "35°C", "humidity": "80%", "condition": "Hot & humid", "rainfall_7d": "0mm"},
    "west bengal":  {"temp": "30°C", "humidity": "85%", "condition": "Overcast", "rainfall_7d": "40mm"},
    "rajasthan":    {"temp": "40°C", "humidity": "25%", "condition": "Hot & dry", "rainfall_7d": "0mm"},
}


@tool
def get_weather_data(location: str) -> str:
    """Get current weather and recent rainfall for a farming location.

    Args:
        location: Location name (e.g. Punjab, Maharashtra, Tamil Nadu).
    """
    key = location.lower().strip()
    data = _MOCK_WEATHER.get(key, {
        "temp": "30°C",
        "humidity": "60%",
        "condition": "Data unavailable — using generic estimate",
        "rainfall_7d": "unknown",
    })
    return (
        f"Weather for {location.title()}:\n"
        f"  Temperature: {data['temp']}\n"
        f"  Humidity:    {data['humidity']}\n"
        f"  Condition:   {data['condition']}\n"
        f"  Rainfall (last 7 days): {data['rainfall_7d']}"
    )

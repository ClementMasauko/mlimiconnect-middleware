from datetime import date, timedelta

from django.utils import timezone

from .models import HistoricalMarketPrice
from .weather import WeatherUnavailable, get_weather


KNOWLEDGE_VERSION = "2026-08-31"
KNOWLEDGE_SOURCE = "MlimiConnect safety-reviewed crop planning rules"
KNOWLEDGE = {
    "maize": {"soils": {"loamy", "clay"}, "seasons": {"rainy"}, "water": "Requires dependable moisture, especially around flowering.", "risks": ["Confirm the local planting window and seed choice with an extension worker.", "Scout the whorl regularly for fall armyworm and avoid unapproved pesticide use."]},
    "beans": {"soils": {"loamy", "clay"}, "seasons": {"rainy", "dry"}, "water": "Avoid prolonged waterlogging and severe moisture stress.", "risks": ["Use clean seed and rotate away from other legumes where disease pressure is high.", "Check the variety and planting window against local extension guidance."]},
    "groundnuts": {"soils": {"loamy", "sandy"}, "seasons": {"rainy"}, "water": "Needs moisture for establishment but well-drained soil near harvest.", "risks": ["Dry promptly and store safely to reduce aflatoxin risk.", "Confirm locally suitable seed and disease-management practices."]},
    "rice": {"soils": {"clay", "loamy"}, "seasons": {"rainy"}, "water": "Suitable only where water availability and field conditions can support the chosen production system.", "risks": ["Confirm water access before committing land or inputs.", "Do not infer irrigation suitability from a seven-day forecast alone."]},
    "cassava": {"soils": {"loamy", "sandy"}, "seasons": {"rainy", "dry"}, "water": "Established plants tolerate dry periods, but cuttings need moisture to establish.", "risks": ["Use clean planting material and inspect for mosaic or brown-streak symptoms.", "Confirm variety and harvest timing with local extension guidance."]},
}


def _market_evidence(crop, location):
    rows = HistoricalMarketPrice.objects.filter(crop=crop)
    local = rows.filter(district__iexact=location).order_by("-price_date").first()
    row = local or rows.order_by("-price_date").first()
    if not row:
        return None
    return {
        "market": row.market,
        "district": row.district,
        "price": str(row.closing_price),
        "currency": row.currency,
        "unit": row.unit,
        "price_date": row.price_date.isoformat(),
        "local_match": bool(local),
        "spatially_interpolated": row.spatially_interpolated,
    }


def build_crop_plan(*, location, soil_type, season, preferred_crop=""):
    location = " ".join(str(location or "").split())[:80]
    soil = str(soil_type or "").strip().casefold()
    selected_season = str(season or "").strip().casefold()
    preferred = str(preferred_crop or "").strip().casefold()
    if not location or soil not in {"loamy", "sandy", "clay"} or selected_season not in {"rainy", "dry"}:
        raise ValueError("Choose a supported Malawi district, soil type and season.")

    weather = None
    weather_error = ""
    try:
        weather = get_weather(location)
    except (WeatherUnavailable, ValueError) as error:
        weather_error = str(error)

    ordered = list(KNOWLEDGE)
    if preferred in KNOWLEDGE:
        ordered.remove(preferred)
        ordered.insert(0, preferred)
    recommendations = []
    for crop in ordered:
        rule = KNOWLEDGE[crop]
        matches = []
        cautions = list(rule["risks"])
        if soil in rule["soils"]:
            matches.append(f"The selected {soil} soil is generally compatible with this crop.")
        else:
            cautions.insert(0, f"The selected {soil} soil is not among this assistant's preferred soil conditions for {crop}.")
        if selected_season in rule["seasons"]:
            matches.append(f"The {selected_season} planning season is within the rule coverage for this crop.")
        else:
            cautions.insert(0, f"The {selected_season} season requires local confirmation for this crop.")
        market = _market_evidence(crop, location)
        if market:
            matches.append(f"Historical price information is available from {market['market']} ({market['district']}).")
        recommendations.append({
            "crop": crop.title(), "evidence": matches, "water_note": rule["water"],
            "cautions": cautions, "market": market,
            "next_step": "Verify field conditions, input availability and the planting window with a qualified local extension worker before spending money.",
        })

    def evidence_rank(item):
        preferred_bonus = 10 if item["crop"].casefold() == preferred else 0
        return preferred_bonus + len(item["evidence"])
    recommendations.sort(key=evidence_rank, reverse=True)

    now = timezone.now()
    latest_market = HistoricalMarketPrice.objects.order_by("-price_date").values_list("price_date", flat=True).first()
    market_stale = latest_market is None or latest_market < timezone.localdate() - timedelta(days=90)
    weather_collected = weather.get("collected_at") if weather else None
    sources = [
        {"name": "Open-Meteo", "kind": "weather forecast", "url": "https://open-meteo.com/", "collected_at": weather_collected, "stale": bool(weather and weather.get("stale")), "available": weather is not None, "notice": weather_error or "Forecasts are estimates and should not replace local warnings."},
        {"name": "World Bank Microdata Library", "kind": "historical modelled market prices", "url": "https://microdata.worldbank.org/catalog/6171", "dataset": "MWI_2021_RTFP_v02_M", "collected_at": latest_market.isoformat() if latest_market else None, "stale": market_stale, "available": latest_market is not None, "notice": "Historical estimates are not live quotes or guaranteed selling prices."},
        {"name": KNOWLEDGE_SOURCE, "kind": "planning and safety rules", "version": KNOWLEDGE_VERSION, "collected_at": KNOWLEDGE_VERSION, "stale": date.fromisoformat(KNOWLEDGE_VERSION) < timezone.localdate() - timedelta(days=180), "available": True, "notice": "General crop planning coverage only; local variety, soil testing and agronomic inspection remain necessary."},
    ]
    return {
        "recommendations": recommendations[:3],
        "context": {"location": location.title(), "soil_type": soil.title(), "season": selected_season.title(), "preferred_crop": preferred.title() if preferred else None, "weather": weather},
        "sources": sources, "generated_at": now.isoformat(),
        "method": "Deterministic evidence rules; no generative AI was used to create agricultural facts.",
        "notice": "Planning support only. This is not a yield, profit, treatment or planting guarantee.",
    }

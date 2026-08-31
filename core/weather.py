import json
from datetime import datetime, timezone as datetime_timezone
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings
from django.core.cache import cache


class WeatherUnavailable(RuntimeError):
    pass


# District-centre coordinates keep lookups predictable and avoid a second
# external request. Clients may supply more precise coordinates with consent.
MALAWI_DISTRICTS = {
    "balaka": (-14.99, 34.96), "blantyre": (-15.79, 35.01),
    "chikwawa": (-16.03, 34.79), "chiradzulu": (-15.67, 35.14),
    "chitipa": (-9.70, 33.27), "dedza": (-14.38, 34.33),
    "dowa": (-13.65, 33.94), "karonga": (-9.93, 33.93),
    "kasungu": (-13.03, 33.48), "likoma": (-12.07, 34.74),
    "lilongwe": (-13.96, 33.77), "machinga": (-14.97, 35.52),
    "mangochi": (-14.48, 35.26), "mchinji": (-13.80, 32.88),
    "mulanje": (-16.03, 35.50), "mwanza": (-15.60, 34.52),
    "mzimba": (-11.90, 33.60), "mzuzu": (-11.46, 34.02),
    "neno": (-15.40, 34.65), "nkhata bay": (-11.61, 34.30),
    "nkhotakota": (-12.93, 34.30), "nsanje": (-16.92, 35.26),
    "ntcheu": (-14.82, 34.64), "ntchisi": (-13.53, 33.91),
    "phalombe": (-15.81, 35.65), "rumphi": (-11.02, 33.86),
    "salima": (-13.78, 34.46), "thyolo": (-16.07, 35.14),
    "zomba": (-15.39, 35.32),
}

WEATHER_CODES = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Rime fog", 51: "Light drizzle", 53: "Drizzle",
    55: "Heavy drizzle", 56: "Freezing drizzle", 57: "Heavy freezing drizzle",
    61: "Light rain", 63: "Rain", 65: "Heavy rain", 66: "Freezing rain",
    67: "Heavy freezing rain", 71: "Light snow", 73: "Snow", 75: "Heavy snow",
    77: "Snow grains", 80: "Light showers", 81: "Showers", 82: "Heavy showers",
    85: "Snow showers", 86: "Heavy snow showers", 95: "Thunderstorm",
    96: "Thunderstorm with hail", 99: "Severe thunderstorm with hail",
}


def _number(value):
    return value if isinstance(value, (int, float)) else None


def _resolve_location(district, latitude=None, longitude=None):
    name = " ".join(str(district or "Lilongwe").strip().split())[:80]
    if latitude is not None and longitude is not None:
        try:
            lat, lon = float(latitude), float(longitude)
        except (TypeError, ValueError) as error:
            raise ValueError("Latitude and longitude must be numbers.") from error
        if not (-17.5 <= lat <= -9.0 and 32.5 <= lon <= 36.0):
            raise ValueError("Coordinates must be within Malawi.")
        return name or "Current location", lat, lon
    coordinates = MALAWI_DISTRICTS.get(name.casefold())
    if not coordinates:
        raise ValueError("Choose a supported Malawi district.")
    return name.title(), *coordinates


def _transform(payload, location, latitude, longitude):
    current = payload.get("current") or {}
    daily = payload.get("daily") or {}
    dates = daily.get("time") or []
    forecast = []
    for index, date in enumerate(dates):
        def daily_value(field):
            values = daily.get(field) or []
            return values[index] if index < len(values) else None
        code = daily_value("weather_code")
        forecast.append({
            "date": date,
            "condition": WEATHER_CODES.get(code, "Conditions unavailable"),
            "weather_code": code,
            "temperature_max_c": _number(daily_value("temperature_2m_max")),
            "temperature_min_c": _number(daily_value("temperature_2m_min")),
            "rain_probability_percent": _number(daily_value("precipitation_probability_max")),
            "precipitation_mm": _number(daily_value("precipitation_sum")),
            "et0_mm": _number(daily_value("et0_fao_evapotranspiration")),
        })
    code = current.get("weather_code")
    collected_at = datetime.now(datetime_timezone.utc).isoformat()
    return {
        "location": location, "latitude": latitude, "longitude": longitude,
        "timezone": payload.get("timezone", "Africa/Blantyre"),
        "current": {
            "time": current.get("time"), "condition": WEATHER_CODES.get(code, "Conditions unavailable"),
            "weather_code": code, "temperature_c": _number(current.get("temperature_2m")),
            "humidity_percent": _number(current.get("relative_humidity_2m")),
            "precipitation_mm": _number(current.get("precipitation")),
            "wind_speed_kmh": _number(current.get("wind_speed_10m")),
            "soil_temperature_c": _number(current.get("soil_temperature_0cm")),
            "soil_moisture": _number(current.get("soil_moisture_0_to_1cm")),
        },
        "forecast": forecast, "source": "Open-Meteo", "source_url": "https://open-meteo.com/",
        "collected_at": collected_at, "stale": False,
        "disclaimer": "Forecasts are estimates. Check local warnings before making safety-critical farming decisions.",
    }


def get_weather(district="Lilongwe", latitude=None, longitude=None):
    location, lat, lon = _resolve_location(district, latitude, longitude)
    identity = f"{lat:.3f}:{lon:.3f}"
    fresh_key, fallback_key = f"weather:fresh:{identity}", f"weather:fallback:{identity}"
    fresh = cache.get(fresh_key)
    if fresh:
        return {**fresh, "cached": True}
    params = {
        "latitude": lat, "longitude": lon, "timezone": "Africa/Blantyre", "forecast_days": 7,
        "current": "temperature_2m,relative_humidity_2m,precipitation,weather_code,wind_speed_10m,soil_temperature_0cm,soil_moisture_0_to_1cm",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum,precipitation_probability_max,et0_fao_evapotranspiration",
    }
    try:
        request = Request(f"{settings.WEATHER_API_URL}?{urlencode(params)}", headers={"Accept": "application/json", "User-Agent": "MlimiConnect/1.0"})
        with urlopen(request, timeout=settings.WEATHER_TIMEOUT_SECONDS) as response:
            result = _transform(json.loads(response.read().decode("utf-8")), location, lat, lon)
        cache.set(fresh_key, result, settings.WEATHER_CACHE_SECONDS)
        cache.set(fallback_key, result, settings.WEATHER_STALE_SECONDS)
        return {**result, "cached": False}
    except Exception as error:
        fallback = cache.get(fallback_key)
        if fallback:
            return {**fallback, "cached": True, "stale": True}
        raise WeatherUnavailable("Weather information is temporarily unavailable.") from error

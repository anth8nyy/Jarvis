"""Weather for wherever the user actually is — no API keys, no accounts.

Location comes from IP geolocation (city-level, which is what a "what's the
weather" question needs); the forecast comes from Open-Meteo. Both are free
and keyless, matching the no-paid-services rule for this project.
"""

from __future__ import annotations

import requests

from jarvis.registry import Registry, Tool

# WMO weather interpretation codes → speakable phrases.
_WMO = {
    0: "clear skies", 1: "mostly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "freezing fog",
    51: "light drizzle", 53: "drizzle", 55: "heavy drizzle",
    56: "freezing drizzle", 57: "heavy freezing drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain",
    66: "freezing rain", 67: "heavy freezing rain",
    71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains",
    80: "light showers", 81: "showers", 82: "violent showers",
    85: "snow showers", 86: "heavy snow showers",
    95: "a thunderstorm", 96: "a thunderstorm with hail",
    99: "a severe thunderstorm with hail",
}


def _locate() -> dict:
    """Where this Mac is right now (city-level, via IP)."""
    for url, fields in (
        ("https://ipapi.co/json/", ("city", "latitude", "longitude")),
        ("http://ip-api.com/json/", ("city", "lat", "lon")),
    ):
        try:
            d = requests.get(url, timeout=6).json()
            city, lat, lon = (d.get(f) for f in fields)
            if lat is not None and lon is not None:
                return {"city": city or "your location", "lat": float(lat), "lon": float(lon)}
        except Exception:
            continue
    raise RuntimeError("couldn't work out the current location")


def get_weather() -> str:
    """Current conditions + today's outlook for the user's location."""
    loc = _locate()
    r = requests.get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": loc["lat"],
            "longitude": loc["lon"],
            "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m",
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code",
            "timezone": "auto",
            "forecast_days": 1,
        },
        timeout=8,
    )
    r.raise_for_status()
    d = r.json()
    cur, day = d["current"], d["daily"]

    now_temp = round(cur["temperature_2m"])
    feels = round(cur["apparent_temperature"])
    sky = _WMO.get(cur["weather_code"], "unsettled weather")
    lo, hi = round(day["temperature_2m_min"][0]), round(day["temperature_2m_max"][0])
    rain = day["precipitation_probability_max"][0]

    msg = f"In {loc['city']} it's {now_temp} degrees with {sky}"
    if abs(feels - now_temp) >= 3:
        msg += f", feeling like {feels}"
    msg += f". Today runs from {lo} to {hi}"
    if rain is not None and rain >= 30:
        msg += f", with a {rain} percent chance of rain"
    return msg + ", sir."


def register(registry: Registry) -> None:
    registry.register(
        Tool(
            name="get_weather",
            description=(
                "The weather right now and today's forecast for the user's "
                "current location (found automatically — never ask where they "
                "are). Use for any 'what's the weather' question."
            ),
            input_schema={"type": "object", "properties": {}, "required": []},
            handler=get_weather,
        )
    )

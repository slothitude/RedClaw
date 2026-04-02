"""Weather skill — fetches current weather using wttr.in."""

from __future__ import annotations

import json
import urllib.request


def get_weather(location: str) -> str:
    """Get current weather for a location using wttr.in."""
    try:
        url = f"https://wttr.in/{location}?format=j1"
        req = urllib.request.Request(url, headers={"User-Agent": "RedClaw/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())

        current = data.get("current_condition", [{}])[0]
        area = data.get("nearest_area", [{}])[0]

        result = (
            f"Weather for {area.get('areaName', [{}])[0].get('value', location)}:\n"
            f"  Temperature: {current.get('temp_C', '?')}°C / {current.get('temp_F', '?')}°F\n"
            f"  Condition: {current.get('weatherDesc', [{}])[0].get('value', '?')}\n"
            f"  Humidity: {current.get('humidity', '?')}%\n"
            f"  Wind: {current.get('windspeedKmph', '?')} km/h {current.get('winddir16Point', '')}\n"
            f"  Feels like: {current.get('FeelsLikeC', '?')}°C"
        )
        return result
    except Exception as e:
        return f"Error fetching weather: {e}"

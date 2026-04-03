"""Daily briefing generator — weather, tasks, reminders, news, notes."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import httpx

from redclaw.assistant.config import AssistantConfig
from redclaw.assistant.reminders import ReminderStore
from redclaw.assistant.tasks import TaskStore

logger = logging.getLogger(__name__)


class BriefingGenerator:
    """Generate a daily briefing string."""

    def __init__(
        self,
        config: AssistantConfig,
        tasks: TaskStore,
        reminders: ReminderStore,
        search_url: str | None = None,
    ) -> None:
        self.config = config
        self.tasks = tasks
        self.reminders = reminders
        self.search_url = search_url

    async def generate(self) -> str:
        """Generate the full briefing."""
        try:
            tz = ZoneInfo(self.config.timezone)
        except Exception:
            tz = ZoneInfo("UTC")

        now = datetime.now(tz)
        parts: list[str] = []

        # Greeting
        parts.append(f"Good morning! Today is {now.strftime('%A, %B %d, %Y')}.\n")

        # Weather
        if self.config.briefing_weather and self.config.weather_location:
            weather = await self._get_weather()
            if weather:
                parts.append(f"**Weather:** {weather}\n")

        # Tasks
        if self.config.briefing_tasks:
            due_tasks = self.tasks.get_due_tasks(within_minutes=24 * 60)
            if due_tasks:
                parts.append("**Tasks due today:**")
                for t in due_tasks[:10]:
                    parts.append(f"  - [{t.priority}] {t.title}" + (f" (due {t.due})" if t.due else ""))
                parts.append("")
            else:
                parts.append("**Tasks:** No tasks due today.\n")

        # Reminders
        pending = self.reminders.get_pending()
        if pending:
            parts.append("**Pending reminders:**")
            for r in pending[:5]:
                parts.append(f"  - {r.text} (at {r.trigger_at})")
            parts.append("")

        # News
        if self.config.briefing_news and self.search_url:
            news = await self._get_news()
            if news:
                parts.append(f"**News:**\n{news}\n")

        # Recent notes (last 3, titles only)
        from redclaw.assistant.notes import NoteStore
        notes = NoteStore()
        recent = notes.list_notes(limit=3)
        if recent:
            parts.append("**Recent notes:**")
            for n in recent:
                parts.append(f"  - {n.title}")
            parts.append("")

        return "\n".join(parts) if len(parts) > 1 else "Good morning! Nothing to report today."

    async def _get_weather(self) -> str | None:
        """Fetch weather via Open-Meteo API (free, no key)."""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                # Geocode the location
                geo = await client.get(
                    "https://geocoding-api.open-meteo.com/v1/search",
                    params={"name": self.config.weather_location, "count": 1},
                )
                geo_data = geo.json()
                results = geo_data.get("results", [])
                if not results:
                    return None
                lat = results[0]["latitude"]
                lon = results[0]["longitude"]

                # Get current weather
                resp = await client.get(
                    "https://api.open-meteo.com/v1/forecast",
                    params={
                        "latitude": lat,
                        "longitude": lon,
                        "current": "temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m",
                        "temperature_unit": "celsius",
                    },
                )
                data = resp.json()
                current = data.get("current", {})
                temp = current.get("temperature_2m", "?")
                humidity = current.get("relative_humidity_2m", "?")
                wind = current.get("wind_speed_10m", "?")
                code = current.get("weather_code", 0)
                desc = _weather_code_to_text(code)
                return f"{temp}C, {desc}, humidity {humidity}%, wind {wind} km/h"
        except Exception as e:
            logger.error(f"Weather fetch failed: {e}")
            return None

    async def _get_news(self) -> str | None:
        """Fetch news headlines via SearXNG."""
        try:
            from redclaw.tools.search import execute_web_search
            topics = " ".join(self.config.news_topics)
            result = await execute_web_search(
                query=f"{topics} latest news today",
                search_url=self.search_url or "",
                categories="news",
            )
            # Trim to first 500 chars
            if result and len(result) > 500:
                result = result[:500] + "..."
            return result
        except Exception as e:
            logger.error(f"News fetch failed: {e}")
            return None


def _weather_code_to_text(code: int) -> str:
    """Convert WMO weather code to human-readable text."""
    codes = {
        0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
        45: "Fog", 48: "Depositing rime fog",
        51: "Light drizzle", 53: "Moderate drizzle", 55: "Dense drizzle",
        61: "Slight rain", 63: "Moderate rain", 65: "Heavy rain",
        71: "Slight snow", 73: "Moderate snow", 75: "Heavy snow",
        80: "Slight rain showers", 81: "Moderate rain showers", 82: "Violent rain showers",
        95: "Thunderstorm", 96: "Thunderstorm with slight hail", 99: "Thunderstorm with heavy hail",
    }
    return codes.get(code, "Unknown")

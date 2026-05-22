"""Weather skill — fetches current conditions via wttr.in or OpenWeatherMap.

Uses the free wttr.in JSON API by default (no API key required).
Set ``PRISMAL_WEATHER_API_KEY`` and ``PRISMAL_WEATHER_PROVIDER=openweathermap``
to switch to OpenWeatherMap.

Example::

    skill = WeatherSkill()
    tools = skill.get_tools()
    result = tools[0].invoke({"city": "Caracas"})
"""

from __future__ import annotations

import os

import httpx
from langchain_core.tools import BaseTool, tool

from prismal.core.logging import get_logger
from prismal.skills.base import BaseSkill, SkillMetadata

logger = get_logger("prismal.skills.weather")

_WTTR_URL = "https://wttr.in/{city}?format=j1"
_OWM_URL = "https://api.openweathermap.org/data/2.5/weather"


def _get_weather_wttr(city: str, units: str) -> str:
    """Fetch weather from wttr.in.

    Args:
        city: City name or coordinates.
        units: 'metric' or 'imperial'.

    Returns:
        Formatted weather string.
    """
    try:
        resp = httpx.get(_WTTR_URL.format(city=city), timeout=10)
        resp.raise_for_status()
        data = resp.json()
        current = data["current_condition"][0]
        temp_c = current["temp_C"]
        temp_f = current["temp_F"]
        desc = current["weatherDesc"][0]["value"]
        humidity = current["humidity"]
        feels_c = current["FeelsLikeC"]
        temp = temp_c if units == "metric" else temp_f
        unit_sym = "°C" if units == "metric" else "°F"
        return (
            f"Weather in {city}: {desc}. "
            f"Temperature: {temp}{unit_sym} (feels like {feels_c}°C). "
            f"Humidity: {humidity}%."
        )
    except httpx.HTTPError as exc:
        return f"[weather] HTTP error fetching weather for '{city}': {exc}"
    except (KeyError, ValueError) as exc:
        return f"[weather] Unexpected response format: {exc}"


def _get_weather_owm(city: str, units: str, api_key: str) -> str:
    """Fetch weather from OpenWeatherMap.

    Args:
        city: City name.
        units: 'metric' or 'imperial'.
        api_key: OpenWeatherMap API key.

    Returns:
        Formatted weather string.
    """
    try:
        resp = httpx.get(
            _OWM_URL,
            params={"q": city, "units": units, "appid": api_key},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        desc = data["weather"][0]["description"]
        temp = data["main"]["temp"]
        feels = data["main"]["feels_like"]
        humidity = data["main"]["humidity"]
        unit_sym = "°C" if units == "metric" else "°F"
        return (
            f"Weather in {city}: {desc}. "
            f"Temperature: {temp}{unit_sym} (feels like {feels}{unit_sym}). "
            f"Humidity: {humidity}%."
        )
    except httpx.HTTPError as exc:
        return f"[weather] HTTP error fetching weather for '{city}': {exc}"
    except (KeyError, ValueError) as exc:
        return f"[weather] Unexpected response format: {exc}"


class WeatherSkill(BaseSkill):
    """Provides weather lookup tools using wttr.in or OpenWeatherMap."""

    @property
    def metadata(self) -> SkillMetadata:
        """Return weather skill metadata."""
        return SkillMetadata(
            name="weather",
            description="Get current weather conditions and forecasts for any city",
            version="1.0.0",
            author="prismal",
            safe_to_auto_activate=True,
            tags=["utility", "weather", "api"],
        )

    def get_tools(self) -> list[BaseTool]:
        """Return weather tools.

        Returns:
            List containing the get_weather tool.
        """

        @tool
        def get_weather(city: str, units: str = "metric") -> str:
            """Get current weather for a city.

            Args:
                city: City name (e.g. 'London', 'New York', 'Caracas').
                units: Temperature units — 'metric' (°C) or 'imperial' (°F).

            Returns:
                Weather summary string with temperature, description, humidity.
            """
            api_key = os.getenv("PRISMAL_WEATHER_API_KEY", "")
            provider = os.getenv("PRISMAL_WEATHER_PROVIDER", "wttr")
            if provider == "openweathermap" and api_key:
                return _get_weather_owm(city, units, api_key)
            return _get_weather_wttr(city, units)

        return [get_weather]

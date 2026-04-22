"""Calendar skill — reads and creates events from .ics files using icalendar.

Reads ICS files from the directory specified by ``LIGHTAGENT_CALENDAR_DIR``
(defaults to ``~/calendars``). Parses VEVENT components and returns upcoming
events sorted by start date.

Example::

    skill = CalendarSkill()
    tools = skill.get_tools()
    result = tools[0].invoke({"days_ahead": 7})
"""

from __future__ import annotations

import os
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from langchain_core.tools import BaseTool, tool

from lightagent.core.logging import get_logger
from lightagent.skills.base import BaseSkill, SkillMetadata

logger = get_logger("lightagent.skills.calendar")


def _get_calendar_dir() -> Path:
    """Return the configured calendar directory.

    Returns:
        Path to the calendar directory (LIGHTAGENT_CALENDAR_DIR or ~/calendars).
    """
    return Path(os.getenv("LIGHTAGENT_CALENDAR_DIR", str(Path.home() / "calendars")))


def _parse_dt(value: object) -> datetime | None:
    """Normalise a vDatetime/vDate value to an aware datetime.

    Args:
        value: Raw value from icalendar (datetime, date, or vDDDTypes).

    Returns:
        Aware datetime or None if conversion fails.
    """
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=UTC)
    return None


def _read_events(calendar_dir: Path, days_ahead: int) -> list[dict[str, str]]:
    """Scan ICS files and collect upcoming events.

    Args:
        calendar_dir: Directory containing .ics files.
        days_ahead: Number of days into the future to include.

    Returns:
        List of event dicts sorted by start time.
    """
    try:
        from icalendar import Calendar
    except ImportError:
        return []

    now = datetime.now(tz=UTC)
    cutoff = datetime(now.year, now.month, now.day, tzinfo=UTC)
    future_limit_days = days_ahead * 86400  # seconds

    events: list[dict[str, str]] = []

    if not calendar_dir.is_dir():
        return events

    for ics_file in calendar_dir.glob("*.ics"):
        try:
            cal = Calendar.from_ical(ics_file.read_bytes())
            for raw_component in cal.walk():
                component: Any = raw_component
                if component.name != "VEVENT":
                    continue
                dtstart = _parse_dt(
                    component.get("DTSTART").dt if component.get("DTSTART") else None
                )
                if dtstart is None:
                    continue
                delta = (dtstart - cutoff).total_seconds()
                if delta < 0 or delta > future_limit_days:
                    continue
                summary = str(component.get("SUMMARY", "(no title)"))
                location = str(component.get("LOCATION", ""))
                description = str(component.get("DESCRIPTION", ""))
                events.append(
                    {
                        "start": dtstart.strftime("%Y-%m-%d %H:%M %Z"),
                        "summary": summary,
                        "location": location,
                        "description": description[:200],
                    }
                )
        except Exception as exc:
            logger.warning("calendar_parse_error", file=str(ics_file), error=str(exc))

    events.sort(key=lambda e: e["start"])
    return events


class CalendarSkill(BaseSkill):
    """Reads upcoming calendar events from local .ics files."""

    @property
    def metadata(self) -> SkillMetadata:
        """Return calendar skill metadata."""
        return SkillMetadata(
            name="calendar",
            description="Read upcoming calendar events from local .ics files",
            version="1.0.0",
            author="lightagent",
            safe_to_auto_activate=True,
            tags=["utility", "calendar", "ics", "scheduling"],
        )

    def get_tools(self) -> list[BaseTool]:
        """Return calendar tools.

        Returns:
            List containing the list_events tool.
        """

        @tool
        def list_events(days_ahead: int = 7) -> str:
            """List upcoming calendar events from local .ics files.

            Args:
                days_ahead: Number of days ahead to look for events (default: 7).

            Returns:
                Formatted list of upcoming events with date, title, and location.
            """
            cal_dir = _get_calendar_dir()
            if not cal_dir.is_dir():
                return (
                    f"[calendar] Calendar directory not found: {cal_dir}. "
                    "Set LIGHTAGENT_CALENDAR_DIR to a directory containing .ics files."
                )

            events = _read_events(cal_dir, days_ahead)
            if not events:
                return f"No events found in the next {days_ahead} days."

            lines: list[str] = [f"Upcoming events (next {days_ahead} days):"]
            for ev in events:
                line = f"  • {ev['start']} — {ev['summary']}"
                if ev["location"]:
                    line += f" @ {ev['location']}"
                if ev["description"]:
                    line += f"\n    {ev['description']}"
                lines.append(line)
            return "\n".join(lines)

        return [list_events]

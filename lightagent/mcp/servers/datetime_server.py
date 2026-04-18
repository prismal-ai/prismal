"""
lightagent-datetime MCP server — standalone, zero internal imports.

Provides six tools for timezone-aware datetime operations, cron expression
validation, and timezone listing.  Agents call these tools via MCP instead
of implementing timezone logic directly.

Launch (stdio transport)::

    python -m lightagent.mcp.servers.datetime_server

Environment variables read at runtime:
    LIGHTAGENT_TIMEZONE      — override OS timezone (IANA name or "")
    LIGHTAGENT_CRON_TIMEZONE — default cron timezone (IANA name or "")

Note:
    This module intentionally does NOT use ``from __future__ import annotations``.
    The MCP SDK's ``Tool.from_function()`` calls
    ``issubclass(param.annotation, Context)`` on raw ``inspect.signature``
    annotations without resolving them via ``typing.get_type_hints()``.
    PEP 563 string annotations would raise
    ``TypeError: issubclass() arg 1 must be a class`` at decorator time.
"""

import os
from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

import tzlocal
from croniter import croniter
from pydantic import BaseModel

from mcp.server.fastmcp import FastMCP

# ── FastMCP application ────────────────────────────────────────────────────────

mcp = FastMCP(
    "lightagent-datetime",
    instructions=(
        "Timezone-aware datetime tools for LightAgent agents. "
        "Use datetime_get_timezone_info() to inspect the current system timezone, "
        "datetime_cron_next_run() to preview cron schedules, and "
        "datetime_convert() to convert timestamps between timezones."
    ),
)

# ── Result models ──────────────────────────────────────────────────────────────


class SystemTimeResult(BaseModel):
    """Result of datetime_get_system_time()."""

    iso8601: str
    """Current time as ISO-8601 string with UTC offset, e.g. '2026-03-28T09:00:00-04:00'."""

    unix_timestamp: float
    """Current time as POSIX timestamp (seconds since epoch)."""

    timezone_name: str
    """IANA timezone name used for formatting, e.g. 'America/Caracas'."""

    utc_offset: str
    """Current UTC offset string, e.g. '-04:00'."""

    is_dst: bool
    """Whether DST is currently active in the requested timezone."""


class TimezoneInfoResult(BaseModel):
    """Result of datetime_get_timezone_info()."""

    os_timezone: str
    """Timezone name reported by the OS (via tzlocal)."""

    lightagent_timezone: str
    """Value of LIGHTAGENT_TIMEZONE env var, or empty string if unset."""

    cron_timezone: str
    """Value of LIGHTAGENT_CRON_TIMEZONE env var, or empty string if unset."""

    resolved_timezone: str
    """Effective IANA name after applying the resolution chain."""

    utc_offset: str
    """Current UTC offset for the resolved timezone."""

    is_dst: bool
    """Whether DST is active in the resolved timezone."""


class ConvertResult(BaseModel):
    """Result of datetime_convert()."""

    success: bool
    """True when the conversion succeeded."""

    result_iso8601: str | None = None
    """Converted datetime as ISO-8601 string, or None on error."""

    error: str | None = None
    """Human-readable error message, or None on success."""


class CronNextRunResult(BaseModel):
    """Result of datetime_cron_next_run()."""

    success: bool
    """True when the schedule was parsed successfully."""

    schedule: str
    """The cron expression that was evaluated."""

    timezone: str
    """Effective timezone used for the computation."""

    next_runs: list[str] = []
    """Next fire times as ISO-8601 strings in the requested timezone."""

    next_runs_utc: list[str] = []
    """Same fire times converted to UTC ISO-8601."""

    error: str | None = None
    """Human-readable error message, or None on success."""


class CronValidateResult(BaseModel):
    """Result of datetime_validate_cron()."""

    valid: bool
    """True when the cron expression is syntactically valid."""

    description: str | None = None
    """Human-readable description of the schedule, e.g. 'At 09:00 every day'."""

    error: str | None = None
    """Human-readable validation error, or None when valid."""


# ── Internal helpers ───────────────────────────────────────────────────────────

_UTC_ZONE = ZoneInfo("UTC")

# POSIX aliases that are valid ZoneInfo names but look unusual
_ALIAS_MAP: dict[str, str] = {
    "@yearly": "0 0 1 1 *",
    "@annually": "0 0 1 1 *",
    "@monthly": "0 0 1 * *",
    "@weekly": "0 0 * * 0",
    "@daily": "0 0 * * *",
    "@midnight": "0 0 * * *",
    "@hourly": "0 * * * *",
}


def _resolve_tz(tz_str: str) -> ZoneInfo:
    """Resolve an IANA timezone string using the environment resolution chain.

    Resolution order:
      1. Explicit ``tz_str`` if non-empty.
      2. ``LIGHTAGENT_CRON_TIMEZONE`` env var.
      3. ``LIGHTAGENT_TIMEZONE`` env var.
      4. OS local timezone via ``tzlocal``.
      5. UTC as final fallback.

    Invalid IANA names fall back to UTC silently.

    Args:
        tz_str: Explicit IANA name, or empty string to walk the chain.

    Returns:
        A :class:`~zoneinfo.ZoneInfo` instance. Never raises.
    """
    candidates = [
        tz_str,
        os.environ.get("LIGHTAGENT_CRON_TIMEZONE", ""),
        os.environ.get("LIGHTAGENT_TIMEZONE", ""),
    ]
    for candidate in candidates:
        if candidate:
            try:
                return ZoneInfo(candidate)
            except (ZoneInfoNotFoundError, KeyError):
                pass

    # OS fallback
    try:
        local = tzlocal.get_localzone()
        tz_name = getattr(local, "key", None) or str(local)
        return ZoneInfo(tz_name)
    except Exception as exc:  # tzlocal failure is non-fatal
        _ = exc  # fail-open: fall through to UTC

    return _UTC_ZONE


def _utc_offset_str(tz: ZoneInfo, dt: datetime) -> str:
    """Return the UTC offset string for *tz* at *dt*, e.g. ``'-04:00'``.

    Args:
        tz: Timezone to inspect.
        dt: Reference datetime (needed for DST-aware offset).

    Returns:
        Offset string in ``±HH:MM`` format.
    """
    aware = dt.astimezone(tz)
    offset = aware.utcoffset()
    if offset is None:
        return "+00:00"
    total_seconds = int(offset.total_seconds())
    sign = "+" if total_seconds >= 0 else "-"
    total_seconds = abs(total_seconds)
    hours, remainder = divmod(total_seconds, 3600)
    minutes = remainder // 60
    return f"{sign}{hours:02d}:{minutes:02d}"


def _is_dst(tz: ZoneInfo, dt: datetime) -> bool:
    """Return whether DST is active in *tz* at *dt*.

    Args:
        tz: Timezone to check.
        dt: Reference datetime.

    Returns:
        ``True`` if DST is active, ``False`` otherwise.
    """
    aware = dt.astimezone(tz)
    dst = aware.dst()
    if dst is None:
        return False
    return dst.total_seconds() != 0


def _normalise_cron(schedule: str) -> str:
    """Expand ``@`` aliases to their 5-field equivalents.

    Args:
        schedule: Cron expression, possibly an ``@alias``.

    Returns:
        5-field cron expression string.
    """
    return _ALIAS_MAP.get(schedule.strip().lower(), schedule)


def _describe_cron(schedule: str) -> str:
    """Generate a minimal human-readable description of a cron expression.

    Only handles the most common patterns.  Agents should not rely on this
    for complex schedules — use ``datetime_cron_next_run`` to preview actual
    fire times instead.

    Args:
        schedule: A valid 5-field cron expression or ``@`` alias.

    Returns:
        A short English description, e.g. ``'At 09:00 every day'``.
    """
    normalised = _normalise_cron(schedule)
    try:
        fields, _ = croniter.expand(normalised)
    except Exception:
        return schedule

    minutes, hours, dom, months, dow = fields

    # croniter represents wildcards as ['*'], not as a full range.
    def _is_wildcard(field: list[int | str]) -> bool:
        return field == ["*"]

    def _is_single_int(field: list[int | str]) -> bool:
        return len(field) == 1 and isinstance(field[0], int)

    all_minutes = _is_wildcard(minutes)
    all_hours = _is_wildcard(hours)
    all_dom = _is_wildcard(dom)
    all_months = _is_wildcard(months)
    all_dow = _is_wildcard(dow)

    month_names = [
        "",
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
    ]
    dow_names = [
        "Sunday",
        "Monday",
        "Tuesday",
        "Wednesday",
        "Thursday",
        "Friday",
        "Saturday",
    ]

    time_part: str
    if _is_single_int(minutes) and _is_single_int(hours):
        # e.g. "0 9 * * *" → "At 09:00"
        time_part = f"At {int(hours[0]):02d}:{int(minutes[0]):02d}"
    elif all_hours and _is_single_int(minutes):
        # e.g. "0 * * * *" → "At minute 0 of every hour"
        time_part = f"At minute {minutes[0]} of every hour"
    elif _is_single_int(hours) and all_minutes:
        # e.g. "* 9 * * *" → "Every minute past hour 09"
        time_part = f"Every minute past hour {int(hours[0]):02d}"
    elif all_hours and all_minutes:
        time_part = "Every minute"
    else:
        time_part = schedule

    freq_part: str
    if all_dom and all_months and all_dow:
        freq_part = "every day"
    elif not all_dow and _is_single_int(dow) and all_dom and all_months:
        idx = int(dow[0]) % 7
        freq_part = f"every {dow_names[idx]}"
    elif not all_dom and _is_single_int(dom) and all_months and all_dow:
        freq_part = f"on day {dom[0]} of every month"
    elif not all_months and _is_single_int(months) and all_dow:
        idx = int(months[0])
        month_name = month_names[idx] if 1 <= idx <= 12 else str(months[0])
        freq_part = f"in {month_name}"
    else:
        freq_part = ""

    if time_part and freq_part:
        return f"{time_part} {freq_part}"
    return time_part or schedule


# ── MCP Tools ──────────────────────────────────────────────────────────────────


@mcp.tool()
def datetime_get_system_time(timezone: str = "") -> SystemTimeResult:
    """Return the current system time in the requested timezone.

    Args:
        timezone: IANA timezone name (e.g. ``'America/Caracas'``).
            Empty string uses the resolution chain:
            ``LIGHTAGENT_CRON_TIMEZONE`` → ``LIGHTAGENT_TIMEZONE`` → OS → UTC.

    Returns:
        :class:`SystemTimeResult` with ``iso8601``, ``unix_timestamp``,
        ``timezone_name``, ``utc_offset``, and ``is_dst``.
    """
    tz = _resolve_tz(timezone)
    now_utc = datetime.now(UTC)
    now_local = now_utc.astimezone(tz)
    return SystemTimeResult(
        iso8601=now_local.isoformat(),
        unix_timestamp=now_utc.timestamp(),
        timezone_name=str(tz.key) if hasattr(tz, "key") else str(tz),
        utc_offset=_utc_offset_str(tz, now_utc),
        is_dst=_is_dst(tz, now_utc),
    )


@mcp.tool()
def datetime_get_timezone_info() -> TimezoneInfoResult:
    """Return full timezone diagnostic information for the current process.

    Includes the OS timezone, LightAgent environment overrides, the
    resolved effective timezone, and the current UTC offset.

    Returns:
        :class:`TimezoneInfoResult` with all timezone fields populated.
    """
    lightagent_tz = os.environ.get("LIGHTAGENT_TIMEZONE", "")
    cron_tz = os.environ.get("LIGHTAGENT_CRON_TIMEZONE", "")

    # OS timezone
    try:
        local = tzlocal.get_localzone()
        os_tz_name = getattr(local, "key", None) or str(local)
    except Exception:
        os_tz_name = "UTC"

    resolved = _resolve_tz("")
    resolved_name = str(resolved.key) if hasattr(resolved, "key") else str(resolved)
    now_utc = datetime.now(UTC)

    return TimezoneInfoResult(
        os_timezone=os_tz_name,
        lightagent_timezone=lightagent_tz,
        cron_timezone=cron_tz,
        resolved_timezone=resolved_name,
        utc_offset=_utc_offset_str(resolved, now_utc),
        is_dst=_is_dst(resolved, now_utc),
    )


@mcp.tool()
def datetime_convert(dt_iso: str, from_tz: str, to_tz: str) -> ConvertResult:
    """Convert a datetime string from one timezone to another.

    Args:
        dt_iso: Datetime string in ISO-8601 or ``'YYYY-MM-DD HH:MM:SS'`` format.
            May be naive (no offset) or timezone-aware.
        from_tz: Source IANA timezone name (e.g. ``'America/Caracas'``).
            Ignored when *dt_iso* already contains a UTC offset.
        to_tz: Target IANA timezone name (e.g. ``'UTC'``).

    Returns:
        :class:`ConvertResult` with ``result_iso8601`` on success, or
        ``error`` message on failure.

    Example::

        datetime_convert("2026-03-28T09:00:00", "America/Caracas", "UTC")
        # → {"success": true, "result_iso8601": "2026-03-28T13:00:00+00:00"}
    """
    try:
        source_tz = ZoneInfo(from_tz)
    except (ZoneInfoNotFoundError, KeyError):
        return ConvertResult(success=False, error=f"Invalid source timezone: {from_tz!r}")

    try:
        target_tz = ZoneInfo(to_tz)
    except (ZoneInfoNotFoundError, KeyError):
        return ConvertResult(success=False, error=f"Invalid target timezone: {to_tz!r}")

    # Parse the input datetime
    parsed: datetime | None = None
    for fmt in (
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%d %H:%M:%S%z",
    ):
        try:
            parsed = datetime.strptime(dt_iso, fmt)  # noqa: DTZ007
            break
        except ValueError:
            continue

    if parsed is None:
        return ConvertResult(success=False, error=f"Cannot parse datetime: {dt_iso!r}")

    # Attach source timezone if naive
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=source_tz)

    result = parsed.astimezone(target_tz)
    return ConvertResult(success=True, result_iso8601=result.isoformat())


@mcp.tool()
def datetime_cron_next_run(
    schedule: str,
    from_now: str = "",
    timezone: str = "",
    count: int = 5,
) -> CronNextRunResult:
    """Return the next *count* fire times for a cron expression.

    Args:
        schedule: 5-field cron expression or ``@`` alias
            (e.g. ``'0 9 * * *'``, ``'@daily'``).
        from_now: Optional ISO-8601 baseline datetime.  Empty string uses
            the current time.
        timezone: IANA timezone for the computation and output.
            Empty string uses the resolution chain.
        count: Number of next run times to return (1-20, default 5).

    Returns:
        :class:`CronNextRunResult` with ``next_runs`` (in *timezone*) and
        ``next_runs_utc``, or ``error`` on failure.
    """
    normalised = _normalise_cron(schedule)
    if not croniter.is_valid(normalised):
        return CronNextRunResult(
            success=False,
            schedule=schedule,
            timezone=timezone or "resolved",
            error=f"Invalid cron expression: {schedule!r}",
        )

    tz = _resolve_tz(timezone)
    tz_name = str(tz.key) if hasattr(tz, "key") else str(tz)
    count = max(1, min(count, 20))

    # Determine baseline
    if from_now:
        baseline: datetime | None = None
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
            try:
                naive = datetime.strptime(from_now, fmt)  # noqa: DTZ007
                baseline = naive.replace(tzinfo=tz)
                break
            except ValueError:
                continue
        if baseline is None:
            return CronNextRunResult(
                success=False,
                schedule=schedule,
                timezone=tz_name,
                error=f"Cannot parse from_now: {from_now!r}",
            )
    else:
        baseline = datetime.now(UTC).astimezone(tz)

    cron = croniter(normalised, baseline)
    next_runs: list[str] = []
    next_runs_utc: list[str] = []

    for _ in range(count):
        fire: datetime = cron.get_next(datetime)
        if fire.tzinfo is None:
            fire = fire.replace(tzinfo=tz)
        next_runs.append(fire.isoformat())
        next_runs_utc.append(fire.astimezone(_UTC_ZONE).isoformat())

    return CronNextRunResult(
        success=True,
        schedule=schedule,
        timezone=tz_name,
        next_runs=next_runs,
        next_runs_utc=next_runs_utc,
    )


@mcp.tool()
def datetime_validate_cron(schedule: str) -> CronValidateResult:
    """Validate a cron expression and return a human-readable description.

    Args:
        schedule: 5-field cron expression or ``@`` alias
            (e.g. ``'0 9 * * *'``, ``'@daily'``, ``'99 9 * * *'``).

    Returns:
        :class:`CronValidateResult` with ``valid=True`` and a ``description``
        on success, or ``valid=False`` and an ``error`` message on failure.
    """
    normalised = _normalise_cron(schedule)
    if not croniter.is_valid(normalised):
        return CronValidateResult(
            valid=False,
            error=f"Invalid cron expression: {schedule!r}",
        )

    description = _describe_cron(schedule)
    return CronValidateResult(valid=True, description=description)


@mcp.tool()
def datetime_list_timezones(region: str = "") -> list[str]:
    """List available IANA timezone names, optionally filtered by region.

    Args:
        region: Region prefix to filter by (e.g. ``'America'``, ``'Europe'``).
            Empty string returns all timezones (500+).

    Returns:
        Sorted list of IANA timezone name strings.

    Example::

        datetime_list_timezones("America")
        # → ["America/Anguilla", "America/Antigua", ..., "America/Winnipeg"]
    """
    all_tz = sorted(available_timezones())
    if not region:
        return all_tz
    prefix = region.rstrip("/") + "/"
    return [tz for tz in all_tz if tz.startswith(prefix)]


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":  # pragma: no cover
    mcp.run()

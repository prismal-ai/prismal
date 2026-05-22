"""Unit tests for the lightagent-datetime MCP server (T-297 / SPEC-029 AC-029-7).

All tools are tested by calling them directly as Python functions — no MCP
transport is started.  This keeps the tests fast and independent of the MCP
infrastructure.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from prismal.mcp.servers.datetime_server import (
    CronNextRunResult,
    CronValidateResult,
    SystemTimeResult,
    TimezoneInfoResult,
    _describe_cron,
    _resolve_tz,
    _utc_offset_str,
    datetime_convert,
    datetime_cron_next_run,
    datetime_get_system_time,
    datetime_get_timezone_info,
    datetime_list_timezones,
    datetime_validate_cron,
)

# ── _resolve_tz helper ────────────────────────────────────────────────────────


def test_resolve_tz_explicit() -> None:
    """Explicit IANA name overrides everything."""
    tz = _resolve_tz("Asia/Tokyo")
    assert tz == ZoneInfo("Asia/Tokyo")


def test_resolve_tz_invalid_falls_back_to_env_or_utc() -> None:
    """Invalid explicit tz_str falls through to next step."""
    with patch.dict(os.environ, {"LIGHTAGENT_CRON_TIMEZONE": "America/Caracas"}, clear=False):
        tz = _resolve_tz("Not/Real")
    assert tz == ZoneInfo("America/Caracas")


def test_resolve_tz_uses_cron_timezone_env() -> None:
    """LIGHTAGENT_CRON_TIMEZONE env var is used when tz_str is empty."""
    with patch.dict(os.environ, {"LIGHTAGENT_CRON_TIMEZONE": "Europe/Berlin"}, clear=False):
        tz = _resolve_tz("")
    assert tz == ZoneInfo("Europe/Berlin")


def test_resolve_tz_uses_lightagent_timezone_env() -> None:
    """LIGHTAGENT_TIMEZONE env var used when CRON_TIMEZONE is not set."""
    env = {"LIGHTAGENT_TIMEZONE": "Asia/Seoul", "LIGHTAGENT_CRON_TIMEZONE": ""}
    with patch.dict(os.environ, env, clear=False):
        tz = _resolve_tz("")
    assert tz == ZoneInfo("Asia/Seoul")


# ── datetime_get_system_time ──────────────────────────────────────────────────


def test_get_system_time_default_tz() -> None:
    """Returns SystemTimeResult with all required fields."""
    result = datetime_get_system_time()
    assert isinstance(result, SystemTimeResult)
    assert result.iso8601
    assert result.unix_timestamp > 0
    assert result.timezone_name
    assert result.utc_offset
    assert isinstance(result.is_dst, bool)


def test_get_system_time_explicit_tz_caracas() -> None:
    """Returns time in America/Caracas when requested (UTC-4:00 since 2016)."""
    result = datetime_get_system_time(timezone="America/Caracas")
    assert isinstance(result, SystemTimeResult)
    assert result.timezone_name == "America/Caracas"
    assert result.utc_offset == "-04:00"


def test_get_system_time_invalid_tz_falls_back() -> None:
    """Invalid timezone falls back gracefully (no exception)."""
    result = datetime_get_system_time(timezone="Bad/Zone")
    # Falls back through resolution chain, should still return a result
    assert isinstance(result, SystemTimeResult)
    assert result.iso8601


# ── datetime_get_timezone_info ────────────────────────────────────────────────


def test_get_timezone_info_fields_present() -> None:
    """All required fields are present in the result."""
    result = datetime_get_timezone_info()
    assert isinstance(result, TimezoneInfoResult)
    assert hasattr(result, "os_timezone")
    assert hasattr(result, "lightagent_timezone")
    assert hasattr(result, "cron_timezone")
    assert hasattr(result, "resolved_timezone")
    assert hasattr(result, "utc_offset")
    assert hasattr(result, "is_dst")


def test_get_timezone_info_uses_env_vars() -> None:
    """Env vars appear in lightagent_timezone and cron_timezone fields."""
    env = {
        "LIGHTAGENT_TIMEZONE": "Europe/Madrid",
        "LIGHTAGENT_CRON_TIMEZONE": "America/New_York",
    }
    with patch.dict(os.environ, env, clear=False):
        result = datetime_get_timezone_info()
    assert result.lightagent_timezone == "Europe/Madrid"
    assert result.cron_timezone == "America/New_York"
    # resolved_timezone follows resolution chain: CRON_TIMEZONE wins
    assert result.resolved_timezone == "America/New_York"


# ── datetime_convert ──────────────────────────────────────────────────────────


def test_convert_caracas_to_utc() -> None:
    """2026-03-28T09:00:00 America/Caracas → 13:00 UTC (UTC-4:00 since 2016)."""
    result = datetime_convert(
        dt_iso="2026-03-28T09:00:00",
        from_tz="America/Caracas",
        to_tz="UTC",
    )
    assert result.success is True
    assert result.result_iso8601 is not None
    # America/Caracas is UTC-4:00 since May 2016
    assert "13:00:00" in result.result_iso8601


def test_convert_invalid_from_tz() -> None:
    """Invalid from_tz returns success=False with error."""
    result = datetime_convert(
        dt_iso="2026-03-28T09:00:00",
        from_tz="Not/Real",
        to_tz="UTC",
    )
    assert result.success is False
    assert result.error is not None
    assert "Not/Real" in result.error


def test_convert_invalid_to_tz() -> None:
    """Invalid to_tz returns success=False with error."""
    result = datetime_convert(
        dt_iso="2026-03-28T09:00:00",
        from_tz="UTC",
        to_tz="Not/Real",
    )
    assert result.success is False
    assert result.error is not None
    assert "Not/Real" in result.error


def test_convert_unparseable_dt_iso() -> None:
    """Unparseable dt_iso returns success=False with error."""
    result = datetime_convert(dt_iso="not-a-date", from_tz="UTC", to_tz="UTC")
    assert result.success is False
    assert result.error is not None


def test_convert_space_format() -> None:
    """'YYYY-MM-DD HH:MM:SS' format is accepted."""
    result = datetime_convert(
        dt_iso="2026-01-01 12:00:00",
        from_tz="UTC",
        to_tz="America/Caracas",
    )
    assert result.success is True
    assert result.result_iso8601 is not None


# ── datetime_cron_next_run ────────────────────────────────────────────────────


def test_cron_next_run_daily_caracas() -> None:
    """'0 9 * * *' returns 5 fire times at 09:00 in Caracas."""
    result = datetime_cron_next_run(
        schedule="0 9 * * *",
        timezone="America/Caracas",
    )
    assert isinstance(result, CronNextRunResult)
    assert result.success is True
    assert len(result.next_runs) == 5
    assert len(result.next_runs_utc) == 5
    for run in result.next_runs:
        dt = datetime.fromisoformat(run)
        assert dt.hour == 9
        assert dt.minute == 0


def test_cron_next_run_invalid_expression() -> None:
    """Invalid cron expression returns success=False."""
    result = datetime_cron_next_run(schedule="99 99 * * *")
    assert isinstance(result, CronNextRunResult)
    assert result.success is False
    assert result.error is not None


def test_cron_next_run_with_from_now_override() -> None:
    """from_now overrides the baseline datetime."""
    result = datetime_cron_next_run(
        schedule="0 9 * * *",
        from_now="2026-01-01T00:00:00",
        timezone="UTC",
    )
    assert result.success is True
    assert len(result.next_runs) == 5
    # All results should be on or after 2026-01-01T09:00:00
    first_run = datetime.fromisoformat(result.next_runs[0])
    assert first_run >= datetime(2026, 1, 1, 9, 0, 0, tzinfo=ZoneInfo("UTC"))


def test_cron_next_run_returns_n_results() -> None:
    """count parameter controls number of returned results."""
    result = datetime_cron_next_run(schedule="0 * * * *", count=3)
    assert result.success is True
    assert len(result.next_runs) == 3


def test_cron_next_run_count_clamped_to_20() -> None:
    """count > 20 is clamped to 20."""
    result = datetime_cron_next_run(schedule="0 * * * *", count=50)
    assert result.success is True
    assert len(result.next_runs) == 20


def test_cron_next_run_count_minimum_1() -> None:
    """count < 1 is clamped to 1."""
    result = datetime_cron_next_run(schedule="0 * * * *", count=0)
    assert result.success is True
    assert len(result.next_runs) == 1


def test_cron_next_run_invalid_from_now() -> None:
    """Unparseable from_now returns success=False."""
    result = datetime_cron_next_run(
        schedule="0 9 * * *",
        from_now="not-a-date",
        timezone="UTC",
    )
    assert result.success is False
    assert result.error is not None


def test_cron_next_run_at_alias() -> None:
    """@daily alias is expanded and returns valid results."""
    result = datetime_cron_next_run(schedule="@daily", timezone="UTC")
    assert result.success is True
    assert len(result.next_runs) == 5
    for run in result.next_runs:
        dt = datetime.fromisoformat(run)
        assert dt.hour == 0
        assert dt.minute == 0


# ── datetime_validate_cron ────────────────────────────────────────────────────


def test_validate_cron_valid_expression() -> None:
    """'0 9 * * *' is valid with a description."""
    result = datetime_validate_cron("0 9 * * *")
    assert isinstance(result, CronValidateResult)
    assert result.valid is True
    assert result.description is not None
    assert "09:00" in result.description
    assert result.error is None


def test_validate_cron_invalid_expression() -> None:
    """'99 9 * * *' is invalid."""
    result = datetime_validate_cron("99 9 * * *")
    assert isinstance(result, CronValidateResult)
    assert result.valid is False
    assert result.error is not None
    assert result.description is None


def test_validate_cron_at_alias_daily() -> None:
    """'@daily' is valid and describes midnight every day."""
    result = datetime_validate_cron("@daily")
    assert result.valid is True
    assert result.description is not None
    assert "00:00" in result.description


def test_validate_cron_at_alias_hourly() -> None:
    """'@hourly' is valid."""
    result = datetime_validate_cron("@hourly")
    assert result.valid is True
    assert result.description is not None


def test_validate_cron_at_alias_weekly() -> None:
    """'@weekly' is valid."""
    result = datetime_validate_cron("@weekly")
    assert result.valid is True
    assert result.description is not None


# ── datetime_list_timezones ────────────────────────────────────────────────────


def test_list_timezones_all() -> None:
    """No region returns many timezones (500+)."""
    result = datetime_list_timezones()
    assert isinstance(result, list)
    assert len(result) > 400
    assert all(isinstance(tz, str) for tz in result)
    # Should be sorted
    assert result == sorted(result)


def test_list_timezones_region_filter_america() -> None:
    """region='America' returns only America/* timezones."""
    result = datetime_list_timezones(region="America")
    assert len(result) > 0
    assert all(tz.startswith("America/") for tz in result)


def test_list_timezones_region_filter_europe() -> None:
    """region='Europe' returns only Europe/* timezones."""
    result = datetime_list_timezones(region="Europe")
    assert len(result) > 0
    assert all(tz.startswith("Europe/") for tz in result)


def test_list_timezones_region_no_match() -> None:
    """Unknown region prefix returns an empty list."""
    result = datetime_list_timezones(region="Narnia")
    assert result == []


def test_list_timezones_region_with_trailing_slash() -> None:
    """region='Asia/' (with trailing slash) still filters correctly."""
    result = datetime_list_timezones(region="Asia/")
    assert all(tz.startswith("Asia/") for tz in result)


# ── _describe_cron helper ──────────────────────────────────────────────────────


def test_describe_cron_daily() -> None:
    """'0 9 * * *' → 'At 09:00 every day'."""
    desc = _describe_cron("0 9 * * *")
    assert "09:00" in desc
    assert "day" in desc


def test_describe_cron_midnight() -> None:
    """'0 0 * * *' → contains '00:00'."""
    desc = _describe_cron("0 0 * * *")
    assert "00:00" in desc


def test_describe_cron_weekly_monday() -> None:
    """'0 9 * * 1' → mentions 09:00 and Monday."""
    desc = _describe_cron("0 9 * * 1")
    assert "09:00" in desc
    assert "Monday" in desc


# ── _utc_offset_str helper ────────────────────────────────────────────────────


def test_utc_offset_str_utc() -> None:
    """UTC returns '+00:00'."""
    tz = ZoneInfo("UTC")
    now = datetime.now(UTC)
    assert _utc_offset_str(tz, now) == "+00:00"


def test_utc_offset_str_negative() -> None:
    """Negative offset (America/Caracas, UTC-4:00 since 2016) returns '-04:00'."""
    tz = ZoneInfo("America/Caracas")
    now = datetime.now(UTC)
    assert _utc_offset_str(tz, now) == "-04:00"


# ── Additional coverage tests ─────────────────────────────────────────────────


def test_resolve_tz_falls_back_to_utc_when_tzlocal_raises() -> None:
    """When tzlocal raises and env vars are empty, _resolve_tz returns UTC."""
    env = {"LIGHTAGENT_CRON_TIMEZONE": "", "LIGHTAGENT_TIMEZONE": ""}
    with patch.dict(os.environ, env, clear=False):
        with patch(
            "lightagent.mcp.servers.datetime_server.tzlocal.get_localzone",
            side_effect=Exception("no tz db"),
        ):
            tz = _resolve_tz("")
    assert tz == ZoneInfo("UTC")


def test_utc_offset_str_none_offset() -> None:
    """When utcoffset() returns None the function returns '+00:00'."""
    from unittest.mock import MagicMock

    tz = ZoneInfo("UTC")

    # Pass a mock object as dt whose astimezone() returns None utcoffset
    mock_dt = MagicMock()
    mock_aware = MagicMock()
    mock_aware.utcoffset.return_value = None
    mock_dt.astimezone.return_value = mock_aware

    result = _utc_offset_str(tz, mock_dt)  # type: ignore[arg-type]
    assert result == "+00:00"


def test_is_dst_returns_false_when_dst_none() -> None:
    """When dst() returns None _is_dst returns False."""
    from unittest.mock import MagicMock

    from prismal.mcp.servers.datetime_server import _is_dst

    tz = ZoneInfo("UTC")

    mock_dt = MagicMock()
    mock_aware = MagicMock()
    mock_aware.dst.return_value = None
    mock_dt.astimezone.return_value = mock_aware

    result = _is_dst(tz, mock_dt)  # type: ignore[arg-type]
    assert result is False


def test_describe_cron_every_minute_past_hour() -> None:
    """'* 9 * * *' → 'Every minute past hour 09 every day'."""
    desc = _describe_cron("* 9 * * *")
    assert "Every minute past hour" in desc
    assert "09" in desc


def test_describe_cron_every_minute() -> None:
    """'* * * * *' → 'Every minute every day'."""
    desc = _describe_cron("* * * * *")
    assert "Every minute" in desc


def test_describe_cron_dom_pattern() -> None:
    """'0 9 15 * *' → mentions 'day 15'."""
    desc = _describe_cron("0 9 15 * *")
    assert "15" in desc


def test_describe_cron_month_pattern() -> None:
    """'0 9 * 6 *' → mentions June."""
    desc = _describe_cron("0 9 * 6 *")
    assert "June" in desc


def test_describe_cron_fallback_on_expand_error() -> None:
    """_describe_cron returns the original schedule if croniter.expand raises."""
    from unittest.mock import patch as mpatch

    schedule = "0 9 * * *"
    with mpatch(
        "lightagent.mcp.servers.datetime_server.croniter.expand",
        side_effect=Exception("bad"),
    ):
        result = _describe_cron(schedule)
    assert result == schedule


def test_describe_cron_complex_no_freq_part() -> None:
    """Complex expression produces a result without crashing."""
    # Multiple DOW values — falls into the else branch for freq_part
    desc = _describe_cron("0 9 * * 1,3")
    # Should not raise; description may be the raw schedule or a partial string
    assert isinstance(desc, str)


def test_get_timezone_info_os_tz_fallback() -> None:
    """When tzlocal.get_localzone() raises, os_timezone defaults to 'UTC'."""
    with patch(
        "lightagent.mcp.servers.datetime_server.tzlocal.get_localzone",
        side_effect=Exception("no tz db"),
    ):
        result = datetime_get_timezone_info()
    assert result.os_timezone == "UTC"
    assert isinstance(result, TimezoneInfoResult)


def test_convert_with_aware_dt_iso() -> None:
    """dt_iso with embedded UTC offset is parsed without attaching from_tz."""
    result = datetime_convert(
        dt_iso="2026-03-28T09:00:00+00:00",
        from_tz="America/Caracas",  # should be ignored since dt_iso is aware
        to_tz="America/Caracas",
    )
    assert result.success is True
    assert result.result_iso8601 is not None
    # 09:00 UTC → 05:00 Caracas (UTC-4)
    assert "05:00:00" in result.result_iso8601


def test_cron_next_run_naive_fire_time() -> None:
    """next_run attaches tzinfo when croniter returns a naive datetime."""
    from unittest.mock import MagicMock
    from unittest.mock import patch as mpatch

    naive_dt = datetime(2026, 4, 1, 9, 0, 0)  # no tzinfo
    mock_cron = MagicMock()
    mock_cron.get_next.return_value = naive_dt

    with mpatch(
        "lightagent.mcp.servers.datetime_server.croniter", return_value=mock_cron
    ) as mock_cls:
        mock_cls.is_valid.return_value = True
        result = datetime_cron_next_run(schedule="0 9 * * *", count=1, timezone="UTC")

    assert result.success is True
    assert len(result.next_runs) == 1

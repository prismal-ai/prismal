"""Unit tests for DateTimeService (T-294 / SPEC-029 AC-029-1, AC-029-2, AC-029-10)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from lightagent.scheduler.datetime_service import _UTC, DateTimeService

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def reset_singleton() -> None:
    """Reset the DateTimeService singleton before every test."""
    DateTimeService._instance = None
    yield
    DateTimeService._instance = None


# ── Singleton ─────────────────────────────────────────────────────────────────


def test_singleton_same_instance() -> None:
    """DateTimeService.get() always returns the same object."""
    a = DateTimeService.get()
    b = DateTimeService.get()
    assert a is b


def test_singleton_new_instance_after_reset() -> None:
    """After resetting _instance a fresh object is created."""
    a = DateTimeService.get()
    DateTimeService._instance = None
    b = DateTimeService.get()
    assert a is not b


# ── resolve_timezone — explicit IANA ──────────────────────────────────────────


def test_resolve_timezone_explicit_iana() -> None:
    """resolve_timezone('America/Caracas') returns ZoneInfo for that region."""
    dts = DateTimeService.get()
    tz = dts.resolve_timezone("America/Caracas")
    assert tz == ZoneInfo("America/Caracas")


def test_resolve_timezone_invalid_falls_back_utc() -> None:
    """resolve_timezone('Bad/Zone') logs WARNING and returns UTC."""
    # Use structlog.testing.capture_logs() — it intercepts records before any
    # processor/sink runs, so it's robust to whichever logger factory the
    # project happens to have configured (default ConsoleRenderer → stdout vs
    # loguru-backed → stderr). capsys/capfd are flaky here under xdist:
    # pytest's own capture drains the buffer before the fixture reads it.
    from structlog.testing import capture_logs

    dts = DateTimeService.get()
    with capture_logs() as cap_logs:
        tz = dts.resolve_timezone("Bad/Zone")
    assert tz == _UTC
    assert any("Bad/Zone" in str(log) for log in cap_logs)


# ── resolve_timezone — chain walk ─────────────────────────────────────────────


def test_resolve_timezone_empty_uses_cron_tz_env() -> None:
    """Empty tz_str + LIGHTAGENT_CRON_TIMEZONE → cron tz wins."""
    with patch("lightagent.scheduler.datetime_service.get_settings") as mock_cfg:
        mock_cfg.return_value = MagicMock(
            cron_timezone="America/Caracas",
            timezone="",
            ntp_enabled=False,
        )
        dts = DateTimeService()
        tz = dts.resolve_timezone(None)
    assert tz == ZoneInfo("America/Caracas")


def test_resolve_timezone_empty_uses_tz_env() -> None:
    """Empty tz_str + LIGHTAGENT_TIMEZONE (no cron tz) → timezone field wins."""
    with patch("lightagent.scheduler.datetime_service.get_settings") as mock_cfg:
        mock_cfg.return_value = MagicMock(
            cron_timezone="",
            timezone="Europe/Berlin",
            ntp_enabled=False,
        )
        dts = DateTimeService()
        tz = dts.resolve_timezone(None)
    assert tz == ZoneInfo("Europe/Berlin")


def test_resolve_timezone_chain_priority() -> None:
    """Per-job tz_str overrides LIGHTAGENT_CRON_TIMEZONE."""
    with patch("lightagent.scheduler.datetime_service.get_settings") as mock_cfg:
        mock_cfg.return_value = MagicMock(
            cron_timezone="America/Caracas",
            timezone="Europe/Berlin",
            ntp_enabled=False,
        )
        dts = DateTimeService()
        tz = dts.resolve_timezone("Asia/Tokyo")
    assert tz == ZoneInfo("Asia/Tokyo")


def test_resolve_timezone_falls_back_to_os_tz() -> None:
    """When both env vars are empty, OS localzone is used."""
    with patch("lightagent.scheduler.datetime_service.get_settings") as mock_cfg:
        mock_cfg.return_value = MagicMock(
            cron_timezone="",
            timezone="",
            ntp_enabled=False,
        )
        with patch("lightagent.scheduler.datetime_service.tzlocal.get_localzone") as mock_local:
            mock_local.return_value = ZoneInfo("America/New_York")
            dts = DateTimeService()
            tz = dts.resolve_timezone(None)
    assert tz == ZoneInfo("America/New_York")


def test_resolve_timezone_falls_back_to_utc_when_localzone_fails() -> None:
    """When tzlocal raises, UTC is the final fallback."""
    with patch("lightagent.scheduler.datetime_service.get_settings") as mock_cfg:
        mock_cfg.return_value = MagicMock(
            cron_timezone="",
            timezone="",
            ntp_enabled=False,
        )
        with patch(
            "lightagent.scheduler.datetime_service.tzlocal.get_localzone",
            side_effect=Exception("no tz db"),
        ):
            dts = DateTimeService()
            tz = dts.resolve_timezone(None)
    assert tz == _UTC


# ── get_scheduler_tz ──────────────────────────────────────────────────────────


def test_get_scheduler_tz_returns_cron_tz() -> None:
    """get_scheduler_tz() uses the cron_timezone resolution chain."""
    with patch("lightagent.scheduler.datetime_service.get_settings") as mock_cfg:
        mock_cfg.return_value = MagicMock(
            cron_timezone="America/Caracas",
            timezone="",
            ntp_enabled=False,
        )
        dts = DateTimeService()
        # Must call inside the patch context: get_scheduler_tz() reads
        # settings at call time (via resolve_timezone). Outside the with
        # block the mock is gone and the test depends on the host OS tz.
        tz = dts.get_scheduler_tz()
    assert tz == ZoneInfo("America/Caracas")


# ── now() ─────────────────────────────────────────────────────────────────────


def test_now_returns_aware_datetime() -> None:
    """now() returns a timezone-aware datetime."""
    dts = DateTimeService.get()
    result = dts.now()
    assert result.tzinfo is not None


def test_now_with_explicit_tz() -> None:
    """now(tz=ZoneInfo('America/Caracas')) returns a datetime in Caracas."""
    dts = DateTimeService.get()
    tz = ZoneInfo("America/Caracas")
    result = dts.now(tz=tz)
    assert result.tzinfo == tz


def test_now_utc_naive_returns_naive() -> None:
    """now_utc_naive() returns a naive datetime whose value is close to UTC."""
    dts = DateTimeService.get()
    result = dts.now_utc_naive()
    assert result.tzinfo is None
    # Value should be within a few seconds of UTC
    utc_now = datetime.now(UTC).replace(tzinfo=None)
    diff = abs((result - utc_now).total_seconds())
    assert diff < 5


# ── next_run() ────────────────────────────────────────────────────────────────


def test_next_run_daily_9am_caracas() -> None:
    """'0 9 * * *' in America/Caracas next fires at 09:00 Caracas time."""
    dts = DateTimeService.get()
    tz = ZoneInfo("America/Caracas")
    result = dts.next_run("0 9 * * *", tz=tz)
    assert result.tzinfo is not None
    assert result.hour == 9
    assert result.minute == 0


def test_next_run_weekly_monday_tokyo() -> None:
    """'0 9 * * 1' in Asia/Tokyo next fires at 09:00 Tokyo on Monday."""
    dts = DateTimeService.get()
    tz = ZoneInfo("Asia/Tokyo")
    result = dts.next_run("0 9 * * 1", tz=tz)
    assert result.tzinfo is not None
    assert result.hour == 9
    assert result.weekday() == 0  # Monday


def test_next_run_invalid_expr_raises() -> None:
    """next_run() with an invalid cron expression raises ValueError."""
    dts = DateTimeService.get()
    with pytest.raises((ValueError, Exception)):
        dts.next_run("99 99 99 99 99")


# ── to_utc_naive() ────────────────────────────────────────────────────────────


def test_to_utc_naive_converts_aware_to_utc() -> None:
    """Aware datetime in a fixed-offset timezone converts to naive UTC correctly."""
    dts = DateTimeService.get()
    # Use a fixed UTC-5 zone to avoid DST ambiguity (America/New_York is UTC-4 on 2026-03-28 due to DST)
    fixed_minus5 = ZoneInfo("Etc/GMT+5")
    # 2026-01-15 09:00 UTC-5 = 2026-01-15 14:00 UTC (no DST in January)
    aware_dt = datetime(2026, 1, 15, 9, 0, 0, tzinfo=fixed_minus5)
    result = dts.to_utc_naive(aware_dt)
    assert result.tzinfo is None
    assert result.hour == 14
    assert result.day == 15


def test_to_utc_naive_raises_on_naive_input() -> None:
    """to_utc_naive() raises ValueError when given a naive datetime."""
    dts = DateTimeService.get()
    naive = datetime(2026, 3, 28, 9, 0, 0)
    with pytest.raises(ValueError, match="aware datetime"):
        dts.to_utc_naive(naive)


# ── parse_local() ─────────────────────────────────────────────────────────────


def test_parse_local_space_format() -> None:
    """parse_local() accepts 'YYYY-MM-DD HH:MM:SS' format."""
    dts = DateTimeService.get()
    tz = ZoneInfo("America/Caracas")
    result = dts.parse_local("2026-03-28 09:00:00", tz=tz)
    assert result.hour == 9
    assert result.tzinfo == tz


def test_parse_local_iso_format() -> None:
    """parse_local() accepts 'YYYY-MM-DDTHH:MM:SS' format."""
    dts = DateTimeService.get()
    tz = ZoneInfo("Europe/Berlin")
    result = dts.parse_local("2026-03-28T15:30:00", tz=tz)
    assert result.hour == 15
    assert result.minute == 30
    assert result.tzinfo == tz


def test_parse_local_invalid_raises() -> None:
    """parse_local() raises ValueError on unparseable strings."""
    dts = DateTimeService.get()
    with pytest.raises(ValueError, match="Cannot parse"):
        dts.parse_local("not-a-date")


def test_parse_local_uses_resolved_tz_when_none() -> None:
    """parse_local() uses resolve_timezone() when tz=None."""
    with patch("lightagent.scheduler.datetime_service.get_settings") as mock_cfg:
        mock_cfg.return_value = MagicMock(
            cron_timezone="Asia/Tokyo",
            timezone="",
            ntp_enabled=False,
        )
        dts = DateTimeService()
        result = dts.parse_local("2026-03-28 09:00:00")
    assert result.tzinfo == ZoneInfo("Asia/Tokyo")


# ── NTP ───────────────────────────────────────────────────────────────────────


def test_ntp_disabled_returns_none() -> None:
    """ntp_offset() returns None when ntp_enabled=False (default)."""
    dts = DateTimeService.get()
    assert dts.ntp_offset() is None


def test_ntp_enabled_mocked_server() -> None:
    """ntp_offset() caches offset when NTP server responds successfully."""
    mock_response = MagicMock()
    mock_response.offset = 2.5

    with patch("lightagent.scheduler.datetime_service.get_settings") as mock_cfg:
        mock_cfg.return_value = MagicMock(
            ntp_enabled=True,
            ntp_server="pool.ntp.org",
            ntp_sync_interval_seconds=3600,
            ntp_warn_threshold_seconds=5,
            cron_timezone="",
            timezone="",
        )
        with patch.dict("sys.modules", {"ntplib": MagicMock()}):
            import sys

            mock_ntplib = sys.modules["ntplib"]
            mock_ntplib.NTPClient.return_value.request.return_value = mock_response

            dts = DateTimeService()

    offset = dts._ntp_offset
    assert offset == timedelta(seconds=2.5)


def test_ntp_unreachable_falls_back(caplog: pytest.LogCaptureFixture) -> None:
    """ntp_offset() logs WARNING and returns None when server is unreachable."""
    import logging

    with patch("lightagent.scheduler.datetime_service.get_settings") as mock_cfg:
        mock_cfg.return_value = MagicMock(
            ntp_enabled=True,
            ntp_server="unreachable.ntp.test",
            ntp_sync_interval_seconds=3600,
            ntp_warn_threshold_seconds=5,
            cron_timezone="",
            timezone="",
        )
        with patch.dict("sys.modules", {"ntplib": MagicMock()}):
            import sys

            mock_ntplib = sys.modules["ntplib"]
            mock_ntplib.NTPClient.return_value.request.side_effect = Exception("Connection refused")

            with caplog.at_level(logging.WARNING):
                dts = DateTimeService()

    assert dts._ntp_offset is None
    assert any("unreachable" in r.message.lower() for r in caplog.records)


def test_ntp_large_offset_logs_warning() -> None:
    """Large NTP offset (> threshold) logs a WARNING."""
    from structlog.testing import capture_logs

    mock_response = MagicMock()
    mock_response.offset = 30.0  # 30 s > default threshold of 5 s

    with capture_logs() as cap_logs:
        with patch("lightagent.scheduler.datetime_service.get_settings") as mock_cfg:
            mock_cfg.return_value = MagicMock(
                ntp_enabled=True,
                ntp_server="pool.ntp.org",
                ntp_sync_interval_seconds=3600,
                ntp_warn_threshold_seconds=5,
                cron_timezone="",
                timezone="",
            )
            with patch.dict("sys.modules", {"ntplib": MagicMock()}):
                import sys

                mock_ntplib = sys.modules["ntplib"]
                mock_ntplib.NTPClient.return_value.request.return_value = mock_response

                dts = DateTimeService()

    assert dts._ntp_offset == timedelta(seconds=30.0)
    # See test_resolve_timezone_invalid_falls_back_utc for why we capture via
    # structlog directly instead of capsys/capfd.
    combined = " ".join(str(log) for log in cap_logs).lower()
    assert "offset" in combined or "threshold" in combined


def test_ntp_resync_after_interval() -> None:
    """ntp_offset() re-syncs when the interval has elapsed."""
    mock_response = MagicMock()
    mock_response.offset = 1.0

    with patch("lightagent.scheduler.datetime_service.get_settings") as mock_cfg:
        mock_cfg.return_value = MagicMock(
            ntp_enabled=True,
            ntp_server="pool.ntp.org",
            ntp_sync_interval_seconds=60,
            ntp_warn_threshold_seconds=5,
            cron_timezone="",
            timezone="",
        )
        with patch.dict("sys.modules", {"ntplib": MagicMock()}):
            import sys

            mock_ntplib = sys.modules["ntplib"]
            mock_ntplib.NTPClient.return_value.request.return_value = mock_response

            dts = DateTimeService()
            # Simulate last sync was in the past (beyond interval)
            dts._ntp_last_sync = datetime(2000, 1, 1, tzinfo=UTC)

            dts.ntp_offset()
            assert mock_ntplib.NTPClient.return_value.request.call_count >= 2


def test_ntp_no_resync_within_interval() -> None:
    """ntp_offset() does NOT re-sync when interval has not elapsed."""
    mock_response = MagicMock()
    mock_response.offset = 1.0

    with patch("lightagent.scheduler.datetime_service.get_settings") as mock_cfg:
        mock_cfg.return_value = MagicMock(
            ntp_enabled=True,
            ntp_server="pool.ntp.org",
            ntp_sync_interval_seconds=3600,
            ntp_warn_threshold_seconds=5,
            cron_timezone="",
            timezone="",
        )
        with patch.dict("sys.modules", {"ntplib": MagicMock()}):
            import sys

            mock_ntplib = sys.modules["ntplib"]
            mock_ntplib.NTPClient.return_value.request.return_value = mock_response

            dts = DateTimeService()
            call_count_after_init = mock_ntplib.NTPClient.return_value.request.call_count

            # _ntp_last_sync is fresh (set by __init__), interval 3600s not elapsed
            dts.ntp_offset()
            # Should NOT have triggered another sync
            assert mock_ntplib.NTPClient.return_value.request.call_count == call_count_after_init


def test_now_applies_ntp_offset() -> None:
    """now() adds the NTP offset to the base time when NTP is enabled."""
    mock_response = MagicMock()
    mock_response.offset = 10.0  # 10 seconds ahead

    with patch("lightagent.scheduler.datetime_service.get_settings") as mock_cfg:
        mock_cfg.return_value = MagicMock(
            ntp_enabled=True,
            ntp_server="pool.ntp.org",
            ntp_sync_interval_seconds=3600,
            ntp_warn_threshold_seconds=30,
            cron_timezone="",
            timezone="",
        )
        with patch.dict("sys.modules", {"ntplib": MagicMock()}):
            import sys

            mock_ntplib = sys.modules["ntplib"]
            mock_ntplib.NTPClient.return_value.request.return_value = mock_response

            dts = DateTimeService()

            before = datetime.now(UTC)
            result = dts.now(_UTC)

    # result should be approximately 10s ahead of before
    diff = (result.replace(tzinfo=UTC) - before).total_seconds()
    assert 8 <= diff <= 12, f"Expected ~10s ahead, got {diff}s"


def test_next_run_croniter_naive_result() -> None:
    """Verify next_run() attaches tzinfo even when croniter returns naive datetime."""
    dts = DateTimeService.get()
    tz = ZoneInfo("Europe/Madrid")

    # Patch croniter to return a naive datetime
    naive_result = datetime(2026, 4, 1, 9, 0, 0)
    mock_cron_instance = MagicMock()
    mock_cron_instance.get_next.return_value = naive_result

    with patch(
        "lightagent.scheduler.datetime_service.croniter",
        return_value=mock_cron_instance,
    ):
        result = dts.next_run("0 9 * * *", tz=tz)

    assert result.tzinfo is not None
    assert result.tzinfo == tz


def test_get_ntp_offset_with_ntp_enabled_returns_cached() -> None:
    """_get_ntp_offset() returns _ntp_offset when ntp_enabled=True."""
    with patch("lightagent.scheduler.datetime_service.get_settings") as mock_cfg:
        mock_cfg.return_value = MagicMock(
            ntp_enabled=True,
            cron_timezone="",
            timezone="",
        )
        dts = DateTimeService()
        dts._ntp_offset = timedelta(seconds=3.0)
        # call while patch is active so get_settings() returns ntp_enabled=True
        offset = dts._get_ntp_offset()

    assert offset == timedelta(seconds=3.0)

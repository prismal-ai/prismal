"""Integration tests — cron timezone end-to-end (T-298 / SPEC-029 AC-029-3 to AC-029-5).

These tests exercise the full path from CronManager job definition through
CronExecutor._register_job() to the APScheduler trigger that is actually
constructed — verifying that timezone information is correctly propagated at
every step without any real APScheduler scheduling or LLM calls.

Test scenarios:
  1. Recurring job in America/Caracas fires with Caracas timezone, not UTC.
  2. Once-job at 09:00 VET in a UTC container uses the correct aware run_date.
  3. Once-job with a past run_date is silently skipped and never added.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_executor(tmp_path: Path) -> tuple[object, MagicMock, MagicMock]:
    """Return (executor, mock_scheduler, mock_manager) wired together.

    The CronManager is backed by a real temp-dir SQLite database so that
    job persistence (add / list_jobs) is exercised end-to-end.
    The APScheduler is mocked so no real scheduling occurs.
    """
    from prismal.scheduler.cron_manager import CronManager
    from prismal.scheduler.executor import CronExecutor

    db_path = tmp_path / "cron_tz_e2e.db"
    manager = CronManager(db_path=db_path)

    mock_scheduler = MagicMock()
    mock_scheduler.running = False  # hasn't been started yet

    executor = CronExecutor(manager=manager)
    executor._scheduler = mock_scheduler  # swap out real APScheduler
    return executor, mock_scheduler, manager


# ── Test 1: recurring job uses the correct per-job timezone ───────────────────


@pytest.mark.asyncio
async def test_recurring_job_fires_in_user_timezone(tmp_path: Path) -> None:
    """Schedule '0 9 * * *' in America/Caracas inside a UTC container.

    AC-029-3: CronTrigger must be constructed with timezone=America/Caracas,
    not with the system (UTC) timezone.
    """
    executor, mock_scheduler, manager = _make_executor(tmp_path)

    # Add a recurring job with an explicit Caracas timezone
    manager.add("daily-brief", "0 9 * * *", "Send daily brief", timezone="America/Caracas")

    caracas_tz = ZoneInfo("America/Caracas")

    with patch("lightagent.scheduler.executor.CronTrigger.from_crontab") as mock_from_crontab:
        mock_from_crontab.return_value = MagicMock()  # trigger stub

        # Simulate system timezone = UTC (container environment)
        with patch(
            "lightagent.scheduler.datetime_service.tzlocal.get_localzone",
            return_value=ZoneInfo("UTC"),
        ):
            mock_scheduler.running = False
            await executor.start()

    # CronTrigger.from_crontab must have been called exactly once
    mock_from_crontab.assert_called_once()
    _, kwargs = mock_from_crontab.call_args
    trigger_tz = kwargs.get("timezone")

    assert trigger_tz is not None, "CronTrigger.from_crontab must receive a timezone kwarg"
    assert trigger_tz == caracas_tz, (
        f"Expected trigger timezone America/Caracas, got {trigger_tz!r}. "
        "Per-job timezone must override the system (UTC) timezone."
    )


# ── Test 2: once-job run_date carries the correct timezone ────────────────────


@pytest.mark.asyncio
async def test_once_job_fires_correctly_in_utc_container(tmp_path: Path) -> None:
    """Once-job at 09:00 VET (America/Caracas, UTC-4) in a UTC container.

    AC-029-4: The DateTrigger run_date must be timezone-aware in Caracas time,
    so APScheduler fires the job at the correct wall-clock instant (13:00 UTC).
    """
    executor, mock_scheduler, manager = _make_executor(tmp_path)

    # Future date guaranteed to be in the future (year 2099)
    run_at_dt = datetime(2099, 6, 15, 9, 0, 0)  # naive — add_once attaches timezone internally
    expected_tz = ZoneInfo("America/Caracas")
    manager.add_once("morning-alert", run_at_dt, "Morning alert", timezone="America/Caracas")

    # DateTrigger is imported lazily inside _register_job; capture via sys.modules stub.
    import sys

    mock_date_trigger_cls = MagicMock()
    captured_trigger: list[object] = []

    def capture_trigger(**kwargs: object) -> MagicMock:
        captured_trigger.append(kwargs.get("run_date"))
        return MagicMock()

    mock_date_trigger_cls.side_effect = capture_trigger
    sys.modules["apscheduler.triggers.date"].DateTrigger = mock_date_trigger_cls  # type: ignore[attr-defined]

    # Simulate UTC container — system TZ is UTC
    with patch(
        "lightagent.scheduler.datetime_service.tzlocal.get_localzone",
        return_value=ZoneInfo("UTC"),
    ):
        mock_scheduler.running = False
        await executor.start()

    assert captured_trigger, "DateTrigger must have been instantiated for the once-job"
    run_date = captured_trigger[0]
    assert isinstance(run_date, datetime), f"Expected datetime, got {type(run_date)}"
    assert run_date.tzinfo is not None, "run_date must be timezone-aware (BUG-001 fix)"
    assert run_date.tzinfo == expected_tz, (
        f"run_date timezone should be America/Caracas, got {run_date.tzinfo!r}"
    )
    assert run_date.hour == 9, f"Wall-clock hour in Caracas must be 09:00, got {run_date.hour}"

    # Cross-check: converting to UTC must give 13:00 (UTC-4 = +4 hours)
    utc_equivalent = run_date.astimezone(UTC)
    assert utc_equivalent.hour == 13, (
        f"UTC equivalent should be 13:00 (Caracas UTC-4), got {utc_equivalent.hour}"
    )


# ── Test 3: expired once-job is silently skipped ──────────────────────────────


@pytest.mark.asyncio
async def test_once_job_past_time_is_not_scheduled(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Schedule a once-job with a run_date 5 minutes in the past.

    AC-029-5: The executor must skip the job (no APScheduler job added) and
    emit a WARNING-level log instead of raising an exception.
    """
    executor, mock_scheduler, manager = _make_executor(tmp_path)

    # Create a run_date that is 5 minutes in the past in Caracas time
    now_caracas = datetime.now(ZoneInfo("America/Caracas"))
    past_time = now_caracas - timedelta(minutes=5)
    past_str = past_time.strftime("%Y-%m-%dT%H:%M:%S")

    # Bypass the normal add_once validation by inserting directly via add_once
    # (add_once itself may reject past dates — use manager internals to force it)
    # We insert a "once:" job directly via the DB to simulate an expired job
    # that was valid when registered but has since passed.
    db_path = tmp_path / "cron_tz_e2e.db"
    import sqlite3
    from datetime import UTC as _UTC

    conn = sqlite3.connect(str(db_path))
    now_utc_naive = datetime.now(_UTC).replace(tzinfo=None)
    _DT_FMT = "%Y-%m-%dT%H:%M:%S"
    conn.execute(
        """
        INSERT INTO cron_jobs
            (name, schedule, task, status, created_at, timezone)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            "expired-alert",
            f"once:{past_str}",
            "Expired task",
            "active",
            now_utc_naive.strftime(_DT_FMT),
            "America/Caracas",
        ),
    )
    conn.commit()
    conn.close()

    with patch(
        "lightagent.scheduler.datetime_service.tzlocal.get_localzone",
        return_value=ZoneInfo("UTC"),
    ):
        mock_scheduler.running = False
        await executor.start()

    # APScheduler must NOT have had any job added
    mock_scheduler.add_job.assert_not_called()

    # structlog writes warnings to stdout in test environments
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert "skip" in output.lower() or "expired" in output.lower() or "expired-alert" in output, (
        f"Expected a warning about the skipped job in stdout, got:\n{output}"
    )

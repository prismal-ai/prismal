"""Unit tests for lightagent.scheduler.cron_manager (T-081)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from lightagent.scheduler.cron_manager import CronJob, CronManager, CronRunRecord, CronStatus


# ── CronJob model ─────────────────────────────────────────────────────────────


def test_cron_job_defaults() -> None:
    """CronJob sets correct defaults."""
    job = CronJob(name="daily-report", schedule="0 9 * * *", task="Run daily report")
    assert job.status == "active"
    assert job.last_run is None
    assert job.next_run is None
    assert isinstance(job.created_at, datetime)


def test_cron_job_paused_status() -> None:
    """CronJob accepts 'paused' status."""
    job = CronJob(name="x", schedule="* * * * *", task="t", status="paused")
    assert job.status == "paused"


# ── CronManager.add ──────────────────────────────────────────────────────────


def test_add_creates_job(tmp_path: Path) -> None:
    """add() creates and persists a new cron job."""
    manager = CronManager(db_path=tmp_path / "cron.db")
    job = manager.add("morning-brief", "0 9 * * *", "Morning briefing")
    assert job.name == "morning-brief"
    assert job.schedule == "0 9 * * *"
    assert job.status == "active"
    assert job.next_run is not None
    assert isinstance(job.next_run, datetime)


def test_add_duplicate_raises(tmp_path: Path) -> None:
    """add() raises ValueError when job name already exists."""
    manager = CronManager(db_path=tmp_path / "cron.db")
    manager.add("dup", "0 9 * * *", "First")
    with pytest.raises(ValueError, match="already exists"):
        manager.add("dup", "0 10 * * *", "Second")


def test_add_returns_cron_job_instance(tmp_path: Path) -> None:
    """add() returns a CronJob instance."""
    manager = CronManager(db_path=tmp_path / "cron.db")
    result = manager.add("j1", "* * * * *", "Task")
    assert isinstance(result, CronJob)
    assert result.next_run is not None


def test_add_computes_next_run(tmp_path: Path) -> None:
    """add() computes next_run in the future from the cron expression."""
    manager = CronManager(db_path=tmp_path / "cron.db")
    before = datetime.now(UTC).replace(tzinfo=None)
    job = manager.add("future-job", "0 9 * * *", "Daily job")
    assert job.next_run is not None
    assert job.next_run > before


def test_resume_updates_next_run(tmp_path: Path) -> None:
    """resume() recomputes and persists next_run after unpausing."""
    manager = CronManager(db_path=tmp_path / "cron.db")
    manager.add("resumable", "0 9 * * *", "Daily job")
    manager.pause("resumable")
    before = datetime.now(UTC).replace(tzinfo=None)
    manager.resume("resumable")
    job = manager.get_job("resumable")
    assert job is not None
    assert job.status == "active"
    assert job.next_run is not None
    assert job.next_run > before


# ── CronManager.list_jobs ─────────────────────────────────────────────────────


def test_list_jobs_empty(tmp_path: Path) -> None:
    """list_jobs() returns empty list when no jobs exist."""
    manager = CronManager(db_path=tmp_path / "cron.db")
    assert manager.list_jobs() == []


def test_list_jobs_returns_all(tmp_path: Path) -> None:
    """list_jobs() returns all added jobs."""
    manager = CronManager(db_path=tmp_path / "cron.db")
    manager.add("job-a", "0 1 * * *", "Task A")
    manager.add("job-b", "0 2 * * *", "Task B")
    jobs = manager.list_jobs()
    assert len(jobs) == 2
    names = {j.name for j in jobs}
    assert "job-a" in names
    assert "job-b" in names


# ── CronManager.pause ────────────────────────────────────────────────────────


def test_pause_changes_status(tmp_path: Path) -> None:
    """pause() sets job status to 'paused'."""
    manager = CronManager(db_path=tmp_path / "cron.db")
    manager.add("nightly", "0 0 * * *", "Nightly task")
    manager.pause("nightly")
    job = manager.get_job("nightly")
    assert job is not None
    assert job.status == "paused"


def test_pause_unknown_job_raises(tmp_path: Path) -> None:
    """pause() raises KeyError for unknown job name."""
    manager = CronManager(db_path=tmp_path / "cron.db")
    with pytest.raises(KeyError):
        manager.pause("ghost")


# ── CronManager.resume ───────────────────────────────────────────────────────


def test_resume_changes_status(tmp_path: Path) -> None:
    """resume() sets job status back to 'active'."""
    manager = CronManager(db_path=tmp_path / "cron.db")
    manager.add("weekly", "0 0 * * 0", "Weekly task")
    manager.pause("weekly")
    manager.resume("weekly")
    job = manager.get_job("weekly")
    assert job is not None
    assert job.status == "active"


def test_resume_unknown_job_raises(tmp_path: Path) -> None:
    """resume() raises KeyError for unknown job name."""
    manager = CronManager(db_path=tmp_path / "cron.db")
    with pytest.raises(KeyError):
        manager.resume("ghost")


# ── CronManager.remove ───────────────────────────────────────────────────────


def test_remove_deletes_job(tmp_path: Path) -> None:
    """remove() deletes a job from the store."""
    manager = CronManager(db_path=tmp_path / "cron.db")
    manager.add("temp", "* * * * *", "Temp task")
    manager.remove("temp")
    assert manager.get_job("temp") is None
    assert len(manager.list_jobs()) == 0


def test_remove_unknown_job_raises(tmp_path: Path) -> None:
    """remove() raises KeyError for unknown job name."""
    manager = CronManager(db_path=tmp_path / "cron.db")
    with pytest.raises(KeyError):
        manager.remove("ghost")


# ── CronManager.get_job ──────────────────────────────────────────────────────


def test_get_job_returns_none_for_missing(tmp_path: Path) -> None:
    """get_job() returns None when job doesn't exist."""
    manager = CronManager(db_path=tmp_path / "cron.db")
    assert manager.get_job("missing") is None


def test_get_job_returns_correct_fields(tmp_path: Path) -> None:
    """get_job() returns a job with correct field values."""
    manager = CronManager(db_path=tmp_path / "cron.db")
    manager.add("check", "0 8 * * 1-5", "Weekday check")
    job = manager.get_job("check")
    assert job is not None
    assert job.name == "check"
    assert job.schedule == "0 8 * * 1-5"
    assert job.task == "Weekday check"


# ── CronManager.update_last_run ──────────────────────────────────────────────


def test_update_last_run_persists(tmp_path: Path) -> None:
    """update_last_run() stores a timestamp that survives re-read."""
    manager = CronManager(db_path=tmp_path / "cron.db")
    manager.add("stamped", "* * * * *", "Time it")
    ts = datetime(2026, 1, 1, 9, 0, 0)
    manager.update_last_run("stamped", ts)
    job = manager.get_job("stamped")
    assert job is not None
    assert job.last_run is not None
    assert job.last_run.year == 2026


# ── Persistence across instances ─────────────────────────────────────────────


def test_persistence_across_instances(tmp_path: Path) -> None:
    """Jobs survive CronManager reconstruction (SQLite persistence)."""
    db = tmp_path / "cron.db"
    mgr1 = CronManager(db_path=db)
    mgr1.add("persist-me", "0 6 * * *", "Persisted task")

    mgr2 = CronManager(db_path=db)
    job = mgr2.get_job("persist-me")
    assert job is not None
    assert job.schedule == "0 6 * * *"


def test_pause_persists_across_instances(tmp_path: Path) -> None:
    """Paused status persists across CronManager instances."""
    db = tmp_path / "cron.db"
    mgr1 = CronManager(db_path=db)
    mgr1.add("reboot-safe", "0 12 * * *", "Noon task")
    mgr1.pause("reboot-safe")

    mgr2 = CronManager(db_path=db)
    job = mgr2.get_job("reboot-safe")
    assert job is not None
    assert job.status == "paused"


# ── CronRunHistory ────────────────────────────────────────────────────────────


class TestCronRunHistory:
    """Tests for cron run history persistence."""

    def test_add_run_record_success(self, tmp_path: Path) -> None:
        """add_run_record stores a successful run in the DB."""
        manager = CronManager(db_path=tmp_path / "cron.db")
        manager.add("hist-job", "* * * * *", "task")
        started = datetime(2026, 3, 9, 10, 0, 0)
        finished = datetime(2026, 3, 9, 10, 0, 5)
        manager.add_run_record(
            "hist-job",
            started_at=started,
            finished_at=finished,
            outcome="success",
            output="done",
        )
        records = manager.get_run_history("hist-job")
        assert len(records) == 1
        r = records[0]
        assert r.job_name == "hist-job"
        assert r.outcome == "success"
        assert r.output == "done"
        assert r.error is None
        assert r.duration_seconds == 5.0

    def test_add_run_record_failure(self, tmp_path: Path) -> None:
        """add_run_record stores a failed run with error message."""
        manager = CronManager(db_path=tmp_path / "cron.db")
        manager.add("fail-job", "* * * * *", "task")
        started = datetime(2026, 3, 9, 10, 0, 0)
        finished = datetime(2026, 3, 9, 10, 0, 2)
        manager.add_run_record(
            "fail-job",
            started_at=started,
            finished_at=finished,
            outcome="failure",
            error="something went wrong",
        )
        records = manager.get_run_history("fail-job")
        assert len(records) == 1
        r = records[0]
        assert r.outcome == "failure"
        assert r.error == "something went wrong"
        assert r.output is None

    def test_get_run_history_empty(self, tmp_path: Path) -> None:
        """get_run_history returns empty list for a job with no runs."""
        manager = CronManager(db_path=tmp_path / "cron.db")
        manager.add("no-run-job", "* * * * *", "task")
        assert manager.get_run_history("no-run-job") == []

    def test_get_run_history_limit(self, tmp_path: Path) -> None:
        """get_run_history respects limit parameter."""
        from datetime import timedelta

        manager = CronManager(db_path=tmp_path / "cron.db")
        manager.add("limit-job", "* * * * *", "task")
        base = datetime(2026, 3, 9, 10, 0, 0)
        for i in range(5):
            manager.add_run_record(
                "limit-job",
                started_at=base + timedelta(minutes=i),
                finished_at=base + timedelta(minutes=i, seconds=1),
                outcome="success",
                output=f"run {i}",
            )
        records = manager.get_run_history("limit-job", limit=3)
        assert len(records) == 3

    def test_get_run_history_ordered_newest_first(self, tmp_path: Path) -> None:
        """get_run_history returns records newest-first."""
        from datetime import timedelta

        manager = CronManager(db_path=tmp_path / "cron.db")
        manager.add("order-job", "* * * * *", "task")
        base = datetime(2026, 3, 9, 10, 0, 0)
        for i in range(3):
            manager.add_run_record(
                "order-job",
                started_at=base + timedelta(minutes=i),
                finished_at=base + timedelta(minutes=i, seconds=1),
                outcome="success",
                output=f"run {i}",
            )
        records = manager.get_run_history("order-job")
        assert records[0].started_at > records[1].started_at


# ── CronManager.get_last_run ──────────────────────────────────────────────────


@pytest.fixture()
def manager(tmp_path: Path) -> CronManager:
    """Provide a fresh CronManager backed by a temp SQLite DB."""
    return CronManager(db_path=tmp_path / "cron.db")


class TestGetLastRun:
    """Tests for CronManager.get_last_run."""

    def test_get_last_run_returns_most_recent(self, manager: CronManager) -> None:
        """get_last_run returns the most recent run record."""
        from datetime import datetime, timedelta
        manager.add("lr-job", "* * * * *", "task")
        base = datetime(2026, 3, 9, 10, 0, 0)
        manager.add_run_record(
            "lr-job",
            started_at=base,
            finished_at=base + timedelta(seconds=1),
            outcome="success",
            output="first",
        )
        manager.add_run_record(
            "lr-job",
            started_at=base + timedelta(minutes=1),
            finished_at=base + timedelta(minutes=1, seconds=2),
            outcome="failure",
            error="boom",
        )
        record = manager.get_last_run("lr-job")
        assert record is not None
        assert record.outcome == "failure"
        assert record.error == "boom"

    def test_get_last_run_none_when_no_history(self, manager: CronManager) -> None:
        """get_last_run returns None when no runs have been recorded."""
        manager.add("empty-job", "* * * * *", "task")
        assert manager.get_last_run("empty-job") is None

    def test_get_last_run_none_for_unknown_job(self, manager: CronManager) -> None:
        """get_last_run returns None for a job name not in history."""
        assert manager.get_last_run("nonexistent") is None


class TestRetryFields:
    """Tests for retry policy fields on CronJob."""

    def test_job_has_default_retry_fields(self, manager: CronManager) -> None:
        """New jobs default to max_retries=0, retry_delay_seconds=60, retry_count=0."""
        job = manager.add("retry-default", "* * * * *", "task")
        assert job.max_retries == 0
        assert job.retry_delay_seconds == 60
        assert job.retry_count == 0

    def test_job_created_with_custom_retry(self, manager: CronManager) -> None:
        """Jobs can be created with custom max_retries and retry_delay_seconds."""
        job = manager.add(
            "retry-custom", "* * * * *", "task",
            max_retries=3, retry_delay_seconds=30,
        )
        assert job.max_retries == 3
        assert job.retry_delay_seconds == 30

    def test_set_retry_count(self, manager: CronManager) -> None:
        """set_retry_count persists the new value to SQLite."""
        manager.add("count-job", "* * * * *", "task")
        manager.set_retry_count("count-job", 2)
        job = manager.get_job("count-job")
        assert job is not None
        assert job.retry_count == 2

    def test_reset_retry_count(self, manager: CronManager) -> None:
        """set_retry_count(name, 0) resets the counter."""
        manager.add("reset-job", "* * * * *", "task", max_retries=3)
        manager.set_retry_count("reset-job", 3)
        manager.set_retry_count("reset-job", 0)
        job = manager.get_job("reset-job")
        assert job is not None
        assert job.retry_count == 0


# ── Output routing fields ─────────────────────────────────────────────────────


def test_cron_job_has_output_channel_fields() -> None:
    """CronJob model has output_channel and output_target with None defaults."""
    job = CronJob(name="x", schedule="* * * * *", task="t")
    assert job.output_channel is None
    assert job.output_target is None


def test_add_with_output_routing(tmp_path: Path) -> None:
    """CronManager.add() persists output_channel and output_target."""
    manager = CronManager(db_path=tmp_path / "test.db")
    job = manager.add(
        "notify-job",
        "0 8 * * *",
        "Morning check",
        output_channel="telegram",
        output_target="123456789",
    )
    assert job.output_channel == "telegram"
    assert job.output_target == "123456789"
    loaded = manager.get_job("notify-job")
    assert loaded is not None
    assert loaded.output_channel == "telegram"
    assert loaded.output_target == "123456789"


def test_add_without_output_routing_defaults_to_none(tmp_path: Path) -> None:
    """CronManager.add() without output routing stores None for both fields."""
    manager = CronManager(db_path=tmp_path / "test.db")
    job = manager.add("silent-job", "0 9 * * *", "Silent task")
    assert job.output_channel is None
    assert job.output_target is None
    loaded = manager.get_job("silent-job")
    assert loaded is not None
    assert loaded.output_channel is None
    assert loaded.output_target is None


# ── Timezone field (Phase 28) ─────────────────────────────────────────────────


def test_cron_job_timezone_default_empty() -> None:
    """CronJob.timezone defaults to empty string."""
    job = CronJob(name="x", schedule="* * * * *", task="t")
    assert job.timezone == ""


def test_cron_job_timezone_stored() -> None:
    """CronJob.timezone stores an IANA name."""
    job = CronJob(name="x", schedule="* * * * *", task="t", timezone="America/Caracas")
    assert job.timezone == "America/Caracas"


def test_add_with_timezone_persists(tmp_path: Path) -> None:
    """CronManager.add() stores and retrieves the timezone field."""
    manager = CronManager(db_path=tmp_path / "tz.db")
    job = manager.add(
        "tz-job",
        "0 9 * * *",
        "Morning job",
        timezone="America/Caracas",
    )
    assert job.timezone == "America/Caracas"
    loaded = manager.get_job("tz-job")
    assert loaded is not None
    assert loaded.timezone == "America/Caracas"


def test_add_without_timezone_defaults_to_empty(tmp_path: Path) -> None:
    """CronManager.add() without timezone stores empty string."""
    manager = CronManager(db_path=tmp_path / "tz.db")
    job = manager.add("no-tz-job", "0 9 * * *", "Task")
    assert job.timezone == ""
    loaded = manager.get_job("no-tz-job")
    assert loaded is not None
    assert loaded.timezone == ""


def test_add_once_with_timezone_persists(tmp_path: Path) -> None:
    """CronManager.add_once() stores and retrieves the timezone field."""
    manager = CronManager(db_path=tmp_path / "tz.db")
    run_at = datetime(2026, 6, 1, 9, 0, 0)
    job = manager.add_once(
        "tz-once",
        run_at=run_at,
        task="One-shot",
        timezone="Europe/Berlin",
    )
    assert job.timezone == "Europe/Berlin"
    loaded = manager.get_job("tz-once")
    assert loaded is not None
    assert loaded.timezone == "Europe/Berlin"


def test_resume_with_timezone_recomputes_next_run(tmp_path: Path) -> None:
    """resume() uses the job's timezone when recomputing next_run."""
    manager = CronManager(db_path=tmp_path / "tz.db")
    manager.add("tz-resume", "0 9 * * *", "Task", timezone="America/Caracas")
    manager.pause("tz-resume")
    manager.resume("tz-resume")
    job = manager.get_job("tz-resume")
    assert job is not None
    assert job.status == "active"
    assert job.next_run is not None


def test_timezone_survives_migration(tmp_path: Path) -> None:
    """A DB without the timezone column gets it added via _migrate_db()."""
    import sqlite3

    db = tmp_path / "legacy.db"
    # Create a DB without the timezone column (simulating pre-T-290 schema)
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE cron_jobs ("
        " name TEXT PRIMARY KEY,"
        " schedule TEXT NOT NULL,"
        " task TEXT NOT NULL,"
        " status TEXT NOT NULL DEFAULT 'active',"
        " last_run TEXT,"
        " next_run TEXT,"
        " created_at TEXT NOT NULL,"
        " max_retries INTEGER NOT NULL DEFAULT 0,"
        " retry_delay_seconds INTEGER NOT NULL DEFAULT 60,"
        " retry_count INTEGER NOT NULL DEFAULT 0,"
        " output_channel TEXT,"
        " output_target TEXT"
        ")"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS cron_run_history ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " job_name TEXT NOT NULL,"
        " started_at TEXT NOT NULL,"
        " finished_at TEXT NOT NULL,"
        " duration_seconds REAL NOT NULL,"
        " outcome TEXT NOT NULL,"
        " output TEXT,"
        " error TEXT"
        ")"
    )
    conn.commit()
    conn.close()

    # Opening the DB via CronManager must succeed and add the timezone column
    manager = CronManager(db_path=db)
    job = manager.add("legacy-job", "0 9 * * *", "Task")
    assert job.timezone == ""
    loaded = manager.get_job("legacy-job")
    assert loaded is not None
    assert loaded.timezone == ""

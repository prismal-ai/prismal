"""Unit tests for lightagent.scheduler.cron_manager (T-081)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from lightagent.scheduler.cron_manager import CronJob, CronManager, CronStatus


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

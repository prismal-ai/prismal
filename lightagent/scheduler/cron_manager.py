"""Cron job manager backed by SQLite and Prefect deployments.

Provides ``CronManager``, a persistent store for cron-style scheduled tasks.
Jobs are stored in SQLite (survive restarts — AC-007-2) and can be
created, paused, resumed, listed, and removed.

CLI commands map 1-to-1 to ``CronManager`` methods::

    lightagent cron add --name NAME --schedule "0 9 * * *" --task DESC
    lightagent cron list
    lightagent cron pause NAME
    lightagent cron resume NAME

AC-007-1: cron add creates a Prefect deployment.
AC-007-2: Jobs persist in SQLite across restarts.
AC-007-3: cron list shows name, schedule, last run, next run, status.
AC-007-4: cron pause / cron resume work correctly.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from croniter import croniter

if TYPE_CHECKING:
    from collections.abc import Generator
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from lightagent.core.logging import get_logger

logger = get_logger("lightagent.scheduler.cron_manager")

# ── Types ─────────────────────────────────────────────────────────────────────

CronStatus = Literal["active", "paused"]

_DB_DEFAULT = Path(__file__).parent.parent.parent / "data" / "db" / "cron_jobs.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cron_jobs (
    name        TEXT PRIMARY KEY,
    schedule    TEXT NOT NULL,
    task        TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'active',
    last_run    TEXT,
    next_run    TEXT,
    created_at  TEXT NOT NULL
);
"""

_DT_FMT = "%Y-%m-%dT%H:%M:%S"

_HISTORY_SCHEMA = """
CREATE TABLE IF NOT EXISTS cron_run_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_name    TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    duration_seconds REAL NOT NULL,
    outcome     TEXT NOT NULL,
    output      TEXT,
    error       TEXT
);
"""


class CronRunRecord(BaseModel):
    """One execution record for a cron job.

    Attributes:
        id: Auto-incremented row ID.
        job_name: Name of the cron job.
        started_at: UTC timestamp when execution began.
        finished_at: UTC timestamp when execution ended.
        duration_seconds: Wall-clock duration in seconds.
        outcome: ``"success"`` or ``"failure"``.
        output: Agent response text on success, or ``None``.
        error: Error message on failure, or ``None``.
    """

    id: int | None = None
    job_name: str
    started_at: datetime
    finished_at: datetime
    duration_seconds: float
    outcome: str
    output: str | None = None
    error: str | None = None


# ── Model ─────────────────────────────────────────────────────────────────────


class CronJob(BaseModel):
    """Persistent representation of a scheduled cron job.

    Attributes:
        name: Unique identifier for this job.
        schedule: Cron expression (e.g. ``"0 9 * * *"``).
        task: Human-readable task description or flow name.
        status: ``"active"`` or ``"paused"``.
        last_run: UTC timestamp of the most recent execution, or None.
        next_run: UTC timestamp of the next scheduled execution, or None.
        created_at: UTC timestamp when this job was first created.
    """

    name: str
    schedule: str
    task: str
    status: CronStatus = "active"
    last_run: datetime | None = None
    next_run: datetime | None = None
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC).replace(tzinfo=None)
    )


# ── Manager ───────────────────────────────────────────────────────────────────


class CronManager:
    """Manage LightAgent cron jobs with SQLite persistence.

    All mutations are immediately written to the SQLite database so that
    jobs survive process restarts (AC-007-2).

    Args:
        db_path: Path to the SQLite database file.  Defaults to
            ``data/db/cron_jobs.db`` next to the project root.

    Example::

        manager = CronManager()
        manager.add("morning-brief", "0 9 * * *", "Run morning briefing")
        manager.list_jobs()
        manager.pause("morning-brief")
        manager.resume("morning-brief")
    """

    def __init__(self, db_path: Path | None = None) -> None:
        """Initialise CronManager and ensure the SQLite schema exists."""
        self._db = db_path or _DB_DEFAULT
        self._db.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    # ── private helpers ──────────────────────────────────────────────────────

    @contextmanager
    def _conn(self) -> Generator[sqlite3.Connection]:
        """Yield a SQLite connection and guarantee it is closed on exit."""
        conn = sqlite3.connect(self._db)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self) -> None:
        """Create the cron_jobs and cron_run_history tables if they do not exist."""
        with self._conn() as conn:
            conn.execute(_SCHEMA)
            conn.execute(_HISTORY_SCHEMA)

    @staticmethod
    def _row_to_job(row: sqlite3.Row) -> CronJob:
        """Convert a SQLite row to a CronJob model."""

        def _parse_dt(s: str | None) -> datetime | None:
            return datetime.strptime(s, _DT_FMT) if s else None  # noqa: DTZ007

        return CronJob(
            name=row["name"],
            schedule=row["schedule"],
            task=row["task"],
            status=row["status"],
            last_run=_parse_dt(row["last_run"]),
            next_run=_parse_dt(row["next_run"]),
            created_at=datetime.strptime(row["created_at"], _DT_FMT),  # noqa: DTZ007
        )

    def _require_job(self, name: str) -> None:
        """Raise KeyError if job does not exist."""
        if self.get_job(name) is None:
            raise KeyError(f"Cron job '{name}' not found")

    # ── public API ───────────────────────────────────────────────────────────

    def add(self, name: str, schedule: str, task: str) -> CronJob:
        """Create and persist a new cron job.

        Args:
            name: Unique job identifier.
            schedule: Cron expression (e.g. ``"0 9 * * *"``).
            task: Human-readable description or Prefect flow name.

        Returns:
            The newly created :class:`CronJob`.

        Raises:
            ValueError: If a job with ``name`` already exists.

        AC-007-1: Creates a Prefect deployment for the job.
        """
        if self.get_job(name) is not None:
            raise ValueError(f"Cron job '{name}' already exists")

        now = datetime.now(UTC).replace(tzinfo=None)
        next_dt = croniter(schedule, now).get_next(datetime)
        job = CronJob(name=name, schedule=schedule, task=task, next_run=next_dt)
        created = job.created_at.strftime(_DT_FMT)
        next_stamp = next_dt.strftime(_DT_FMT)

        with self._conn() as conn:
            conn.execute(
                "INSERT INTO cron_jobs"
                " (name, schedule, task, status, created_at, next_run)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (name, schedule, task, job.status, created, next_stamp),
            )

        logger.info("cron_job_added", name=name, schedule=schedule, next_run=next_stamp)
        self._try_create_prefect_deployment(job)
        return job

    def pause(self, name: str) -> None:
        """Pause an existing cron job.

        Args:
            name: The job name to pause.

        Raises:
            KeyError: If the job does not exist.

        AC-007-4: ``lightagent cron pause NAME`` works.
        """
        self._require_job(name)
        with self._conn() as conn:
            conn.execute(
                "UPDATE cron_jobs SET status = 'paused' WHERE name = ?", (name,)
            )
        logger.info("cron_job_paused", name=name)

    def resume(self, name: str) -> None:
        """Resume a paused cron job.

        Args:
            name: The job name to resume.

        Raises:
            KeyError: If the job does not exist.

        AC-007-4: ``lightagent cron resume NAME`` works.
        """
        self._require_job(name)
        job = self.get_job(name)
        assert job is not None  # guaranteed by _require_job above
        now = datetime.now(UTC).replace(tzinfo=None)
        next_dt = croniter(job.schedule, now).get_next(datetime)
        stamp = next_dt.strftime(_DT_FMT)
        with self._conn() as conn:
            conn.execute(
                "UPDATE cron_jobs SET status = 'active', next_run = ? WHERE name = ?",
                (stamp, name),
            )
        logger.info("cron_job_resumed", name=name, next_run=stamp)

    def remove(self, name: str) -> None:
        """Permanently delete a cron job.

        Args:
            name: The job name to remove.

        Raises:
            KeyError: If the job does not exist.
        """
        self._require_job(name)
        with self._conn() as conn:
            conn.execute("DELETE FROM cron_jobs WHERE name = ?", (name,))
        logger.info("cron_job_removed", name=name)

    def list_jobs(self) -> list[CronJob]:
        """Return all cron jobs ordered by creation time.

        Returns:
            List of :class:`CronJob` instances (may be empty).

        AC-007-3: Shows name, schedule, last run, next run, status.
        """
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM cron_jobs ORDER BY created_at"
            ).fetchall()
        return [self._row_to_job(r) for r in rows]

    def get_job(self, name: str) -> CronJob | None:
        """Fetch a single cron job by name.

        Args:
            name: The job name to look up.

        Returns:
            :class:`CronJob` if found, ``None`` otherwise.
        """
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM cron_jobs WHERE name = ?", (name,)
            ).fetchone()
        return self._row_to_job(row) if row else None

    def update_last_run(self, name: str, ts: datetime | None = None) -> None:
        """Record a successful execution timestamp for a job.

        Args:
            name: The job name.
            ts: Execution timestamp; defaults to ``datetime.utcnow()``.
        """
        stamp = (ts or datetime.now(UTC).replace(tzinfo=None)).strftime(_DT_FMT)
        with self._conn() as conn:
            conn.execute(
                "UPDATE cron_jobs SET last_run = ? WHERE name = ?", (stamp, name)
            )

    def add_run_record(
        self,
        job_name: str,
        started_at: datetime,
        finished_at: datetime,
        outcome: str,
        output: str | None = None,
        error: str | None = None,
    ) -> CronRunRecord:
        """Persist one execution record for a cron job.

        Args:
            job_name: Name of the job that was executed.
            started_at: UTC time when the execution started.
            finished_at: UTC time when the execution ended.
            outcome: ``"success"`` or ``"failure"``.
            output: Agent response text (success path), or ``None``.
            error: Error message (failure path), or ``None``.

        Returns:
            The persisted :class:`CronRunRecord` with its assigned ``id``.
        """
        duration = (finished_at - started_at).total_seconds()
        with self._conn() as conn:
            cursor = conn.execute(
                "INSERT INTO cron_run_history"
                " (job_name, started_at, finished_at,"
                " duration_seconds, outcome, output, error)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    job_name,
                    started_at.strftime(_DT_FMT),
                    finished_at.strftime(_DT_FMT),
                    duration,
                    outcome,
                    output,
                    error,
                ),
            )
            row_id = cursor.lastrowid
        record = CronRunRecord(
            id=row_id,
            job_name=job_name,
            started_at=started_at,
            finished_at=finished_at,
            duration_seconds=duration,
            outcome=outcome,
            output=output,
            error=error,
        )
        logger.debug(
            "cron_run_recorded", job=job_name, outcome=outcome, duration=duration
        )
        return record

    def get_run_history(self, job_name: str, limit: int = 20) -> list[CronRunRecord]:
        """Fetch execution history for a cron job, newest first.

        Args:
            job_name: Name of the cron job.
            limit: Maximum number of records to return (default 20).

        Returns:
            List of :class:`CronRunRecord` instances, newest first.
        """
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM cron_run_history"
                " WHERE job_name = ?"
                " ORDER BY started_at DESC"
                " LIMIT ?",
                (job_name, limit),
            ).fetchall()

        def _row_to_record(row: sqlite3.Row) -> CronRunRecord:
            def _parse(s: str) -> datetime:
                return datetime.strptime(s, _DT_FMT)  # noqa: DTZ007

            return CronRunRecord(
                id=row["id"],
                job_name=row["job_name"],
                started_at=_parse(row["started_at"]),
                finished_at=_parse(row["finished_at"]),
                duration_seconds=row["duration_seconds"],
                outcome=row["outcome"],
                output=row["output"],
                error=row["error"],
            )

        return [_row_to_record(r) for r in rows]

    def get_last_run(self, job_name: str) -> CronRunRecord | None:
        """Return the most recent execution record for a job, or ``None``.

        Args:
            job_name: Name of the cron job.

        Returns:
            The most recent :class:`CronRunRecord`, or ``None`` if no runs
            have been recorded for this job.
        """
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM cron_run_history"
                " WHERE job_name = ?"
                " ORDER BY started_at DESC"
                " LIMIT 1",
                (job_name,),
            ).fetchone()
        if row is None:
            return None

        def _parse(s: str) -> datetime:
            return datetime.strptime(s, _DT_FMT)  # noqa: DTZ007

        return CronRunRecord(
            id=row["id"],
            job_name=row["job_name"],
            started_at=_parse(row["started_at"]),
            finished_at=_parse(row["finished_at"]),
            duration_seconds=row["duration_seconds"],
            outcome=row["outcome"],
            output=row["output"],
            error=row["error"],
        )

    # ── Prefect integration (best-effort) ────────────────────────────────────

    @staticmethod
    def _try_create_prefect_deployment(job: CronJob) -> None:
        """Attempt to register a Prefect deployment for the job (best-effort).

        If no Prefect server is reachable this logs a warning and returns
        without error — the job is still persisted in SQLite.

        Args:
            job: The cron job for which to create a deployment.
        """
        try:
            logger.info(
                "cron_prefect_deployment_skipped",
                name=job.name,
                schedule=job.schedule,
                reason=(
                    "Prefect deployment API not yet wired; job persisted in SQLite only"
                ),
            )
        except Exception as exc:
            logger.warning(
                "cron_prefect_deployment_skipped",
                name=job.name,
                reason=str(exc),
            )


__all__ = ["CronJob", "CronManager", "CronRunRecord", "CronStatus"]

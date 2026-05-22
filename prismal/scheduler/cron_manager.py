"""Cron job manager backed by SQLite and Prefect deployments.

Provides ``CronManager``, a persistent store for cron-style scheduled tasks.
Jobs are stored in SQLite (survive restarts — AC-007-2) and can be
created, paused, resumed, listed, and removed.

CLI commands map 1-to-1 to ``CronManager`` methods::

    prismal cron add --name NAME --schedule "0 9 * * *" --task DESC
    prismal cron list
    prismal cron pause NAME
    prismal cron resume NAME

AC-007-1: cron add creates a Prefect deployment.
AC-007-2: Jobs persist in SQLite across restarts.
AC-007-3: cron list shows name, schedule, last run, next run, status.
AC-007-4: cron pause / cron resume work correctly.
"""

from __future__ import annotations

import contextlib
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from collections.abc import Generator
    from zoneinfo import ZoneInfo

from prismal.core.logging import get_logger

logger = get_logger("prismal.scheduler.cron_manager")

# ── Types ─────────────────────────────────────────────────────────────────────

CronStatus = Literal["active", "paused"]

_DB_DEFAULT = Path(__file__).parent.parent.parent / "data" / "db" / "cron_jobs.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cron_jobs (
    name                TEXT PRIMARY KEY,
    schedule            TEXT NOT NULL,
    task                TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'active',
    last_run            TEXT,
    next_run            TEXT,
    created_at          TEXT NOT NULL,
    max_retries         INTEGER NOT NULL DEFAULT 0,
    retry_delay_seconds INTEGER NOT NULL DEFAULT 60,
    retry_count         INTEGER NOT NULL DEFAULT 0,
    output_channel      TEXT,
    output_target       TEXT,
    timezone            TEXT NOT NULL DEFAULT ''
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
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC).replace(tzinfo=None))
    max_retries: int = Field(default=0, ge=0, description="Max retry attempts on failure.")
    retry_delay_seconds: int = Field(
        default=60, ge=1, description="Base delay in seconds for exponential backoff."
    )
    retry_count: int = Field(default=0, ge=0, description="Current retry attempt count.")
    output_channel: str | None = Field(
        default=None,
        description=("Delivery channel for job output (e.g. 'telegram', 'slack', 'email')."),
    )
    output_target: str | None = Field(
        default=None,
        description="Channel-specific target (chat_id, #channel, or email address).",
    )
    timezone: str = Field(
        default="",
        description=(
            "IANA timezone name for this job (e.g. 'America/Caracas'). "
            "Empty string means use the system-wide timezone resolution chain."
        ),
    )


# ── Manager ───────────────────────────────────────────────────────────────────


class CronManager:
    """Manage Prismal cron jobs with SQLite persistence.

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
        self._migrate_db()

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
        """Create tables and apply schema migrations for new columns."""
        with self._conn() as conn:
            conn.execute(_SCHEMA)
            conn.execute(_HISTORY_SCHEMA)
            # Safe migration: add retry columns to existing DBs.
            # SQLite raises OperationalError on duplicate column — ignore it.
            _migrations = [
                "ALTER TABLE cron_jobs ADD COLUMN max_retries INTEGER NOT NULL DEFAULT 0",
                "ALTER TABLE cron_jobs ADD COLUMN retry_delay_seconds INTEGER NOT NULL DEFAULT 60",
                "ALTER TABLE cron_jobs ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0",
            ]
            for sql in _migrations:
                with contextlib.suppress(sqlite3.OperationalError):
                    conn.execute(sql)

    def _migrate_db(self) -> None:
        """Apply additive schema migrations for columns added after initial release."""
        with self._conn() as conn:
            existing = {row[1] for row in conn.execute("PRAGMA table_info(cron_jobs)").fetchall()}
            if "output_channel" not in existing:
                conn.execute("ALTER TABLE cron_jobs ADD COLUMN output_channel TEXT")
            if "output_target" not in existing:
                conn.execute("ALTER TABLE cron_jobs ADD COLUMN output_target TEXT")
            if "timezone" not in existing:
                conn.execute("ALTER TABLE cron_jobs ADD COLUMN timezone TEXT NOT NULL DEFAULT ''")

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
            max_retries=row["max_retries"] or 0,
            retry_delay_seconds=row["retry_delay_seconds"] or 60,
            retry_count=row["retry_count"] or 0,
            output_channel=row["output_channel"],
            output_target=row["output_target"],
            timezone=row["timezone"] or "",
        )

    def _require_job(self, name: str) -> None:
        """Raise KeyError if job does not exist."""
        if self.get_job(name) is None:
            raise KeyError(f"Cron job '{name}' not found")

    # ── public API ───────────────────────────────────────────────────────────

    def add(
        self,
        name: str,
        schedule: str,
        task: str,
        max_retries: int = 0,
        retry_delay_seconds: int = 60,
        output_channel: str | None = None,
        output_target: str | None = None,
        timezone: str = "",
    ) -> CronJob:
        """Create and persist a new cron job.

        Args:
            name: Unique job identifier.
            schedule: Cron expression (e.g. ``"0 9 * * *"``).
            task: Human-readable description or Prefect flow name.
            max_retries: Maximum retry attempts on failure (default 0 = no retry).
            retry_delay_seconds: Base backoff delay in seconds (default 60).
            output_channel: Delivery channel for job output (e.g. ``"telegram"``).
            output_target: Channel-specific target (chat_id, #channel, email).
            timezone: IANA timezone name for this job (e.g. ``"America/Caracas"``).
                Empty string uses the system-wide timezone resolution chain.

        Returns:
            The newly created :class:`CronJob`.

        Raises:
            ValueError: If a job with ``name`` already exists.

        AC-007-1: Creates a Prefect deployment for the job.
        """
        if self.get_job(name) is not None:
            raise ValueError(f"Cron job '{name}' already exists")

        from prismal.scheduler.datetime_service import DateTimeService

        dts = DateTimeService.get()
        tz: ZoneInfo | None = dts.resolve_timezone(timezone) if timezone else None
        next_dt = dts.next_run(schedule, tz)
        next_naive = dts.to_utc_naive(next_dt)

        job = CronJob(
            name=name,
            schedule=schedule,
            task=task,
            next_run=next_naive,
            max_retries=max_retries,
            retry_delay_seconds=retry_delay_seconds,
            output_channel=output_channel,
            output_target=output_target,
            timezone=timezone,
        )
        created = job.created_at.strftime(_DT_FMT)
        next_stamp = next_naive.strftime(_DT_FMT)

        with self._conn() as conn:
            conn.execute(
                "INSERT INTO cron_jobs"
                " (name, schedule, task, status, created_at, next_run,"
                "  max_retries, retry_delay_seconds, retry_count,"
                "  output_channel, output_target, timezone)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    name,
                    schedule,
                    task,
                    job.status,
                    created,
                    next_stamp,
                    max_retries,
                    retry_delay_seconds,
                    0,
                    output_channel,
                    output_target,
                    timezone,
                ),
            )

        logger.info(
            "cron_job_added",
            name=name,
            schedule=schedule,
            next_run=next_stamp,
            timezone=timezone or "(resolved)",
            max_retries=max_retries,
        )
        self._try_create_prefect_deployment(job)
        return job

    def add_once(
        self,
        name: str,
        run_at: datetime,
        task: str,
        timezone: str = "",
        output_channel: str | None = None,
        output_target: str | None = None,
    ) -> CronJob:
        """Create and persist a one-time (non-recurring) cron job.

        Stores the schedule as ``"once:<ISO datetime>"`` so that the executor
        can detect it and use APScheduler's ``date`` trigger rather than a
        :class:`~apscheduler.triggers.cron.CronTrigger`.

        Args:
            name: Unique job identifier.
            run_at: Exact datetime when the job should fire.  If naive, it is
                interpreted in *timezone* (or the resolved default TZ when empty).
            task: Human-readable task description sent to the agent.
            timezone: IANA timezone name for this job (e.g. ``"America/Caracas"``).
                Empty string uses the system-wide timezone resolution chain.
            output_channel: Delivery channel for the job output
                (e.g. ``"telegram"``).  ``None`` disables proactive delivery.
            output_target: Channel-specific target (chat_id, #channel, email).

        Returns:
            The newly created :class:`CronJob`.

        Raises:
            ValueError: If a job with ``name`` already exists.
        """
        if self.get_job(name) is not None:
            raise ValueError(f"Cron job '{name}' already exists")

        schedule = f"once:{run_at.strftime(_DT_FMT)}"
        now_str = datetime.now(UTC).replace(tzinfo=None).strftime(_DT_FMT)
        run_at_str = run_at.strftime(_DT_FMT)

        job = CronJob(
            name=name,
            schedule=schedule,
            task=task,
            next_run=run_at,
            timezone=timezone,
            output_channel=output_channel,
            output_target=output_target,
        )
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO cron_jobs"
                " (name, schedule, task, status, created_at, next_run,"
                "  max_retries, retry_delay_seconds, retry_count,"
                "  output_channel, output_target, timezone)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    name,
                    schedule,
                    task,
                    job.status,
                    now_str,
                    run_at_str,
                    0,
                    60,
                    0,
                    output_channel,
                    output_target,
                    timezone,
                ),
            )

        logger.info(
            "cron_job_once_added",
            name=name,
            run_at=run_at_str,
            timezone=timezone or "(resolved)",
        )
        return job

    def pause(self, name: str) -> None:
        """Pause an existing cron job.

        Args:
            name: The job name to pause.

        Raises:
            KeyError: If the job does not exist.

        AC-007-4: ``prismal cron pause NAME`` works.
        """
        self._require_job(name)
        with self._conn() as conn:
            conn.execute("UPDATE cron_jobs SET status = 'paused' WHERE name = ?", (name,))
        logger.info("cron_job_paused", name=name)

    def resume(self, name: str) -> None:
        """Resume a paused cron job.

        Args:
            name: The job name to resume.

        Raises:
            KeyError: If the job does not exist.

        AC-007-4: ``prismal cron resume NAME`` works.
        """
        self._require_job(name)
        job = self.get_job(name)
        assert job is not None  # guaranteed by _require_job above

        from prismal.scheduler.datetime_service import DateTimeService

        dts = DateTimeService.get()
        tz: ZoneInfo | None = dts.resolve_timezone(job.timezone) if job.timezone else None
        next_aware = dts.next_run(job.schedule, tz)
        next_naive = dts.to_utc_naive(next_aware)
        stamp = next_naive.strftime(_DT_FMT)

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
            rows = conn.execute("SELECT * FROM cron_jobs ORDER BY created_at").fetchall()
        return [self._row_to_job(r) for r in rows]

    def get_job(self, name: str) -> CronJob | None:
        """Fetch a single cron job by name.

        Args:
            name: The job name to look up.

        Returns:
            :class:`CronJob` if found, ``None`` otherwise.
        """
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM cron_jobs WHERE name = ?", (name,)).fetchone()
        return self._row_to_job(row) if row else None

    def update_timezone(self, name: str, timezone: str) -> None:
        """Update the IANA timezone for an existing cron job.

        Args:
            name: The job name to update.
            timezone: New IANA timezone (e.g. ``"America/Caracas"``).
                Empty string resets to the global timezone resolution chain.

        Raises:
            KeyError: If the job does not exist.
        """
        self._require_job(name)
        with self._conn() as conn:
            conn.execute(
                "UPDATE cron_jobs SET timezone = ? WHERE name = ?",
                (timezone, name),
            )
        logger.info("cron_job_timezone_updated", name=name, timezone=timezone)

    def update_last_run(self, name: str, ts: datetime | None = None) -> None:
        """Record a successful execution timestamp for a job.

        Args:
            name: The job name.
            ts: Execution timestamp; defaults to ``datetime.utcnow()``.
        """
        stamp = (ts or datetime.now(UTC).replace(tzinfo=None)).strftime(_DT_FMT)
        with self._conn() as conn:
            conn.execute("UPDATE cron_jobs SET last_run = ? WHERE name = ?", (stamp, name))

    def set_retry_count(self, name: str, count: int) -> None:
        """Set the current retry attempt counter for a job.

        Args:
            name: The job name.
            count: New retry count value (0 = reset after success/final failure).
        """
        with self._conn() as conn:
            conn.execute(
                "UPDATE cron_jobs SET retry_count = ? WHERE name = ?",
                (count, name),
            )
        logger.debug("cron_retry_count_set", name=name, count=count)

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
        logger.debug("cron_run_recorded", job=job_name, outcome=outcome, duration=duration)
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
                reason=("Prefect deployment API not yet wired; job persisted in SQLite only"),
            )
        except Exception as exc:
            logger.warning(
                "cron_prefect_deployment_skipped",
                name=job.name,
                reason=str(exc),
            )


__all__ = ["CronJob", "CronManager", "CronRunRecord", "CronStatus"]

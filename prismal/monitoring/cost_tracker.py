"""Cost and token tracking for Prismal sessions and users.

Persists LLM token usage and cost per session (and optionally per user) to
a SQLite database.  Cost data comes from LiteLLM response metadata
(``response_cost`` field) or can be recorded manually.

Features
--------
* Record token usage + cost after every LLM call.
* Query usage summaries filtered by user and/or date range.
* Budget alert check — returns True when a user has exceeded the configured
  threshold within the billing period.

SPEC-018 / T-192 acceptance criteria
--------------------------------------
* Cost tracked per session from LiteLLM response metadata.
* Cost aggregated per user when RBAC is enabled.
* ``GET /api/v1/billing/usage`` exposes this data (see billing router).
* Budget alerts when user exceeds ``settings.budget_alert_usd``.

Example::

    from prismal.monitoring.cost_tracker import CostTracker

    tracker = CostTracker()
    tracker.record(
        session_id="sess-abc",
        user_id="user-123",
        model="claude-sonnet-4-5",
        tokens_in=500,
        tokens_out=200,
        cost_usd=0.003,
    )
    summary = tracker.get_summary(user_id="user-123")
    print(summary.total_cost_usd)
"""

from __future__ import annotations

import sqlite3
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Generator

from pydantic import BaseModel

from prismal.core.logging import get_logger

logger = get_logger("prismal.monitoring.cost_tracker")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cost_entries (
    id          TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL,
    user_id     TEXT,
    model       TEXT NOT NULL,
    tokens_in   INTEGER NOT NULL DEFAULT 0,
    tokens_out  INTEGER NOT NULL DEFAULT 0,
    cost_usd    REAL NOT NULL DEFAULT 0.0,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cost_user ON cost_entries (user_id);
CREATE INDEX IF NOT EXISTS idx_cost_session ON cost_entries (session_id);
CREATE INDEX IF NOT EXISTS idx_cost_created ON cost_entries (created_at);
"""

_DT_FMT = "%Y-%m-%dT%H:%M:%S"
_DB_DEFAULT = Path(__file__).parent.parent.parent / "data" / "db" / "cost.db"


# ── Pydantic models ───────────────────────────────────────────────────────────


class CostEntry(BaseModel):
    """A single LLM usage record.

    Attributes:
        id: UUID4 string identifier.
        session_id: Session that triggered the LLM call.
        user_id: Optional user ID (from RBAC if enabled).
        model: LiteLLM model identifier (e.g. ``claude-sonnet-4-5``).
        tokens_in: Input / prompt tokens.
        tokens_out: Output / completion tokens.
        cost_usd: Estimated cost in US dollars from LiteLLM metadata.
        created_at: UTC timestamp of the call.
    """

    id: str
    session_id: str
    user_id: str | None
    model: str
    tokens_in: int
    tokens_out: int
    cost_usd: float
    created_at: datetime


class UsageSummary(BaseModel):
    """Aggregated usage statistics for a query.

    Attributes:
        total_tokens_in: Total prompt tokens consumed.
        total_tokens_out: Total completion tokens consumed.
        total_cost_usd: Total estimated cost in US dollars.
        request_count: Number of LLM calls in the period.
        period_start: Earliest entry timestamp, or None if no entries.
        period_end: Latest entry timestamp, or None if no entries.
    """

    total_tokens_in: int
    total_tokens_out: int
    total_cost_usd: float
    request_count: int
    period_start: datetime | None
    period_end: datetime | None


# ── CostTracker ───────────────────────────────────────────────────────────────


class CostTracker:
    """SQLite-backed LLM cost and token tracker.

    Thread-safe via per-call connection creation.  The database file is
    created automatically on first use.

    Args:
        db_path: SQLite file path.  Defaults to ``data/db/cost.db``.
    """

    def __init__(self, db_path: Path | None = None) -> None:
        """Initialise the tracker and ensure the schema exists."""
        self._db = db_path or _DB_DEFAULT
        self._db.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    # ── SQLite helpers ────────────────────────────────────────────────────────

    @contextmanager
    def _conn(self) -> Generator[sqlite3.Connection]:
        """Yield a committed SQLite connection."""
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

    def _init_schema(self) -> None:
        """Create tables and indexes if they do not exist."""
        with self._conn() as conn:
            conn.executescript(_SCHEMA)

    # ── Public API ────────────────────────────────────────────────────────────

    def record(
        self,
        session_id: str,
        model: str,
        tokens_in: int,
        tokens_out: int,
        cost_usd: float,
        user_id: str | None = None,
    ) -> str:
        """Record a single LLM call's usage.

        Args:
            session_id: Session that triggered the call.
            model: LiteLLM model identifier.
            tokens_in: Input / prompt tokens.
            tokens_out: Output / completion tokens.
            cost_usd: Estimated cost from LiteLLM response metadata.
            user_id: Optional user UUID (from RBAC).

        Returns:
            The UUID string of the newly created :class:`CostEntry`.
        """
        entry_id = str(uuid.uuid4())
        now = datetime.now(UTC).replace(tzinfo=None).strftime(_DT_FMT)

        with self._conn() as conn:
            conn.execute(
                "INSERT INTO cost_entries "
                "(id, session_id, user_id, model, tokens_in, tokens_out, "
                "cost_usd, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    entry_id,
                    session_id,
                    user_id,
                    model,
                    tokens_in,
                    tokens_out,
                    cost_usd,
                    now,
                ),
            )

        logger.debug(
            "cost_recorded",
            session_id=session_id,
            user_id=user_id,
            model=model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=cost_usd,
        )
        return entry_id

    def get_summary(
        self,
        user_id: str | None = None,
        from_date: datetime | None = None,
        to_date: datetime | None = None,
    ) -> UsageSummary:
        """Return aggregated usage statistics.

        Args:
            user_id: If provided, restrict to this user's usage.
            from_date: If provided, only include entries on or after this date.
            to_date: If provided, only include entries on or before this date.

        Returns:
            :class:`UsageSummary` with aggregated counts and cost.
        """
        conditions: list[str] = []
        params: list[object] = []

        if user_id is not None:
            conditions.append("user_id = ?")
            params.append(user_id)
        if from_date is not None:
            conditions.append("created_at >= ?")
            params.append(from_date.replace(tzinfo=None).strftime(_DT_FMT))
        if to_date is not None:
            conditions.append("created_at <= ?")
            params.append(to_date.replace(tzinfo=None).strftime(_DT_FMT))

        where_clause = ("WHERE " + " AND ".join(conditions)) if conditions else ""

        query = (
            f"SELECT "  # noqa: S608
            f"  SUM(tokens_in) AS total_in, "
            f"  SUM(tokens_out) AS total_out, "
            f"  SUM(cost_usd) AS total_cost, "
            f"  COUNT(*) AS cnt, "
            f"  MIN(created_at) AS period_start, "
            f"  MAX(created_at) AS period_end "
            f"FROM cost_entries {where_clause}"
        )

        with self._conn() as conn:
            row = conn.execute(query, params).fetchone()

        if row is None or row["cnt"] == 0:
            return UsageSummary(
                total_tokens_in=0,
                total_tokens_out=0,
                total_cost_usd=0.0,
                request_count=0,
                period_start=None,
                period_end=None,
            )

        def _parse_dt(val: str | None) -> datetime | None:
            """Parse a stored datetime string or return None."""
            if val is None:
                return None
            try:
                return datetime.strptime(val, _DT_FMT).replace(tzinfo=UTC)
            except ValueError:
                return None

        return UsageSummary(
            total_tokens_in=int(row["total_in"] or 0),
            total_tokens_out=int(row["total_out"] or 0),
            total_cost_usd=float(row["total_cost"] or 0.0),
            request_count=int(row["cnt"]),
            period_start=_parse_dt(row["period_start"]),
            period_end=_parse_dt(row["period_end"]),
        )

    def get_user_cost_today(self, user_id: str) -> float:
        """Return the total cost incurred by a user today (UTC).

        Args:
            user_id: The user's UUID string.

        Returns:
            Total cost in USD for the current UTC day.
        """
        today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
        summary = self.get_summary(user_id=user_id, from_date=today)
        return summary.total_cost_usd

    def check_budget_alert(
        self,
        user_id: str,
        budget_usd: float | None = None,
        period_days: int = 30,
    ) -> bool:
        """Return True if the user has exceeded the budget threshold.

        Args:
            user_id: The user's UUID string.
            budget_usd: Budget threshold in USD.  Defaults to
                ``settings.budget_alert_usd``.
            period_days: Number of rolling days to sum.  Defaults to 30.

        Returns:
            True if total cost in the period exceeds ``budget_usd``.
        """
        if budget_usd is None:
            from prismal.core.config import get_settings

            budget_usd = get_settings().budget_alert_usd

        from datetime import timedelta

        since = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=period_days)
        summary = self.get_summary(user_id=user_id, from_date=since)

        exceeded = summary.total_cost_usd >= budget_usd
        if exceeded:
            logger.warning(
                "budget_alert",
                user_id=user_id,
                cost_usd=summary.total_cost_usd,
                budget_usd=budget_usd,
            )
        return exceeded

    def list_entries(
        self,
        user_id: str | None = None,
        session_id: str | None = None,
        limit: int = 100,
    ) -> list[CostEntry]:
        """Return raw cost entries for inspection.

        Args:
            user_id: Optional filter by user.
            session_id: Optional filter by session.
            limit: Maximum number of entries to return (default 100).

        Returns:
            List of :class:`CostEntry` objects ordered by descending date.
        """
        conditions: list[str] = []
        params: list[object] = []

        if user_id is not None:
            conditions.append("user_id = ?")
            params.append(user_id)
        if session_id is not None:
            conditions.append("session_id = ?")
            params.append(session_id)

        where_clause = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        params.append(limit)

        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM cost_entries {where_clause} "  # noqa: S608
                f"ORDER BY created_at DESC LIMIT ?",
                params,
            ).fetchall()

        entries: list[CostEntry] = []
        for row in rows:
            try:
                entries.append(
                    CostEntry(
                        id=row["id"],
                        session_id=row["session_id"],
                        user_id=row["user_id"],
                        model=row["model"],
                        tokens_in=row["tokens_in"],
                        tokens_out=row["tokens_out"],
                        cost_usd=row["cost_usd"],
                        created_at=datetime.strptime(row["created_at"], _DT_FMT).replace(
                            tzinfo=UTC
                        ),
                    )
                )
            except (ValueError, KeyError):
                continue
        return entries


__all__ = ["CostEntry", "CostTracker", "UsageSummary"]

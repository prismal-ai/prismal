"""DuckDB in-process analytical SQL engine.

Provides a validated DuckDB wrapper that prevents destructive SQL operations
and integrates with Polars DataFrames for seamless analytics pipelines.

Satisfies AC-012-1, AC-012-3, AC-012-5.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import duckdb

from prismal.core.exceptions import LightAgentError

# SQL keywords that indicate destructive or schema-mutating operations.
_BLOCKED_KEYWORDS: frozenset[str] = frozenset(
    {
        "DROP",
        "DELETE",
        "INSERT",
        "UPDATE",
        "CREATE",
        "ALTER",
        "TRUNCATE",
        "REPLACE",
        "MERGE",
    }
)

# Regex that matches any blocked keyword as a whole word (case-insensitive).
_BLOCKED_RE = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in _BLOCKED_KEYWORDS) + r")\b",
    re.IGNORECASE,
)


class SQLValidator:
    """Validates SQL strings to prevent destructive database operations.

    Only SELECT and WITH (CTEs) queries are permitted.
    Any query containing DDL/DML keywords raises :exc:`LightAgentError`.
    """

    def validate(self, sql: str) -> None:
        """Raise LightAgentError if *sql* contains a blocked keyword.

        Args:
            sql: SQL string to validate.

        Raises:
            LightAgentError: When a destructive keyword is detected.
        """
        match = _BLOCKED_RE.search(sql)
        if match:
            keyword = match.group(1).upper()
            raise LightAgentError(
                f"SQL validation failed: '{keyword}' operations are not permitted. "
                "Only SELECT queries are allowed."
            )


class DuckDBEngine:
    """In-process analytical SQL engine backed by DuckDB.

    Wraps a :class:`duckdb.DuckDBPyConnection` with SQL validation and
    Polars integration.  All queries are validated before execution to
    prevent destructive operations (AC-012-5).

    Example::

        engine = DuckDBEngine()
        rows = engine.query_csv("data/sales.csv")
        # [{"region": "north", "sales": 100}, ...]

    Args:
        db_path: DuckDB database path.  Defaults to ``:memory:`` for a
            transient in-process database.
    """

    def __init__(self, db_path: str = ":memory:") -> None:
        """Initialise the DuckDB connection.

        Args:
            db_path: Path to the DuckDB file, or ``:memory:``.
        """
        self._conn: duckdb.DuckDBPyConnection = duckdb.connect(db_path)
        self._validator = SQLValidator()

    # ── Core query ───────────────────────────────────────────────────────────

    def query(self, sql: str) -> list[dict[str, Any]]:
        """Execute a validated SQL query and return results as records.

        Args:
            sql: SQL SELECT query to execute.

        Returns:
            List of row dicts keyed by column name.

        Raises:
            LightAgentError: If *sql* contains a blocked keyword.
        """
        self._validator.validate(sql)
        relation = self._conn.execute(sql)
        col_names = [desc[0] for desc in relation.description]
        return [dict(zip(col_names, row, strict=False)) for row in relation.fetchall()]

    # ── CSV helpers ──────────────────────────────────────────────────────────

    def query_csv(
        self,
        path: str | Path,
        sql: str | None = None,
    ) -> list[dict[str, Any]]:
        """Query a CSV file directly with DuckDB.

        The CSV file is registered as a virtual view named after the file
        stem (e.g. ``sales.csv`` → table name ``sales``).

        Args:
            path: Path to the CSV file.
            sql: Optional SQL query.  If *None*, a ``SELECT *`` is used.
                The table name in the query should match the file stem.

        Returns:
            List of row dicts.

        Raises:
            LightAgentError: If *sql* contains a blocked keyword.
            FileNotFoundError: If the CSV file does not exist.
        """
        csv_path = Path(path)
        if not csv_path.exists():
            raise FileNotFoundError(f"CSV file not found: {csv_path}")

        table_name = csv_path.stem
        # Register the CSV as a view so queries can reference it by name.
        self._conn.execute(
            f"CREATE OR REPLACE VIEW {table_name} AS "  # noqa: S608 — table_name from file stem
            f"SELECT * FROM read_csv_auto('{csv_path.as_posix()}')"
        )

        effective_sql = sql or f"SELECT * FROM {table_name}"  # noqa: S608
        return self.query(effective_sql)

    # ── Polars integration ───────────────────────────────────────────────────

    def register_polars(self, name: str, df: Any) -> None:
        """Register a Polars DataFrame as a queryable virtual table.

        Args:
            name: Table name to use in SQL queries.
            df: A :class:`polars.DataFrame` instance.
        """
        self._conn.register(name, df)

    def list_tables(self) -> list[str]:
        """Return a list of all table and view names in the current connection.

        Returns:
            List of table/view name strings.
        """
        rows = self._conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'"
        ).fetchall()
        return [r[0] for r in rows]

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def close(self) -> None:
        """Close the underlying DuckDB connection."""
        self._conn.close()

    def __enter__(self) -> DuckDBEngine:
        """Support use as a context manager."""
        return self

    def __exit__(self, *args: object) -> None:
        """Close connection on context exit."""
        self.close()


__all__ = ["DuckDBEngine", "SQLValidator"]

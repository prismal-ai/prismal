"""Database query skill — runs read-only SQL queries via DuckDB.

Only SELECT statements are permitted. Any query that modifies state
(INSERT, UPDATE, DELETE, DROP, CREATE, etc.) is rejected before execution.

Requires the following environment variable:

* ``LIGHTAGENT_DB_PATH`` — Path to the DuckDB or SQLite database file
  (default: ``data/db/lightagent.db``).

If the database file is not found the tool returns a helpful error message
without raising an exception (graceful degradation).

Example::

    skill = DatabaseQuerySkill()
    tools = skill.get_tools()
    result = tools[0].invoke({"query": "SELECT * FROM users LIMIT 5"})
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from langchain_core.tools import BaseTool, tool

from lightagent.core.logging import get_logger
from lightagent.skills.base import BaseSkill, SkillMetadata

logger = get_logger("lightagent.skills.database_query")

_DEFAULT_DB = "data/db/lightagent.db"
_MAX_ROWS = 100

# Disallow anything that could mutate state
_MUTATING_PATTERN = re.compile(
    r"^\s*(INSERT|UPDATE|DELETE|DROP|CREATE|ALTER|TRUNCATE|REPLACE|MERGE|CALL|EXEC)\b",
    re.IGNORECASE,
)


def _is_select_only(query: str) -> bool:
    """Return True if the query is read-only (starts with SELECT or WITH).

    Args:
        query: SQL query string to validate.

    Returns:
        True if query is safe (SELECT/WITH), False if it looks mutating.
    """
    stripped = query.strip()
    if _MUTATING_PATTERN.match(stripped):
        return False
    return bool(re.match(r"^\s*(SELECT|WITH)\b", stripped, re.IGNORECASE))


def _run_query(db_path: Path, query: str) -> str:
    """Execute a read-only SQL query using DuckDB.

    Args:
        db_path: Path to the database file.
        query: Validated SELECT query.

    Returns:
        Query results formatted as a text table, or an error message.
    """
    try:
        import duckdb  # optional dependency
    except ImportError:
        return "[database_query] DuckDB is not installed. Run: pip install duckdb"

    try:
        conn = duckdb.connect(str(db_path), read_only=True)
        try:
            rel = conn.execute(query)
            rows = rel.fetchmany(_MAX_ROWS)
            columns = [desc[0] for desc in (rel.description or [])]

            if not rows:
                return "Query returned no rows."

            # Simple text table
            col_widths = [len(c) for c in columns]
            for row in rows:
                for i, val in enumerate(row):
                    col_widths[i] = max(col_widths[i], len(str(val)))

            header = " | ".join(c.ljust(col_widths[i]) for i, c in enumerate(columns))
            separator = "-+-".join("-" * w for w in col_widths)
            data_rows = [
                " | ".join(str(v).ljust(col_widths[i]) for i, v in enumerate(row)) for row in rows
            ]
            result = "\n".join([header, separator, *data_rows])
            if len(rows) == _MAX_ROWS:
                result += f"\n(results limited to {_MAX_ROWS} rows)"
            return result
        finally:
            conn.close()
    except Exception as exc:
        return f"[database_query] Query error: {exc}"


class DatabaseQuerySkill(BaseSkill):
    """Executes read-only SQL SELECT queries against a DuckDB database."""

    @property
    def metadata(self) -> SkillMetadata:
        """Return database query skill metadata."""
        return SkillMetadata(
            name="database_query",
            description="Run read-only SQL SELECT queries against a DuckDB database",
            version="1.0.0",
            author="lightagent",
            safe_to_auto_activate=False,
            requires_permissions=["filesystem.read"],
            tags=["utility", "database", "sql", "duckdb", "analytics"],
        )

    def get_tools(self) -> list[BaseTool]:
        """Return database query tools.

        Returns:
            List containing the query_database tool.
        """

        @tool
        def query_database(query: str) -> str:
            """Execute a read-only SQL SELECT query against the configured database.

            Only SELECT and WITH (CTE) statements are permitted.
            Results are limited to 100 rows.

            Args:
                query: A valid SQL SELECT (e.g. 'SELECT * FROM users LIMIT 5').

            Returns:
                Query results as a text table, or an error/safety message.
            """
            if not _is_select_only(query):
                return (
                    "[database_query] Only SELECT queries are allowed. "
                    "Mutating statements (INSERT, UPDATE, DELETE, DROP, etc.) "
                    "are not permitted."
                )

            db_path = Path(os.getenv("LIGHTAGENT_DB_PATH", _DEFAULT_DB))
            if not db_path.exists():
                return (
                    f"[database_query] Database file not found: {db_path}. "
                    "Set LIGHTAGENT_DB_PATH to an existing database file."
                )

            logger.info("database_query_run", db=str(db_path), query=query[:100])
            return _run_query(db_path, query)

        return [query_database]

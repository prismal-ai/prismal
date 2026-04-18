"""Unit tests for lightagent.data.duckdb_engine — T-130."""

from __future__ import annotations

import csv
from pathlib import Path

import polars as pl
import pytest

from lightagent.core.exceptions import LightAgentError
from lightagent.data.duckdb_engine import DuckDBEngine, SQLValidator

# ── SQLValidator ─────────────────────────────────────────────────────────────


def test_validator_allows_select() -> None:
    """SELECT queries pass validation."""
    v = SQLValidator()
    v.validate("SELECT * FROM foo")  # should not raise


def test_validator_blocks_drop() -> None:
    """DROP TABLE is blocked."""
    v = SQLValidator()
    with pytest.raises(LightAgentError, match="DROP"):
        v.validate("DROP TABLE foo")


def test_validator_blocks_delete() -> None:
    """DELETE is blocked."""
    v = SQLValidator()
    with pytest.raises(LightAgentError, match="DELETE"):
        v.validate("DELETE FROM foo WHERE id=1")


def test_validator_blocks_insert() -> None:
    """INSERT is blocked."""
    v = SQLValidator()
    with pytest.raises(LightAgentError, match="INSERT"):
        v.validate("INSERT INTO foo VALUES (1)")


def test_validator_blocks_update() -> None:
    """UPDATE is blocked."""
    v = SQLValidator()
    with pytest.raises(LightAgentError, match="UPDATE"):
        v.validate("UPDATE foo SET bar=1")


def test_validator_blocks_create() -> None:
    """CREATE TABLE is blocked."""
    v = SQLValidator()
    with pytest.raises(LightAgentError, match="CREATE"):
        v.validate("CREATE TABLE foo (id INT)")


def test_validator_case_insensitive() -> None:
    """Validation is case-insensitive."""
    v = SQLValidator()
    with pytest.raises(LightAgentError):
        v.validate("drop table foo")


# ── DuckDBEngine ─────────────────────────────────────────────────────────────


def test_engine_initializes_in_memory() -> None:
    """Engine initialises with an in-memory DuckDB connection."""
    engine = DuckDBEngine()
    assert engine is not None


def test_query_simple_select() -> None:
    """Simple SELECT on a registered table returns records."""
    engine = DuckDBEngine()
    engine._conn.execute("CREATE TABLE nums AS SELECT * FROM range(3) t(n)")
    rows = engine.query("SELECT n FROM nums ORDER BY n")
    assert rows == [{"n": 0}, {"n": 1}, {"n": 2}]


def test_query_returns_list_of_dicts() -> None:
    """Query result is a list of dicts keyed by column name."""
    engine = DuckDBEngine()
    rows = engine.query("SELECT 42 AS answer")
    assert isinstance(rows, list)
    assert rows[0]["answer"] == 42


def test_query_raises_on_destructive_sql() -> None:
    """query() raises LightAgentError for destructive SQL."""
    engine = DuckDBEngine()
    with pytest.raises(LightAgentError):
        engine.query("DROP TABLE IF EXISTS foo")


def test_query_csv_reads_file(tmp_path: Path) -> None:
    """query_csv can SELECT from a CSV file."""
    csv_file = tmp_path / "sales.csv"
    with csv_file.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["region", "sales"])
        writer.writeheader()
        writer.writerows(
            [
                {"region": "north", "sales": "100"},
                {"region": "south", "sales": "200"},
            ]
        )
    engine = DuckDBEngine()
    rows = engine.query_csv(csv_file)
    assert len(rows) == 2
    assert {r["region"] for r in rows} == {"north", "south"}


def test_query_csv_with_custom_sql(tmp_path: Path) -> None:
    """query_csv accepts custom SQL with AVG aggregate."""
    csv_file = tmp_path / "data.csv"
    with csv_file.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["sales"])
        writer.writeheader()
        writer.writerows([{"sales": "100"}, {"sales": "200"}, {"sales": "300"}])
    engine = DuckDBEngine()
    rows = engine.query_csv(csv_file, sql="SELECT AVG(sales::DOUBLE) AS avg_sales FROM data")
    assert abs(rows[0]["avg_sales"] - 200.0) < 0.01


def test_register_polars_and_query() -> None:
    """register_polars makes a Polars DataFrame queryable by name."""
    engine = DuckDBEngine()
    df = pl.DataFrame({"x": [1, 2, 3], "y": [10, 20, 30]})
    engine.register_polars("my_df", df)
    rows = engine.query("SELECT SUM(y) AS total FROM my_df")
    assert rows[0]["total"] == 60


def test_list_tables_includes_registered() -> None:
    """list_tables returns registered table names."""
    engine = DuckDBEngine()
    df = pl.DataFrame({"a": [1]})
    engine.register_polars("my_table", df)
    tables = engine.list_tables()
    assert "my_table" in tables

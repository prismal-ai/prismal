"""Analytics data layer — DuckDB and Polars utilities.

Phase 14 — SPEC-012 Analytics Layer.

Public re-exports:

- :class:`~prismal.data.duckdb_engine.DuckDBEngine` — validated SQL engine
- :class:`~prismal.data.duckdb_engine.SQLValidator` — SQL safety guard
- :func:`~prismal.data.polars_utils.filter_rows` — row filtering
- :func:`~prismal.data.polars_utils.group_by_aggregate` — group & aggregate
- :func:`~prismal.data.polars_utils.sort_by` — sort DataFrame
- :func:`~prismal.data.polars_utils.select_columns` — column projection
- :func:`~prismal.data.polars_utils.to_records` — convert to dicts
- :func:`~prismal.data.polars_utils.save_chart` — chart generation
"""

from prismal.data.duckdb_engine import DuckDBEngine, SQLValidator
from prismal.data.polars_utils import (
    filter_rows,
    group_by_aggregate,
    save_chart,
    select_columns,
    sort_by,
    to_records,
)

__all__ = [
    "DuckDBEngine",
    "SQLValidator",
    "filter_rows",
    "group_by_aggregate",
    "save_chart",
    "select_columns",
    "sort_by",
    "to_records",
]

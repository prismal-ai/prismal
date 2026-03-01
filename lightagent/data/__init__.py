"""Analytics data layer — DuckDB and Polars utilities.

Phase 14 — SPEC-012 Analytics Layer.

Public re-exports:

- :class:`~lightagent.data.duckdb_engine.DuckDBEngine` — validated SQL engine
- :class:`~lightagent.data.duckdb_engine.SQLValidator` — SQL safety guard
- :func:`~lightagent.data.polars_utils.filter_rows` — row filtering
- :func:`~lightagent.data.polars_utils.group_by_aggregate` — group & aggregate
- :func:`~lightagent.data.polars_utils.sort_by` — sort DataFrame
- :func:`~lightagent.data.polars_utils.select_columns` — column projection
- :func:`~lightagent.data.polars_utils.to_records` — convert to dicts
- :func:`~lightagent.data.polars_utils.save_chart` — chart generation
"""

from lightagent.data.duckdb_engine import DuckDBEngine, SQLValidator
from lightagent.data.polars_utils import (
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

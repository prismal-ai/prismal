"""Polars DataFrame transformation utilities.

Provides high-level helpers for filtering, aggregating, sorting, and
visualising Polars DataFrames.  All public functions are pure (they return
new DataFrames rather than mutating the input) and accept only serialisable
arguments so they can be called from agent tool descriptions.

Satisfies SPEC-012 AC-012-2 (DataFrame transformations) and
AC-012-4 (chart generation saved to data/workspace/).
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import polars as pl

from prismal.core.exceptions import PrismalError

# Supported comparison operators for filter_rows()
FilterOp = Literal["==", "!=", ">", ">=", "<", "<="]

# Supported aggregation functions for group_by_aggregate()
AggFn = Literal["sum", "mean", "min", "max", "count", "median"]

# Supported chart types
ChartKind = Literal["bar", "line", "scatter", "hist"]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _apply_filter(df: pl.DataFrame, column: str, op: FilterOp, value: object) -> pl.DataFrame:
    """Return rows from *df* where *column op value* is True.

    Args:
        df: Source DataFrame.
        column: Column to filter on.
        op: Comparison operator string.
        value: Value to compare against.

    Returns:
        Filtered DataFrame.

    Raises:
        PrismalError: If *column* does not exist or *op* is unsupported.
    """
    if column not in df.columns:
        raise PrismalError(f"Column '{column}' not found. Available: {df.columns}")

    col_expr = pl.col(column)
    mask: pl.Expr
    match op:
        case "==":
            mask = col_expr.eq(value)
        case "!=":
            mask = col_expr.ne(value)
        case ">":
            mask = col_expr.gt(value)
        case ">=":
            mask = col_expr.ge(value)
        case "<":
            mask = col_expr.lt(value)
        case "<=":
            mask = col_expr.le(value)
        case _:  # pragma: no cover
            raise PrismalError(f"Unsupported filter operator: '{op}'")

    return df.filter(mask)


# ---------------------------------------------------------------------------
# Public transformation helpers
# ---------------------------------------------------------------------------


def filter_rows(
    df: pl.DataFrame,
    column: str,
    op: FilterOp,
    value: object,
) -> pl.DataFrame:
    """Filter DataFrame rows by a column condition.

    Example::

        filtered = filter_rows(df, "sales", ">", 1000)

    Args:
        df: Source DataFrame.
        column: Column name to filter on.
        op: Comparison operator: ``"=="``, ``"!="``, ``">"``, ``">="``,
            ``"<"``, or ``"<="``.
        value: Scalar value to compare against.

    Returns:
        New DataFrame containing only rows that satisfy the condition.

    Raises:
        PrismalError: If *column* is missing or *op* is unknown.
    """
    return _apply_filter(df, column, op, value)


def group_by_aggregate(
    df: pl.DataFrame,
    group_col: str,
    agg_col: str,
    agg_fn: AggFn = "sum",
) -> pl.DataFrame:
    """Group *df* by *group_col* and aggregate *agg_col* with *agg_fn*.

    Example::

        result = group_by_aggregate(df, "region", "sales", "sum")
        # region | sales
        # north  | 45000
        # south  | 32000

    Args:
        df: Source DataFrame.
        group_col: Column to group by.
        agg_col: Column to aggregate.
        agg_fn: Aggregation function: ``"sum"``, ``"mean"``, ``"min"``,
            ``"max"``, ``"count"``, or ``"median"``.

    Returns:
        Aggregated DataFrame sorted by *group_col*.

    Raises:
        PrismalError: If *group_col* or *agg_col* is missing, or if
            *agg_fn* is unsupported.
    """
    for col in (group_col, agg_col):
        if col not in df.columns:
            raise PrismalError(f"Column '{col}' not found. Available: {df.columns}")

    col_expr = pl.col(agg_col)
    match agg_fn:
        case "sum":
            agg_expr = col_expr.sum()
        case "mean":
            agg_expr = col_expr.mean()
        case "min":
            agg_expr = col_expr.min()
        case "max":
            agg_expr = col_expr.max()
        case "count":
            agg_expr = col_expr.count()
        case "median":
            agg_expr = col_expr.median()
        case _:  # pragma: no cover
            raise PrismalError(f"Unsupported aggregation function: '{agg_fn}'")

    return df.group_by(group_col).agg(agg_expr.alias(agg_col)).sort(group_col)


def sort_by(
    df: pl.DataFrame,
    column: str,
    ascending: bool = True,
) -> pl.DataFrame:
    """Sort *df* by *column*.

    Args:
        df: Source DataFrame.
        column: Column to sort on.
        ascending: Sort direction.  ``True`` for ascending (default).

    Returns:
        Sorted DataFrame.

    Raises:
        PrismalError: If *column* does not exist.
    """
    if column not in df.columns:
        raise PrismalError(f"Column '{column}' not found. Available: {df.columns}")

    return df.sort(column, descending=not ascending)


def select_columns(df: pl.DataFrame, columns: list[str]) -> pl.DataFrame:
    """Return a DataFrame with only the specified *columns*.

    Args:
        df: Source DataFrame.
        columns: Column names to keep.

    Returns:
        New DataFrame with the selected columns in the given order.

    Raises:
        PrismalError: If any column in *columns* is missing.
    """
    missing = [c for c in columns if c not in df.columns]
    if missing:
        raise PrismalError(f"Columns not found: {missing}. Available: {df.columns}")
    return df.select(columns)


def to_records(df: pl.DataFrame) -> list[dict[str, object]]:
    """Convert a Polars DataFrame to a list of row dicts.

    Example::

        rows = to_records(df)
        # [{"region": "north", "sales": 100}, ...]

    Args:
        df: Source DataFrame.

    Returns:
        List of dicts, one per row, keyed by column name.
    """
    return df.to_dicts()


# ---------------------------------------------------------------------------
# Chart generation
# ---------------------------------------------------------------------------


def save_chart(
    df: pl.DataFrame,
    x_col: str,
    y_col: str,
    output_path: str | Path,
    kind: ChartKind = "bar",
    title: str = "",
    *,
    dpi: int = 150,
) -> Path:
    """Generate a chart from *df* and save it to *output_path*.

    Supports bar, line, scatter, and histogram chart types.
    The output directory is created automatically if it does not exist.

    Example::

        path = save_chart(df, "region", "sales", "data/workspace/chart.png")

    Args:
        df: Source DataFrame.
        x_col: Column for the x-axis (or the data column for histograms).
        y_col: Column for the y-axis (not used for histograms).
        output_path: File path to write the image (PNG/SVG/PDF).
        kind: Chart type: ``"bar"``, ``"line"``, ``"scatter"``, or
            ``"hist"``.
        title: Optional chart title.
        dpi: Image resolution (dots per inch).  Default 150.

    Returns:
        Resolved :class:`pathlib.Path` to the saved file.

    Raises:
        PrismalError: If required columns are missing or *kind* is
            unsupported.
    """
    import matplotlib as mpl

    mpl.use("Agg")  # non-interactive backend — safe in server context
    import matplotlib.pyplot as plt

    for col in (x_col, y_col) if kind != "hist" else (x_col,):
        if col not in df.columns:
            raise PrismalError(f"Column '{col}' not found. Available: {df.columns}")

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    fig, ax = plt.subplots(figsize=(8, 5))

    xs = df[x_col].to_list()
    ys = df[y_col].to_list() if kind != "hist" else []

    match kind:
        case "bar":
            ax.bar(xs, ys)
            ax.set_xlabel(x_col)
            ax.set_ylabel(y_col)
        case "line":
            ax.plot(xs, ys, marker="o")
            ax.set_xlabel(x_col)
            ax.set_ylabel(y_col)
        case "scatter":
            ax.scatter(xs, ys)
            ax.set_xlabel(x_col)
            ax.set_ylabel(y_col)
        case "hist":
            ax.hist(xs, bins="auto")
            ax.set_xlabel(x_col)
            ax.set_ylabel("frequency")
        case _:  # pragma: no cover
            plt.close(fig)
            raise PrismalError(f"Unsupported chart kind: '{kind}'")

    if title:
        ax.set_title(title)

    fig.tight_layout()
    fig.savefig(str(out), dpi=dpi)
    plt.close(fig)

    return out.resolve()


__all__ = [
    "AggFn",
    "ChartKind",
    "FilterOp",
    "filter_rows",
    "group_by_aggregate",
    "save_chart",
    "select_columns",
    "sort_by",
    "to_records",
]

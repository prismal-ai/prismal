"""Unit tests for lightagent.data.polars_utils — T-141."""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from lightagent.core.exceptions import LightAgentError
from lightagent.data.polars_utils import (
    filter_rows,
    group_by_aggregate,
    save_chart,
    select_columns,
    sort_by,
    to_records,
)


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
def sales_df() -> pl.DataFrame:
    """A small sales DataFrame used across multiple tests."""
    return pl.DataFrame(
        {
            "region": ["north", "south", "east", "west", "north"],
            "sales": [1200, 800, 1500, 400, 900],
            "units": [10, 8, 15, 4, 9],
        }
    )


# ---------------------------------------------------------------------------
# filter_rows
# ---------------------------------------------------------------------------


def test_filter_rows_greater_than(sales_df: pl.DataFrame) -> None:
    """filter_rows with '>' returns only rows above threshold."""
    result = filter_rows(sales_df, "sales", ">", 1000)
    assert all(r["sales"] > 1000 for r in result.to_dicts())
    assert len(result) == 2


def test_filter_rows_equal(sales_df: pl.DataFrame) -> None:
    """filter_rows with '==' returns matching rows."""
    result = filter_rows(sales_df, "region", "==", "north")
    assert len(result) == 2
    assert all(r["region"] == "north" for r in result.to_dicts())


def test_filter_rows_not_equal(sales_df: pl.DataFrame) -> None:
    """filter_rows with '!=' excludes matching rows."""
    result = filter_rows(sales_df, "region", "!=", "north")
    assert all(r["region"] != "north" for r in result.to_dicts())


def test_filter_rows_less_than_or_equal(sales_df: pl.DataFrame) -> None:
    """filter_rows with '<=' works correctly."""
    result = filter_rows(sales_df, "sales", "<=", 800)
    assert all(r["sales"] <= 800 for r in result.to_dicts())


def test_filter_rows_less_than(sales_df: pl.DataFrame) -> None:
    """filter_rows with '<' works correctly."""
    result = filter_rows(sales_df, "sales", "<", 900)
    assert all(r["sales"] < 900 for r in result.to_dicts())


def test_filter_rows_greater_equal(sales_df: pl.DataFrame) -> None:
    """filter_rows with '>=' works correctly."""
    result = filter_rows(sales_df, "sales", ">=", 1500)
    assert all(r["sales"] >= 1500 for r in result.to_dicts())


def test_filter_rows_missing_column(sales_df: pl.DataFrame) -> None:
    """filter_rows raises LightAgentError for missing column."""
    with pytest.raises(LightAgentError, match="Column 'nonexistent'"):
        filter_rows(sales_df, "nonexistent", ">", 0)


def test_filter_rows_does_not_mutate_input(sales_df: pl.DataFrame) -> None:
    """filter_rows leaves the source DataFrame unchanged."""
    original_len = len(sales_df)
    filter_rows(sales_df, "sales", ">", 1000)
    assert len(sales_df) == original_len


def test_filter_rows_empty_result(sales_df: pl.DataFrame) -> None:
    """filter_rows can return an empty DataFrame (no matches)."""
    result = filter_rows(sales_df, "sales", ">", 99999)
    assert len(result) == 0


# ---------------------------------------------------------------------------
# group_by_aggregate
# ---------------------------------------------------------------------------


def test_group_by_sum(sales_df: pl.DataFrame) -> None:
    """group_by_aggregate with 'sum' totals the aggregate column."""
    result = group_by_aggregate(sales_df, "region", "sales", "sum")
    north_row = result.filter(pl.col("region") == "north").to_dicts()
    assert len(north_row) == 1
    assert north_row[0]["sales"] == 2100  # 1200 + 900


def test_group_by_mean(sales_df: pl.DataFrame) -> None:
    """group_by_aggregate with 'mean' computes the average."""
    result = group_by_aggregate(sales_df, "region", "sales", "mean")
    north_row = result.filter(pl.col("region") == "north").to_dicts()
    assert abs(north_row[0]["sales"] - 1050.0) < 0.01


def test_group_by_count(sales_df: pl.DataFrame) -> None:
    """group_by_aggregate with 'count' counts rows per group."""
    result = group_by_aggregate(sales_df, "region", "sales", "count")
    north_row = result.filter(pl.col("region") == "north").to_dicts()
    assert north_row[0]["sales"] == 2


def test_group_by_min(sales_df: pl.DataFrame) -> None:
    """group_by_aggregate with 'min' returns minimum value per group."""
    result = group_by_aggregate(sales_df, "region", "sales", "min")
    north_row = result.filter(pl.col("region") == "north").to_dicts()
    assert north_row[0]["sales"] == 900


def test_group_by_max(sales_df: pl.DataFrame) -> None:
    """group_by_aggregate with 'max' returns maximum value per group."""
    result = group_by_aggregate(sales_df, "region", "sales", "max")
    north_row = result.filter(pl.col("region") == "north").to_dicts()
    assert north_row[0]["sales"] == 1200


def test_group_by_median(sales_df: pl.DataFrame) -> None:
    """group_by_aggregate with 'median' computes the median per group."""
    result = group_by_aggregate(sales_df, "region", "sales", "median")
    assert len(result) == 4  # north, south, east, west


def test_group_by_result_is_sorted(sales_df: pl.DataFrame) -> None:
    """group_by_aggregate result is sorted by group_col."""
    result = group_by_aggregate(sales_df, "region", "sales", "sum")
    regions = result["region"].to_list()
    assert regions == sorted(regions)


def test_group_by_missing_group_col(sales_df: pl.DataFrame) -> None:
    """group_by_aggregate raises LightAgentError for missing group_col."""
    with pytest.raises(LightAgentError, match="Column 'bad_col'"):
        group_by_aggregate(sales_df, "bad_col", "sales", "sum")


def test_group_by_missing_agg_col(sales_df: pl.DataFrame) -> None:
    """group_by_aggregate raises LightAgentError for missing agg_col."""
    with pytest.raises(LightAgentError, match="Column 'bad_col'"):
        group_by_aggregate(sales_df, "region", "bad_col", "sum")


# ---------------------------------------------------------------------------
# sort_by
# ---------------------------------------------------------------------------


def test_sort_ascending(sales_df: pl.DataFrame) -> None:
    """sort_by ascending produces smallest values first."""
    result = sort_by(sales_df, "sales")
    vals = result["sales"].to_list()
    assert vals == sorted(vals)


def test_sort_descending(sales_df: pl.DataFrame) -> None:
    """sort_by descending produces largest values first."""
    result = sort_by(sales_df, "sales", ascending=False)
    vals = result["sales"].to_list()
    assert vals == sorted(vals, reverse=True)


def test_sort_missing_column(sales_df: pl.DataFrame) -> None:
    """sort_by raises LightAgentError for missing column."""
    with pytest.raises(LightAgentError, match="Column 'missing'"):
        sort_by(sales_df, "missing")


# ---------------------------------------------------------------------------
# select_columns
# ---------------------------------------------------------------------------


def test_select_columns_subset(sales_df: pl.DataFrame) -> None:
    """select_columns returns only the requested columns."""
    result = select_columns(sales_df, ["region", "sales"])
    assert result.columns == ["region", "sales"]
    assert "units" not in result.columns


def test_select_columns_single(sales_df: pl.DataFrame) -> None:
    """select_columns with one column returns a single-column DataFrame."""
    result = select_columns(sales_df, ["region"])
    assert result.columns == ["region"]


def test_select_columns_missing(sales_df: pl.DataFrame) -> None:
    """select_columns raises LightAgentError when a column is missing."""
    with pytest.raises(LightAgentError, match="Columns not found"):
        select_columns(sales_df, ["region", "nonexistent"])


# ---------------------------------------------------------------------------
# to_records
# ---------------------------------------------------------------------------


def test_to_records_returns_list_of_dicts(sales_df: pl.DataFrame) -> None:
    """to_records converts DataFrame to list of dicts."""
    records = to_records(sales_df)
    assert isinstance(records, list)
    assert all(isinstance(r, dict) for r in records)
    assert len(records) == len(sales_df)


def test_to_records_keys_match_columns(sales_df: pl.DataFrame) -> None:
    """to_records row dicts have keys matching DataFrame columns."""
    records = to_records(sales_df)
    for record in records:
        assert set(record.keys()) == set(sales_df.columns)


def test_to_records_empty_dataframe() -> None:
    """to_records on an empty DataFrame returns an empty list."""
    df = pl.DataFrame({"a": [], "b": []})
    assert to_records(df) == []


# ---------------------------------------------------------------------------
# save_chart
# ---------------------------------------------------------------------------


def test_save_chart_bar(tmp_path: Path, sales_df: pl.DataFrame) -> None:
    """save_chart with kind='bar' creates a PNG file."""
    out = tmp_path / "chart.png"
    result = save_chart(sales_df, "region", "sales", out, kind="bar", title="Sales by Region")
    assert result.exists()
    assert result.suffix == ".png"


def test_save_chart_line(tmp_path: Path, sales_df: pl.DataFrame) -> None:
    """save_chart with kind='line' creates a file."""
    out = tmp_path / "line.png"
    result = save_chart(sales_df, "region", "sales", out, kind="line")
    assert result.exists()


def test_save_chart_scatter(tmp_path: Path, sales_df: pl.DataFrame) -> None:
    """save_chart with kind='scatter' creates a file."""
    out = tmp_path / "scatter.png"
    result = save_chart(sales_df, "units", "sales", out, kind="scatter")
    assert result.exists()


def test_save_chart_hist(tmp_path: Path, sales_df: pl.DataFrame) -> None:
    """save_chart with kind='hist' creates a histogram."""
    out = tmp_path / "hist.png"
    result = save_chart(sales_df, "sales", "sales", out, kind="hist")
    assert result.exists()


def test_save_chart_creates_parent_dir(tmp_path: Path, sales_df: pl.DataFrame) -> None:
    """save_chart creates parent directories automatically."""
    out = tmp_path / "nested" / "deep" / "chart.png"
    result = save_chart(sales_df, "region", "sales", out)
    assert result.exists()


def test_save_chart_missing_column(tmp_path: Path, sales_df: pl.DataFrame) -> None:
    """save_chart raises LightAgentError when a column is missing."""
    with pytest.raises(LightAgentError, match="Column 'bad'"):
        save_chart(sales_df, "bad", "sales", tmp_path / "c.png")


def test_save_chart_returns_resolved_path(tmp_path: Path, sales_df: pl.DataFrame) -> None:
    """save_chart returns an absolute resolved Path."""
    out = tmp_path / "chart.png"
    result = save_chart(sales_df, "region", "sales", out)
    assert result.is_absolute()

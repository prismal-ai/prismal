"""Unit tests for data_etl subgraph (C3).

Pipeline::

    extractor → validator → transformer → loader → auditor
                       └─(fail)──────────────────────────↑

- extractor: reads source_spec → polars DataFrame in state.
- validator: schema / non-empty checks; writes {passed, errors}.
- transformer: applies the user-supplied ops (select, filter, rename).
- loader: writes DataFrame to destination (CSV/Parquet); records row count.
- auditor: summarises the run (row counts, columns, duration-ish).

Validator routes to auditor on failure (skipping transformer + loader).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import polars as pl
import pytest
from langchain_core.messages import AIMessage, HumanMessage

from lightagent.agents.subgraphs.data_etl import (
    DataETLError,
    make_auditor_node,
    make_etl_branching_gate,
    make_extractor_node,
    make_loader_node,
    make_transformer_node,
    make_validator_node,
)
from lightagent.agents.subgraphs.data_etl.builder import build_data_etl_subgraph
from lightagent.core.exceptions import LightAgentError

# ── Helpers ───────────────────────────────────────────────────────────────────


def _df() -> pl.DataFrame:
    return pl.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "z"]})


def _state(
    source: dict[str, Any] | None = None,
    destination: dict[str, Any] | None = None,
    transforms: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    data_etl: dict[str, Any] = {}
    if source is not None:
        data_etl["source"] = source
    if destination is not None:
        data_etl["destination"] = destination
    if transforms is not None:
        data_etl["transforms"] = transforms
    return {
        "messages": [HumanMessage(content="run etl")],
        "metadata": {"data_etl": data_etl} if data_etl else {},
    }


# ── Exception ─────────────────────────────────────────────────────────────────


def test_data_etl_error_inherits_from_lightagent_error() -> None:
    assert issubclass(DataETLError, LightAgentError)


# ── extractor ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_extractor_uses_injected_callable() -> None:
    df = _df()

    async def fake_extractor(source: dict[str, Any]) -> pl.DataFrame:
        assert source["path"] == "x.csv"
        return df

    node = make_extractor_node(extractor_fn=fake_extractor)
    state = _state(source={"type": "csv", "path": "x.csv"})
    update = await node(state)
    et = update["metadata"]["data_etl"]
    assert et["dataframe"].equals(df)
    assert et["raw_row_count"] == 3
    assert et["raw_columns"] == ["a", "b"]


@pytest.mark.asyncio
async def test_extractor_returns_empty_when_source_missing() -> None:
    """Without a source spec, extractor cannot do anything."""
    node = make_extractor_node(extractor_fn=MagicMock())
    state = _state()
    update = await node(state)
    assert update == {}


@pytest.mark.asyncio
async def test_extractor_rejects_unknown_source_type() -> None:
    node = make_extractor_node()
    state = _state(source={"type": "doesnotexist", "path": "x"})
    with pytest.raises(ValueError):
        await node(state)


@pytest.mark.asyncio
async def test_extractor_propagates_extraction_errors() -> None:
    async def failing(source: dict[str, Any]) -> pl.DataFrame:
        raise RuntimeError("boom")

    node = make_extractor_node(extractor_fn=failing)
    state = _state(source={"type": "csv", "path": "x"})
    with pytest.raises(RuntimeError):
        await node(state)


@pytest.mark.asyncio
async def test_extractor_supports_sync_callable() -> None:
    df = _df()

    def sync_extractor(source: dict[str, Any]) -> pl.DataFrame:
        return df

    node = make_extractor_node(extractor_fn=sync_extractor)
    state = _state(source={"type": "csv", "path": "x.csv"})
    update = await node(state)
    assert update["metadata"]["data_etl"]["raw_row_count"] == 3


@pytest.mark.asyncio
async def test_extractor_reads_csv_via_default_implementation(tmp_path: Path) -> None:
    """Default extractor handles a real CSV file end-to-end."""
    csv_path = tmp_path / "t.csv"
    csv_path.write_text("a,b\n1,x\n2,y\n3,z\n")

    node = make_extractor_node()  # use default polars-backed extractor
    state = _state(source={"type": "csv", "path": str(csv_path)})
    update = await node(state)
    et = update["metadata"]["data_etl"]
    assert et["raw_row_count"] == 3
    assert set(et["raw_columns"]) == {"a", "b"}


# ── validator ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_validator_passes_on_non_empty_dataframe() -> None:
    node = make_validator_node()
    state = _state(source={"type": "csv", "path": "x"})
    state["metadata"]["data_etl"]["dataframe"] = _df()
    update = await node(state)
    v = update["metadata"]["data_etl"]["validation"]
    assert v["passed"] is True
    assert v["errors"] == []


@pytest.mark.asyncio
async def test_validator_fails_on_empty_dataframe() -> None:
    node = make_validator_node()
    state = _state(source={"type": "csv", "path": "x"})
    state["metadata"]["data_etl"]["dataframe"] = pl.DataFrame({"a": []})
    update = await node(state)
    v = update["metadata"]["data_etl"]["validation"]
    assert v["passed"] is False
    assert any("empty" in e.lower() for e in v["errors"])


@pytest.mark.asyncio
async def test_validator_fails_on_missing_required_columns() -> None:
    node = make_validator_node(required_columns=["a", "b", "c"])
    state = _state(source={"type": "csv", "path": "x"})
    state["metadata"]["data_etl"]["dataframe"] = _df()
    update = await node(state)
    v = update["metadata"]["data_etl"]["validation"]
    assert v["passed"] is False
    assert any("c" in e for e in v["errors"])


@pytest.mark.asyncio
async def test_validator_no_dataframe_returns_empty() -> None:
    node = make_validator_node()
    state = _state(source={"type": "csv", "path": "x"})
    update = await node(state)
    assert update == {}


# ── branching gate (after validator) ─────────────────────────────────────────


def test_etl_branching_gate_routes_passed_to_transformer() -> None:
    gate = make_etl_branching_gate()
    state = _state()
    state["metadata"]["data_etl"] = {"validation": {"passed": True, "errors": []}}
    assert gate(state) == "transformer"


def test_etl_branching_gate_routes_failed_to_auditor() -> None:
    gate = make_etl_branching_gate()
    state = _state()
    state["metadata"]["data_etl"] = {"validation": {"passed": False, "errors": ["e"]}}
    assert gate(state) == "auditor"


def test_etl_branching_gate_defaults_to_auditor_on_missing_validation() -> None:
    gate = make_etl_branching_gate()
    state = _state()
    assert gate(state) == "auditor"


# ── transformer ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_transformer_applies_select_op() -> None:
    node = make_transformer_node()
    state = _state(transforms=[{"op": "select", "columns": ["a"]}])
    state["metadata"]["data_etl"]["dataframe"] = _df()
    update = await node(state)
    df_out = update["metadata"]["data_etl"]["dataframe"]
    assert df_out.columns == ["a"]
    log = update["metadata"]["data_etl"]["transform_log"]
    assert log == ["select:['a']"]


@pytest.mark.asyncio
async def test_transformer_applies_filter_op() -> None:
    node = make_transformer_node()
    state = _state(transforms=[{"op": "filter", "column": "a", "operator": ">", "value": 1}])
    state["metadata"]["data_etl"]["dataframe"] = _df()
    update = await node(state)
    df_out = update["metadata"]["data_etl"]["dataframe"]
    assert df_out.height == 2


@pytest.mark.asyncio
async def test_transformer_applies_rename_op() -> None:
    node = make_transformer_node()
    state = _state(transforms=[{"op": "rename", "mapping": {"a": "A"}}])
    state["metadata"]["data_etl"]["dataframe"] = _df()
    update = await node(state)
    df_out = update["metadata"]["data_etl"]["dataframe"]
    assert "A" in df_out.columns
    assert "a" not in df_out.columns


@pytest.mark.asyncio
async def test_transformer_uses_custom_callable_when_supplied() -> None:
    async def custom(df: pl.DataFrame, ops: Any) -> tuple[pl.DataFrame, list[str]]:
        return df.select("b"), ["custom_op"]

    node = make_transformer_node(transformer_fn=custom)
    state = _state(transforms=[{"op": "select", "columns": ["a"]}])
    state["metadata"]["data_etl"]["dataframe"] = _df()
    update = await node(state)
    df_out = update["metadata"]["data_etl"]["dataframe"]
    assert df_out.columns == ["b"]


@pytest.mark.asyncio
async def test_transformer_noop_when_no_dataframe() -> None:
    node = make_transformer_node()
    update = await node(_state(transforms=[{"op": "select", "columns": ["a"]}]))
    assert update == {}


# ── loader ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_loader_writes_csv(tmp_path: Path) -> None:
    dest = tmp_path / "out.csv"
    node = make_loader_node()
    state = _state(destination={"type": "csv", "path": str(dest)})
    state["metadata"]["data_etl"]["dataframe"] = _df()
    update = await node(state)
    et = update["metadata"]["data_etl"]
    assert et["loaded_row_count"] == 3
    assert dest.exists()


@pytest.mark.asyncio
async def test_loader_uses_injected_callable() -> None:
    captured: dict[str, Any] = {}

    async def fake_loader(df: pl.DataFrame, dest: dict[str, Any]) -> int:
        captured["dest"] = dest
        return df.height

    node = make_loader_node(loader_fn=fake_loader)
    state = _state(destination={"type": "mock", "path": "anywhere"})
    state["metadata"]["data_etl"]["dataframe"] = _df()
    update = await node(state)
    assert update["metadata"]["data_etl"]["loaded_row_count"] == 3
    assert captured["dest"]["type"] == "mock"


@pytest.mark.asyncio
async def test_loader_noop_when_no_dataframe() -> None:
    node = make_loader_node()
    state = _state(destination={"type": "csv", "path": "out.csv"})
    update = await node(state)
    assert update == {}


@pytest.mark.asyncio
async def test_loader_writes_parquet(tmp_path: Path) -> None:
    dest = tmp_path / "out.parquet"
    node = make_loader_node()
    state = _state(destination={"type": "parquet", "path": str(dest)})
    state["metadata"]["data_etl"]["dataframe"] = _df()
    update = await node(state)
    assert update["metadata"]["data_etl"]["loaded_row_count"] == 3
    assert dest.exists()


@pytest.mark.asyncio
async def test_loader_rejects_unknown_destination_type(tmp_path: Path) -> None:
    node = make_loader_node()
    state = _state(destination={"type": "doesnotexist", "path": str(tmp_path / "x")})
    state["metadata"]["data_etl"]["dataframe"] = _df()
    with pytest.raises(ValueError):
        await node(state)


@pytest.mark.asyncio
async def test_loader_supports_sync_callable() -> None:
    def sync_loader(df: pl.DataFrame, dest: dict[str, Any]) -> int:
        return df.height

    node = make_loader_node(loader_fn=sync_loader)
    state = _state(destination={"type": "x", "path": "nowhere"})
    state["metadata"]["data_etl"]["dataframe"] = _df()
    update = await node(state)
    assert update["metadata"]["data_etl"]["loaded_row_count"] == 3


# ── auditor ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_auditor_produces_summary_and_ai_message() -> None:
    node = make_auditor_node()
    state = _state(source={"type": "csv", "path": "x.csv"})
    state["metadata"]["data_etl"].update(
        {
            "raw_row_count": 3,
            "raw_columns": ["a", "b"],
            "validation": {"passed": True, "errors": []},
            "transform_log": ["select:['a']"],
            "loaded_row_count": 3,
        }
    )
    update = await node(state)
    audit = update["metadata"]["data_etl"]["audit"]
    assert audit["passed"] is True
    assert audit["raw_row_count"] == 3
    assert audit["loaded_row_count"] == 3
    assert isinstance(update["messages"][0], AIMessage)


@pytest.mark.asyncio
async def test_auditor_reports_failure_when_validation_failed() -> None:
    node = make_auditor_node()
    state = _state(source={"type": "csv", "path": "x"})
    state["metadata"]["data_etl"].update(
        {
            "raw_row_count": 0,
            "validation": {"passed": False, "errors": ["dataset empty"]},
        }
    )
    update = await node(state)
    audit = update["metadata"]["data_etl"]["audit"]
    assert audit["passed"] is False
    assert audit["errors"] == ["dataset empty"]


# ── builder ──────────────────────────────────────────────────────────────────


def test_build_data_etl_subgraph_has_five_nodes() -> None:
    defn = build_data_etl_subgraph()
    assert set(defn.nodes.keys()) == {
        "extractor",
        "validator",
        "transformer",
        "loader",
        "auditor",
    }


def test_build_data_etl_subgraph_entry_point_is_extractor() -> None:
    defn = build_data_etl_subgraph()
    assert defn.entry_point == "extractor"


def test_build_data_etl_subgraph_has_conditional_gate_on_validator() -> None:
    defn = build_data_etl_subgraph()
    assert "validator" in defn.conditional_edges


def test_build_data_etl_subgraph_linear_happy_path_edges() -> None:
    defn = build_data_etl_subgraph()
    edges = set(defn.edges)
    assert ("extractor", "validator") in edges
    assert ("transformer", "loader") in edges
    assert ("loader", "auditor") in edges

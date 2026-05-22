"""Transformer node for data_etl subgraph.

Applies an ordered list of declarative transforms to the DataFrame. Each
transform is a dict, e.g.::

    {"op": "select", "columns": ["a", "b"]}
    {"op": "filter", "column": "a", "operator": ">", "value": 10}
    {"op": "rename", "mapping": {"a": "A"}}

The default implementation supports ``select``, ``filter``, and ``rename``;
a custom ``transformer_fn(df, transforms) -> (new_df, log)`` can be
injected for more complex pipelines (joins, window funcs, etc.).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import polars as pl

from prismal.core.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

logger = get_logger("prismal.subgraphs.data_etl.transformer")

_FILTER_OPS: dict[str, Callable[[pl.Expr, Any], pl.Expr]] = {
    "==": lambda col, v: col == v,
    "!=": lambda col, v: col != v,
    ">": lambda col, v: col > v,
    ">=": lambda col, v: col >= v,
    "<": lambda col, v: col < v,
    "<=": lambda col, v: col <= v,
}


def _default_transformer(
    df: pl.DataFrame, transforms: list[dict[str, Any]]
) -> tuple[pl.DataFrame, list[str]]:
    log: list[str] = []
    current = df
    for t in transforms:
        op = t.get("op")
        if op == "select":
            cols = list(t.get("columns") or [])
            current = current.select(cols)
            log.append(f"select:{cols}")
        elif op == "filter":
            column = t["column"]
            operator = t["operator"]
            value = t["value"]
            if operator not in _FILTER_OPS:
                raise ValueError(f"Unsupported filter operator: {operator!r}")
            expr = _FILTER_OPS[operator](pl.col(column), value)
            current = current.filter(expr)
            log.append(f"filter:{column} {operator} {value}")
        elif op == "rename":
            mapping = dict(t.get("mapping") or {})
            current = current.rename(mapping)
            log.append(f"rename:{mapping}")
        else:
            raise ValueError(f"Unsupported transform op: {op!r}")
    return current, log


def make_transformer_node(
    transformer_fn: (
        Callable[
            [pl.DataFrame, list[dict[str, Any]]],
            Awaitable[tuple[pl.DataFrame, list[str]]] | tuple[pl.DataFrame, list[str]],
        ]
        | None
    ) = None,
) -> Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]:
    """Return an async LangGraph node that applies transforms to the DataFrame."""
    fn = transformer_fn or _default_transformer

    async def transformer_node(state: dict[str, Any]) -> dict[str, Any]:
        existing = dict(state.get("metadata") or {}).get("data_etl") or {}
        df = existing.get("dataframe")
        transforms = list(existing.get("transforms") or [])
        if df is None:
            logger.info("data_etl_transform_skip", reason="no_dataframe")
            return {}

        try:
            raw = fn(df, transforms)
            df_out, log = await _maybe_await(raw)
        except Exception as exc:
            logger.warning("data_etl_transform_error", error=str(exc))
            raise

        logger.info(
            "data_etl_transformed",
            in_rows=df.height,
            out_rows=df_out.height,
            ops=len(log),
        )
        updated = {
            **existing,
            "dataframe": df_out,
            "transform_log": log,
        }
        return {
            "metadata": {
                **(state.get("metadata") or {}),
                "data_etl": updated,
            }
        }

    return transformer_node


async def _maybe_await(result: Any) -> Any:
    import inspect

    if inspect.iscoroutine(result):
        return await result
    return result


__all__ = ["make_transformer_node"]

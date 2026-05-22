"""Extractor node for data_etl subgraph.

Reads a data source (spec provided by the caller at
``state["metadata"]["data_etl"]["source"]``) into a polars DataFrame and
stores it along with row count / column list.

Source spec shape::

    {"type": "csv" | "parquet" | "json", "path": "<path>"}

The default implementation uses polars' readers. A custom ``extractor_fn``
can be injected for alternate back-ends (SQL, REST, in-memory fixtures).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import polars as pl

from prismal.core.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

logger = get_logger("prismal.subgraphs.data_etl.extractor")


async def _default_extractor(source: dict[str, Any]) -> pl.DataFrame:
    source_type = source.get("type", "csv").lower()
    path = source["path"]
    if source_type == "csv":
        return pl.read_csv(path)
    if source_type == "parquet":
        return pl.read_parquet(path)
    if source_type == "json":
        return pl.read_json(path)
    raise ValueError(f"Unsupported source type: {source_type!r}")


def make_extractor_node(
    extractor_fn: Callable[[dict[str, Any]], Awaitable[pl.DataFrame]] | None = None,
) -> Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]:
    """Return an async LangGraph node that extracts the source into state."""
    fn = extractor_fn or _default_extractor

    async def extractor_node(state: dict[str, Any]) -> dict[str, Any]:
        existing = dict(state.get("metadata") or {}).get("data_etl") or {}
        source = existing.get("source")
        if not source:
            logger.info("data_etl_extract_skip", reason="no_source")
            return {}

        try:
            df = await _maybe_await(fn(source))
        except Exception as exc:
            logger.warning("data_etl_extract_error", error=str(exc))
            raise

        updated = {
            **existing,
            "dataframe": df,
            "raw_row_count": df.height,
            "raw_columns": list(df.columns),
        }
        logger.info(
            "data_etl_extracted",
            row_count=df.height,
            columns=len(df.columns),
        )
        return {
            "metadata": {
                **(state.get("metadata") or {}),
                "data_etl": updated,
            }
        }

    return extractor_node


async def _maybe_await(result: Any) -> Any:
    """Await *result* only if it's a coroutine; return as-is otherwise."""
    import inspect

    if inspect.iscoroutine(result):
        return await result
    return result


__all__ = ["make_extractor_node"]

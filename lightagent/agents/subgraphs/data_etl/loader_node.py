"""Loader node for data_etl subgraph.

Writes the (possibly transformed) DataFrame to a destination and records
the number of rows actually persisted.

Destination spec shape::

    {"type": "csv" | "parquet", "path": "<path>"}

A custom ``loader_fn(df, destination) -> int`` can be injected for
alternative sinks (database inserts, object storage, etc.). It must return
the loaded row count.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from lightagent.core.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    import polars as pl

logger = get_logger("lightagent.subgraphs.data_etl.loader")


async def _default_loader(df: pl.DataFrame, destination: dict[str, Any]) -> int:
    dest_type = destination.get("type", "csv").lower()
    path = Path(destination["path"])
    path.parent.mkdir(parents=True, exist_ok=True)
    if dest_type == "csv":
        df.write_csv(str(path))
    elif dest_type == "parquet":
        df.write_parquet(str(path))
    else:
        raise ValueError(f"Unsupported destination type: {dest_type!r}")
    return df.height


def make_loader_node(
    loader_fn: (Callable[[pl.DataFrame, dict[str, Any]], Awaitable[int] | int] | None) = None,
) -> Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]:
    """Return an async LangGraph node that writes the DataFrame out."""
    fn = loader_fn or _default_loader

    async def loader_node(state: dict[str, Any]) -> dict[str, Any]:
        existing = dict(state.get("metadata") or {}).get("data_etl") or {}
        df = existing.get("dataframe")
        destination = existing.get("destination")
        if df is None or not destination:
            logger.info(
                "data_etl_load_skip",
                reason="no_dataframe" if df is None else "no_destination",
            )
            return {}

        try:
            raw = fn(df, destination)
            n = await _maybe_await(raw)
        except Exception as exc:
            logger.warning("data_etl_load_error", error=str(exc))
            raise

        logger.info("data_etl_loaded", row_count=n, destination=destination.get("path"))
        updated = {**existing, "loaded_row_count": int(n)}
        return {
            "metadata": {
                **(state.get("metadata") or {}),
                "data_etl": updated,
            }
        }

    return loader_node


async def _maybe_await(result: Any) -> Any:
    import inspect

    if inspect.iscoroutine(result):
        return await result
    return result


__all__ = ["make_loader_node"]

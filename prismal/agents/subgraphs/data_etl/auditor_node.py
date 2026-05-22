"""Auditor node for data_etl subgraph.

Terminal node. Produces a compact audit summary of the run and appends an
``AIMessage`` with a human-readable digest.

Summary shape::

    {
        "passed": bool,
        "source": dict,
        "destination": dict | None,
        "raw_row_count": int,
        "loaded_row_count": int | None,
        "transform_log": list[str],
        "errors": list[str],
    }
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from langchain_core.messages import AIMessage

from prismal.core.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

logger = get_logger("prismal.subgraphs.data_etl.auditor")


def make_auditor_node() -> Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]:
    """Return an async LangGraph node that summarises the ETL run."""

    async def auditor_node(state: dict[str, Any]) -> dict[str, Any]:
        existing = dict(state.get("metadata") or {}).get("data_etl") or {}
        validation = existing.get("validation") or {}
        passed = bool(validation.get("passed", False))

        errors_list: list[str] = list(validation.get("errors") or [])
        transform_log: list[str] = list(existing.get("transform_log") or [])
        audit: dict[str, Any] = {
            "passed": passed,
            "source": existing.get("source"),
            "destination": existing.get("destination"),
            "raw_row_count": existing.get("raw_row_count"),
            "loaded_row_count": existing.get("loaded_row_count"),
            "transform_log": transform_log,
            "errors": errors_list,
        }

        logger.info(
            "data_etl_audit",
            passed=passed,
            raw_row_count=audit["raw_row_count"],
            loaded_row_count=audit["loaded_row_count"],
            n_errors=len(errors_list),
        )

        if passed:
            summary = (
                f"ETL completed — loaded {audit['loaded_row_count']} rows "
                f"from {audit['raw_row_count']} source rows "
                f"(transforms: {len(transform_log)})."
            )
        else:
            err_preview = "; ".join(errors_list[:3]) or "unknown failure"
            summary = f"ETL failed validation: {err_preview}"

        updated = {**existing, "audit": audit}
        return {
            "messages": [AIMessage(content=summary)],
            "metadata": {
                **(state.get("metadata") or {}),
                "data_etl": updated,
            },
        }

    return auditor_node


__all__ = ["make_auditor_node"]

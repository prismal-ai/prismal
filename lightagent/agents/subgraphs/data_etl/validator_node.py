"""Validator node for data_etl subgraph.

Runs schema + shape checks on the DataFrame produced by ``extractor_node``.
Records ``{"passed": bool, "errors": list[str]}`` under
``state["metadata"]["data_etl"]["validation"]``.

Default checks:
- DataFrame is non-empty (``height > 0``).
- All ``required_columns`` are present.

A custom ``validator_fn(df, source_spec) -> (passed, errors)`` can be
injected to swap in stricter schema validation (e.g. Pandera, Pydantic).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from lightagent.core.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    import polars as pl

logger = get_logger("lightagent.subgraphs.data_etl.validator")


def make_validator_node(
    required_columns: list[str] | None = None,
    validator_fn: (Callable[[pl.DataFrame, dict[str, Any]], tuple[bool, list[str]]] | None) = None,
) -> Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]:
    """Return an async LangGraph node that validates the extracted DataFrame."""

    def _default_validator(df: pl.DataFrame, _source: dict[str, Any]) -> tuple[bool, list[str]]:
        errors: list[str] = []
        if df.height == 0:
            errors.append("dataset is empty")
        if required_columns:
            missing = [c for c in required_columns if c not in df.columns]
            if missing:
                errors.append(f"missing required columns: {missing}")
        return (not errors, errors)

    fn = validator_fn or _default_validator

    async def validator_node(state: dict[str, Any]) -> dict[str, Any]:
        existing = dict(state.get("metadata") or {}).get("data_etl") or {}
        df = existing.get("dataframe")
        if df is None:
            logger.info("data_etl_validate_skip", reason="no_dataframe")
            return {}
        source = existing.get("source") or {}

        try:
            passed, errors = fn(df, source)
        except Exception as exc:
            logger.warning("data_etl_validate_error", error=str(exc))
            passed, errors = False, [f"validator crashed: {exc}"]

        logger.info(
            "data_etl_validated",
            passed=passed,
            n_errors=len(errors),
        )
        updated = {
            **existing,
            "validation": {"passed": passed, "errors": errors},
        }
        return {
            "metadata": {
                **(state.get("metadata") or {}),
                "data_etl": updated,
            }
        }

    return validator_node


def make_etl_branching_gate() -> Callable[[dict[str, Any]], str]:
    """Conditional edge: route to transformer on pass, auditor on fail."""

    def gate(state: dict[str, Any]) -> str:
        validation = (state.get("metadata") or {}).get("data_etl", {}).get("validation")
        if validation is None:
            return "auditor"
        return "transformer" if validation.get("passed") else "auditor"

    return gate


__all__ = ["make_etl_branching_gate", "make_validator_node"]

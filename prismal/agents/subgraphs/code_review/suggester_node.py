"""Suggester node for code_review subgraph.

Takes the accumulated issues and produces one remediation suggestion per
issue. Stores the resulting list at
``state["metadata"]["code_review"]["suggestions"]``.

Default implementation is a no-op (empty list). Production usage injects a
``suggester_fn(code, issues) -> list[str]`` that prompts an LLM or a
rule-based fixer. When there are no issues the node short-circuits.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from prismal.core.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from prismal.agents.subgraphs.code_review.types import CodeIssue

logger = get_logger("lightagent.subgraphs.code_review.suggester")


async def _default_suggester(_code: str, _issues: list[CodeIssue]) -> list[str]:
    return []


def make_suggester_node(
    suggester_fn: (Callable[[str, list[CodeIssue]], Awaitable[list[str]]] | None) = None,
) -> Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]:
    """Return an async LangGraph node that derives remediation suggestions."""
    fn = suggester_fn or _default_suggester

    async def suggester_node(state: dict[str, Any]) -> dict[str, Any]:
        existing = dict(state.get("metadata") or {}).get("code_review") or {}
        code = existing.get("code") or ""
        issues: list[CodeIssue] = list(existing.get("issues") or [])

        if not issues:
            # No issues → no suggestions; still record an empty list so the
            # report_generator downstream can trust the field is present.
            updated = {**existing, "suggestions": []}
            return {
                "metadata": {
                    **(state.get("metadata") or {}),
                    "code_review": updated,
                }
            }

        try:
            suggestions = list(await _maybe_await(fn(code, issues)))
        except Exception as exc:
            logger.warning("code_review_suggester_error", error=str(exc))
            suggestions = []

        logger.info(
            "code_review_suggester_done",
            n_issues=len(issues),
            n_suggestions=len(suggestions),
        )
        updated = {**existing, "suggestions": suggestions}
        return {
            "metadata": {
                **(state.get("metadata") or {}),
                "code_review": updated,
            }
        }

    return suggester_node


async def _maybe_await(result: Any) -> Any:
    import inspect

    if inspect.iscoroutine(result):
        return await result
    return result


__all__ = ["make_suggester_node"]

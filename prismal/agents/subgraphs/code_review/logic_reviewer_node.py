"""Logic-reviewer node for code_review subgraph.

Asks an LLM (or a custom ``reviewer_fn``) to scrutinise business-logic
correctness — off-by-one errors, dead code paths, incorrect error
handling, etc. Same ``(code, file) -> list[CodeIssue]`` contract as the
other analyzers.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from prismal.core.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from prismal.agents.subgraphs.code_review.types import CodeIssue

logger = get_logger("prismal.subgraphs.code_review.logic_reviewer")


async def _default_reviewer(_code: str, _file: str) -> list[CodeIssue]:
    return []


def make_logic_reviewer_node(
    reviewer_fn: Callable[[str, str], Awaitable[list[CodeIssue]]] | None = None,
) -> Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]:
    """Return an async LangGraph node that runs the logic-review pass."""
    fn = reviewer_fn or _default_reviewer

    async def logic_reviewer_node(state: dict[str, Any]) -> dict[str, Any]:
        existing = dict(state.get("metadata") or {}).get("code_review") or {}
        code = existing.get("code")
        file = existing.get("file") or "unknown.py"
        if not code:
            return {}

        try:
            new_issues = await _maybe_await(fn(code, file))
        except Exception as exc:
            logger.warning("code_review_logic_error", error=str(exc))
            new_issues = []

        merged = list(existing.get("issues") or []) + list(new_issues)
        logger.info("code_review_logic_done", n_issues=len(new_issues))
        return {
            "metadata": {
                **(state.get("metadata") or {}),
                "code_review": {**existing, "issues": merged},
            }
        }

    return logic_reviewer_node


async def _maybe_await(result: Any) -> Any:
    import inspect

    if inspect.iscoroutine(result):
        return await result
    return result


__all__ = ["make_logic_reviewer_node"]

"""Linter node for code_review subgraph.

Runs a static-analysis pass and appends any style / convention issues it
finds to ``state["metadata"]["code_review"]["issues"]``.

The default implementation is a no-op (returns an empty list). Production
wiring is expected to inject a ``linter_fn`` that calls ruff / mypy inside a
sandbox (e.g. :class:`SandboxExecutor` from the CodeAct flow).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from prismal.core.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from prismal.agents.subgraphs.code_review.types import CodeIssue

logger = get_logger("lightagent.subgraphs.code_review.linter")


async def _default_linter(_code: str, _file: str) -> list[CodeIssue]:
    return []


def make_linter_node(
    linter_fn: Callable[[str, str], Awaitable[list[CodeIssue]]] | None = None,
) -> Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]:
    """Return an async LangGraph node that runs the linter step."""
    fn = linter_fn or _default_linter

    async def linter_node(state: dict[str, Any]) -> dict[str, Any]:
        existing = dict(state.get("metadata") or {}).get("code_review") or {}
        code = existing.get("code")
        file = existing.get("file") or "unknown.py"
        if not code:
            return {}

        try:
            new_issues = await _maybe_await(fn(code, file))
        except Exception as exc:
            logger.warning("code_review_linter_error", error=str(exc))
            new_issues = []

        merged = list(existing.get("issues") or []) + list(new_issues)
        logger.info("code_review_linter_done", n_issues=len(new_issues))
        return {
            "metadata": {
                **(state.get("metadata") or {}),
                "code_review": {**existing, "issues": merged},
            }
        }

    return linter_node


async def _maybe_await(result: Any) -> Any:
    import inspect

    if inspect.iscoroutine(result):
        return await result
    return result


__all__ = ["make_linter_node"]

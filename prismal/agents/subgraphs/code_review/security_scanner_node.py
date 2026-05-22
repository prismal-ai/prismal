"""Security-scanner node for code_review subgraph.

Scans the code for security-sensitive patterns (dangerous calls, hardcoded
secrets, unvalidated input). Appends any findings to
``state["metadata"]["code_review"]["issues"]``.

Default implementation is a no-op; callers should inject a ``scanner_fn``
that wraps bandit or an LLM-based analyzer. The same contract as the
linter: ``(code, file) -> list[CodeIssue]``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from prismal.core.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from prismal.agents.subgraphs.code_review.types import CodeIssue

logger = get_logger("prismal.subgraphs.code_review.security_scanner")


async def _default_scanner(_code: str, _file: str) -> list[CodeIssue]:
    return []


def make_security_scanner_node(
    scanner_fn: Callable[[str, str], Awaitable[list[CodeIssue]]] | None = None,
) -> Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]:
    """Return an async LangGraph node that runs the security pass."""
    fn = scanner_fn or _default_scanner

    async def security_scanner_node(state: dict[str, Any]) -> dict[str, Any]:
        existing = dict(state.get("metadata") or {}).get("code_review") or {}
        code = existing.get("code")
        file = existing.get("file") or "unknown.py"
        if not code:
            return {}

        try:
            new_issues = await _maybe_await(fn(code, file))
        except Exception as exc:
            logger.warning("code_review_scanner_error", error=str(exc))
            new_issues = []

        merged = list(existing.get("issues") or []) + list(new_issues)
        logger.info("code_review_scanner_done", n_issues=len(new_issues))
        return {
            "metadata": {
                **(state.get("metadata") or {}),
                "code_review": {**existing, "issues": merged},
            }
        }

    return security_scanner_node


async def _maybe_await(result: Any) -> Any:
    import inspect

    if inspect.iscoroutine(result):
        return await result
    return result


__all__ = ["make_security_scanner_node"]

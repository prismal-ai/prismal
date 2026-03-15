"""Approval gate functions for conditional edges in subgraphs.

Gates are plain Python callables that accept an ``AgentState`` dict and return
the name of the next node.  They are registered as conditional edges in the
``SubgraphFactory``.

All gates use dot-notation paths into ``state["metadata"]`` to read artifact
scores so that subgraph agents never need direct access to parent state.

Example::

    gate = score_gate(
        field="dev_pipeline.review_result.score",
        threshold=0.8,
        on_pass="__end__",
        on_fail="developer",
        max_iterations=3,
    )
    # Register as conditional edge:
    builder.add_conditional_edges("reviewer", gate)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable


def _get_nested(data: dict[str, Any], dotted_path: str) -> Any:  # noqa: ANN401
    """Traverse a nested dict using a dot-separated path.

    Args:
        data: Root dictionary.
        dotted_path: Dot-separated key path, e.g.
            ``"dev_pipeline.review_result.score"``.

    Returns:
        The value at the path, or ``None`` if any key is missing.
    """
    parts = dotted_path.split(".")
    current: Any = data
    for part in parts:
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def score_gate(
    field: str,
    threshold: float,
    on_pass: str,
    on_fail: str,
    max_iterations: int = 3,
) -> Callable[[dict[str, Any]], str]:
    """Create a conditional edge based on a numeric score in metadata.

    Reads ``state["metadata"][field]`` (dot-notation) and compares to
    ``threshold``.  When ``state["iteration_count"] >= max_iterations`` the
    gate always returns ``on_pass`` to prevent infinite loops.

    Args:
        field: Dot-notation path into ``state["metadata"]``, e.g.
            ``"dev_pipeline.review_result.score"``.
        threshold: Minimum score (inclusive) to route to ``on_pass``.
        on_pass: Node name to route to when score >= threshold.
        on_fail: Node name to route to when score < threshold.
        max_iterations: Force ``on_pass`` after this many iterations (default 3).

    Returns:
        A callable ``(state) -> str`` suitable as a LangGraph conditional edge.
    """

    def gate(state: dict[str, Any]) -> str:
        """Evaluate the score gate.

        Args:
            state: Current ``AgentState`` dict.

        Returns:
            Either ``on_pass`` or ``on_fail``.
        """
        iteration = state.get("iteration_count", 0)
        if iteration >= max_iterations:
            return on_pass

        meta = state.get("metadata", {})
        value = _get_nested(meta, field)
        if value is None:
            return on_fail

        try:
            score = float(value)
        except (TypeError, ValueError):
            return on_fail

        return on_pass if score >= threshold else on_fail

    return gate


def failure_gate(
    field: str,
    on_pass: str,
    on_fail: str,
    max_iterations: int = 3,
) -> Callable[[dict[str, Any]], str]:
    """Create a conditional edge that routes based on non-empty failure lists.

    Routes to ``on_fail`` when the list at ``field`` is non-empty (tests
    failed), otherwise to ``on_pass``.  Respects ``max_iterations`` guard.

    Args:
        field: Dot-notation path to a ``list[str]`` in ``state["metadata"]``.
        on_pass: Node name when the list is empty (all tests pass).
        on_fail: Node name when the list is non-empty (some tests fail).
        max_iterations: Force ``on_pass`` after this many iterations.

    Returns:
        A callable ``(state) -> str``.
    """

    def gate(state: dict[str, Any]) -> str:
        """Evaluate the test-failure gate.

        Args:
            state: Current ``AgentState`` dict.

        Returns:
            Either ``on_pass`` or ``on_fail``.
        """
        iteration = state.get("iteration_count", 0)
        if iteration >= max_iterations:
            return on_pass

        meta = state.get("metadata", {})
        value = _get_nested(meta, field)
        if not isinstance(value, list):
            return on_pass  # field missing → assume no failures

        return on_fail if value else on_pass

    return gate


__all__ = ["failure_gate", "score_gate"]

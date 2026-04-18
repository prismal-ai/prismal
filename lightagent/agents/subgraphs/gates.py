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

from langgraph.types import Command, interrupt

from lightagent.core.config import get_settings
from lightagent.core.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Callable

logger = get_logger("lightagent.subgraphs.gates")


def _get_nested(data: dict[str, Any], dotted_path: str) -> Any:
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


# ---------------------------------------------------------------------------
# Human-in-the-Loop (Phase 35 / SPEC-037)
# ---------------------------------------------------------------------------

#: Valid HITL decision actions accepted by ``human_approval_node``.
_HITL_ACTIONS: frozenset[str] = frozenset({"approve", "reject", "request_changes"})

#: Metadata key under which ``hitl_gate`` stashes the artifact field name so
#: ``human_approval_node`` knows which artifact to surface in the interrupt
#: payload.  Used because the gate is registered as a conditional edge that
#: cannot pass arguments directly to the upstream approval node.
_HITL_ARTIFACT_FIELD_KEY = "_hitl_artifact_field"

#: Metadata key for the risk level (HIGH | MEDIUM) included in the payload.
_HITL_RISK_LEVEL_KEY = "_hitl_risk_level"

#: Metadata key under which the post-resume routing decision is recorded so
#: a downstream conditional edge (or the gate's wrapper) can map the human
#: action to ``on_approve`` / ``on_reject`` / generator nodes.
_HITL_LAST_ACTION_KEY = "_hitl_last_action"


async def human_approval_node(state: dict[str, Any]) -> Command[Any]:
    """Pause workflow execution and wait for a human decision.

    Calls LangGraph's :func:`~langgraph.types.interrupt` with a payload
    describing the artifact awaiting approval, the risk level, and the
    valid response options.  LangGraph persists the full state in the
    checkpointer at this point and the workflow remains suspended until
    a caller resumes execution with
    ``Command(resume={"action": ..., "modifications": ...})``.

    On resume, the function inspects ``decision["action"]`` and returns a
    :class:`~langgraph.types.Command` with the merged metadata so that the
    downstream :func:`hitl_gate` routing function can read
    ``state["metadata"]["_hitl_last_action"]`` and dispatch accordingly:

    * ``approve`` → routes to ``on_approve``.
    * ``reject`` → routes to ``on_reject``.
    * ``request_changes`` → merges ``decision["modifications"]`` into
      ``state["metadata"]`` and routes back to the generator (the
      ``on_reject`` target by convention).

    Args:
        state: Current agent state.  Must include ``metadata`` with the
            ``_hitl_artifact_field`` and ``_hitl_risk_level`` keys
            populated by :func:`hitl_gate`.

    Returns:
        A :class:`~langgraph.types.Command` updating ``metadata`` with the
        recorded action and any human-supplied modifications.
    """
    metadata: dict[str, Any] = dict(state.get("metadata", {}))
    artifact_field = str(metadata.get(_HITL_ARTIFACT_FIELD_KEY, "review_result"))
    risk_level = str(metadata.get(_HITL_RISK_LEVEL_KEY, "HIGH"))

    artifact = _get_nested(metadata, artifact_field)

    payload = {
        "artifact_field": artifact_field,
        "artifact": artifact,
        "risk_level": risk_level,
        "options": sorted(_HITL_ACTIONS),
        "message": f"Approval required for: {artifact_field}",
    }

    logger.info(
        "hitl.interrupt_raised",
        artifact_field=artifact_field,
        risk_level=risk_level,
    )

    # interrupt() blocks here until Command(resume=...) arrives.  When the
    # workflow resumes, the return value is whatever the caller passed as
    # ``resume`` — typically ``{"action": ..., "modifications": ...}``.
    decision: Any = interrupt(payload)

    if not isinstance(decision, dict):
        logger.warning(
            "hitl.invalid_decision_payload",
            received_type=type(decision).__name__,
        )
        decision = {"action": "reject", "modifications": {}}

    raw_action = str(decision.get("action", "reject")).lower()
    action = raw_action if raw_action in _HITL_ACTIONS else "reject"
    modifications: dict[str, Any] = (
        decision.get("modifications", {}) if isinstance(decision.get("modifications"), dict) else {}
    )

    # Record the action so the routing wrapper from ``hitl_gate`` can read it.
    metadata[_HITL_LAST_ACTION_KEY] = action

    if action == "request_changes" and modifications:
        # Shallow-merge the human modifications into metadata so downstream
        # generators can pick them up without re-parsing the interrupt payload.
        metadata.update(modifications)
        logger.info(
            "hitl.modifications_merged",
            keys=sorted(modifications.keys()),
        )

    logger.info("hitl.decision_received", action=action)

    return Command(update={"metadata": metadata})


def hitl_gate(
    artifact_field: str,
    on_approve: str,
    on_reject: str = "__end__",
    risk_level: str = "HIGH",  # noqa: ARG001 -- exposed for API symmetry; consumed by seed_hitl_metadata
    bypass_condition: Callable[[dict[str, Any]], bool] | None = None,
) -> Callable[[dict[str, Any]], str]:
    """Create a HITL conditional-edge function for a subgraph.

    The returned routing function is intended to be registered as a
    conditional edge from a :func:`human_approval_node` so that the human
    decision recorded in ``state["metadata"]["_hitl_last_action"]`` is
    translated into the appropriate next node:

    * ``approve`` → ``on_approve``
    * ``reject`` → ``on_reject``
    * ``request_changes`` → ``on_reject`` (by convention; the generator
      sits at the same node as the rejection target so the workflow loops
      back through the developer / generator)

    The factory also writes ``_hitl_artifact_field`` and ``_hitl_risk_level``
    into ``state["metadata"]`` on first invocation so that
    :func:`human_approval_node` can read them when constructing the
    interrupt payload.

    Bypass:
    * If ``bypass_condition`` is provided and returns ``True`` for the
      current state, the gate routes directly to ``on_approve`` without
      involving the human, logging ``hitl_bypassed_by_callback`` at WARNING.
    * If ``settings.hitl_enabled`` is ``False`` (default
      ``LIGHTAGENT_HITL_ENABLED=true``), the gate routes directly to
      ``on_approve`` and logs ``hitl_bypassed_by_config`` at WARNING.

    Args:
        artifact_field: Dot-notation path into ``state["metadata"]``
            identifying the artifact under review (e.g.
            ``"dev_pipeline.code_artifact"``).
        on_approve: Node name to route to when the human approves.
        on_reject: Node name to route to when the human rejects or
            requests changes.  Defaults to ``"__end__"``.
        risk_level: ``"HIGH"`` or ``"MEDIUM"`` — surfaced in the interrupt
            payload so the UI can display the appropriate context.
        bypass_condition: Optional callable that, when it returns
            ``True`` for the current state, skips HITL entirely.

    Returns:
        A routing callable ``(state) -> str`` suitable for
        ``StateGraph.add_conditional_edges``.
    """

    def gate(state: dict[str, Any]) -> str:
        """Evaluate the HITL gate and decide the next node.

        Args:
            state: Current ``AgentState`` dict.

        Returns:
            The name of the next node (``on_approve`` or ``on_reject``).
        """
        # Stash artifact metadata so ``human_approval_node`` (which runs
        # *before* this routing function) can read it on the next iteration.
        # Because LangGraph routing edges are evaluated *after* the source
        # node, the gate also runs in two contexts:
        #   1. As a routing edge from ``human_approval_node`` — at which
        #      point ``_hitl_last_action`` is populated and we route on it.
        #   2. As a routing edge from a generator — in which case we just
        #      seed the metadata and the workflow walks into
        #      ``human_approval_node``.  This second use is handled by the
        #      caller registering the gate appropriately; the function
        #      itself remains a pure router.
        metadata = state.get("metadata", {})

        # CI/CD bypass via env / settings.
        if not get_settings().hitl_enabled:
            logger.warning(
                "hitl_bypassed_by_config",
                artifact_field=artifact_field,
                on_approve=on_approve,
            )
            return on_approve

        # Caller-provided bypass (e.g. test mode, low-risk skip).
        if bypass_condition is not None and bypass_condition(state):
            logger.warning(
                "hitl_bypassed_by_callback",
                artifact_field=artifact_field,
                on_approve=on_approve,
            )
            return on_approve

        # Read the action recorded by ``human_approval_node``.
        action = metadata.get(_HITL_LAST_ACTION_KEY)
        if action == "approve":
            return on_approve
        if action in {"reject", "request_changes"}:
            return on_reject

        # No action yet — this should not happen when the gate is wired
        # correctly (a human_approval_node always runs first), but to keep
        # the workflow safe we default to the reject path.
        logger.warning(
            "hitl_gate_no_action_defaulting_to_reject",
            artifact_field=artifact_field,
        )
        return on_reject

    gate.__name__ = f"hitl_gate_{artifact_field.replace('.', '_')}"
    return gate


def seed_hitl_metadata(
    artifact_field: str,
    risk_level: str = "HIGH",
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Build a node function that seeds HITL routing metadata.

    Returns an async-compatible passthrough that writes the artifact field
    and risk level into ``state["metadata"]`` so the downstream
    :func:`human_approval_node` knows what to surface.  Wire it as the
    immediate predecessor of ``human_approval_node`` in a subgraph::

        nodes = {
            "approval_seed": seed_hitl_metadata(
                "dev_pipeline.code_artifact",
                "HIGH",
            ),
            "human_approval": human_approval_node,
        }
        edges = [("approval_seed", "human_approval")]

    Args:
        artifact_field: Dot-notation path stored under
            ``state["metadata"][_HITL_ARTIFACT_FIELD_KEY]``.
        risk_level: ``"HIGH"`` or ``"MEDIUM"``.

    Returns:
        A node-shaped function that returns a partial state update.
    """

    def _seed(state: dict[str, Any]) -> dict[str, Any]:
        metadata = dict(state.get("metadata", {}))
        metadata[_HITL_ARTIFACT_FIELD_KEY] = artifact_field
        metadata[_HITL_RISK_LEVEL_KEY] = risk_level
        # Clear any stale action so the next gate evaluation does not
        # short-circuit on a previous turn's decision.
        metadata.pop(_HITL_LAST_ACTION_KEY, None)
        return {"metadata": metadata}

    _seed.__name__ = f"seed_hitl_{artifact_field.replace('.', '_')}"
    return _seed


__all__ = [
    "failure_gate",
    "hitl_gate",
    "human_approval_node",
    "score_gate",
    "seed_hitl_metadata",
]

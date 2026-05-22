"""Swarm / decentralised handoff pattern.

Implements SPEC-PAT-007. Provides :func:`swarm_handoff`, a pure function
that transfers control from one agent to another without routing through a
central supervisor. The handoff is recorded in
``state["metadata"]["handoff_history"]`` as a list of dict snapshots so
downstream auditing and inspection can reconstruct the agent trajectory.

Key guarantees:

- **Immutability**: the input state is not mutated. A new state dict is
  returned (shallow copy with fresh ``metadata`` sub-dict).
- **Self-handoff rejected** with :class:`ValueError`: an agent handing off
  to itself is almost always a bug.
- **Target allow-listing**: only agents in ``valid_targets`` (defaults to
  :data:`VALID_HANDOFF_TARGETS`) can receive control — prevents typos and
  keeps the swarm's reachability graph tractable.
- **Structured audit**: a ``swarm_handoff_recorded`` structlog event is
  emitted for every successful handoff.

Example::

    from prismal.agents.patterns.swarm import swarm_handoff

    new_state = await swarm_handoff(
        current_agent="coder",
        target_agent="critic",
        state=state,
        reason="code needs review before shipping",
    )
    assert new_state["next_agent"] == "critic"
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from prismal.core.exceptions import SwarmError
from prismal.core.logging import get_logger
from prismal.monitoring.otel import OTelManager

logger = get_logger("lightagent.agents.patterns.swarm")


@dataclass
class HandoffRecord:
    """One entry in the handoff history.

    Attributes:
        from_agent: Agent ceding control.
        to_agent: Agent receiving control.
        reason: Human-readable motivation.
        timestamp: ISO-8601 UTC timestamp.
        context_snapshot: Small dict of state fields captured at handoff
            time (for audit — never stores large blobs like full messages).
    """

    from_agent: str
    to_agent: str
    reason: str
    timestamp: str
    context_snapshot: dict[str, Any]


VALID_HANDOFF_TARGETS: frozenset[str] = frozenset(
    {
        "coder",
        "researcher",
        "data_analyst",
        "planner",
        "file_manager",
        "rag_agent",
        "critic",
    }
)


# Keys copied into ``HandoffRecord.context_snapshot`` for audit. Small by
# design — we never snapshot ``messages`` or ``retrieved_docs`` here.
_SNAPSHOT_KEYS: tuple[str, ...] = (
    "session_id",
    "iteration_count",
    "current_agent",
    "task_plan",
    "risk_score",
)


async def swarm_handoff(
    current_agent: str,
    target_agent: str,
    state: dict[str, Any],
    reason: str,
    valid_targets: frozenset[str] | None = None,
) -> dict[str, Any]:
    """Transfer control from *current_agent* to *target_agent*.

    Args:
        current_agent: Agent relinquishing control.
        target_agent: Agent receiving control.
        state: Current agent state; returned unchanged (copy-on-write).
        reason: Audit reason for the handoff.
        valid_targets: Allow-list of target ids. ``None`` uses
            :data:`VALID_HANDOFF_TARGETS`.

    Returns:
        New state dict with ``next_agent`` set, a fresh ``metadata`` dict,
        and the handoff appended to ``metadata["handoff_history"]``.

    Raises:
        ValueError: For self-handoff or a target outside ``valid_targets``.
    """
    if current_agent == target_agent:
        raise ValueError(f"self-handoff not allowed ({current_agent!r} → {target_agent!r})")
    targets = valid_targets if valid_targets is not None else VALID_HANDOFF_TARGETS
    if target_agent not in targets:
        raise ValueError(
            f"{target_agent!r} is not a valid handoff target (allowed: {sorted(targets)})"
        )

    otel = OTelManager()
    with otel.start_span("swarm.handoff") as span:
        span.set_attribute("lightagent.swarm.from", current_agent)
        span.set_attribute("lightagent.swarm.to", target_agent)

        snapshot = {k: state[k] for k in _SNAPSHOT_KEYS if k in state}
        # deepcopy mutable values we lifted out of `state` so later mutations
        # on the state don't leak into the recorded snapshot.
        snapshot = deepcopy(snapshot)

        record = HandoffRecord(
            from_agent=current_agent,
            to_agent=target_agent,
            reason=reason,
            timestamp=datetime.now(UTC).isoformat(),
            context_snapshot=snapshot,
        )
        entry: dict[str, Any] = {
            "from_agent": record.from_agent,
            "to_agent": record.to_agent,
            "reason": record.reason,
            "timestamp": record.timestamp,
            "context_snapshot": record.context_snapshot,
        }

        new_metadata: dict[str, Any] = dict(state.get("metadata") or {})
        history = list(new_metadata.get("handoff_history") or [])
        history.append(entry)
        new_metadata["handoff_history"] = history

        new_state: dict[str, Any] = {**state, "metadata": new_metadata}
        new_state["next_agent"] = target_agent

        logger.info(
            "swarm_handoff_recorded",
            from_agent=current_agent,
            to_agent=target_agent,
            reason=reason,
        )
        return new_state


__all__ = [
    "VALID_HANDOFF_TARGETS",
    "HandoffRecord",
    "SwarmError",
    "swarm_handoff",
]

"""Value objects for the Skynet swarm supervisor (SPEC-SKY-TYP-001).

Frozen dataclasses shared by the supervisor, workers, reducer, and subgraph
nodes.  They are plain data — no LLM, provider, or graph dependency — so every
Skynet component round-trips them in unit tests without a backend.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SwarmOrder:
    """A single sub-order to be executed by one worker.

    Attributes:
        order_id: Stable id, e.g. ``"ord-3"``.
        instruction: What this worker must do (user-derived — must reach a
            model only via ``SecurePromptBuilder``).
        role: Optional specialist role (Phase S+: capability key).
        context: Small, audited context snapshot for the worker.
        attempt: Incremented on re-dispatch in later rounds.
    """

    order_id: str
    instruction: str
    role: str = "worker"
    context: dict[str, Any] = field(default_factory=dict)
    attempt: int = 1


@dataclass(frozen=True)
class SwarmPlan:
    """The supervisor's decomposition of an order into sub-orders.

    Attributes:
        goal: The original order being decomposed.
        orders: The sub-orders the swarm will execute this round (≤ the
            effective cap).
        round: 1-indexed control-loop round.
        rationale: Why the work was split this way.
        deferred: Overflow orders beyond the cap, carried to the next round
            rather than dropped (RF-SKY-03).  Additive extension of
            SPEC-SKY-TYP-001 required by SPEC-SKY-SUP-001's "returned via the
            plan's deferred set".
    """

    goal: str
    orders: list[SwarmOrder]
    round: int = 1
    rationale: str = ""
    deferred: list[SwarmOrder] = field(default_factory=list)

    @property
    def size(self) -> int:
        """The swarm size the supervisor chose (RF-SKY-02)."""
        return len(self.orders)


@dataclass(frozen=True)
class WorkerResult:
    """The outcome of one worker executing one :class:`SwarmOrder`.

    Attributes:
        order_id: Id of the order this result answers.
        output: The worker's output (empty on failure).
        success: Whether the order was completed.
        error: Captured failure description (never raised out of the node).
        tool_calls: Number of gated tool calls the worker made.
    """

    order_id: str
    output: str
    success: bool
    error: str | None = None
    tool_calls: int = 0


@dataclass(frozen=True)
class SwarmResult:
    """The reduced outcome of one or more rounds.

    Attributes:
        goal: The original order.
        answer: Synthesized final result.
        worker_results: Every worker result across all rounds.
        rounds_completed: How many plan→dispatch→evaluate rounds ran.
        deferred_orders: Capped overflow carried to the next round (RF-SKY-03).
        complete: Whether the evaluator judged the goal met.
    """

    goal: str
    answer: str
    worker_results: list[WorkerResult]
    rounds_completed: int
    deferred_orders: list[SwarmOrder]
    complete: bool

"""Routing ports (Phase AMR — SPEC-AMR-TYP-002).

Policies are sync, pure and must not raise on the hot path (fail-open to the
static choice); predictors are swappable behind :class:`SuccessPredictorPort`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from prismal.budget.types import BudgetStatus
    from prismal.routing.types import ModelChoice, RoutingOutcome, TaskFeatures


@runtime_checkable
class RoutingPolicyPort(Protocol):
    """Chooses a model for a call and reacts to the call's outcome."""

    name: str

    def select(self, features: TaskFeatures, *, budget: BudgetStatus | None = None) -> ModelChoice:
        """The model to use for *features*."""
        ...

    def on_outcome(self, choice: ModelChoice, outcome: RoutingOutcome) -> ModelChoice | None:
        """Return an escalated ModelChoice to retry with, or None to accept. Must not raise."""
        ...


@runtime_checkable
class SuccessPredictorPort(Protocol):
    """Predicts P(success) and output length of a model on a task."""

    version: str

    def p_success(self, features: TaskFeatures, model_ref: str) -> float:
        """Probability in [0, 1]."""
        ...

    def expected_output_tokens(self, features: TaskFeatures, model_ref: str) -> int:
        """Expected completion tokens."""
        ...

    def update(self, features: TaskFeatures, model_ref: str, success: bool) -> None:
        """Online update with one observed outcome."""
        ...

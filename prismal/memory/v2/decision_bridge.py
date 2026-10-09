"""Bridge the composed ``DecisionCascade`` to the memory controllers' ``DecisionPort``.

The memory write gate, recall controller and consolidator call
``DecisionPort.decide(fields, questions, policy=)``; the runtime's
``DecisionCascade`` needs a :class:`~prismal.decisions.state.DecisionState` and an
``integration`` name. :class:`CascadeDecisionPort` rebuilds the state
conservatively — every field is re-added as *untrusted* through
``DecisionStateBuilder`` (re-fenced, re-scanned, registry-checked) so the
cascade's fail-closed rules (tainted ⇒ escalate, CRITICAL never auto-accepted)
still see the injection flags — and absorbs every error into abstentions.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from prismal.core.logging import get_logger
from prismal.decisions.ports import abstain_all
from prismal.decisions.state import DecisionStateBuilder
from prismal.decisions.types import DecisionMode

if TYPE_CHECKING:
    from collections.abc import Mapping

    from prismal.decisions.cascade import DecisionCascade
    from prismal.decisions.types import Decision, GatePolicy, QuestionSpec

logger = get_logger(__name__)

MEMORY_INTEGRATION = "memory"


class CascadeDecisionPort:
    """``DecisionPort`` view of a ``DecisionCascade`` for the ``memory`` integration."""

    name = "cascade"
    model = ""

    def __init__(
        self,
        cascade: DecisionCascade,
        *,
        org_id: str | None = None,
        mode: DecisionMode = DecisionMode.ENFORCE,
    ) -> None:
        """``mode`` is reported to the cascade (metrics/audit); pass ``SHADOW`` when
        ``memory_mode="shadow"`` so shadow decisions are not recorded as enforced."""
        self._cascade = cascade
        self._org_id = org_id
        self._mode = mode

    @property
    def mode(self) -> DecisionMode:
        """The mode reported to the cascade for every memory decision."""
        return self._mode

    def supports(self, question: QuestionSpec, *, language: str) -> bool:
        """The cascade decides per tier; unsupported questions abstain there."""
        del question, language
        return True

    async def decide(
        self,
        state: Mapping[str, str],
        questions: Mapping[str, QuestionSpec],
        *,
        policy: GatePolicy,
    ) -> Mapping[str, Decision]:
        """Resolve via the cascade; never raises (errors ⇒ abstentions)."""
        try:
            builder = DecisionStateBuilder()
            for key, value in state.items():
                builder.add_untrusted(key, value, source=MEMORY_INTEGRATION)
            return await self._cascade.decide(
                builder.build(),
                questions,
                policy=policy,
                integration=MEMORY_INTEGRATION,
                mode=self._mode,
                context={"org_id": self._org_id},
            )
        except Exception as exc:
            logger.warning("memory_decision_bridge_failed", error_type=type(exc).__name__)
            return abstain_all(
                questions, backend=self.name, model=self.model, reason="backend_error"
            )

    async def aclose(self) -> None:
        """The cascade is owned (and closed) by the runtime's ``DecisionRuntime``."""
        return


__all__ = ["MEMORY_INTEGRATION", "CascadeDecisionPort"]

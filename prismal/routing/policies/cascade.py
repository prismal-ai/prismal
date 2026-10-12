"""``CascadePolicy`` + ``AdequacyChecker`` (Phase AMR — SPEC-AMR-CAS-001, DD-AMR-004).

Start at the cheapest allowed tier, escalate one tier at a time on a bad outcome
(structured-output failure, inadequate answer, repeated tool errors), at most
``routing_cascade_max_escalations`` times. ``FRONTIER`` and ``AUTO`` are terminal.

Adequacy is a typed decision (DEC Noul). Request and answer are untrusted, so
they enter the decision state only through ``DecisionStateBuilder`` (the
decision-model counterpart of ``SecurePromptBuilder``: sanitized, fenced,
authority-claim/injection-flagged); a tainted state abstains without calling
the backend. Abstention escalates only for high/critical risk.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from typing import TYPE_CHECKING, Any, Literal

from prismal.core.config import get_settings
from prismal.core.logging import get_logger
from prismal.decisions.state import DecisionStateBuilder
from prismal.decisions.types import DecisionKind, GatePolicy, QuestionSpec, RiskTier
from prismal.monitoring.otel import OTelManager
from prismal.routing.policies._base import floor_for, static_of
from prismal.routing.policies.static import StaticTierPolicy
from prismal.routing.types import (
    TIER_ORDER,
    ModelChoice,
    ModelTier,
    max_tier,
    tier_index,
)

if TYPE_CHECKING:
    from prismal.budget.types import BudgetStatus
    from prismal.core.config import Settings
    from prismal.decisions.ports import DecisionPort
    from prismal.routing.ports import RoutingPolicyPort
    from prismal.routing.types import RoutingOutcome, TaskFeatures

logger = get_logger(__name__)

EscalationCause = Literal["structured_invalid", "inadequate", "tool_errors"]

ADEQUACY_QUESTION = QuestionSpec(
    id="routing.adequacy",
    kind=DecisionKind.NOUL,
    instructions="Is the answer in the state an adequate response to the request in the state?",
    criteria=(
        "Adequate means the answer addresses the request completely and correctly, "
        "without refusing or deflecting."
    ),
    risk_tier=RiskTier.MEDIUM,
)

_HIGH_RISK = frozenset({RiskTier.HIGH, RiskTier.CRITICAL})
# Fallback only (record_outcome passes features on the outcome): keep it small so
# _MAX_RUNS per-session policies cannot hold millions of entries.
_MAX_REMEMBERED = 64


class AdequacyChecker:
    """DEC Noul "is this answer adequate?" → P(true), or None (absent/abstain/timeout/taint)."""

    def __init__(self, decision_port: DecisionPort | None, *, tau: float, timeout_ms: int) -> None:
        self._port = decision_port
        self._cascade: Any = None
        self.tau = tau
        self._timeout_s = timeout_ms / 1000.0
        self._policy = GatePolicy(timeout_ms=timeout_ms)

    @classmethod
    def from_cascade(cls, cascade: Any, *, tau: float, timeout_ms: int) -> AdequacyChecker:
        """Checker over a DEC ``DecisionCascade`` (non-LLM tiers only — a cheap check)."""
        checker = cls(None, tau=tau, timeout_ms=timeout_ms)
        checker._cascade = cascade
        return checker

    async def check(
        self,
        request_summary: str,
        answer: str,
        *,
        risk_tier: RiskTier,  # noqa: ARG002 — abstention is risk-weighted by the cascade
    ) -> float | None:
        """P(answer adequate) from the DEC Noul question, or ``None`` to abstain.

        Abstains when no port/cascade is configured, the state is tainted or over limit,
        on timeout or any error, or when the decision abstains. Never raises.
        """
        if self._port is None and self._cascade is None:
            return None
        state = (
            DecisionStateBuilder()
            .add_untrusted("request", request_summary, source="user")
            .add_untrusted("answer", answer, source="model")
            .build()
        )
        if state.tainted or state.over_limit:
            return None  # DEC forbidden use: never decide over tainted input
        questions = {ADEQUACY_QUESTION.id: ADEQUACY_QUESTION}
        try:
            if self._cascade is not None:
                call = self._cascade.decide(
                    state, questions, policy=self._policy, integration="routing", allow_llm=False
                )
            else:
                assert self._port is not None
                call = self._port.decide(state.fields, questions, policy=self._policy)
            answers = await asyncio.wait_for(call, timeout=self._timeout_s)
        except Exception as exc:  # TimeoutError included — abstain, never raise
            logger.debug("routing.adequacy_abstained", error=type(exc).__name__)
            return None
        decision = answers.get(ADEQUACY_QUESTION.id)
        if decision is None or decision.abstained:
            return None
        p_true = decision.probabilities.get("true")
        return float(p_true) if p_true is not None else None


class CascadePolicy:
    """Cheapest-first with one-way, capped escalation."""

    name = "cascade"

    def __init__(
        self,
        inner: RoutingPolicyPort,
        *,
        settings: Settings | None = None,
        adequacy: AdequacyChecker | None = None,
    ) -> None:
        self.inner = inner
        self._settings = settings or get_settings()
        self.adequacy = adequacy
        self._static = static_of(inner) or StaticTierPolicy(settings=self._settings)
        self._floor = self._static.floor
        self._caps = {
            ModelTier(t): frozenset(caps)
            for t, caps in self._settings.routing_tier_capabilities.items()
        }
        # choice -> features, so on_outcome knows the risk/capabilities (bounded).
        self._features: OrderedDict[ModelChoice, TaskFeatures] = OrderedDict()

    def model_for(self, tier: ModelTier) -> str:
        """Model ref of *tier* from the static policy."""
        return self._static.model_for(tier)

    def _capable(self, tier: ModelTier, features: TaskFeatures) -> bool:
        caps = self._caps.get(tier)
        if caps is None:
            return True
        if features.needs_tools and "tools" not in caps:
            return False
        return not (features.needs_structured_output and "structured_output" not in caps)

    def _first_capable_from(self, tier: ModelTier, features: TaskFeatures) -> ModelTier | None:
        for candidate in TIER_ORDER[tier_index(tier) :]:
            if self._capable(candidate, features):
                return candidate
        return None

    def _remember(self, choice: ModelChoice, features: TaskFeatures) -> None:
        self._features[choice] = features
        self._features.move_to_end(choice)
        while len(self._features) > _MAX_REMEMBERED:
            self._features.popitem(last=False)

    def select(self, features: TaskFeatures, *, budget: BudgetStatus | None = None) -> ModelChoice:
        """Start at ``routing_cascade_start_tier`` (never above the inner choice).

        Tainted turns stay at or above the static tier, the risk floor is honoured and
        the first tier with the required capabilities is chosen. ``AUTO`` passes through.
        """
        base = self.inner.select(features, budget=budget)
        if base.tier is ModelTier.AUTO:
            return base
        start = ModelTier(self._settings.routing_cascade_start_tier)
        tier = start if tier_index(start) <= tier_index(base.tier) else base.tier
        if features.tainted:
            tier = max_tier(tier, base.tier)  # SEC-001: tainted never below static
        minimum = floor_for(features, self._floor)
        if minimum is not None:
            tier = max_tier(tier, minimum)
        capable = self._first_capable_from(tier, features) or tier
        choice = ModelChoice(
            model_ref=self.model_for(capable),
            tier=capable,
            reason="cascade_start",
            escalation_of=base.escalation_of,
            policy=self.name,
        )
        self._remember(choice, features)
        return choice

    def _cause(
        self, outcome: RoutingOutcome, features: TaskFeatures | None
    ) -> EscalationCause | None:
        if outcome.structured_valid is False:
            return "structured_invalid"
        if outcome.adequacy is not None and outcome.adequacy < self._settings.routing_adequacy_tau:
            return "inadequate"
        if outcome.tool_errors >= self._settings.routing_cascade_tool_error_threshold:
            return "tool_errors"
        if (
            outcome.adequacy is None
            and outcome.adequacy_checked
            and features is not None
            and features.risk_tier in _HIGH_RISK
        ):
            return "inadequate"  # abstention never silently accepts a high-risk cheap answer
        return None

    def on_outcome(self, choice: ModelChoice, outcome: RoutingOutcome) -> ModelChoice | None:
        """Escalate one capable tier on a structured/adequacy/tool-error failure, else ``None``.

        Capped by ``routing_cascade_max_escalations``; never raises.
        """
        try:
            return self._on_outcome(choice, outcome)
        except Exception as exc:
            logger.warning("routing.cascade_on_outcome_failed", error=type(exc).__name__)
            return None

    def _on_outcome(self, choice: ModelChoice, outcome: RoutingOutcome) -> ModelChoice | None:
        features = outcome.features or self._features.get(choice)
        cause = self._cause(outcome, features)
        if cause is None:
            return None
        otel = OTelManager()
        terminal = choice.tier in (ModelTier.FRONTIER, ModelTier.AUTO)
        if terminal or choice.escalation_count >= self._settings.routing_cascade_max_escalations:
            otel.increment_counter("routing_escalations", attributes={"reason": "cap"})
            return None
        nxt = TIER_ORDER[tier_index(choice.tier) + 1]
        target = (
            self._first_capable_from(nxt, features) if features else nxt
        ) or ModelTier.FRONTIER
        escalated = ModelChoice(
            model_ref=self.model_for(target),
            tier=target,
            reason=f"escalation:{cause}",
            escalation_of=choice,
            policy=self.name,
        )
        if features is not None:
            self._remember(escalated, features)
        otel.increment_counter("routing_escalations", attributes={"reason": cause})
        return escalated

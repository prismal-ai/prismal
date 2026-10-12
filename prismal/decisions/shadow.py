"""Shadow mode, promotion criteria and drift demotion (Phase DEC — SPEC-DEC-SHD-001/002).

In ``shadow`` the decision runs alongside today's path: the graph always gets
the pre-DEC result, the decision task is bounded and its failure never
surfaces. Agreement is recorded per integration / language / backend.
Promotion to ``enforce`` is an operator config change (never automatic); a drop
of rolling audited agreement below ``decision_demote_floor`` demotes an
integration back to ``shadow`` in-process (``decision_auto_demote``).
"""

from __future__ import annotations

import asyncio
import random
import time
from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from prismal.core.logging import get_logger
from prismal.decisions._telemetry import gauge, inc
from prismal.decisions.calibration import language_enforceable
from prismal.decisions.catalog import configured_mode
from prismal.decisions.types import DecisionMode, Outcome

if TYPE_CHECKING:
    from collections.abc import Mapping

    from prismal.core.config import Settings
    from prismal.decisions.calibration import CalibrationRecord
    from prismal.decisions.cascade import DecisionCascade
    from prismal.decisions.state import DecisionState
    from prismal.decisions.types import Decision, GatePolicy, QuestionSpec

logger = get_logger(__name__)

_AUDIT_WINDOW = 500
_OBSERVATION_WINDOW = 10_000  # per integration; reports cover the most recent window

# Integrations demoted to shadow in this process (SHD-002). Cleared on restart:
# re-promotion stays an operator decision.
_DEMOTED: set[str] = set()


def is_demoted(integration: str) -> bool:
    """True when *integration* was demoted to shadow in this process."""
    return integration in _DEMOTED


def reset_demotions() -> None:
    """Forget in-process demotions (tests / operator re-promotion)."""
    _DEMOTED.clear()


def effective_mode(settings: Settings, integration: str) -> DecisionMode:
    """Configured mode, downgraded ``enforce`` → ``shadow`` after a drift alarm."""
    mode = DecisionMode(configured_mode(settings, integration))
    if mode is DecisionMode.ENFORCE and is_demoted(integration):
        return DecisionMode.SHADOW
    return mode


@dataclass(frozen=True)
class AgreementReport:
    """Shadow agreement for one integration (or all)."""

    integration: str
    n: int
    n_accepted: int
    agreement_all: float
    agreement_accepted: float
    coverage: float
    by_language: Mapping[str, float]
    by_backend: Mapping[str, float]
    days: float = 0.0


@dataclass(frozen=True)
class PromotionCriteria:
    """Defaults per ARCHITECTURE §3.5."""

    min_n: int = 2000
    min_days: int = 14
    min_agreement_accepted: float = 0.97
    max_ece: float = 0.05
    min_es_examples: int = 300
    max_es_precision_gap: float = 0.02


def promotion_ready(
    report: AgreementReport,
    criteria: PromotionCriteria,
    *,
    calibration: CalibrationRecord | None = None,
) -> tuple[bool, list[str]]:
    """``(ready, unmet reasons)`` — advisory only; promotion is an operator change."""
    reasons: list[str] = []
    if report.n < criteria.min_n and report.days < criteria.min_days:
        reasons.append(
            f"volume: {report.n} decisions over {report.days:.1f} days "
            f"(need {criteria.min_n} or {criteria.min_days} days)"
        )
    if report.agreement_accepted < criteria.min_agreement_accepted:
        reasons.append(
            f"agreement on accepted subset {report.agreement_accepted:.3f} "
            f"< {criteria.min_agreement_accepted}"
        )
    if calibration is None:
        reasons.append("calibration: no record")
    else:
        if calibration.ece_after > criteria.max_ece:
            reasons.append(f"ece {calibration.ece_after:.3f} > {criteria.max_ece}")
        if "es" in report.by_language or "es" in calibration.n_by_language:
            if not language_enforceable(calibration, "es", min_examples=criteria.min_es_examples):
                reasons.append("es slice below examples / precision target")
            gap = calibration.precision_by_language.get(
                "en", 0.0
            ) - calibration.precision_by_language.get("es", 0.0)
            if gap > criteria.max_es_precision_gap:
                reasons.append(f"es precision gap {gap:.3f} > {criteria.max_es_precision_gap}")
    return (not reasons, reasons)


@dataclass(frozen=True)
class _Observation:
    agree: bool
    accepted: bool
    language: str
    backend: str


class ShadowRunner:
    """Runs a cascade in shadow and tracks agreement with the reference path."""

    def __init__(
        self,
        cascade: DecisionCascade,
        *,
        sample_rate: float = 1.0,
        audit: Any | None = None,
        settings: Settings | None = None,
        grace_ms: int = 50,
        demote_min_window: int = 100,
        rng: random.Random | None = None,
    ) -> None:
        self._cascade = cascade
        self._sample_rate = sample_rate
        self._audit = audit
        self._settings = settings
        self._grace_ms = grace_ms
        self._demote_min_window = demote_min_window
        self._rng = rng or random.Random()  # noqa: S311 # nosec B311 — sampling
        self._observations: dict[str, deque[_Observation]] = {}
        self._lang_counts: dict[tuple[str, str], list[int]] = {}  # (integration, lang): [agree, n]
        self._started: dict[str, float] = {}
        self._audited: dict[str, deque[bool]] = {}
        self._tasks: set[asyncio.Task[Mapping[str, Decision]]] = set()
        self.errors = 0

    # ── shadow execution ──────────────────────────────────────────────────────

    def launch(
        self,
        state: DecisionState,
        questions: Mapping[str, QuestionSpec],
        *,
        policy: GatePolicy,
        integration: str,
        context: Mapping[str, str | None] | None = None,
    ) -> asyncio.Task[Mapping[str, Decision]] | None:
        """Schedule a shadow decision; ``None`` when sampled out. Never raises."""
        if self._sample_rate <= 0.0 or self._rng.random() >= self._sample_rate:
            return None
        task = asyncio.create_task(self._run(state, questions, policy, integration, context))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def _run(
        self,
        state: DecisionState,
        questions: Mapping[str, QuestionSpec],
        policy: GatePolicy,
        integration: str,
        context: Mapping[str, str | None] | None,
    ) -> Mapping[str, Decision]:
        try:
            return await asyncio.wait_for(
                self._cascade.decide(
                    state,
                    questions,
                    policy=policy,
                    integration=integration,
                    mode=DecisionMode.SHADOW,
                    context=context,
                ),
                timeout=(policy.timeout_ms + self._grace_ms) / 1000.0,
            )
        except Exception as exc:
            self.errors += 1
            logger.info("decision_shadow_failed", integration=integration, error=type(exc).__name__)
            return {}

    # ── agreement ─────────────────────────────────────────────────────────────

    def record_reference(
        self,
        integration: str,
        question_id: str,  # noqa: ARG002 — SPEC-DEC-SHD-001 signature (per-question keying is the caller's)
        reference: str | bool,
        decision: Decision,
        *,
        language: str = "en",
    ) -> bool:
        """Record whether ``decision`` matched the pre-DEC ``reference``; return agreement."""
        agree = not decision.abstained and _same(decision.value, reference)
        obs = _Observation(
            agree=agree,
            accepted=decision.outcome is Outcome.ACCEPTED,
            language=language,
            backend=decision.backend,
        )
        self._observations.setdefault(integration, deque(maxlen=_OBSERVATION_WINDOW)).append(obs)
        self._started.setdefault(integration, time.time())
        counts = self._lang_counts.setdefault((integration, language), [0, 0])
        counts[0] += int(agree)
        counts[1] += 1
        gauge(
            "decision_shadow_agreement_ratio",
            counts[0] / counts[1],
            {"integration": integration, "language": language},
        )
        return agree

    def report(self, integration: str | None = None) -> AgreementReport:
        """Agreement report for *integration* (or all integrations when ``None``)."""
        if integration is None:
            obs = [o for items in self._observations.values() for o in items]
            started = min(self._started.values(), default=time.time())
        else:
            obs = list(self._observations.get(integration, ()))
            started = self._started.get(integration, time.time())
        accepted = [o for o in obs if o.accepted]
        return AgreementReport(
            integration=integration or "*",
            n=len(obs),
            n_accepted=len(accepted),
            agreement_all=_ratio(obs),
            agreement_accepted=_ratio(accepted),
            coverage=len(accepted) / len(obs) if obs else 0.0,
            by_language=_grouped(obs, "language"),
            by_backend=_grouped(obs, "backend"),
            days=(time.time() - started) / 86_400.0,
        )

    # ── drift demotion (SHD-002) ──────────────────────────────────────────────

    def record_audit(self, integration: str, *, agree: bool) -> None:
        """Feed one audited (LLM re-judged / human-labelled) outcome; may demote."""
        window = self._audited.setdefault(integration, deque(maxlen=_AUDIT_WINDOW))
        window.append(agree)
        settings = self._settings
        if settings is None or not settings.decision_auto_demote:
            return
        if len(window) < self._demote_min_window or is_demoted(integration):
            return
        if self.rolling_agreement(integration) < settings.decision_demote_floor:
            _DEMOTED.add(integration)
            logger.warning("decision_drift_demoted", integration=integration)
            inc("decision_drift_alarms", {"integration": integration})

    def rolling_agreement(self, integration: str) -> float:
        """Agreement ratio of the audited window (1.0 when empty)."""
        window = self._audited.get(integration)
        return sum(window) / len(window) if window else 1.0


def _same(value: str | bool | None, reference: str | bool) -> bool:
    if isinstance(value, bool) or isinstance(reference, bool):
        return str(value).lower() == str(reference).lower()
    return value == reference


def _ratio(obs: list[_Observation]) -> float:
    return sum(o.agree for o in obs) / len(obs) if obs else 0.0


def _grouped(obs: list[_Observation], attr: str) -> dict[str, float]:
    groups: dict[str, list[_Observation]] = {}
    for o in obs:
        groups.setdefault(getattr(o, attr), []).append(o)
    return {k: _ratio(v) for k, v in groups.items()}

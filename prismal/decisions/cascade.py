"""Decision cascade — rule → Laya → Jev → LLM (Phase DEC — SPEC-DEC-CAS-001/003).

Tiers are tried in order. A deterministic ``rule`` hook may accept outright; a
model tier is calibrated, then gated (τ per risk tier, margin, taint,
option-order shuffle for high-stakes Choice). Any abstention, timeout, error or
gate failure escalates to the next tier (DD-DEC-002); an abstention is never
turned into an accept. The terminal ``llm`` tier keeps pre-DEC semantics (not
τ-gated); without it, unresolved questions come back ``ESCALATED`` so the caller
runs its pre-DEC path. Non-LLM time is bounded by ``policy.timeout_ms`` in total.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from prismal.core.logging import get_logger
from prismal.decisions._telemetry import hist, inc
from prismal.decisions.audit import audit_decision, build_audit_record
from prismal.decisions.calibration import (
    CalibrationStore,
    apply_calibration,
    language_enforceable,
)
from prismal.decisions.gate import evaluate_gate, shuffle_options, shuffle_required
from prismal.decisions.types import Decision, DecisionMode, Outcome, RiskTier
from prismal.decisions.validation import validate_question

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from prismal.agents.extension.ports import AuditPort, CredentialVaultPort
    from prismal.core.config import Settings
    from prismal.decisions.ports import DecisionPort
    from prismal.decisions.state import DecisionState
    from prismal.decisions.types import GatePolicy, QuestionSpec, Reason
    from prismal.security.taint import TaintRegistry

    RuleHook = Callable[[Mapping[str, str], QuestionSpec], Decision | None]

logger = get_logger(__name__)

LLM_TIER = "llm"
RULE_TIER = "rule"
_DEFAULT_LLM_TIMEOUT_MS = 30_000


@dataclass
class _Pending:
    question: QuestionSpec
    tried: list[str]
    last: Decision | None = None
    tau: float | None = None
    shuffle_order: tuple[str, ...] | None = None


class DecisionCascade:
    """Tiered, gated, audited typed-decision resolver."""

    def __init__(
        self,
        backends: Sequence[DecisionPort],
        *,
        rule_hook: RuleHook | None = None,
        calibration: CalibrationStore | None = None,
        audit: AuditPort | Any | None = None,
        taint: TaintRegistry | None = None,
        settings: Settings | None = None,
        llm_timeout_ms: int = _DEFAULT_LLM_TIMEOUT_MS,
        rule_enabled: bool | None = None,
    ) -> None:
        self._backends = list(backends)
        self._rule_hook = rule_hook
        # The rule tier runs a per-call hook (integration-specific) or the default one.
        self._rule_enabled = rule_hook is not None if rule_enabled is None else rule_enabled
        self._calibration = calibration
        self._audit = audit
        self._taint = taint
        self._settings = settings
        self._llm_timeout_ms = llm_timeout_ms
        self._validated: set[tuple[str, str, str]] = set()
        self._stale_warned: set[tuple[str, str]] = set()

    @property
    def tier_names(self) -> tuple[str, ...]:
        """Tier names in cascade order (``rule`` first when enabled)."""
        names = [b.name for b in self._backends]
        return ((RULE_TIER,) if self._rule_enabled else ()) + tuple(names)

    @property
    def backends(self) -> tuple[DecisionPort, ...]:
        """The model-tier backends in cascade order."""
        return tuple(self._backends)

    @property
    def audit(self) -> Any:
        """The audit sink records are written to (may be ``None``)."""
        return self._audit

    # ── public API ────────────────────────────────────────────────────────────

    async def decide(
        self,
        state: DecisionState,
        questions: Mapping[str, QuestionSpec],
        *,
        policy: GatePolicy,
        integration: str,
        mode: DecisionMode = DecisionMode.ENFORCE,
        corroborate: Callable[[Decision], bool] | None = None,
        context: Mapping[str, str | None] | None = None,
        rule_hook: RuleHook | None = None,
        allow_llm: bool = True,
        observe: Callable[[Decision], None] | None = None,
    ) -> dict[str, Decision]:
        """Resolve every question; never raises for backend failures.

        ``rule_hook`` overrides the default hook for this call (only when the
        cascade has a rule tier). ``allow_llm=False`` skips the terminal LLM tier
        (hot paths such as the safety pre-screen). ``observe`` receives every
        non-abstained raw answer of every tier, in order, before gating — even
        when a later tier decides (one-way gates such as HITL need them); an
        observer error is logged and ignored.
        """
        self._validate(questions, integration)
        pending = {qid: _Pending(q, []) for qid, q in questions.items()}
        track = dict(pending)  # same objects; survives resolution (audit τ / shuffle order)
        resolved: dict[str, Decision] = {}

        if not self._language_allowed(state.language):
            self._finish_all(pending, resolved, reason="unsupported")
        else:
            self._skip_over_limit(state, pending)
            self._rule_tier(state, pending, resolved, rule_hook or self._rule_hook, observe)
            await self._model_tiers(
                state, pending, resolved, policy, corroborate, allow_llm, observe
            )
            self._finish_all(pending, resolved, reason=None)

        tenant = (context or {}).get("org_id") or None
        self._emit(state, resolved, integration=integration, mode=mode, tenant=tenant)
        self._audit_all(state, track, resolved, integration, mode, policy, context)
        return resolved

    async def aclose(self) -> None:
        """Close every backend (errors are logged, not raised)."""
        for backend in self._backends:
            try:
                await backend.aclose()
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("decision_backend_close_failed", error_type=type(exc).__name__)

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        audit: AuditPort | Any | None = None,
        vault: CredentialVaultPort | None = None,
        rule_hook: RuleHook | None = None,
        org_id: str | None = None,
    ) -> DecisionCascade:
        """Build the tiers in ``decision_backend_order`` from settings (adapters deferred)."""
        from prismal.decisions.adapters.factory import build_backends

        backends: list[DecisionPort] = []
        for name in settings.decision_backend_order:
            if name == RULE_TIER:
                continue
            # "ollama" expands into ollama:fast, ollama:accurate (SPEC-DEC-OLL-001).
            backends.extend(build_backends(name, settings, vault=vault, org_id=org_id))
        has_rule = RULE_TIER in settings.decision_backend_order
        return cls(
            backends,
            rule_hook=rule_hook if has_rule else None,
            rule_enabled=has_rule,
            calibration=CalibrationStore(settings.decision_calibration_dir),
            audit=audit,
            settings=settings,
        )

    # ── tiers ─────────────────────────────────────────────────────────────────

    def _validate(self, questions: Mapping[str, QuestionSpec], integration: str) -> None:
        for q in questions.values():
            key = (integration, q.id, q.schema_hash())
            if key not in self._validated:
                validate_question(q, integration=integration)
                self._validated.add(key)

    def _language_allowed(self, language: str) -> bool:
        if self._settings is None:
            return language in {"en", "es"}
        return language in self._settings.decision_languages

    @staticmethod
    def _skip_over_limit(state: DecisionState, pending: dict[str, _Pending]) -> None:
        if not state.over_limit:
            return
        for qid in list(pending):
            p = pending[qid]
            if p.question.risk_tier.at_least(RiskTier.MEDIUM):
                p.last = Decision.abstain(p.question, backend="none", model="", reason="over_limit")

    def _rule_tier(
        self,
        state: DecisionState,
        pending: dict[str, _Pending],
        resolved: dict[str, Decision],
        hook: RuleHook | None,
        observe: Callable[[Decision], None] | None = None,
    ) -> None:
        if not self._rule_enabled or hook is None:
            return
        for qid in list(pending):
            p = pending[qid]
            if p.question.risk_tier is RiskTier.CRITICAL:
                p.tried.append(RULE_TIER)
                continue
            try:
                d = hook(state.fields, p.question)
            except Exception as exc:
                logger.warning("decision_rule_hook_failed", error_type=type(exc).__name__)
                d = None
            if d is not None and not d.abstained and d.value is not None:
                resolved[qid] = replace(
                    d, backend=RULE_TIER, outcome=Outcome.ACCEPTED, escalated_from=()
                )
                _observe(observe, resolved[qid])
                del pending[qid]
            else:
                p.tried.append(RULE_TIER)

    async def _model_tiers(
        self,
        state: DecisionState,
        pending: dict[str, _Pending],
        resolved: dict[str, Decision],
        policy: GatePolicy,
        corroborate: Callable[[Decision], bool] | None,
        allow_llm: bool = True,
        observe: Callable[[Decision], None] | None = None,
    ) -> None:
        deadline = time.perf_counter() + policy.timeout_ms / 1000.0
        for backend in self._backends:
            if not pending:
                return
            if backend.name == LLM_TIER:
                if allow_llm:
                    await self._llm_tier(
                        backend, state, pending, resolved, policy, corroborate, observe
                    )
                continue
            eligible = {
                qid: p.question
                for qid, p in pending.items()
                if not _blocked(p) and _supports(backend, p.question, state.language)
            }
            for qid, p in pending.items():
                if qid not in eligible and not _blocked(p):
                    p.tried.append(backend.name)
                    p.last = p.last or Decision.abstain(
                        p.question, backend=backend.name, model=backend.model, reason="unsupported"
                    )
            if not eligible:
                continue
            own_ms = _own_budget_ms(backend)
            tier_start = time.perf_counter()
            remaining_ms = own_ms or int((deadline - tier_start) * 1000)
            if remaining_ms <= 0:
                for qid in eligible:
                    p = pending[qid]
                    p.tried.append(backend.name)
                    p.last = Decision.abstain(
                        p.question, backend=backend.name, model=backend.model, reason="timeout"
                    )
                continue
            tier_policy = replace(policy, timeout_ms=remaining_ms)
            tier_deadline = tier_start + remaining_ms / 1000.0 if own_ms else deadline
            answers = await backend.decide(state.fields, eligible, policy=tier_policy)
            for qid, q in eligible.items():
                p = pending[qid]
                raw = answers.get(qid) or Decision.abstain(
                    q, backend=backend.name, model=backend.model, reason="backend_error"
                )
                _observe(observe, raw)
                accepted, d = await self._gate(
                    backend, raw, p, state, tier_policy, corroborate, tier_deadline
                )
                if accepted:
                    resolved[qid] = replace(
                        d, outcome=Outcome.ACCEPTED, escalated_from=tuple(p.tried), reason=""
                    )
                    del pending[qid]
                else:
                    p.tried.append(backend.name)
                    p.last = d
            if own_ms:
                # An own-budget tier (local Ollama) never consumes the shared budget.
                deadline += time.perf_counter() - tier_start

    async def _gate(
        self,
        backend: DecisionPort,
        raw: Decision,
        p: _Pending,
        state: DecisionState,
        policy: GatePolicy,
        corroborate: Callable[[Decision], bool] | None,
        deadline: float,
    ) -> tuple[bool, Decision]:
        q = p.question
        record = None if raw.abstained else self._record(q.id, backend.model)
        if record is None and not raw.abstained:
            self._note_stale(q.id, backend)
        try:
            d = apply_calibration(raw, record, q) if record is not None else raw
        except Exception:
            return False, Decision.abstain(
                q, backend=backend.name, model=backend.model, reason="backend_error"
            )
        calibrated = record is not None and self._slice_ok(record, state.language)
        q_policy = policy.with_tau_floor(q.risk_tier, record.tau) if record else policy
        p.tau = q_policy.tau(q.risk_tier)
        corroborated = state.tainted and not d.abstained and _corroborated(corroborate, d)
        ok, reason = evaluate_gate(
            d,
            q,
            q_policy,
            calibrated=calibrated,
            tainted=state.tainted,
            corroborated=corroborated,
            shuffle_consistent=True,
        )
        if ok and shuffle_required(q, q_policy):
            consistent = await self._shuffle_check(backend, d, q, state, policy, deadline, p)
            ok, reason = evaluate_gate(
                d,
                q,
                q_policy,
                calibrated=calibrated,
                tainted=state.tainted,
                corroborated=corroborated,
                shuffle_consistent=consistent,
            )
            if not consistent:
                inc("decision_shuffle_inconsistent", None)
        if ok:
            return True, d
        return False, replace(d, outcome=Outcome.ESCALATED, reason=reason)

    async def _shuffle_check(
        self,
        backend: DecisionPort,
        d: Decision,
        q: QuestionSpec,
        state: DecisionState,
        policy: GatePolicy,
        deadline: float,
        p: _Pending,
    ) -> bool:
        seed = int(state.sha256[:16], 16)
        permuted = shuffle_options(q, seed=seed)
        p.shuffle_order = permuted.labels()
        remaining_ms = int((deadline - time.perf_counter()) * 1000)
        if remaining_ms <= 0:
            return False
        answers = await backend.decide(
            state.fields, {q.id: permuted}, policy=replace(policy, timeout_ms=remaining_ms)
        )
        second = answers.get(q.id)
        return second is not None and not second.abstained and second.value == d.value

    async def _llm_tier(
        self,
        backend: DecisionPort,
        state: DecisionState,
        pending: dict[str, _Pending],
        resolved: dict[str, Decision],
        policy: GatePolicy,
        corroborate: Callable[[Decision], bool] | None,
        observe: Callable[[Decision], None] | None = None,
    ) -> None:
        """Terminal tier: pre-DEC semantics, but never over over-limit or uncorroborated
        tainted ≥ MEDIUM states (SEC-003 applies to every tier)."""
        eligible = {qid: p.question for qid, p in pending.items() if not _blocked(p)}
        if not eligible:
            return
        answers = await backend.decide(
            state.fields, eligible, policy=replace(policy, timeout_ms=self._llm_timeout_ms)
        )
        for qid in list(eligible):
            p = pending[qid]
            d = answers.get(qid)
            if d is not None:
                _observe(observe, d)
            if (
                d is not None
                and not d.abstained
                and state.tainted
                and p.question.risk_tier.at_least(RiskTier.MEDIUM)
                and not _corroborated(corroborate, d)
            ):
                p.tried.append(backend.name)
                p.last = replace(d, outcome=Outcome.ESCALATED, reason="tainted")
                continue
            if d is not None and not d.abstained and d.value is not None:
                resolved[qid] = replace(
                    d, outcome=Outcome.ACCEPTED, escalated_from=tuple(p.tried), reason=""
                )
                del pending[qid]
            else:
                p.tried.append(backend.name)
                p.last = d or p.last

    # ── helpers ───────────────────────────────────────────────────────────────

    def _record(self, decision_id: str, model: str) -> Any:
        if self._calibration is None:
            return None
        try:
            return self._calibration.get(decision_id, model)
        except Exception:
            return None

    def _note_stale(self, decision_id: str, backend: DecisionPort) -> None:
        """OLL-003: report an Ollama digest change that left ``decision_id`` uncalibrated.

        Reporting only — the decision stays uncalibrated, so the gate never accepts
        it (shadow-only behaviour: it escalates to the next tier). Counted on every
        occurrence; warned once per ``(decision_id, model)`` per cascade.
        """
        if self._calibration is None:
            return
        try:
            stale = self._calibration.stale(decision_id, backend.model)
        except Exception:
            return
        if not stale:
            return
        inc("decision_calibration_stale", {"decision_id": decision_id, "backend": backend.name})
        key = (decision_id, backend.model)
        if key not in self._stale_warned:
            self._stale_warned.add(key)
            logger.warning(
                "decision_calibration_stale",
                decision_id=decision_id,
                backend=backend.name,
                model=backend.model,
            )

    def _slice_ok(self, record: Any, language: str) -> bool:
        min_examples = (
            self._settings.decision_min_calibration_examples if self._settings is not None else 300
        )
        return language_enforceable(record, language, min_examples=min_examples)

    @staticmethod
    def _finish_all(
        pending: dict[str, _Pending], resolved: dict[str, Decision], *, reason: Reason | None
    ) -> None:
        for qid, p in list(pending.items()):
            base = p.last
            if reason is not None or base is None:
                base = Decision.abstain(
                    p.question, backend="none", model="", reason=reason or "unsupported"
                )
            resolved[qid] = replace(base, outcome=Outcome.ESCALATED, escalated_from=tuple(p.tried))
        pending.clear()

    @staticmethod
    def _emit(
        state: DecisionState,
        resolved: Mapping[str, Decision],
        *,
        integration: str,
        mode: DecisionMode,
        tenant: str | None = None,
    ) -> None:
        # HRS: tag the host-report counters with the tenant only when it is known.
        scope = {"tenant": tenant} if tenant else {}
        if state.injection_flags:
            inc("decision_injection_flags", {"integration": integration})
        for d in resolved.values():
            inc(
                "decisions",
                {
                    "integration": integration,
                    "backend": d.backend,
                    "outcome": d.outcome.value,
                    **scope,
                },
            )
            hist("decision_latency_ms", d.latency_ms, {"backend": d.backend})
            if (
                mode is DecisionMode.ENFORCE
                and d.outcome is Outcome.ACCEPTED
                and d.backend != LLM_TIER
            ):
                inc("llm_calls_avoided", {"integration": integration, **scope})

    def _audit_all(
        self,
        state: DecisionState,
        track: Mapping[str, _Pending],
        resolved: Mapping[str, Decision],
        integration: str,
        mode: DecisionMode,
        policy: GatePolicy,
        context: Mapping[str, str | None] | None,
    ) -> None:
        if self._audit is None:
            return
        for qid, d in resolved.items():
            p = track[qid]
            q = p.question
            record = build_audit_record(
                decision=d,
                question=q,
                state=state,
                integration=integration,
                mode=mode,
                tau=p.tau if p.tau is not None else policy.tau(q.risk_tier),
                shuffle_order=p.shuffle_order,
                context=context,
            )
            audit_decision(self._audit, record)


def _observe(observe: Callable[[Decision], None] | None, d: Decision) -> None:
    """Hand a raw, non-abstained answer to the observer (fail-open)."""
    if observe is None or d.abstained or d.value is None:
        return
    try:
        observe(d)
    except Exception as exc:
        logger.warning("decision_observer_failed", error_type=type(exc).__name__)


def _corroborated(corroborate: Callable[[Decision], bool] | None, d: Decision) -> bool:
    """Deterministic corroborator verdict; a missing or crashing one counts as ``False``."""
    if corroborate is None:
        return False
    try:
        return bool(corroborate(d))
    except Exception:
        return False


def _own_budget_ms(backend: DecisionPort) -> int:
    """A backend's own time budget (``budget_ms``, e.g. Ollama sub-tiers), else ``0``."""
    value = getattr(backend, "budget_ms", None)
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def _blocked(p: _Pending) -> bool:
    return p.last is not None and p.last.reason == "over_limit"


def _supports(backend: DecisionPort, q: QuestionSpec, language: str) -> bool:
    try:
        return bool(backend.supports(q, language=language))
    except Exception:
        return False

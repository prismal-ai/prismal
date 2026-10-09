"""Decision-gated memory writes (Phase MEM — SPEC-MEM-WRT-001, Jev-Mem write side).

Pipeline for one :class:`MemoryCandidate`:

1. **Deterministic pre-filters** — secret redaction (``memory.long_term``) + PII
   sanitization (``PIISanitizer``, as ``memory.long_term_store``), length bounds,
   an injection/authority-claim scan that taints the candidate (T1), and
   exact-hash dedupe against the scope's nearest records (⇒ ``merged``).
2. **Worth + type** — one :meth:`DecisionPort.decide` call with a Noul ("worth
   remembering?") and a Choice over the four :class:`MemoryType` values. A tainted
   candidate is asked only the Noul: its outcome is *skip* or *store-as-episodic*
   (T2/T4). Abstention / below-τ applies ``WriteGatePolicy.low_confidence``.
3. **Relations** — the top ``relation_k`` neighbors from ``backend.search`` in the
   same scope, typed by **one** batched ``decide()`` (a Choice over
   :class:`RelationKind` per neighbor). Non-``UNRELATED`` relations become edges;
   ``UPDATES`` supersedes the neighbor only when :func:`may_supersede` allows it
   (T6, same scope, provenance rank ≥ the neighbor's).

Untrusted text (candidate and neighbor content) reaches a decision model only as a
state value built by :class:`DecisionStateBuilder` (sanitized, fenced, injection
scanned) — the decision-model analogue of ``SecurePromptBuilder``; question texts
are developer constants (DEC SEC-001). ``memory_write_gate="llm"`` and the
``low_confidence="llm"`` policy use an *injected* async classifier (no provider
import here); without one they skip. ``memory_mode="shadow"`` computes and audits
the verdict but stores exactly as gate ``off``. Audit is hash-first (content is
never logged). ``evaluate`` persists what it decides (``put``, supersession,
optional graph) and returns the stored record.
"""

from __future__ import annotations

import hashlib
import re
import time
from contextlib import ExitStack, suppress
from dataclasses import dataclass, field, replace
from functools import lru_cache
from typing import TYPE_CHECKING, Any, Literal

from prismal.core.logging import get_logger
from prismal.decisions.state import DecisionStateBuilder
from prismal.decisions.types import (
    DecisionKind,
    GatePolicy,
    OptionSpec,
    Outcome,
    QuestionSpec,
    RiskTier,
)
from prismal.memory.long_term import _redact_sensitive
from prismal.memory.v2.taint import supersede_block_reason
from prismal.memory.v2.types import (
    MemoryEdge,
    MemoryRecord,
    MemoryType,
    Provenance,
    RelationKind,
    WriteOutcome,
    new_record_id,
)
from prismal.security.pii_sanitizer import PIISanitizer

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping, Sequence

    from prismal.core.config import Settings
    from prismal.decisions.ports import DecisionPort
    from prismal.decisions.state import DecisionState
    from prismal.decisions.types import Decision
    from prismal.memory.v2.ports import MemoryBackendPort, MemoryGraphPort
    from prismal.memory.v2.types import MemoryCandidate, MemoryContext, Trust, WriteStatus

    LlmClassifier = Callable[[MemoryCandidate], Awaitable[MemoryType | None]]

logger = get_logger(__name__)

GateMode = Literal["off", "decision", "llm"]

WORTH_DECISION_ID = "memory.write.worth"
TYPE_DECISION_ID = "memory.write.type"
_RELATION_PREFIX = "memory.write.relation."
_UNGATED_CONFIDENCE = 0.5
_SPACES = re.compile(r"\s+")

WORTH_QUESTION = QuestionSpec(
    id=WORTH_DECISION_ID,
    kind=DecisionKind.NOUL,
    instructions="Is the candidate in the state worth remembering for future interactions?",
    criteria=(
        "Worth remembering means durable, reusable information about the user, the task "
        "domain or how to do something; not small talk, transient status or one-off noise."
    ),
    risk_tier=RiskTier.LOW,
)

TYPE_QUESTION = QuestionSpec(
    id=TYPE_DECISION_ID,
    kind=DecisionKind.CHOICE,
    instructions="Which kind of memory is the candidate in the state?",
    options=(
        OptionSpec(MemoryType.EPISODIC.value, "A specific event at a point in time."),
        OptionSpec(MemoryType.SEMANTIC.value, "A general fact about the user or the world."),
        OptionSpec(MemoryType.PROCEDURAL.value, "How to perform a task: steps, rules, recipes."),
        OptionSpec(MemoryType.PREFERENCE.value, "A stated like, dislike or way of working."),
    ),
    risk_tier=RiskTier.LOW,
)

_RELATION_OPTIONS = (
    OptionSpec(RelationKind.SUPPORTS.value, "The candidate reinforces the neighbor."),
    OptionSpec(RelationKind.CONTRADICTS.value, "The candidate conflicts with the neighbor."),
    OptionSpec(RelationKind.UPDATES.value, "The candidate is a newer version of the neighbor."),
    OptionSpec(RelationKind.UNRELATED.value, "The candidate and the neighbor are unrelated."),
)


@lru_cache(maxsize=64)
def relation_question(index: int) -> QuestionSpec:
    """The relation Choice for ``neighbor_<index>`` (developer-constant text)."""
    return QuestionSpec(
        id=f"{_RELATION_PREFIX}{index}",
        kind=DecisionKind.CHOICE,
        instructions=(
            f"How does the candidate in the state relate to neighbor_{index} in the state?"
        ),
        options=_RELATION_OPTIONS,
        risk_tier=RiskTier.LOW,
    )


def content_hash(content: str) -> str:
    """sha256 of the whitespace-collapsed content (exact-duplicate key; audit id)."""
    canonical = _SPACES.sub(" ", content).strip()
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def sanitize_content(content: str) -> str:
    """Secret redaction (legacy ``LongTermMemory``) then PII placeholders."""
    return PIISanitizer.sanitize(_redact_sensitive(content))


@dataclass(frozen=True)
class WriteGatePolicy:
    """Thresholds of the write gate (``min_chars``/``max_chars`` are the length bounds)."""

    tau_worth: float = 0.6
    tau_type: float = 0.6
    tau_relation: float = 0.7
    relation_k: int = 5
    low_confidence: Literal["skip", "llm"] = "skip"
    min_chars: int = 1
    max_chars: int = 4000

    @classmethod
    def from_settings(cls, settings: Settings) -> WriteGatePolicy:
        """``relation_k`` / ``low_confidence`` from the MEM settings block."""
        return cls(
            relation_k=settings.memory_relation_k,
            low_confidence=settings.memory_write_low_confidence,
        )


@dataclass
class _Verdict:
    """What the gate decided (applied in enforce mode, only logged in shadow)."""

    status: WriteStatus
    type: MemoryType | None
    confidence: float
    backend: str
    reason: str
    relations: list[tuple[MemoryRecord, RelationKind, float]] = field(default_factory=list)


@dataclass
class _Trace:
    """Hash-first audit material collected along the pipeline."""

    state_sha256: str = ""
    schemas: dict[str, str] = field(default_factory=dict)
    superseded: list[str] = field(default_factory=list)
    taint_blocks: list[str] = field(default_factory=list)
    tenancy_violations: int = 0


def _accepted(decision: Decision | None, tau: float) -> bool:
    return (
        decision is not None
        and not decision.abstained
        and decision.value is not None
        and decision.outcome is Outcome.ACCEPTED
        and decision.answer_confidence >= tau
    )


class WriteGate:
    """Decision-gated write pipeline over one ``MemoryBackendPort``."""

    def __init__(
        self,
        *,
        decisions: DecisionPort | None,
        backend: MemoryBackendPort,
        policy: WriteGatePolicy,
        settings: Settings | None = None,
        llm_classifier: LlmClassifier | None = None,
        graph: MemoryGraphPort | None = None,
        audit: Any = None,
        gate_policy: GatePolicy | None = None,
        mode: GateMode | None = None,
        shadow: bool | None = None,
        otel: Any = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if (mode is None or shadow is None) and settings is None:
            from prismal.core.config import get_settings

            settings = get_settings()
        self._decisions = decisions
        self._backend = backend
        self._policy = policy
        self._classifier = llm_classifier
        self._graph = graph
        self._audit = audit
        self._otel = otel
        self._clock = clock
        if mode is None:
            assert settings is not None
            mode = settings.memory_write_gate
        if shadow is None:
            assert settings is not None
            shadow = settings.memory_mode == "shadow"
        self.mode: GateMode = mode
        self.shadow: bool = shadow
        self.gate_policy = gate_policy or (
            GatePolicy.from_settings(settings) if settings is not None else GatePolicy()
        )

    # ── public API ────────────────────────────────────────────────────────────

    async def evaluate(
        self,
        candidate: MemoryCandidate,
        *,
        ctx: MemoryContext,
        tainted: bool,
        taint_sources: tuple[str, ...] = (),
    ) -> tuple[WriteOutcome, MemoryRecord | None]:
        """Gate, persist and report one candidate. Decision/telemetry failures never raise."""
        with ExitStack() as stack:
            with suppress(Exception):
                stack.enter_context(
                    self._otel_manager().start_span(
                        "prismal.memory.write",
                        {"prismal.memory.gate": self.mode, "prismal.memory.shadow": self.shadow},
                    )
                )
            trace = _Trace()
            outcome, record, verdict = await self._run(
                candidate, ctx=ctx, tainted=tainted, taint_sources=taint_sources, trace=trace
            )
            self._emit(outcome, trace)
            self._audit_write(candidate, ctx, outcome, record, verdict, trace)
            return outcome, record

    # ── pipeline ──────────────────────────────────────────────────────────────

    async def _run(
        self,
        candidate: MemoryCandidate,
        *,
        ctx: MemoryContext,
        tainted: bool,
        taint_sources: tuple[str, ...],
        trace: _Trace,
    ) -> tuple[WriteOutcome, MemoryRecord | None, _Verdict]:
        # Length bounds on the raw input first (cheap, before any regex work).
        content = (
            sanitize_content(candidate.content)
            if len(candidate.content) <= self._policy.max_chars
            else ""
        )
        stripped = content.strip()
        bound = (
            "too_long"
            if len(candidate.content) > self._policy.max_chars
            else "empty"
            if not stripped
            else "too_short"
            if len(stripped) < self._policy.min_chars
            else ""
        )
        if bound:
            verdict = _Verdict("skipped", None, 0.0, "rule", bound)
            return self._final(verdict, record=None), None, verdict

        # Deterministic injection / authority-claim scan (T1) — no model call.
        scan = (
            DecisionStateBuilder()
            .add_untrusted("candidate", content, source=f"memory.{candidate.origin}")
            .build()
        )
        if scan.tainted:
            tainted = True
            flags = tuple(
                f if f.startswith("injection:") else f"injection:{f}" for f in scan.injection_flags
            )
            taint_sources = (*taint_sources, *(f for f in flags if f not in taint_sources))
        clean = replace(candidate, content=content)

        neighbors = await self._neighbors(content, ctx=ctx, trace=trace)
        digest = content_hash(content)
        dup = next((r for r in neighbors if content_hash(r.content) == digest), None)
        if dup is not None:
            verdict = _Verdict("merged", dup.type, dup.confidence, "rule", "duplicate")
            return self._final(verdict, record=dup), dup, verdict
        neighbors = neighbors[: self._policy.relation_k]

        off_type = clean.suggested_type or MemoryType.EPISODIC
        if tainted and off_type is not MemoryType.EPISODIC:
            trace.taint_blocks.append("T2")
            off_type = MemoryType.EPISODIC

        if self.mode == "off":
            verdict = _Verdict("stored", off_type, _UNGATED_CONFIDENCE, "none", "gate_off")
        elif self.mode == "llm":
            verdict = await self._llm_verdict(
                clean, tainted=tainted, trace=trace, reason="gate_llm"
            )
        else:
            verdict = await self._decision_verdict(
                clean, scan, neighbors, ctx=ctx, tainted=tainted, trace=trace
            )

        if self.shadow:
            off = _Verdict("stored", off_type, _UNGATED_CONFIDENCE, "none", "gate_off")
            record = self._build(clean, ctx, off, tainted=tainted, taint_sources=taint_sources)
            await self._persist(record, ctx=ctx, supersede=(), trace=trace)
            return self._final(off, record=record, shadow_of=verdict), record, verdict

        if verdict.status != "stored" or verdict.type is None:
            return self._final(verdict, record=None), None, verdict
        record = self._build(clean, ctx, verdict, tainted=tainted, taint_sources=taint_sources)
        await self._persist(record, ctx=ctx, supersede=verdict.relations, trace=trace)
        return self._final(verdict, record=record), record, verdict

    async def _neighbors(
        self, content: str, *, ctx: MemoryContext, trace: _Trace
    ) -> list[MemoryRecord]:
        """Nearest same-tenant records (one extra slot for the exact-duplicate check)."""
        try:
            hits = await self._backend.search(
                content, scope=ctx.scope, types=None, k=self._policy.relation_k + 1
            )
        except Exception as exc:
            logger.warning("memory_write_search_failed", error_type=type(exc).__name__)
            return []
        out: list[MemoryRecord] = []
        for record, _score in hits:
            if record.scope.org_id != ctx.scope.org_id:  # fail-closed tenancy (TEN-001)
                trace.tenancy_violations += 1
                continue
            out.append(record)
        return out

    async def _decide(
        self, state: DecisionState, questions: Mapping[str, QuestionSpec], trace: _Trace
    ) -> Mapping[str, Decision]:
        trace.schemas.update({qid: q.schema_hash() for qid, q in questions.items()})
        if self._decisions is None:
            return {}
        try:
            return await self._decisions.decide(state.fields, questions, policy=self.gate_policy)
        except Exception as exc:  # a non-conforming port must not break the write
            logger.warning("memory_write_decision_failed", error_type=type(exc).__name__)
            return {}

    async def _decision_verdict(
        self,
        candidate: MemoryCandidate,
        state: DecisionState,
        neighbors: Sequence[MemoryRecord],
        *,
        ctx: MemoryContext,
        tainted: bool,
        trace: _Trace,
    ) -> _Verdict:
        trace.state_sha256 = state.sha256
        questions = {WORTH_DECISION_ID: replace(WORTH_QUESTION, risk_tier=ctx.risk_tier)}
        if tainted:  # T4: only skip vs store-as-episodic
            trace.taint_blocks.append("T4")
        else:
            questions[TYPE_DECISION_ID] = replace(TYPE_QUESTION, risk_tier=ctx.risk_tier)
        answers = await self._decide(state, questions, trace)
        worth = answers.get(WORTH_DECISION_ID)
        if not _accepted(worth, self._policy.tau_worth):
            verdict = await self._low_confidence(candidate, tainted=tainted, trace=trace)
        elif worth is not None and worth.value is False:
            return _Verdict("skipped", None, worth.answer_confidence, worth.backend, "not_worth")
        elif tainted:
            assert worth is not None
            verdict = _Verdict(
                "stored", MemoryType.EPISODIC, worth.answer_confidence, worth.backend, "tainted"
            )
        else:
            assert worth is not None
            kind = answers.get(TYPE_DECISION_ID)
            mtype = _as_type(kind.value) if kind is not None else None
            if kind is None or mtype is None or not _accepted(kind, self._policy.tau_type):
                verdict = await self._low_confidence(candidate, tainted=tainted, trace=trace)
            else:
                confidence = min(worth.answer_confidence, kind.answer_confidence)
                verdict = _Verdict("stored", mtype, confidence, worth.backend, "accepted")
        if verdict.status == "stored" and neighbors:
            verdict.relations = await self._relations(candidate, neighbors, ctx=ctx, trace=trace)
        return verdict

    async def _relations(
        self,
        candidate: MemoryCandidate,
        neighbors: Sequence[MemoryRecord],
        *,
        ctx: MemoryContext,
        trace: _Trace,
    ) -> list[tuple[MemoryRecord, RelationKind, float]]:
        builder = DecisionStateBuilder().add_untrusted(
            "candidate", candidate.content, source=f"memory.{candidate.origin}"
        )
        questions: dict[str, QuestionSpec] = {}
        for i, neighbor in enumerate(neighbors):
            builder.add_untrusted(f"neighbor_{i}", neighbor.content, source="memory.neighbor")
            q = relation_question(i)
            questions[q.id] = replace(q, risk_tier=ctx.risk_tier)
        answers = await self._decide(builder.build(), questions, trace)
        out: list[tuple[MemoryRecord, RelationKind, float]] = []
        for i, neighbor in enumerate(neighbors):
            d = answers.get(relation_question(i).id)
            if d is None or not _accepted(d, self._policy.tau_relation):
                continue
            try:
                relation = RelationKind(str(d.value))
            except ValueError:
                continue
            if relation is not RelationKind.UNRELATED:
                out.append((neighbor, relation, d.answer_confidence))
        return out

    async def _low_confidence(
        self, candidate: MemoryCandidate, *, tainted: bool, trace: _Trace
    ) -> _Verdict:
        if self._policy.low_confidence == "llm" and self._classifier is not None:
            return await self._llm_verdict(candidate, tainted=tainted, trace=trace, reason="llm")
        return _Verdict("skipped", None, 0.0, "none", "low_confidence")

    async def _llm_verdict(
        self, candidate: MemoryCandidate, *, tainted: bool, trace: _Trace, reason: str
    ) -> _Verdict:
        if self._classifier is None:
            return _Verdict("skipped", None, 0.0, "none", "no_classifier")
        try:
            mtype = await self._classifier(candidate)
        except Exception as exc:
            logger.warning("memory_write_classifier_failed", error_type=type(exc).__name__)
            return _Verdict("skipped", None, 0.0, "llm", "classifier_error")
        if mtype is None:
            return _Verdict("skipped", None, 0.0, "llm", "not_worth")
        if tainted and mtype is not MemoryType.EPISODIC:
            trace.taint_blocks.append("T2")
            mtype = MemoryType.EPISODIC
        return _Verdict("stored", mtype, _UNGATED_CONFIDENCE, "llm", reason)

    # ── persistence ───────────────────────────────────────────────────────────

    def _build(
        self,
        candidate: MemoryCandidate,
        ctx: MemoryContext,
        verdict: _Verdict,
        *,
        tainted: bool,
        taint_sources: tuple[str, ...],
    ) -> MemoryRecord:
        assert verdict.type is not None
        now = self._clock()
        rid = new_record_id()
        trust: Trust = (
            "untrusted" if tainted else "unknown" if candidate.origin == "legacy" else "trusted"
        )
        edges = tuple(
            MemoryEdge(src_id=rid, dst_id=n.id, relation=rel, confidence=conf, valid_from=now)
            for n, rel, conf in verdict.relations
        )
        return MemoryRecord(
            id=rid,
            type=verdict.type,
            content=candidate.content,
            scope=ctx.scope,
            provenance=Provenance(
                origin=candidate.origin,
                source_message_ids=candidate.source_message_ids,
                session_id=ctx.session_id,
                agent=ctx.actor,
                created_at=now,
                tainted=tainted,
                trust=trust,
                taint_sources=taint_sources if tainted else (),
            ),
            confidence=verdict.confidence,
            edges=edges,
        )

    async def _persist(
        self,
        record: MemoryRecord,
        *,
        ctx: MemoryContext,
        supersede: Sequence[tuple[MemoryRecord, RelationKind, float]],
        trace: _Trace,
    ) -> None:
        await self._backend.put(record)
        for neighbor, relation, _conf in supersede:
            if relation is not RelationKind.UPDATES:
                continue
            reason = supersede_block_reason(record, neighbor)
            if reason is not None:  # the edge is kept; nothing is superseded
                if reason == "T6":
                    trace.taint_blocks.append("T6")
                continue
            await self._backend.update(replace(neighbor, superseded_by=record.id))
            trace.superseded.append(neighbor.id)
        if self._graph is not None:
            try:
                await self._graph.upsert_node(record)
                for edge in record.edges:
                    await self._graph.add_edge(edge, scope=ctx.scope)
            except Exception as exc:  # graph is optional: fail open
                logger.warning("memory_write_graph_failed", error_type=type(exc).__name__)

    def _final(
        self,
        verdict: _Verdict,
        *,
        record: MemoryRecord | None,
        shadow_of: _Verdict | None = None,
    ) -> WriteOutcome:
        if self.shadow:
            gate = shadow_of or verdict
            return WriteOutcome(
                status="shadow",
                record_id=record.id if record is not None else None,
                type=record.type if record is not None else None,
                edges=(),
                backend=gate.backend,
                reason=f"verdict={gate.status}:{gate.type or '-'}:{gate.reason}",
            )
        return WriteOutcome(
            status=verdict.status,
            record_id=record.id if record is not None else None,
            type=verdict.type,
            edges=record.edges if record is not None and verdict.status == "stored" else (),
            backend=verdict.backend,
            reason=verdict.reason,
        )

    # ── telemetry ─────────────────────────────────────────────────────────────

    def _otel_manager(self) -> Any:
        if self._otel is None:
            from prismal.monitoring.otel import OTelManager

            self._otel = OTelManager()
        return self._otel

    def _emit(self, outcome: WriteOutcome, trace: _Trace) -> None:
        with suppress(Exception):
            otel = self._otel_manager()
            otel.increment_counter(
                "memory_writes",
                attributes={
                    "type": outcome.type.value if outcome.type is not None else "none",
                    "outcome": outcome.status,
                },
            )
            for rule in trace.taint_blocks:
                otel.increment_counter("memory_taint_blocks", attributes={"rule": rule})
            if trace.tenancy_violations:
                otel.increment_counter("memory_tenancy_violations", trace.tenancy_violations)

    def _audit_write(
        self,
        candidate: MemoryCandidate,
        ctx: MemoryContext,
        outcome: WriteOutcome,
        record: MemoryRecord | None,
        verdict: _Verdict,
        trace: _Trace,
    ) -> None:
        if self._audit is None:
            return
        payload: dict[str, Any] = {
            "org_id": ctx.scope.org_id,
            "session_id": ctx.session_id,
            "actor": ctx.actor,
            "origin": candidate.origin,
            "record_id": outcome.record_id,
            "status": outcome.status,
            "type": outcome.type.value if outcome.type is not None else None,
            "verdict": verdict.status,
            "verdict_type": verdict.type.value if verdict.type is not None else None,
            "decision_backend": verdict.backend,
            "reason": verdict.reason,
            "gate": self.mode,
            "shadow": self.shadow,
            "tainted": record.provenance.tainted if record is not None else None,
            "taint_sources": list(record.provenance.taint_sources) if record else [],
            "taint_blocks": list(trace.taint_blocks),
            "edges": len(outcome.edges),
            "superseded": list(trace.superseded),
            "content_sha256": content_hash(sanitize_content(candidate.content)),
            "state_sha256": trace.state_sha256,
            "question_schema_sha256": dict(trace.schemas),
            "tenancy_violations": trace.tenancy_violations,
        }
        try:
            self._audit.log_event("memory.write", payload)
        except Exception as exc:
            logger.warning("memory_write_audit_failed", error_type=type(exc).__name__)


def _as_type(value: object) -> MemoryType | None:
    try:
        return MemoryType(str(value))
    except ValueError:
        return None


__all__ = [
    "TYPE_DECISION_ID",
    "TYPE_QUESTION",
    "WORTH_DECISION_ID",
    "WORTH_QUESTION",
    "GateMode",
    "WriteGate",
    "WriteGatePolicy",
    "content_hash",
    "relation_question",
    "sanitize_content",
]

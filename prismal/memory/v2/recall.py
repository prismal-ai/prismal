"""Budgeted, decision-routed recall controller (Phase MEM — SPEC-MEM-RCL-001).

Flow (ARCHITECTURE §3.4):

1. **View routing** — one Choice over ``policy.views`` (``graph`` offered only when a
   :class:`MemoryGraphPort` is configured); the top ``max_views`` options with
   probability ≥ 0.2 are used; abstention / failure / no decision port ⇒
   :data:`DEFAULT_VIEWS`. ``RecallQuery.types`` further restricts the type views.
2. **Hops** ``1..max_hops`` — each typed view searches the backend (``k_per_hop * hop``;
   from hop 2 the query is expanded with the previous hop's admitted content), and
   with the ``graph`` view the neighbors of the previous hop's items are added
   (``as_of`` forwarded). Candidates are ranked by :func:`traversal_score` and admitted
   whole while the cumulative token cost stays ≤ ``budget_tokens``.
3. **Stop** — ``no_candidates`` (nothing new), ``budget_exhausted`` (a candidate did not
   fit), then a sufficiency Noul: ``sufficient`` when P(true) ≥ ``tau_sufficient``,
   ``abstained`` when it abstains (no further hops), ``max_hops`` at the cap.

Security: the query and recalled content reach decisions **only** as fenced,
sanitized state values via ``DecisionStateBuilder.add_untrusted`` — the DEC
equivalent of "through ``SecurePromptBuilder``" (SPEC-DEC-SEC-001); question texts are
developer constants. A tainted sufficiency state (injection flags) cannot stop recall
early. Cross-tenant hits are dropped and counted (fail-closed). Decision and
telemetry failures never raise.
"""

from __future__ import annotations

import math
import time
from contextlib import contextmanager, suppress
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from prismal.core.logging import get_logger
from prismal.decisions.state import DecisionStateBuilder
from prismal.decisions.types import (
    Decision,
    DecisionKind,
    GatePolicy,
    OptionSpec,
    QuestionSpec,
    RiskTier,
)
from prismal.memory.v2.render import item_line, item_tokens, render_items
from prismal.memory.v2.types import MemoryType, RecalledItem, RecallResult

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Mapping, Sequence

    from prismal.core.config import Settings
    from prismal.decisions.ports import DecisionPort
    from prismal.memory.v2.ports import MemoryBackendPort, MemoryGraphPort
    from prismal.memory.v2.types import (
        MemoryContext,
        MemoryEdge,
        MemoryRecord,
        RecallQuery,
        StopReason,
    )

logger = get_logger(__name__)

GRAPH_VIEW = "graph"
DEFAULT_VIEWS: tuple[str, ...] = ("preference", "semantic")
VIEW_MIN_PROBABILITY = 0.2
_DAY_S = 86_400.0
_VIEW_TYPES: Mapping[str, MemoryType] = {t.value: t for t in MemoryType}

VIEW_QUESTION_ID = "memory.recall.views"
SUFFICIENCY_QUESTION_ID = "memory.recall.sufficient"

_VIEW_DESCRIPTIONS: Mapping[str, str] = {
    "preference": "User preferences, likes, dislikes and settings.",
    "semantic": "Stable facts and knowledge about the user, project or world.",
    "episodic": "Past events and conversations: what happened and when.",
    "procedural": "How-to knowledge: procedures, workflows and lessons learned.",
    GRAPH_VIEW: "Records linked to already-recalled memories through relations.",
}

SUFFICIENCY_QUESTION = QuestionSpec(
    id=SUFFICIENCY_QUESTION_ID,
    kind=DecisionKind.NOUL,
    instructions="Is the recalled evidence in the state sufficient to answer the request?",
    criteria="Sufficient means no further memory lookup is needed to answer the request.",
    risk_tier=RiskTier.LOW,
)


def _view_question(views: Sequence[str]) -> QuestionSpec:
    return QuestionSpec(
        id=VIEW_QUESTION_ID,
        kind=DecisionKind.CHOICE,
        instructions="Which memory views are most likely to hold what the request needs?",
        options=tuple(
            OptionSpec(v, _VIEW_DESCRIPTIONS.get(v, f"Memories of kind {v}.")) for v in views
        ),
        risk_tier=RiskTier.LOW,
    )


def _otel() -> Any:
    from prismal.monitoring.otel import OTelManager

    return OTelManager()


def _count(metric: str, attributes: dict[str, Any] | None = None) -> None:
    with suppress(Exception):
        _otel().increment_counter(metric, attributes=attributes or {})


@contextmanager
def _span(name: str, attributes: dict[str, Any]) -> Iterator[None]:
    cm: Any = None
    with suppress(Exception):
        cm = _otel().start_span(name, attributes)
        cm.__enter__()
    try:
        yield
    finally:
        if cm is not None:
            with suppress(Exception):
                cm.__exit__(None, None, None)


@dataclass(frozen=True)
class RecallPolicy:
    """Recall knobs (SPEC-MEM-RCL-001); weights = (similarity, recency, edge, confidence)."""

    views: tuple[str, ...] = ("preference", "semantic", "episodic", "procedural", GRAPH_VIEW)
    max_views: int = 2
    max_hops: int = 3
    k_per_hop: int = 8
    tau_sufficient: float = 0.8
    recency_half_life_days: float = 30.0
    weights: tuple[float, float, float, float] = (0.55, 0.2, 0.15, 0.1)

    @classmethod
    def from_settings(cls, settings: Settings) -> RecallPolicy:
        """Defaults with ``memory_recall_max_hops`` / ``memory_sufficiency_tau`` applied."""
        return cls(
            max_hops=settings.memory_recall_max_hops,
            tau_sufficient=settings.memory_sufficiency_tau,
        )


def traversal_score(
    sim: float, age_days: float, edge_weight: float, confidence: float, *, policy: RecallPolicy
) -> float:
    """``w1·sim + w2·2^(-age/half_life) + w3·edge + w4·confidence`` (age clamped ≥ 0)."""
    w_sim, w_rec, w_edge, w_conf = policy.weights
    recency = math.pow(2.0, -max(age_days, 0.0) / policy.recency_half_life_days)
    return w_sim * sim + w_rec * recency + w_edge * edge_weight + w_conf * confidence


@dataclass(frozen=True)
class _Candidate:
    record: MemoryRecord
    sim: float
    edge: float


class RecallController:
    """Runs one budgeted recall over a backend (and optional temporal graph)."""

    def __init__(
        self,
        *,
        decisions: DecisionPort | None,
        backend: MemoryBackendPort,
        graph: MemoryGraphPort | None,
        policy: RecallPolicy,
        settings: Settings | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._decisions = decisions
        self._backend = backend
        self._graph = graph
        if settings is not None:
            policy = replace(policy, max_hops=min(policy.max_hops, settings.memory_recall_max_hops))
        self._policy = policy
        self._gate = GatePolicy.from_settings(settings) if settings is not None else GatePolicy()
        self._clock = clock

    async def run(
        self, query: RecallQuery, *, ctx: MemoryContext, budget_tokens: int
    ) -> RecallResult:
        """Recall for ``query`` within ``budget_tokens`` (hard cap, whole items only)."""
        with _span("prismal.memory.recall", {"org_id": ctx.scope.org_id}):
            result = await self._run(query, ctx=ctx, budget_tokens=budget_tokens)
        _count("memory_recall_stops", {"reason": result.stop_reason})
        with suppress(Exception):
            _otel().record_histogram("memory_recall_tokens", float(result.tokens), {})
        return result

    # ── decisions ─────────────────────────────────────────────────────────────

    async def _decide(self, state: Mapping[str, str], question: QuestionSpec) -> Decision | None:
        if self._decisions is None:
            return None
        try:
            answers = await self._decisions.decide(
                state, {question.id: question}, policy=self._gate
            )
            decision = answers.get(question.id)
        except Exception as exc:
            logger.warning("memory_recall_decision_failed", error=type(exc).__name__)
            return None
        return decision if isinstance(decision, Decision) else None

    async def _route_views(self, query: RecallQuery) -> tuple[str, ...]:
        offered = tuple(v for v in self._policy.views if v != GRAPH_VIEW or self._graph is not None)
        # No router configured (≠ abstention): search every type view.
        chosen: tuple[str, ...] = (
            DEFAULT_VIEWS
            if self._decisions is not None
            else tuple(v for v in offered if v in _VIEW_TYPES)
        )
        if self._decisions is not None and len(offered) >= 2:
            state = DecisionStateBuilder().add_untrusted("request", query.text, source="user")
            decision = await self._decide(state.build().fields, _view_question(offered))
            if decision is not None and not decision.abstained:
                ranked = sorted(
                    (
                        v
                        for v in offered
                        if decision.probabilities.get(v, 0.0) >= VIEW_MIN_PROBABILITY
                    ),
                    key=lambda v: (-decision.probabilities.get(v, 0.0), offered.index(v)),
                )
                if ranked:
                    chosen = tuple(ranked[: self._policy.max_views])
        if query.types is not None:
            allowed = query.types
            chosen = tuple(v for v in chosen if v not in _VIEW_TYPES or _VIEW_TYPES[v] in allowed)
            if not any(v in _VIEW_TYPES for v in chosen):
                chosen = (
                    *tuple(t.value for t in MemoryType if t in allowed),
                    *(v for v in chosen if v == GRAPH_VIEW),
                )
        return chosen

    async def _sufficiency(
        self, query: RecallQuery, items: Sequence[RecalledItem]
    ) -> StopReason | None:
        if self._decisions is None:
            return None
        evidence = "\n".join(item_line(i.record) for i in items)
        state = (
            DecisionStateBuilder()
            .add_untrusted("request", query.text, source="user")
            .add_untrusted("evidence", evidence, source="memory")
            .build()
        )
        decision = await self._decide(state.fields, SUFFICIENCY_QUESTION)
        if state.tainted:  # injected evidence may not stop recall early (nor via abstention)
            return None
        if decision is None or decision.abstained:
            return "abstained"
        if decision.probabilities.get("true", 0.0) >= self._policy.tau_sufficient:
            return "sufficient"
        return None

    # ── retrieval ─────────────────────────────────────────────────────────────

    def _visible(self, record: MemoryRecord, ctx: MemoryContext) -> bool:
        if ctx.scope.covers(record.scope):
            return True
        _count("memory_tenancy_violations")
        logger.warning("memory_recall_tenancy_violation", record_org=record.scope.org_id)
        return False

    async def _as_of_visible(
        self, record: MemoryRecord, query: RecallQuery, ctx: MemoryContext
    ) -> bool:
        if query.as_of is None:
            return record.superseded_by is None
        if record.provenance.created_at > query.as_of:
            return False
        if record.superseded_by is None:
            return True
        newer = await self._backend.get(record.superseded_by, scope=ctx.scope)
        # Supersession time unknown ⇒ treat as already superseded (conservative).
        return newer is not None and query.as_of < newer.provenance.created_at

    def _edge_weight(self, record: MemoryRecord, admitted: Mapping[str, RecalledItem]) -> float:
        weights = [
            e.confidence
            for e in record.edges
            if e.valid_to is None and (e.dst_id in admitted or e.src_id in admitted)
        ]
        return max(weights, default=0.0)

    async def _search(
        self, text: str, views: Sequence[str], k: int, query: RecallQuery, ctx: MemoryContext
    ) -> list[tuple[MemoryRecord, float]]:
        hits: list[tuple[MemoryRecord, float]] = []
        for view in views:
            mtype = _VIEW_TYPES.get(view)
            if mtype is None:
                continue
            hits.extend(
                await self._backend.search(
                    text,
                    scope=ctx.scope,
                    types=frozenset({mtype}),
                    k=k,
                    include_superseded=query.as_of is not None,
                )
            )
        return hits

    async def _neighbors(
        self, seeds: Sequence[RecalledItem], query: RecallQuery, ctx: MemoryContext
    ) -> list[tuple[MemoryRecord, MemoryEdge]]:
        if self._graph is None:
            return []
        out: list[tuple[MemoryRecord, MemoryEdge]] = []
        for seed in seeds:
            sid = seed.record.id
            try:
                edges = await self._graph.neighbors(sid, scope=ctx.scope, as_of=query.as_of)
            except Exception as exc:
                logger.warning("memory_recall_graph_failed", error=type(exc).__name__)
                continue
            for edge in edges:
                if query.as_of is None and edge.valid_to is not None:
                    continue
                other = edge.dst_id if edge.src_id == sid else edge.src_id
                rec = await self._backend.get(other, scope=ctx.scope)
                if rec is not None:
                    out.append((rec, edge))
        return out

    async def _candidates(
        self,
        hop: int,
        query: RecallQuery,
        ctx: MemoryContext,
        views: Sequence[str],
        previous: Sequence[RecalledItem],
        admitted: Mapping[str, RecalledItem],
        seen: set[str],
    ) -> list[_Candidate]:
        text = query.text
        if hop > 1 and previous:
            text = " ".join([query.text, *(i.record.content for i in previous)])
        found: dict[str, _Candidate] = {}
        for rec, sim in await self._search(text, views, self._policy.k_per_hop * hop, query, ctx):
            if rec.id in seen or not self._visible(rec, ctx):
                continue
            prior = found.get(rec.id)
            if prior is None or sim > prior.sim:
                found[rec.id] = _Candidate(rec, float(sim), self._edge_weight(rec, admitted))
        if GRAPH_VIEW in views:
            for rec, edge in await self._neighbors(previous, query, ctx):
                if rec.id in seen or not self._visible(rec, ctx):
                    continue
                prior = found.get(rec.id)
                weight = max(edge.confidence, prior.edge if prior else 0.0)
                found[rec.id] = _Candidate(rec, prior.sim if prior else 0.0, weight)
        return [c for c in found.values() if await self._as_of_visible(c.record, query, ctx)]

    async def _run(
        self, query: RecallQuery, *, ctx: MemoryContext, budget_tokens: int
    ) -> RecallResult:
        views = await self._route_views(query)
        now = query.as_of if query.as_of is not None else self._clock()
        admitted: dict[str, RecalledItem] = {}
        seen: set[str] = set()
        previous: list[RecalledItem] = []
        used = 0
        hop = 0
        stop: StopReason = "max_hops"
        while hop < self._policy.max_hops:
            hop += 1
            candidates = await self._candidates(hop, query, ctx, views, previous, admitted, seen)
            if not candidates:
                stop = "no_candidates"
                break
            scored = sorted(
                (
                    (
                        traversal_score(
                            c.sim,
                            (now - c.record.provenance.created_at) / _DAY_S,
                            c.edge,
                            c.record.confidence,
                            policy=self._policy,
                        ),
                        c.record,
                    )
                    for c in candidates
                ),
                key=lambda sr: (-sr[0], sr[1].id),
            )
            previous = []
            overflow = False
            for score, rec in scored:
                seen.add(rec.id)
                cost = item_tokens(rec)
                if used + cost > budget_tokens:
                    overflow = True
                    continue
                used += cost
                item = RecalledItem(record=rec, score=score, tokens=cost)
                admitted[rec.id] = item
                previous.append(item)
            if overflow:
                stop = "budget_exhausted"
                break
            verdict = await self._sufficiency(query, list(admitted.values()))
            if verdict is not None:
                stop = verdict
                break
        items = tuple(admitted.values())
        rendered, tokens = render_items(items, budget_tokens=budget_tokens)
        return RecallResult(
            items=items,
            rendered=rendered,
            tokens=tokens,
            stop_reason=stop,
            views=views,
            hops=hop,
        )


__all__ = [
    "DEFAULT_VIEWS",
    "GRAPH_VIEW",
    "SUFFICIENCY_QUESTION",
    "SUFFICIENCY_QUESTION_ID",
    "VIEW_MIN_PROBABILITY",
    "VIEW_QUESTION_ID",
    "RecallController",
    "RecallPolicy",
    "traversal_score",
]

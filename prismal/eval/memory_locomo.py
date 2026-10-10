"""Synthetic LoCoMo-style memory eval (Phase MEM — SPEC-MEM-EVL-001, MEM6-04).

LoCoMo (snap-research/locomo) is CC BY-NC 4.0, so it is never committed here
(MEM0-05). :func:`generate_locomo_style` builds a seeded, MIT-licensed set with the
same question categories: multi-session dialogues with planted facts, later
updates, chit-chat filler, and single-hop / multi-hop / temporal / open-domain /
adversarial questions carrying their gold evidence turn ids.

QA is scored as retrieval QA without an LLM: a question is correct when its answer
appears in the recalled memory block (adversarial: when no planted value for the
asked key appears). :func:`compare_reports` checks the PLAN §4 targets.
"""

from __future__ import annotations

import random
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any

from prismal.memory.v2.adapters.in_memory import InMemoryBackend
from prismal.memory.v2.types import (
    ConsolidationReport,
    MemoryCandidate,
    MemoryContext,
    MemoryRecord,
    MemoryScope,
    MemoryType,
    Provenance,
    RecalledItem,
    RecallQuery,
    RecallResult,
    WriteOutcome,
    new_record_id,
)
from prismal.utils.token_estimate import estimate_tokens

if TYPE_CHECKING:
    from collections.abc import Callable

    from prismal.memory.v2.ports import MemoryPort
    from prismal.memory.v2.types import ForgetActor, ForgetSelector, StopReason

CATEGORIES: tuple[str, ...] = ("single_hop", "multi_hop", "temporal", "open_domain", "adversarial")

_NAMES = ("Ana", "Bruno", "Carla", "Diego", "Elena", "Fabio", "Gala", "Hugo")
_KINDS = {
    "food": ("paella", "ramen", "tacos", "lasagna", "curry", "falafel"),
    "sport": ("tennis", "rowing", "climbing", "cycling", "fencing", "surfing"),
    "drink": ("matcha", "espresso", "rooibos", "kombucha", "horchata", "mate"),
}
_CITIES = ("Valencia", "Lisbon", "Oslo", "Quito", "Kyoto", "Tbilisi")
_JOBS = ("pharmacist", "carpenter", "astronomer", "translator", "welder", "beekeeper")
_PETS = ("parrot", "tortoise", "ferret", "axolotl")
_FILLER = (
    "Haha, that is funny.",
    "Anyway, how was your day?",
    "I am a bit tired today.",
    "Let me think about it.",
    "Thanks for listening!",
)


@dataclass(frozen=True)
class Turn:
    """One dialogue turn."""

    id: str
    session: int
    ts: float
    speaker: str
    text: str


@dataclass(frozen=True)
class Question:
    """A QA probe with its gold evidence turn ids."""

    id: str
    category: str
    text: str
    answer: str
    evidence: tuple[str, ...]
    absent: tuple[str, ...] = ()


@dataclass(frozen=True)
class Conversation:
    """One persona's multi-session dialogue."""

    id: str
    persona: str
    turns: tuple[Turn, ...]
    questions: tuple[Question, ...]


@dataclass(frozen=True)
class MemoryEvalSet:
    """A generated eval set."""

    seed: int
    conversations: tuple[Conversation, ...]


def generate_locomo_style(
    *, seed: int = 0, conversations: int = 3, sessions: int = 4
) -> MemoryEvalSet:
    """Deterministic LoCoMo-style set; same seed ⇒ identical output."""
    rng = random.Random(seed)  # noqa: S311 # nosec B311 — deterministic test data, not crypto
    convs = [_conversation(rng, i, max(sessions, 3)) for i in range(conversations)]
    return MemoryEvalSet(seed=seed, conversations=tuple(convs))


def _conversation(rng: random.Random, idx: int, sessions: int) -> Conversation:
    persona = _NAMES[idx % len(_NAMES)]
    turns: list[Turn] = []
    t0 = 1_700_000_000.0 + idx * 10_000_000.0

    def say(session: int, text: str, speaker: str = "user") -> str:
        tid = f"c{idx}-t{len(turns)}"
        turns.append(Turn(tid, session, t0 + session * 86_400.0 + len(turns) * 60.0, speaker, text))
        return tid

    kind, kind2 = rng.sample(sorted(_KINDS), 2)
    first, latest = rng.sample(_KINDS[kind], 2)
    stable = rng.choice(_KINDS[kind2])
    sibling = rng.choice([n for n in _NAMES if n != persona])
    city, job, pet = rng.choice(_CITIES), rng.choice(_JOBS), rng.choice(_PETS)
    decoy_kind = next(k for k in sorted(_KINDS) if k not in (kind, kind2))

    t_first = say(0, f"My favourite {kind} is {first}.")
    say(0, rng.choice(_FILLER))
    say(0, "Nice talking to you.", speaker="assistant")
    t_stable = say(1, f"I really love {stable}, it is my favourite {kind2}.")
    t_sib_city = say(1, f"My sibling {sibling} lives in {city}.")
    say(1, rng.choice(_FILLER))
    t_sib_job = say(2, f"{sibling} works as a {job}.")
    t_pet = say(2, f"We adopted a {pet} last spring.")
    say(2, rng.choice(_FILLER))
    for s in range(3, sessions):
        say(s, rng.choice(_FILLER))
    t_latest = say(sessions - 1, f"Actually I switched my favourite {kind} to {latest}.")

    qs = (
        Question(f"c{idx}-q0", "single_hop", f"What is my favourite {kind2}?", stable, (t_stable,)),
        Question(
            f"c{idx}-q1",
            "multi_hop",
            f"What does my sibling who lives in {city} do for work?",
            job,
            (t_sib_city, t_sib_job),
        ),
        Question(
            f"c{idx}-q2",
            "temporal",
            f"What is my favourite {kind} now?",
            latest,
            (t_first, t_latest),
        ),
        Question(f"c{idx}-q3", "open_domain", "What pet did we adopt?", pet, (t_pet,)),
        Question(
            f"c{idx}-q4",
            "adversarial",
            f"What is my favourite {decoy_kind}?",
            "unknown",
            (),
            absent=_KINDS[decoy_kind],
        ),
    )
    return Conversation(id=f"c{idx}", persona=persona, turns=tuple(turns), questions=qs)


@dataclass(frozen=True)
class MemoryEvalReport:
    """QA accuracy, token and write efficiency, latency and stop reasons."""

    accuracy: float
    accuracy_by_category: dict[str, float]
    memory_tokens_per_turn: float
    max_memory_tokens: int
    full_history_tokens_per_turn: float
    stored_records: int
    recall_p95_ms: float
    stop_reasons: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """JSON-serializable view."""
        return asdict(self)


def _correct(q: Question, rendered: str) -> bool:
    text = rendered.lower()
    if q.category == "adversarial":
        return not any(v.lower() in text for v in q.absent)
    return q.answer.lower() in text


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(0.95 * (len(ordered) - 1)))]


async def run_memory_eval(
    port_factory: Callable[[MemoryScope], MemoryPort],
    dataset: MemoryEvalSet,
    *,
    budget_tokens: int = 1200,
) -> MemoryEvalReport:
    """Write every user turn through a fresh per-conversation port, then probe it."""
    hits: Counter[str] = Counter()
    totals: Counter[str] = Counter()
    mem_tokens: list[int] = []
    full_tokens: list[int] = []
    latencies: list[float] = []
    stops: Counter[str] = Counter()
    stored = 0
    for conv in dataset.conversations:
        scope = MemoryScope(org_id=f"eval-{dataset.seed}-{conv.id}", user_id=conv.persona)
        ctx = MemoryContext(scope=scope, session_id=conv.id, actor="eval")
        port = port_factory(scope)
        for turn in conv.turns:
            if turn.speaker != "user":
                continue
            out = await port.write(
                MemoryCandidate(content=turn.text, source_message_ids=(turn.id,)), ctx=ctx
            )
            if out.status in ("stored", "shadow"):
                stored += 1
        history = "\n".join(t.text for t in conv.turns)
        for q in conv.questions:
            start = time.perf_counter()
            result = await port.recall(
                RecallQuery(text=q.text), ctx=ctx, budget_tokens=budget_tokens
            )
            latencies.append((time.perf_counter() - start) * 1000.0)
            totals[q.category] += 1
            hits[q.category] += int(_correct(q, result.rendered))
            mem_tokens.append(result.tokens)
            full_tokens.append(estimate_tokens(history))
            stops[result.stop_reason] += 1
    n = sum(totals.values()) or 1
    return MemoryEvalReport(
        accuracy=sum(hits.values()) / n,
        accuracy_by_category={c: hits[c] / totals[c] if totals[c] else 0.0 for c in CATEGORIES},
        memory_tokens_per_turn=sum(mem_tokens) / n,
        max_memory_tokens=max(mem_tokens, default=0),
        full_history_tokens_per_turn=sum(full_tokens) / n,
        stored_records=stored,
        recall_p95_ms=_p95(latencies),
        stop_reasons=dict(stops),
    )


def compare_reports(
    legacy: MemoryEvalReport, v2: MemoryEvalReport, *, budget_tokens: int = 1200
) -> dict[str, Any]:
    """Deltas of ``v2`` vs ``legacy`` and the PLAN §4 targets each meets."""
    acc_delta = round((v2.accuracy - legacy.accuracy) * 100.0, 2)
    savings = (
        1.0 - v2.memory_tokens_per_turn / v2.full_history_tokens_per_turn
        if v2.full_history_tokens_per_turn
        else 0.0
    )
    write_delta = v2.stored_records / legacy.stored_records - 1.0 if legacy.stored_records else 0.0
    return {
        "accuracy_delta_pts": acc_delta,
        "token_savings_vs_full_history": round(savings, 4),
        "stored_records_delta": round(write_delta, 4),
        "meets": {
            "bounded_context": v2.max_memory_tokens <= budget_tokens,
            "token_savings": savings >= 0.80,
            "recall_quality": acc_delta >= 5.0,
            "write_efficiency": write_delta <= -0.40 and v2.accuracy >= legacy.accuracy,
        },
    }


class UngatedRecallPort:
    """Legacy-recall baseline: store every turn, return the top-``k`` matches unbudgeted.

    Mirrors ``LongTermMemory.save`` / ``recall(k=5)`` semantics (no typing, no gate,
    no token budget) over the deterministic in-memory backend.
    """

    def __init__(self, *, k: int = 5) -> None:
        self._k = k
        self._backend = InMemoryBackend()

    async def write(self, candidate: MemoryCandidate, *, ctx: MemoryContext) -> WriteOutcome:
        rec = MemoryRecord(
            id=new_record_id(),
            type=MemoryType.EPISODIC,
            content=candidate.content,
            scope=ctx.scope,
            provenance=Provenance(
                origin=candidate.origin,
                source_message_ids=candidate.source_message_ids,
                session_id=ctx.session_id,
                agent=ctx.actor,
                created_at=time.time(),
                tainted=False,
                trust="unknown",
            ),
            confidence=1.0,
        )
        await self._backend.put(rec)
        return WriteOutcome("stored", rec.id, rec.type, (), "none", "ungated")

    async def recall(
        self, query: RecallQuery, *, ctx: MemoryContext, budget_tokens: int
    ) -> RecallResult:
        del budget_tokens  # the legacy path has no budget
        hits = await self._backend.search(query.text, scope=ctx.scope, types=None, k=self._k)
        lines = [r.content for r, _ in hits]
        rendered = "\n".join(lines)
        items = tuple(RecalledItem(r, s, estimate_tokens(r.content)) for r, s in hits)
        stop: StopReason = "no_candidates" if not hits else "max_hops"
        return RecallResult(items, rendered, estimate_tokens(rendered), stop, ("all",), 1)

    async def consolidate(self, scope: MemoryScope) -> ConsolidationReport:
        return ConsolidationReport(scope, 0, 0, 0, 0, 0, False, ())

    async def forget(self, selector: ForgetSelector, *, actor: ForgetActor) -> int:
        del selector, actor
        return 0


__all__ = [
    "CATEGORIES",
    "Conversation",
    "MemoryEvalReport",
    "MemoryEvalSet",
    "Question",
    "Turn",
    "UngatedRecallPort",
    "compare_reports",
    "generate_locomo_style",
    "run_memory_eval",
]

"""Unit tests for ``prismal.agents.skynet.supervisor`` (SPEC-SKY-SUP-001).

Covers RF-SKY-01/02/03/07 and the S2 "done when" criteria: dynamic mode varies
N with the goal; fixed mode yields exactly K; overflow is deferred (not
dropped) and visible in audit.
"""

from __future__ import annotations

import json

import pytest

from prismal.agents.skynet.supervisor import SkynetSupervisor
from prismal.agents.skynet.types import SwarmOrder, SwarmPlan, WorkerResult
from prismal.core.config import Settings
from prismal.core.exceptions import SkynetError, SkynetPlanError

# ── fixtures / fakes ─────────────────────────────────────────────────────────


def _orders(n: int, *, prefix: str = "ord") -> list[SwarmOrder]:
    return [SwarmOrder(order_id=f"{prefix}-{i}", instruction=f"task {i}") for i in range(n)]


class FakePlanner:
    """Deterministic plan_fn capturing the messages it receives."""

    def __init__(self, orders: list[SwarmOrder], *, rationale: str = "split") -> None:
        self.orders = orders
        self.rationale = rationale
        self.received: list[list[dict[str, str]]] = []

    async def __call__(self, messages: list[dict[str, str]]) -> SwarmPlan:
        self.received.append(messages)
        return SwarmPlan(goal="", orders=self.orders, rationale=self.rationale)


class FakeEvaluator:
    """Deterministic evaluate_fn capturing the messages it receives."""

    def __init__(self, *, complete: bool = True, answer: str = "done") -> None:
        self.complete = complete
        self.answer = answer
        self.received: list[list[dict[str, str]]] = []

    async def __call__(self, messages: list[dict[str, str]]) -> tuple[bool, str]:
        self.received.append(messages)
        return (self.complete, self.answer)


class SpyAudit:
    """AuditLogger stand-in recording every event."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []

    def log_event(self, event_type: str, payload: dict[str, object]) -> None:
        self.events.append((event_type, payload))

    def event(self, event_type: str) -> dict[str, object]:
        matches = [p for t, p in self.events if t == event_type]
        assert matches, f"no '{event_type}' audit event recorded"
        return matches[-1]


def _supervisor(
    planner: FakePlanner | None = None,
    evaluator: FakeEvaluator | None = None,
    audit: SpyAudit | None = None,
    **settings_kwargs: object,
) -> SkynetSupervisor:
    return SkynetSupervisor(
        plan_fn=planner,
        evaluate_fn=evaluator,
        audit=audit,  # type: ignore[arg-type]
        settings=Settings(**settings_kwargs),  # type: ignore[arg-type]
    )


# ── plan(): dynamic sizing (RF-SKY-01/02) ────────────────────────────────────


async def test_plan_returns_plan_with_orders_and_goal_round_trips() -> None:
    """plan() returns a SwarmPlan with ≥1 order and the original goal."""
    planner = FakePlanner(_orders(3))
    plan = await _supervisor(planner).plan("research three things")
    assert plan.goal == "research three things"
    assert plan.size == 3
    assert plan.round == 1
    assert plan.deferred == []


async def test_plan_dynamic_size_varies_with_planner_output() -> None:
    """Dynamic mode (swarm_size=0): N is whatever the planner chose."""
    assert (await _supervisor(FakePlanner(_orders(2))).plan("g")).size == 2
    assert (await _supervisor(FakePlanner(_orders(5))).plan("g")).size == 5


async def test_plan_empty_decomposition_raises_plan_error() -> None:
    """A decomposition with zero orders is a planner failure (RF-SKY-01)."""
    with pytest.raises(SkynetPlanError):
        await _supervisor(FakePlanner([])).plan("g")


async def test_plan_fn_failure_wrapped_as_plan_error() -> None:
    """Planner backend errors surface as SkynetPlanError."""

    async def boom(messages: list[dict[str, str]]) -> SwarmPlan:
        raise RuntimeError("llm down")

    with pytest.raises(SkynetPlanError, match="llm down"):
        await SkynetSupervisor(plan_fn=boom, settings=Settings()).plan("g")


async def test_plan_goal_reaches_planner_via_secure_prompt() -> None:
    """The goal travels in the sanitized <user_input> channel (RF-SKY-14)."""
    planner = FakePlanner(_orders(1))
    await _supervisor(planner).plan("research competitors")
    messages = planner.received[0]
    assert messages[0]["role"] == "system"
    assert "canary:" in messages[0]["content"]
    assert "<user_input>" in messages[1]["content"]
    assert "research competitors" in messages[1]["content"]


# ── plan(): fixed sizing (RF-SKY-02) ─────────────────────────────────────────


async def test_plan_fixed_mode_instructs_exact_count() -> None:
    """Fixed mode: the planner system prompt demands exactly K sub-orders."""
    planner = FakePlanner(_orders(2))
    await _supervisor(planner, skynet_swarm_size=2).plan("g")
    assert "exactly 2" in planner.received[0][0]["content"]


async def test_plan_fixed_mode_yields_exactly_k() -> None:
    """Fixed mode: plan.size == K when the planner complies."""
    plan = await _supervisor(FakePlanner(_orders(3)), skynet_swarm_size=3).plan("g")
    assert plan.size == 3
    assert plan.deferred == []


async def test_plan_fixed_mode_overshoot_trimmed_and_deferred() -> None:
    """Fixed mode: planner overshoot is trimmed to K, extra orders deferred."""
    plan = await _supervisor(FakePlanner(_orders(5)), skynet_swarm_size=3).plan("g")
    assert plan.size == 3
    assert len(plan.deferred) == 2


# ── plan(): cap + deferred overflow (RF-SKY-03) ──────────────────────────────


async def test_plan_caps_at_skynet_max_swarm_and_defers_overflow() -> None:
    """With cap=3 and a 5-order decomposition, 3 dispatch and 2 are deferred."""
    plan = await _supervisor(FakePlanner(_orders(5)), skynet_max_swarm=3).plan("g")
    assert plan.size == 3
    assert len(plan.deferred) == 2
    dispatched = {o.order_id for o in plan.orders}
    deferred = {o.order_id for o in plan.deferred}
    assert dispatched.isdisjoint(deferred)


async def test_plan_cap_honours_parallel_max_workers_ceiling() -> None:
    """The effective cap is min(skynet_max_swarm, parallel_max_workers)."""
    plan = await _supervisor(
        FakePlanner(_orders(6)), skynet_max_swarm=8, parallel_max_workers=2
    ).plan("g")
    assert plan.size == 2
    assert len(plan.deferred) == 4


async def test_plan_overflow_visible_in_audit() -> None:
    """Deferred overflow is surfaced in the audit event, never silent."""
    audit = SpyAudit()
    await _supervisor(FakePlanner(_orders(5)), audit=audit, skynet_max_swarm=3).plan("g")
    payload = audit.event("skynet_plan")
    assert payload["swarm_size_requested"] == 5
    assert payload["swarm_size_effective"] == 3
    assert payload["deferred"] == 2


async def test_plan_audit_is_hash_first() -> None:
    """The audit payload carries a goal hash, never the goal text."""
    audit = SpyAudit()
    await _supervisor(FakePlanner(_orders(1)), audit=audit).plan("super secret goal")
    payload = audit.event("skynet_plan")
    assert "goal_hash" in payload
    assert all("super secret goal" not in str(v) for v in payload.values())


# ── plan(): re-plan seeded from unmet orders (RF-SKY-07) ─────────────────────


async def test_plan_with_unmet_orders_is_deterministic_pass_through() -> None:
    """Unmet orders seed the next round directly — the planner is not called."""
    planner = FakePlanner(_orders(3))
    unmet = _orders(2, prefix="unmet")
    plan = await _supervisor(planner).plan("g", round=2, unmet=unmet)
    assert planner.received == []
    assert plan.round == 2
    assert [o.order_id for o in plan.orders] == ["unmet-0", "unmet-1"]


async def test_plan_unmet_orders_increment_attempt() -> None:
    """Re-dispatched orders carry attempt+1."""
    unmet = [SwarmOrder(order_id="a", instruction="x", attempt=2)]
    plan = await _supervisor(FakePlanner(_orders(1))).plan("g", round=3, unmet=unmet)
    assert plan.orders[0].attempt == 3


async def test_plan_unmet_orders_are_capped_too() -> None:
    """The cap + deferral also applies to re-planned rounds."""
    unmet = _orders(5, prefix="unmet")
    plan = await _supervisor(FakePlanner([]), skynet_max_swarm=2).plan("g", round=2, unmet=unmet)
    assert plan.size == 2
    assert len(plan.deferred) == 3


# ── evaluate() (RF-SKY-07) ───────────────────────────────────────────────────


def _results(n_ok: int, n_fail: int = 0) -> list[WorkerResult]:
    ok = [WorkerResult(order_id=f"ord-{i}", output=f"out {i}", success=True) for i in range(n_ok)]
    fail = [
        WorkerResult(order_id=f"fail-{i}", output="", success=False, error="boom")
        for i in range(n_fail)
    ]
    return ok + fail


async def test_evaluate_returns_complete_and_answer() -> None:
    """evaluate() relays the evaluator's (complete, answer) verdict."""
    evaluator = FakeEvaluator(complete=True, answer="synthesis")
    sup = _supervisor(FakePlanner(_orders(1)), evaluator)
    complete, answer = await sup.evaluate("g", _results(2))
    assert complete is True
    assert answer == "synthesis"


async def test_evaluate_goal_and_results_travel_in_secure_channel() -> None:
    """Goal and worker outputs are user-derived → sanitized user channel."""
    evaluator = FakeEvaluator()
    sup = _supervisor(FakePlanner(_orders(1)), evaluator)
    await sup.evaluate("the goal", _results(2, n_fail=1))
    messages = evaluator.received[0]
    assert "canary:" in messages[0]["content"]
    user = messages[1]["content"]
    assert "<user_input>" in user
    assert "the goal" in user
    assert "out 0" in user and "out 1" in user


async def test_evaluate_fn_failure_wrapped_as_skynet_error() -> None:
    """Evaluator backend errors surface as SkynetError."""

    async def boom(messages: list[dict[str, str]]) -> tuple[bool, str]:
        raise RuntimeError("llm down")

    sup = SkynetSupervisor(evaluate_fn=boom, settings=Settings())
    with pytest.raises(SkynetError, match="llm down"):
        await sup.evaluate("g", _results(1))


async def test_evaluate_audited_hash_first() -> None:
    """The evaluate audit event records hashes and counts, not content."""
    audit = SpyAudit()
    evaluator = FakeEvaluator(complete=False, answer="partial secret answer")
    sup = _supervisor(FakePlanner(_orders(1)), evaluator, audit)
    await sup.evaluate("g", _results(2, n_fail=1))
    payload = audit.event("skynet_evaluate")
    assert payload["complete"] is False
    assert payload["results"] == 3
    assert payload["failures"] == 1
    assert "answer_hash" in payload
    assert all("partial secret answer" not in str(v) for v in payload.values())


# ── default backends parsing (S2-05) ─────────────────────────────────────────


def test_parse_plan_response_extracts_orders_and_rationale() -> None:
    """The default planner parses a JSON decomposition into SwarmOrders."""
    from prismal.agents.skynet.supervisor import _parse_plan_response

    raw = json.dumps(
        {
            "rationale": "by topic",
            "orders": [
                {"instruction": "research A", "role": "researcher"},
                {"instruction": "research B"},
            ],
        }
    )
    orders, rationale = _parse_plan_response(raw)
    assert rationale == "by topic"
    assert [o.instruction for o in orders] == ["research A", "research B"]
    assert orders[0].role == "researcher"
    assert orders[1].role == "worker"
    assert [o.order_id for o in orders] == ["ord-1", "ord-2"]


def test_parse_plan_response_tolerates_markdown_fences() -> None:
    """JSON inside ```fences``` still parses."""
    from prismal.agents.skynet.supervisor import _parse_plan_response

    raw = '```json\n{"orders": [{"instruction": "x"}]}\n```'
    orders, _ = _parse_plan_response(raw)
    assert len(orders) == 1


def test_parse_plan_response_garbage_yields_no_orders() -> None:
    """Unparseable planner output yields zero orders (caller raises)."""
    from prismal.agents.skynet.supervisor import _parse_plan_response

    orders, rationale = _parse_plan_response("not json at all")
    assert orders == []
    assert rationale == ""


def test_parse_evaluate_response_extracts_verdict() -> None:
    """The default evaluator parses {complete, answer}."""
    from prismal.agents.skynet.supervisor import _parse_evaluate_response

    complete, answer = _parse_evaluate_response('{"complete": true, "answer": "all done"}')
    assert complete is True
    assert answer == "all done"


def test_parse_evaluate_response_garbage_degrades_to_incomplete() -> None:
    """Unparseable evaluator output degrades to (False, raw_text)."""
    from prismal.agents.skynet.supervisor import _parse_evaluate_response

    complete, answer = _parse_evaluate_response("plain text verdict")
    assert complete is False
    assert answer == "plain text verdict"

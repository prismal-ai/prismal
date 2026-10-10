"""DEC-ROUTE (Phase DEC — SPEC-DEC-INT-001, DEC4-01/02/04)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from prismal.core.config import Settings
from prismal.decisions.adapters.fake import FakeDecisionAdapter
from prismal.decisions.calibration import CalibrationRecord, CalibrationStore
from prismal.decisions.cascade import DecisionCascade
from prismal.decisions.integrations.route import (
    ROUTE_DECISION_ID,
    build_route_question,
    decide_route,
    maybe_decide_route,
    route_rule_hook,
)
from prismal.decisions.runtime import DecisionRuntime, set_decision_runtime
from prismal.decisions.shadow import ShadowRunner, reset_demotions
from prismal.decisions.types import Decision, GatePolicy, Outcome

ROUTES = frozenset({"coder", "researcher", "planner", "END"})


@pytest.fixture(autouse=True)
def _reset() -> Any:
    set_decision_runtime(None)
    reset_demotions()
    yield
    set_decision_runtime(None)
    reset_demotions()


def _state(text: str = "write a quicksort in python", **kw: Any) -> dict[str, Any]:
    return {"messages": [HumanMessage(content=text)], "session_id": "s1", **kw}


def _store(tmp_path: Path, model: str) -> CalibrationStore:
    store = CalibrationStore(tmp_path)
    store.save(
        CalibrationRecord.from_dict(
            {
                "decision_id": ROUTE_DECISION_ID,
                "model": model,
                "method": "temperature",
                "params": {"temperature": 1.0},
                "tau": 0.9,
                "target_precision": 0.95,
                "ece_before": 0.3,
                "ece_after": 0.02,
                "n_by_language": {"en": 900, "es": 900},
                "precision_by_language": {"en": 0.99, "es": 0.99},
                "version": "v1",
            }
        )
    )
    return store


def _pick(route: str, p: float = 0.97):
    def fn(_state: Any) -> Decision:
        q = build_route_question(ROUTES)
        labels = q.labels()
        rest = (1 - p) / (len(labels) - 1)
        return Decision.from_probabilities(
            q, {lab: (p if lab == route else rest) for lab in labels}, backend="x", model="x"
        )

    return fn


class TestQuestion:
    def test_options_cover_routes_with_descriptions(self) -> None:
        q = build_route_question(ROUTES)
        assert q.id == ROUTE_DECISION_ID
        assert set(q.labels()) == ROUTES
        assert all(o.description for o in q.options)
        assert q.labels() == tuple(sorted(ROUTES))  # deterministic order

    def test_route_descriptions_pass_forbidden_lint(self) -> None:
        from prismal.agents.supervisor import (
            ADVANCED_MEMBERS,
            BLIND_REVIEW_MEMBERS,
            KOKORO_MEMBERS,
            MEMBERS,
            MULTIMODAL_MEMBERS,
            SKYNET_MEMBERS,
        )
        from prismal.decisions.validation import validate_question

        every = set(MEMBERS + ADVANCED_MEMBERS + MULTIMODAL_MEMBERS + KOKORO_MEMBERS)
        every |= set(SKYNET_MEMBERS + BLIND_REVIEW_MEMBERS) | {"END"}
        validate_question(build_route_question(frozenset(every)), integration="route")


class TestDecideRoute:
    async def test_accepted_route_returned(self, tmp_path: Path) -> None:
        laya = FakeDecisionAdapter({ROUTE_DECISION_ID: _pick("planner")}, name="laya", model="lm")
        cascade = DecisionCascade([laya], calibration=_store(tmp_path, "lm"))
        route = await decide_route(_state(), ROUTES, cascade=cascade, settings=Settings())
        assert route == "planner"
        state_sent, _ = laya.calls[0]
        assert "untrusted:user" in state_sent["user"]
        assert "quicksort" in state_sent["user"]

    async def test_escalation_returns_none(self, tmp_path: Path) -> None:
        laya = FakeDecisionAdapter(
            {ROUTE_DECISION_ID: _pick("planner", 0.5)}, name="laya", model="lm"
        )
        cascade = DecisionCascade([laya], calibration=_store(tmp_path, "lm"))
        assert await decide_route(_state(), ROUTES, cascade=cascade, settings=Settings()) is None

    async def test_more_than_20_options_skip_laya_for_jev(self, tmp_path: Path) -> None:
        many = frozenset({f"agent_{i}" for i in range(25)} | {"END"})

        def pick(_s: Any) -> Decision:
            q = build_route_question(many)
            labels = q.labels()
            return Decision.from_probabilities(
                q,
                {lab: (0.99 if lab == "agent_3" else 0.0004) for lab in labels},
                backend="x",
                model="x",
            )

        laya = FakeDecisionAdapter(
            {ROUTE_DECISION_ID: pick}, name="laya", model="lm", max_choice_options=20
        )
        jev = FakeDecisionAdapter({ROUTE_DECISION_ID: pick}, name="jev", model="jm")
        store = _store(tmp_path, "lm")
        _store(tmp_path, "jm")
        cascade = DecisionCascade([laya, jev], calibration=CalibrationStore(tmp_path))
        assert store.get(ROUTE_DECISION_ID, "lm") is not None
        route = await decide_route(_state(), many, cascade=cascade, settings=Settings())
        assert route == "agent_3"
        assert laya.calls == []
        assert len(jev.calls) == 1

    async def test_history_is_untrusted_and_bounded(self, tmp_path: Path) -> None:
        laya = FakeDecisionAdapter({ROUTE_DECISION_ID: _pick("coder")}, name="laya", model="lm")
        cascade = DecisionCascade([laya], calibration=_store(tmp_path, "lm"))
        msgs = [HumanMessage(content="x" * 5000), AIMessage(content="ok"), HumanMessage("fix it")]
        await decide_route({"messages": msgs}, ROUTES, cascade=cascade, settings=Settings())
        sent, _ = laya.calls[0]
        assert "untrusted:history" in sent["history"]
        assert len(sent["history"]) < 3000
        assert "fix it" in sent["user"]

    def test_rule_hook_uses_match_intent(self) -> None:
        hook = route_rule_hook(frozenset({"cron_manager", "END"}))
        q = build_route_question(frozenset({"cron_manager", "END"}))
        hit = hook(
            {"user": "«untrusted:user»\nschedule a daily report every day at 8\n«/untrusted»"}, q
        )
        assert hit is not None
        assert hit.value == "cron_manager"
        assert hook({"user": "hello"}, q) is None


class TestMaybeDecideRoute:
    def _settings(self, tmp_path: Path, mode: str) -> Settings:
        return Settings.model_construct(
            **{
                **Settings().model_dump(),
                "decisions_enabled": True,
                "decision_routing_enabled": True,
                "decision_modes": {"route": mode},
                "decision_calibration_dir": str(tmp_path),
            }
        )

    async def test_disabled_is_noop(self) -> None:
        outcome = await maybe_decide_route(_state(), ROUTES, settings=Settings())
        assert outcome.accepted_route is None
        outcome.record_reference("coder")  # no-op, never raises

    async def test_enforce_accepts(self, tmp_path: Path) -> None:
        laya = FakeDecisionAdapter({ROUTE_DECISION_ID: _pick("coder")}, name="laya", model="lm")
        s = self._settings(tmp_path, "enforce")
        cascade = DecisionCascade([laya], calibration=_store(tmp_path, "lm"), settings=s)
        set_decision_runtime(DecisionRuntime(cascade=cascade, settings=s))
        outcome = await maybe_decide_route(_state(), ROUTES, settings=s)
        assert outcome.accepted_route == "coder"

    async def test_shadow_never_returns_a_route_and_records_reference(self, tmp_path: Path) -> None:
        laya = FakeDecisionAdapter({ROUTE_DECISION_ID: _pick("coder")}, name="laya", model="lm")
        s = self._settings(tmp_path, "shadow")
        cascade = DecisionCascade([laya], calibration=_store(tmp_path, "lm"), settings=s)
        runner = ShadowRunner(cascade)
        set_decision_runtime(DecisionRuntime(cascade=cascade, settings=s, shadow=runner))
        outcome = await maybe_decide_route(_state(), ROUTES, settings=s)
        assert outcome.accepted_route is None
        outcome.record_reference("researcher")
        await outcome.wait()
        rep = runner.report("route")
        assert rep.n == 1
        assert rep.agreement_all == 0.0

    async def test_enforce_failure_falls_back(self, tmp_path: Path) -> None:
        class Broken:
            async def decide(self, *a: Any, **k: Any) -> Any:
                raise RuntimeError("boom")

        s = self._settings(tmp_path, "enforce")
        set_decision_runtime(DecisionRuntime(cascade=Broken(), settings=s))  # type: ignore[arg-type]
        outcome = await maybe_decide_route(_state(), ROUTES, settings=s)
        assert outcome.accepted_route is None


def test_policy_from_settings_used() -> None:
    rt = DecisionRuntime(cascade=DecisionCascade([]), settings=Settings(decision_timeout_ms=123))
    assert rt.policy == GatePolicy.from_settings(Settings(decision_timeout_ms=123))
    assert Outcome.ACCEPTED.value == "accepted"

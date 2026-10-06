"""Shadow mode, promotion and drift demotion (Phase DEC — SPEC-DEC-SHD-001/002, DEC3-04)."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from prismal.core.config import Settings
from prismal.decisions.adapters.fake import FakeDecisionAdapter
from prismal.decisions.calibration import CalibrationRecord
from prismal.decisions.cascade import DecisionCascade
from prismal.decisions.shadow import (
    AgreementReport,
    PromotionCriteria,
    ShadowRunner,
    effective_mode,
    is_demoted,
    promotion_ready,
    reset_demotions,
)
from prismal.decisions.state import DecisionStateBuilder
from prismal.decisions.types import (
    Decision,
    DecisionKind,
    DecisionMode,
    GatePolicy,
    OptionSpec,
    Outcome,
    QuestionSpec,
)

Q = QuestionSpec(
    id="route.supervisor",
    kind=DecisionKind.CHOICE,
    instructions="Pick.",
    options=(OptionSpec("coder", "C"), OptionSpec("researcher", "R")),
)
POLICY = GatePolicy(timeout_ms=100)


@pytest.fixture(autouse=True)
def _clean() -> Any:
    reset_demotions()
    yield
    reset_demotions()


def _state() -> Any:
    return DecisionStateBuilder().add_untrusted("user", "write code", source="user").build()


def _decision(value: str, outcome: Outcome = Outcome.ACCEPTED, backend: str = "laya") -> Decision:
    d = Decision.from_probabilities(
        Q,
        {value: 0.9, ("researcher" if value == "coder" else "coder"): 0.1},
        backend=backend,
        model="m",
    )
    from dataclasses import replace

    return replace(d, outcome=outcome)


async def test_launch_runs_cascade_in_shadow_mode() -> None:
    fake = FakeDecisionAdapter({Q.id: _decision("coder")}, name="laya", model="m")
    runner = ShadowRunner(DecisionCascade([fake]))
    task = runner.launch(_state(), {Q.id: Q}, policy=POLICY, integration="route")
    assert task is not None
    out = await task
    assert out[Q.id].value == "coder"


async def test_launch_failure_never_surfaces() -> None:
    class Boom:
        async def decide(self, *a: Any, **k: Any) -> Any:
            raise RuntimeError("cascade exploded")

    runner = ShadowRunner(Boom())  # type: ignore[arg-type]
    task = runner.launch(_state(), {Q.id: Q}, policy=POLICY, integration="route")
    assert task is not None
    assert await task == {}
    assert runner.errors == 1


async def test_launch_bounded_by_timeout() -> None:
    fake = FakeDecisionAdapter({Q.id: _decision("coder")}, name="laya", model="m", delay_s=5)

    class Slow:
        async def decide(self, *a: Any, **k: Any) -> Any:
            await asyncio.sleep(5)

    runner = ShadowRunner(Slow(), grace_ms=10)  # type: ignore[arg-type]
    task = runner.launch(_state(), {Q.id: Q}, policy=POLICY, integration="route")
    assert task is not None
    assert await asyncio.wait_for(task, 1.0) == {}
    assert fake.calls == []


async def test_sampling_zero_skips() -> None:
    runner = ShadowRunner(DecisionCascade([]), sample_rate=0.0)
    assert runner.launch(_state(), {Q.id: Q}, policy=POLICY, integration="route") is None


def test_report_agreement_and_coverage(monkeypatch: pytest.MonkeyPatch) -> None:
    from prismal.monitoring import otel

    gauges: list[tuple[str, float, Any]] = []
    monkeypatch.setattr(
        otel.OTelManager,
        "set_gauge",
        lambda self, metric, value, attributes=None: gauges.append((metric, value, attributes)),
    )
    runner = ShadowRunner(DecisionCascade([]))
    runner.record_reference("route", Q.id, "coder", _decision("coder"), language="en")
    runner.record_reference("route", Q.id, "coder", _decision("researcher"), language="en")
    runner.record_reference(
        "route", Q.id, "coder", _decision("coder", Outcome.ESCALATED), language="es"
    )
    runner.record_reference(
        "route", Q.id, "researcher", _decision("researcher", backend="jev"), language="es"
    )
    rep = runner.report("route")
    assert isinstance(rep, AgreementReport)
    assert rep.n == 4
    assert rep.n_accepted == 3
    assert rep.agreement_all == pytest.approx(0.75)
    assert rep.agreement_accepted == pytest.approx(2 / 3)
    assert rep.coverage == pytest.approx(0.75)
    assert rep.by_language == pytest.approx({"en": 0.5, "es": 1.0})
    assert rep.by_backend == pytest.approx({"laya": 2 / 3, "jev": 1.0})
    assert gauges[-1] == (
        "decision_shadow_agreement_ratio",
        pytest.approx(1.0),
        {"integration": "route", "language": "es"},
    )


def test_noul_reference_bool() -> None:
    noul = QuestionSpec(id="n", kind=DecisionKind.NOUL, instructions="Relevant?")
    d = Decision.from_probabilities(noul, {"true": 0.9}, backend="laya", model="m")
    runner = ShadowRunner(DecisionCascade([]))
    runner.record_reference("rag", "n", True, d)
    assert runner.report("rag").agreement_all == 1.0


def test_empty_report() -> None:
    rep = ShadowRunner(DecisionCascade([])).report("route")
    assert rep.n == 0
    assert rep.agreement_all == 0.0


class TestPromotion:
    def _report(self, **over: Any) -> AgreementReport:
        base: dict[str, Any] = {
            "integration": "route",
            "n": 2500,
            "n_accepted": 1500,
            "agreement_all": 0.9,
            "agreement_accepted": 0.98,
            "coverage": 0.6,
            "by_language": {"en": 0.98, "es": 0.97},
            "by_backend": {"laya": 0.98},
            "days": 3.0,
        }
        base.update(over)
        return AgreementReport(**base)

    def _record(self, **over: Any) -> CalibrationRecord:
        base: dict[str, Any] = {
            "decision_id": Q.id,
            "model": "m",
            "method": "temperature",
            "params": {},
            "tau": 0.9,
            "target_precision": 0.95,
            "ece_before": 0.4,
            "ece_after": 0.03,
            "n_by_language": {"en": 800, "es": 400},
            "precision_by_language": {"en": 0.97, "es": 0.96},
        }
        base.update(over)
        return CalibrationRecord.from_dict(base)

    def test_ready(self) -> None:
        ok, reasons = promotion_ready(
            self._report(), PromotionCriteria(), calibration=self._record()
        )
        assert ok, reasons

    def test_days_can_replace_volume(self) -> None:
        ok, _ = promotion_ready(
            self._report(n=100, days=15), PromotionCriteria(), calibration=self._record()
        )
        assert ok

    @pytest.mark.parametrize(
        ("report_over", "record_over", "needle"),
        [
            ({"n": 100, "days": 2}, {}, "volume"),
            ({"agreement_accepted": 0.9}, {}, "agreement"),
            ({}, {"ece_after": 0.2}, "ece"),
            ({}, {"n_by_language": {"en": 800, "es": 10}}, "es"),
            ({}, {"precision_by_language": {"en": 0.99, "es": 0.9}}, "es"),
        ],
    )
    def test_not_ready(self, report_over: Any, record_over: Any, needle: str) -> None:
        ok, reasons = promotion_ready(
            self._report(**report_over),
            PromotionCriteria(),
            calibration=self._record(**record_over),
        )
        assert not ok
        assert any(needle in r for r in reasons)

    def test_missing_calibration_not_ready(self) -> None:
        ok, reasons = promotion_ready(self._report(), PromotionCriteria(), calibration=None)
        assert not ok
        assert any("calibration" in r for r in reasons)


class TestDemotion:
    def _settings(self, **kw: Any) -> Settings:
        return Settings.model_construct(
            decisions_enabled=True,
            decision_routing_enabled=True,
            decision_modes={"route": "enforce"},
            decision_auto_demote=True,
            decision_demote_floor=0.9,
            **kw,
        )

    def test_effective_mode(self) -> None:
        s = self._settings()
        assert effective_mode(s, "route") is DecisionMode.ENFORCE
        assert effective_mode(s, "hitl") is DecisionMode.OFF

    def test_rolling_agreement_below_floor_demotes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from prismal.monitoring import otel

        seen: list[Any] = []
        monkeypatch.setattr(
            otel.OTelManager,
            "increment_counter",
            lambda self, metric, value=1, attributes=None: seen.append((metric, attributes)),
        )
        s = self._settings()
        runner = ShadowRunner(DecisionCascade([]), settings=s, demote_min_window=10)
        for i in range(10):
            runner.record_audit("route", agree=i < 8)  # 0.8 < 0.9
        assert is_demoted("route")
        assert effective_mode(s, "route") is DecisionMode.SHADOW
        assert ("decision_drift_alarms", {"integration": "route"}) in seen

    def test_no_demotion_when_disabled_or_healthy(self) -> None:
        healthy = ShadowRunner(DecisionCascade([]), settings=self._settings(), demote_min_window=10)
        for _ in range(20):
            healthy.record_audit("route", agree=True)
        assert not is_demoted("route")
        off = ShadowRunner(
            DecisionCascade([]),
            settings=self._settings().model_copy(update={"decision_auto_demote": False}),
            demote_min_window=10,
        )
        for _ in range(20):
            off.record_audit("route", agree=False)
        assert not is_demoted("route")

    def test_window_is_last_500(self) -> None:
        runner = ShadowRunner(DecisionCascade([]), settings=self._settings())
        for _ in range(600):
            runner.record_audit("route", agree=True)
        assert runner.rolling_agreement("route") == 1.0
        assert len(runner._audited["route"]) == 500  # noqa: SLF001

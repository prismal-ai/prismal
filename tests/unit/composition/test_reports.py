"""Host-surface report recorders + reports (HRS — SPEC-HRS-DEC-001, RTE-001, WIN-001)."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

import pytest

from prismal.composition import build_test_runtime
from prismal.composition.host_surface import (
    DecisionIntegrationStats,
    DecisionsReportView,
    ReportWindow,
    RoutingPolicyStats,
    RoutingReportView,
)
from prismal.core.config import Settings
from prismal.decisions.catalog import INTEGRATION_FLAGS
from prismal.monitoring import host_stats
from prismal.monitoring.host_stats import (
    DecisionStatsRecorder,
    RoutingStatsRecorder,
    current_org,
    get_decision_stats_recorder,
    get_routing_stats_recorder,
    record_metric,
    use_org,
)
from prismal.monitoring.otel import OTelManager

pytestmark = pytest.mark.unit

T0 = 1_800_000_000.0  # fixed epoch seconds (2027-01-15)


class FakeClock:
    def __init__(self, now: float = T0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def _dt(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, tz=UTC)


@pytest.fixture(autouse=True)
def _reset_process_recorders() -> Iterator[None]:
    get_decision_stats_recorder().reset()
    get_routing_stats_recorder().reset()
    yield
    get_decision_stats_recorder().reset()
    get_routing_stats_recorder().reset()


# ── recorder basics ───────────────────────────────────────────────────────────


def test_recorder_ignores_foreign_metrics() -> None:
    rec = DecisionStatsRecorder(clock=FakeClock())
    rec.record("llm_requests", 1, {})
    assert rec.aggregate(since=None, until=None, org_id=None).totals == {}


def test_recorder_sums_by_metric_and_attributes() -> None:
    clock = FakeClock()
    rec = DecisionStatsRecorder(clock=clock)
    rec.record("decisions", 1, {"integration": "route", "outcome": "accepted", "backend": "laya"})
    rec.record("decisions", 2, {"integration": "route", "outcome": "accepted", "backend": "laya"})
    rec.record("llm_calls_avoided", 1, {"integration": "route"})
    agg = rec.aggregate(since=None, until=None, org_id=None)
    key = ("decisions", (("backend", "laya"), ("integration", "route"), ("outcome", "accepted")))
    assert agg.totals[key] == 3
    assert agg.totals[("llm_calls_avoided", (("integration", "route"),))] == 1


def test_window_since_clamped_to_recorder_start() -> None:
    clock = FakeClock()
    rec = RoutingStatsRecorder(clock=clock)
    clock.now = T0 + 600
    rec.record("plan_cache_hits", 1, {"intent": "x"})
    agg = rec.aggregate(since=_dt(T0 - 86_400), until=None, org_id=None)
    assert agg.since == _dt(T0)  # never extrapolated before the process start
    assert agg.until == _dt(T0 + 600)


def test_window_since_clamped_to_retention() -> None:
    clock = FakeClock()
    rec = RoutingStatsRecorder(clock=clock, retention_h=1)
    clock.now = T0 + 3 * 3600
    rec.record("plan_cache_hits", 1, {"intent": "x"})
    agg = rec.aggregate(since=_dt(T0), until=None, org_id=None)
    assert agg.since == _dt(T0 + 2 * 3600)


def test_query_retention_override_clamps_further() -> None:
    clock = FakeClock()
    rec = RoutingStatsRecorder(clock=clock, retention_h=168)
    clock.now = T0 + 5 * 3600
    agg = rec.aggregate(since=_dt(T0), until=None, org_id=None, retention_h=2)
    assert agg.since == _dt(T0 + 3 * 3600)


def test_buckets_outside_window_excluded_and_old_buckets_pruned() -> None:
    clock = FakeClock()
    rec = RoutingStatsRecorder(clock=clock, retention_h=1)
    rec.record("plan_cache_hits", 1, {"intent": "a"})
    clock.now = T0 + 1800
    rec.record("plan_cache_hits", 5, {"intent": "a"})
    key = ("plan_cache_hits", (("intent", "a"),))
    # window covering only the second minute bucket
    agg = rec.aggregate(since=_dt(T0 + 1700), until=None, org_id=None)
    assert agg.totals[key] == 5
    # until before the second sample
    agg2 = rec.aggregate(since=None, until=_dt(T0 + 120), org_id=None)
    assert agg2.totals[key] == 1
    clock.now = T0 + 2 * 3600
    rec.record("plan_cache_hits", 1, {"intent": "a"})
    assert rec.bucket_count() == 1  # the old buckets fell out of retention


def test_naive_datetimes_are_treated_as_utc() -> None:
    clock = FakeClock()
    rec = RoutingStatsRecorder(clock=clock)
    clock.now = T0 + 600
    naive = _dt(T0 + 60).replace(tzinfo=None)
    agg = rec.aggregate(since=naive, until=None, org_id=None)
    assert agg.since == _dt(T0 + 60)


def test_until_in_the_future_is_clamped_to_now() -> None:
    clock = FakeClock(T0 + 600)
    rec = RoutingStatsRecorder(clock=clock)
    agg = rec.aggregate(since=None, until=_dt(T0 + 99_999), org_id=None)
    assert agg.until == _dt(T0 + 600)


def test_bucket_key_cardinality_is_bounded() -> None:
    rec = RoutingStatsRecorder(clock=FakeClock(), max_keys_per_bucket=3)
    for i in range(10):
        rec.record("plan_cache_hits", 1, {"intent": f"i{i}"})
    assert len(rec.aggregate(since=None, until=None, org_id=None).totals) == 3


def test_record_never_raises_on_bad_value() -> None:
    rec = RoutingStatsRecorder(clock=FakeClock())
    rec.record("plan_cache_hits", "not-a-number", {"intent": "a"})  # type: ignore[arg-type]
    assert rec.aggregate(since=None, until=None, org_id=None).totals == {}


def test_recorder_is_thread_safe() -> None:
    rec = RoutingStatsRecorder(clock=FakeClock())

    def work() -> None:
        for _ in range(500):
            rec.record("plan_cache_hits", 1, {"intent": "a"})

    threads = [threading.Thread(target=work) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    agg = rec.aggregate(since=None, until=None, org_id=None)
    assert agg.totals[("plan_cache_hits", (("intent", "a"),))] == 4000


def test_default_retention_falls_back_when_settings_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom() -> Any:
        raise RuntimeError("no settings")

    monkeypatch.setattr("prismal.core.config.get_settings", boom)
    rec = RoutingStatsRecorder(clock=FakeClock())
    assert rec.retention_h == 168


def test_default_retention_reads_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "prismal.core.config.get_settings", lambda: Settings(host_surface_stats_retention_h=5)
    )
    rec = RoutingStatsRecorder(clock=FakeClock())
    assert rec.retention_h == 5


# ── tenant scoping ────────────────────────────────────────────────────────────


def test_samples_carry_org_from_attributes_or_contextvar() -> None:
    rec = DecisionStatsRecorder(clock=FakeClock())
    rec.record("llm_calls_avoided", 1, {"integration": "route", "tenant": "acme"})
    rec.record("llm_calls_avoided", 1, {"integration": "route", "org_id": "beta"})
    with use_org("acme"):
        assert current_org.get() == "acme"
        rec.record("llm_calls_avoided", 1, {"integration": "route"})
    assert current_org.get() is None
    rec.record("llm_calls_avoided", 1, {"integration": "route", "tenant": ""})  # unscoped
    key = ("llm_calls_avoided", (("integration", "route"),))
    assert rec.aggregate(since=None, until=None, org_id="acme").totals[key] == 2
    assert rec.aggregate(since=None, until=None, org_id="beta").totals[key] == 1
    # org_id=None (single-tenant / global admin context) sees every sample
    assert rec.aggregate(since=None, until=None, org_id=None).totals[key] == 4


# ── the OTel tap feeds the process recorders (parity with the counters) ──────


def test_record_metric_dispatches_to_process_recorders() -> None:
    record_metric("decisions", 1, {"integration": "rag", "outcome": "escalated"})
    record_metric("routing_decisions", 1, {"policy": "cascade", "tier": "small"})
    record_metric("unrelated", 1, {})
    dec = get_decision_stats_recorder().aggregate(since=None, until=None, org_id=None)
    rte = get_routing_stats_recorder().aggregate(since=None, until=None, org_id=None)
    assert sum(dec.totals.values()) == 1
    assert sum(rte.totals.values()) == 1


def test_record_metric_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("x")

    monkeypatch.setattr(DecisionStatsRecorder, "record", boom)
    record_metric("decisions", 1, {"integration": "rag"})  # swallowed


def test_otel_increment_counter_feeds_recorder_with_parity() -> None:
    otel = OTelManager()
    counter = MagicMock()
    saved = otel._counters.get("decisions")
    otel._counters["decisions"] = counter
    try:
        attrs = {"integration": "route", "backend": "rule", "outcome": "accepted"}
        otel.increment_counter("decisions", 3, attributes=attrs)
    finally:
        if saved is None:
            otel._counters.pop("decisions", None)
        else:
            otel._counters["decisions"] = saved
    counter.add.assert_called_once_with(3, attrs)
    dec = get_decision_stats_recorder().aggregate(since=None, until=None, org_id=None)
    assert sum(dec.totals.values()) == 3


def test_otel_tap_works_with_otel_disabled() -> None:
    otel = OTelManager()
    saved = otel._counters.pop("routing_savings_usd", None)
    try:
        otel.increment_counter("routing_savings_usd", 1500, attributes={"policy": "cascade"})
    finally:
        if saved is not None:
            otel._counters["routing_savings_usd"] = saved
    rte = get_routing_stats_recorder().aggregate(since=None, until=None, org_id=None)
    assert sum(rte.totals.values()) == 1500


def test_otel_tap_failure_never_breaks_increment(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("tap")

    monkeypatch.setattr(host_stats, "record_metric", boom)
    OTelManager().increment_counter("decisions", 1, attributes={"integration": "x"})


# ── decisions_report (SPEC-HRS-DEC-001) ───────────────────────────────────────


def _dec_settings(**kw: Any) -> Settings:
    base: dict[str, Any] = {"decisions_enabled": True, "decision_routing_enabled": True}
    base.update(kw)
    return Settings(**base)


def test_decisions_report_disabled() -> None:
    ctx = build_test_runtime(settings=Settings())
    view = ctx.decisions_report()
    assert view == DecisionsReportView(enabled=False, integrations=())


def test_decisions_report_rows_counts_and_agreement() -> None:
    settings = _dec_settings()
    shadow = MagicMock()

    def _report(integration: str) -> Any:
        rep = MagicMock()
        rep.n = 4 if integration == "route" else 0
        rep.agreement_accepted = 0.75 if integration == "route" else 0.0
        return rep

    shadow.report.side_effect = _report
    decisions = MagicMock()
    decisions.shadow = shadow
    ctx = build_test_runtime(settings=settings)
    ctx.decisions = decisions

    OTelManager().increment_counter(
        "decisions", attributes={"integration": "route", "backend": "rule", "outcome": "accepted"}
    )
    OTelManager().increment_counter(
        "decisions", attributes={"integration": "route", "backend": "llm", "outcome": "escalated"}
    )
    OTelManager().increment_counter(
        "decisions", attributes={"integration": "route", "backend": "laya", "outcome": "abstained"}
    )
    OTelManager().increment_counter("llm_calls_avoided", attributes={"integration": "route"})

    view = ctx.decisions_report()
    assert view.enabled is True
    assert [r.integration for r in view.integrations] == list(INTEGRATION_FLAGS)
    route = view.integrations[0]
    assert isinstance(route, DecisionIntegrationStats)
    assert route.mode == "shadow"
    assert (route.samples, route.agreement_ratio) == (4, 0.75)
    assert (route.accepted, route.escalated, route.abstained) == (1, 1, 1)
    assert route.llm_calls_avoided == 1
    assert isinstance(route.window, ReportWindow)
    rag = next(r for r in view.integrations if r.integration == "rag")
    assert rag.mode == "off"
    assert (rag.samples, rag.agreement_ratio, rag.accepted) == (0, None, 0)


def test_decisions_report_without_runtime_has_zero_samples() -> None:
    ctx = build_test_runtime(settings=_dec_settings())
    route = ctx.decisions_report().integrations[0]
    assert (route.samples, route.agreement_ratio) == (0, None)


def test_decisions_report_is_tenant_scoped() -> None:
    ctx_acme = build_test_runtime(settings=_dec_settings(), org_id="acme")
    with use_org("beta"):
        OTelManager().increment_counter("llm_calls_avoided", attributes={"integration": "route"})
    with use_org("acme"):
        OTelManager().increment_counter("llm_calls_avoided", attributes={"integration": "route"})
        OTelManager().increment_counter("llm_calls_avoided", attributes={"integration": "route"})
    assert ctx_acme.decisions_report().integrations[0].llm_calls_avoided == 2
    ctx_all = build_test_runtime(settings=_dec_settings())
    assert ctx_all.decisions_report().integrations[0].llm_calls_avoided == 3


def test_decisions_report_window_clamped() -> None:
    ctx = build_test_runtime(settings=_dec_settings())
    since = datetime.now(UTC) - timedelta(days=3650)
    row = ctx.decisions_report(since=since).integrations[0]
    assert row.window.since > since
    assert row.window.until <= datetime.now(UTC)


# ── routing_report (SPEC-HRS-RTE-001) ─────────────────────────────────────────


def _rte_settings(**kw: Any) -> Settings:
    base: dict[str, Any] = {
        "adaptive_routing_enabled": True,
        "routing_policy": "composite",
        "routing_mode": "enforce",
    }
    base.update(kw)
    return Settings(**base)


def test_routing_report_disabled() -> None:
    view = build_test_runtime(settings=Settings()).routing_report()
    assert view == RoutingReportView(enabled=False, mode="shadow", policies=())


def test_routing_report_rows() -> None:
    ctx = build_test_runtime(settings=_rte_settings())
    otel = OTelManager()
    for tier in ("small", "small", "frontier"):
        otel.increment_counter(
            "routing_decisions", attributes={"policy": "composite", "tier": tier}
        )
    otel.increment_counter("routing_decisions", attributes={"policy": "fallback", "tier": "medium"})
    otel.increment_counter("routing_escalations", attributes={"reason": "inadequate"})
    otel.increment_counter("routing_escalations", attributes={"reason": "cap"})
    otel.increment_counter("plan_cache_hits", attributes={"intent": "summarize"})
    otel.increment_counter(
        "routing_savings_usd", 1_250_000, attributes={"policy": "composite", "tier": "small"}
    )
    otel.increment_counter(
        "routing_savings_usd", 10_000, attributes={"policy": "fallback", "tier": "medium"}
    )

    view = ctx.routing_report()
    assert (view.enabled, view.mode) == (True, "enforce")
    rows = {r.policy: r for r in view.policies}
    assert set(rows) == {"composite", "fallback"}
    comp = rows["composite"]
    assert isinstance(comp, RoutingPolicyStats)
    assert comp.decisions == 3
    assert dict(comp.decisions_by_tier) == {"small": 2, "frontier": 1}
    # escalations / plan-cache hits carry no policy label → the configured policy
    assert comp.escalations == 2
    assert dict(comp.escalations_by_reason) == {"inadequate": 1, "cap": 1}
    assert comp.plan_cache_hits == 1
    assert comp.estimated_savings_usd == pytest.approx(1.25)
    fb = rows["fallback"]
    assert (fb.decisions, fb.escalations, fb.plan_cache_hits) == (1, 0, 0)
    assert fb.estimated_savings_usd == pytest.approx(0.01)


def test_routing_report_configured_policy_row_even_without_samples() -> None:
    view = build_test_runtime(settings=_rte_settings(routing_policy="cascade")).routing_report()
    assert [r.policy for r in view.policies] == ["cascade"]
    assert view.policies[0].decisions == 0


def test_routing_report_tenant_scoped() -> None:
    with use_org("beta"):
        OTelManager().increment_counter(
            "routing_decisions", attributes={"policy": "composite", "tier": "small"}
        )
    view = build_test_runtime(settings=_rte_settings(), org_id="acme").routing_report()
    assert view.policies[0].decisions == 0


def test_otel_tap_key_set_matches_recorders() -> None:
    from prismal.monitoring import otel as otel_module

    assert otel_module._HOST_STATS_METRICS == host_stats.HOST_STATS_METRICS


# ── tenant attribution end-to-end (HRS review finding 7) ──────────────────────


def _emit_dec_and_rte() -> None:
    otel = OTelManager()
    otel.increment_counter(
        "decisions", attributes={"integration": "route", "backend": "rule", "outcome": "accepted"}
    )
    otel.increment_counter("llm_calls_avoided", attributes={"integration": "route"})
    otel.increment_counter("routing_decisions", attributes={"policy": "composite", "tier": "small"})


def _counts(ctx: Any) -> tuple[int, int, int]:
    route = ctx.decisions_report().integrations[0]
    rte = ctx.routing_report().policies[0]
    return route.accepted, route.llm_calls_avoided, rte.decisions


def test_use_org_active_while_emitting_scopes_both_reports() -> None:
    settings = Settings(
        decisions_enabled=True,
        decision_routing_enabled=True,
        adaptive_routing_enabled=True,
        routing_policy="composite",
    )
    with use_org("acme"):
        _emit_dec_and_rte()
    acme = build_test_runtime(settings=settings, org_id="acme")
    other = build_test_runtime(settings=settings, org_id="other")
    assert _counts(acme) == (1, 1, 1)
    assert _counts(other) == (0, 0, 0)


def test_bind_org_binds_the_context_tenant_for_a_run() -> None:
    settings = Settings(
        decisions_enabled=True,
        decision_routing_enabled=True,
        adaptive_routing_enabled=True,
        routing_policy="composite",
    )
    acme = build_test_runtime(settings=settings, org_id="acme")
    other = build_test_runtime(settings=settings, org_id="other")
    assert current_org.get() is None
    with acme.bind_org():
        assert current_org.get() == "acme"
        _emit_dec_and_rte()
    assert current_org.get() is None  # restored on exit
    assert _counts(acme) == (1, 1, 1)
    assert _counts(other) == (0, 0, 0)


def test_bind_org_restores_on_error_and_is_a_noop_without_tenant() -> None:
    acme = build_test_runtime(settings=Settings(), org_id="acme")
    with pytest.raises(RuntimeError), acme.bind_org():
        raise RuntimeError("boom")
    assert current_org.get() is None
    with use_org("outer"), build_test_runtime(settings=Settings()).bind_org():
        assert current_org.get() is None  # org_id=None binds "no tenant"

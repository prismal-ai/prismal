"""Regression tests for the AMR code-review findings (select / features / cascade / registry)."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from langchain_core.messages import HumanMessage

from prismal.budget.types import Usage
from prismal.core.config import Settings
from prismal.decisions.types import RiskTier
from prismal.routing import select as select_mod
from prismal.routing.features import extract_features
from prismal.routing.select import (
    clear_routing_run,
    get_routing_policy,
    record_outcome,
    select_model,
)
from prismal.routing.types import ModelTier, RoutingOutcome, TaskFeatures


@pytest.fixture
def tiers(tmp_path: Path) -> str:
    p = tmp_path / "t.yaml"
    p.write_text(
        "tiers: {nano: nano-m, small: small-m, medium: medium-m, frontier: frontier-m,"
        " auto: auto-m}\n"
        "agents: {coder: frontier, researcher: medium, codeact: medium, file_manager: medium}\n",
        encoding="utf-8",
    )
    return str(p)


def _s(tiers: str, **kw: Any) -> Settings:
    base: dict[str, Any] = {
        "adaptive_routing_enabled": True,
        "routing_mode": "enforce",
        "routing_policy": "cascade",
        "routing_tier_models": tiers,
    }
    base.update(kw)
    return Settings(**base)


def _state(sid: str | None = "r1", text: str = "hello there", **routing: Any) -> dict[str, Any]:
    st: dict[str, Any] = {
        "messages": [HumanMessage(content=text)],
        "metadata": {"routing": dict(routing)} if routing else {},
    }
    if sid:
        st["session_id"] = sid
    return st


@pytest.fixture(autouse=True)
def _clean() -> Any:
    yield
    for sid in ("r1", "r2", "_default"):
        clear_routing_run({"session_id": sid})


def _f(agent: str, **kw: Any) -> TaskFeatures:
    base: dict[str, Any] = {
        "agent": agent,
        "intent": None,
        "est_input_tokens": 1,
        "needs_tools": False,
        "needs_structured_output": False,
    }
    base.update(kw)
    return TaskFeatures(**base)


# 1 — sticky same-turn choice still passes the security floors
def test_sticky_choice_reclamped_when_turn_becomes_tainted(tiers: str) -> None:
    s = _s(tiers)
    state = _state()
    assert (
        select_model(state, "researcher", settings=s, features=_f("researcher")).tier
        is ModelTier.SMALL
    )
    later = select_model(state, "researcher", settings=s, features=_f("researcher", tainted=True))
    assert later.tier is ModelTier.MEDIUM


# 2 — session-less calls are stateless (no shared default bucket)
def test_sessionless_calls_share_no_state(tiers: str) -> None:
    s = _s(tiers)
    first = select_model({}, "researcher", settings=s, features=_f("researcher"))
    assert first.tier is ModelTier.SMALL
    crit = select_model(
        {}, "researcher", settings=s, features=_f("researcher", risk_tier=RiskTier.CRITICAL)
    )
    assert crit.tier is ModelTier.FRONTIER
    assert get_routing_policy({}) is None
    assert (
        record_outcome({}, "researcher", RoutingOutcome(success=False, structured_valid=False))
        is None
    )


# 4a — hints can only raise risk
def test_risk_hint_cannot_lower_agent_risk(tiers: str) -> None:
    s = _s(tiers)
    assert (
        extract_features(_state(risk_tier="low"), "codeact", settings=s).risk_tier is RiskTier.HIGH
    )
    assert (
        extract_features(_state(risk_tier="critical"), "codeact", settings=s).risk_tier
        is RiskTier.CRITICAL
    )


def test_taint_hint_false_does_not_clear_registry_taint(tiers: str) -> None:
    from langchain_core.messages import ToolMessage

    from prismal.security.taint import Provenance, TaintRegistry

    reg = TaintRegistry()
    reg.mark_untrusted("evil tool text", Provenance("tool"))
    state = _state(tainted=False)
    state["metadata"]["hardening"] = {"taint": reg.to_dict()}
    state["messages"].append(ToolMessage(content="evil tool text", tool_call_id="t"))
    assert extract_features(state, "coder", settings=_s(tiers)).tainted is True


# 4b — restored affinity must match the tier map
def test_forged_affinity_is_ignored(tiers: str) -> None:
    s = _s(tiers)
    state = _state(affinity={"coder": {"tier": "frontier", "model_ref": "cheap/unvetted"}})
    choice = select_model(state, "coder", settings=s)
    assert choice.model_ref != "cheap/unvetted"


def test_genuine_affinity_keeps_escalation_count(tiers: str) -> None:
    s = _s(tiers, routing_cascade_max_escalations=1)
    state = _state(
        affinity={"coder": {"tier": "medium", "model_ref": "medium-m", "escalations": 1}}
    )
    choice = select_model(state, "coder", settings=s)
    assert choice.tier is ModelTier.MEDIUM
    # cap of 1 already used: a new failure is accepted, not escalated
    assert (
        record_outcome(state, "coder", RoutingOutcome(success=False, structured_valid=False))
        is None
    )


# 7 — the per-run registry is bounded
def test_run_registry_is_bounded(tiers: str) -> None:
    s = _s(tiers)
    with patch.object(select_mod, "_MAX_RUNS", 3):
        for i in range(6):
            select_model(_state(sid=f"lru-{i}"), "coder", settings=s)
        assert len(select_mod._RUNS) <= 3
    for i in range(6):
        clear_routing_run({"session_id": f"lru-{i}"})


# 12 — a new turn with an unchanged human count still resets same-turn choices
def test_turn_signature_tracks_last_human_message(tiers: str) -> None:
    s = _s(tiers)
    state = _state()
    select_model(state, "researcher", settings=s, features=_f("researcher", tainted=True))
    # compaction keeps one human message but it is a different turn
    state["messages"] = [HumanMessage(content="a brand new question")]
    assert (
        select_model(state, "researcher", settings=s, features=_f("researcher")).tier
        is ModelTier.SMALL
    )


# 17 — tainted turns never run on AUTO unless AUTO is the static tier
def test_tainted_turn_never_on_auto(tiers: str, tmp_path: Path) -> None:
    p = tmp_path / "a.yaml"
    p.write_text("tiers: {medium: medium-m, auto: auto-m}\nagents: {researcher: medium}\n")
    s = _s(str(p), routing_policy="static")
    state = _state(affinity={"researcher": {"tier": "auto", "model_ref": "auto-m"}})
    choice = select_model(state, "researcher", settings=s, features=_f("researcher", tainted=True))
    assert choice.tier is ModelTier.MEDIUM


# 19 — no savings counted for a model that never ran (shadow)
def test_no_savings_in_shadow(tiers: str) -> None:
    s = _s(tiers, routing_mode="shadow")
    state = _state()
    select_model(state, "coder", settings=s)

    class _Est:
        cost_usd = 1.0

    with (
        patch("prismal.routing.select.compute_cost_usd", return_value=_Est()),
        patch("prismal.routing.select.OTelManager") as otel,
    ):
        record_outcome(
            state, "coder", RoutingOutcome(success=True, usage=Usage(prompt_tokens=10, calls=1))
        )
    names = [c.args[0] for c in otel.return_value.increment_counter.call_args_list]
    assert "routing_savings_usd" not in names


# 8 — cascade on_outcome uses the caller's features, not a colliding cached entry
def test_cascade_uses_outcome_features_for_risk(tiers: str) -> None:
    s = _s(tiers, routing_cascade_start_tier="medium")
    state = _state()
    # same ModelChoice value (medium, cascade_start) for a HIGH and a LOW risk agent
    select_model(
        state, "file_manager", settings=s, features=_f("file_manager", risk_tier=RiskTier.HIGH)
    )
    select_model(state, "researcher", settings=s, features=_f("researcher"))
    abstained = RoutingOutcome(success=True, adequacy=None, adequacy_checked=True)
    esc = record_outcome(state, "file_manager", abstained)
    assert esc is not None and esc.tier is ModelTier.FRONTIER


# 6 — routing never injects agent= (no new gateway agent-map / effort activation)
def test_route_kwargs_carry_features_not_agent(tiers: str) -> None:
    from prismal.routing.outcome import llm_route_kwargs

    state = _state()
    kwargs = llm_route_kwargs("coder", state, settings=_s(tiers))
    assert set(kwargs) == {"features", "state"}
    assert kwargs["features"].agent == "coder"


def test_state_without_features_is_3_15_path(tiers: str) -> None:
    from prismal.providers.registry import ProviderRegistry

    s = _s(tiers, default_model="default-x")
    llm = ProviderRegistry(settings=s).get_llm(state=_state())
    assert llm.model == "default-x"  # type: ignore[attr-defined]


def test_gateway_agent_map_pin_wins_over_routing(tiers: str) -> None:
    from prismal.providers.registry import ProviderRegistry

    s = _s(
        tiers,
        default_model="default-x",
        llm_gateways_enabled=True,
        llm_gateway_agent_map={"coder": "gw:openrouter/pinned/model"},
    )
    registry = ProviderRegistry(settings=s, gateway_router=None)
    with patch.object(ProviderRegistry, "_resolve_gateway", return_value=None):
        llm = registry.get_llm(features=_f("coder"), state=_state())
    assert llm.model == "default-x"  # type: ignore[attr-defined]


# 9 — only parse/validation errors escalate; transport errors do not
async def test_transport_error_does_not_escalate(tiers: str) -> None:
    from prismal.routing.outcome import track_outcome

    s = _s(tiers)
    state = _state()
    select_model(state, "coder", settings=s)

    async def boom() -> None:
        raise TimeoutError("upstream timeout")

    with pytest.raises(TimeoutError):
        await track_outcome(state, "coder", boom(), settings=s)
    assert "affinity" not in state["metadata"]["routing"]


async def test_validation_error_escalates(tiers: str) -> None:
    import json

    from prismal.routing.outcome import track_outcome

    s = _s(tiers)
    state = _state()
    select_model(state, "coder", settings=s)

    async def bad_json() -> None:
        json.loads("{not json")

    with pytest.raises(json.JSONDecodeError):
        await track_outcome(state, "coder", bad_json(), settings=s)
    assert state["metadata"]["routing"]["affinity"]["coder"]["tier"] == "medium"


# 10 — adequacy + usage are wired in-graph
class _Checker:
    def __init__(self, p: float | None) -> None:
        self.p = p
        self.calls: list[tuple[str, str]] = []

    async def check(
        self, request_summary: str, answer: str, *, risk_tier: RiskTier
    ) -> float | None:
        self.calls.append((request_summary, answer))
        return self.p


async def test_track_outcome_runs_adequacy_and_escalates(tiers: str) -> None:
    from langchain_core.messages import AIMessage

    from prismal.routing.outcome import track_outcome
    from prismal.routing.select import seed_routing_run

    s = _s(tiers)
    state = _state(text="summarise the report")
    checker = _Checker(0.2)
    seed_routing_run(state, s, adequacy=checker)  # type: ignore[arg-type]
    select_model(state, "coder", settings=s)

    async def work() -> AIMessage:
        return AIMessage(content="meh")

    await track_outcome(state, "coder", work(), settings=s)
    assert checker.calls == [("summarise the report", "meh")]
    assert state["metadata"]["routing"]["affinity"]["coder"]["tier"] == "medium"


async def test_track_outcome_meters_usage_for_savings(tiers: str) -> None:
    from langchain_core.messages import AIMessage

    from prismal.routing.outcome import track_outcome

    s = _s(
        tiers,
        budget_pricing={
            "small-m": {"input": 0.001, "output": 0.001},
            "frontier-m": {"input": 0.01, "output": 0.01},
        },
    )
    state = _state()
    select_model(state, "coder", settings=s)

    async def work() -> AIMessage:
        msg = AIMessage(content="ok")
        msg.usage_metadata = {"input_tokens": 1000, "output_tokens": 0, "total_tokens": 1000}
        return msg

    with patch("prismal.routing.select.OTelManager") as otel:
        await track_outcome(state, "coder", work(), settings=s)
    # frontier 0.01 − small 0.001 = 0.009 USD = 9000 micro-USD
    otel.return_value.increment_counter.assert_any_call(
        "routing_savings_usd", 9000, attributes={"policy": "cascade", "tier": "small"}
    )


def test_seed_uses_decision_runtime_for_adequacy(tiers: str) -> None:
    from prismal.routing import select as sel

    class _RT:
        cascade = object()

    s = _s(tiers, decisions_enabled=True)
    state = _state()
    with patch("prismal.decisions.runtime.get_decision_runtime", return_value=_RT()):
        sel.seed_routing_run(state, s)
    checker = sel.get_adequacy_checker(state)
    assert checker is not None

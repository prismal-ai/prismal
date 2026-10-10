"""End-to-end routing with fakes (Phase AMR — AMR6-07; CAS-001, BAT-001, OTEL-001).

Lives under ``tests/unit`` (marked ``e2e``) rather than ``tests/integration``:
the integration conftest stubs ``langchain_litellm``, which would hide the real
``ChatLiteLLM.model`` these tests assert on. No network: the ReAct loop is faked.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from prismal.agents import coder as coder_module
from prismal.agents.skynet.supervisor import SkynetSupervisor
from prismal.agents.skynet.types import SwarmOrder, SwarmPlan
from prismal.core.config import Settings
from prismal.routing.fanout import build_batch_policy
from prismal.routing.outcome import note_tool_error
from prismal.routing.select import clear_routing_run

pytestmark = pytest.mark.e2e


@pytest.fixture
def tiers(tmp_path: Path) -> str:
    p = tmp_path / "tiers.yaml"
    p.write_text(
        "tiers: {small: small-m, medium: medium-m, frontier: frontier-m}\n"
        "agents: {coder: frontier}\n",
        encoding="utf-8",
    )
    return str(p)


async def test_cascade_escalates_coder_on_tool_errors_and_sticks(
    tiers: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(
        adaptive_routing_enabled=True,
        routing_mode="enforce",
        routing_policy="cascade",
        routing_tier_models=tiers,
    )
    monkeypatch.setattr("prismal.core.config.get_settings", lambda: settings)
    monkeypatch.setattr("prismal.providers.registry.get_settings", lambda: settings)
    models: list[str] = []
    calls = {"n": 0}

    async def fake_react_loop(llm: Any, tools: Any, messages: Any, **_: Any) -> AIMessage:
        models.append(llm.bound.model if hasattr(llm, "bound") else llm.model)
        calls["n"] += 1
        if calls["n"] == 1:  # first turn: two failing tool calls
            note_tool_error()
            note_tool_error()
        return AIMessage(content="ok")

    state: dict[str, Any] = {
        "session_id": "e2e-cascade",
        "messages": [HumanMessage(content="fix the bug")],
        "metadata": {},
    }
    try:
        with (
            patch.object(coder_module, "react_loop", fake_react_loop),
            patch.object(coder_module, "get_tools_for_agent", return_value=[]),
            patch("prismal.routing.policies.cascade.OTelManager") as otel,
        ):
            await coder_module.coder_node(state)
            state["messages"].append(HumanMessage(content="and the next one"))
            await coder_module.coder_node(state)
    finally:
        clear_routing_run(state)
    assert models == ["small-m", "medium-m"]
    assert state["metadata"]["routing"]["affinity"]["coder"]["tier"] == "medium"
    otel.return_value.increment_counter.assert_any_call(
        "routing_escalations", attributes={"reason": "tool_errors"}
    )


async def test_skynet_round_assigned_by_real_batch_policy(tiers: str, tmp_path: Path) -> None:
    table = tmp_path / "pred.json"
    table.write_text(
        '{"version": "e2e", "cells": ['
        '{"intent": "_", "tier": "small", "successes": 9, "trials": 10},'
        '{"intent": "_", "tier": "frontier", "successes": 10, "trials": 10}]}',
        encoding="utf-8",
    )
    settings = Settings(
        adaptive_routing_enabled=True,
        routing_mode="enforce",
        routing_policy="batch",
        routing_tier_models=tiers,
        routing_predictor_path=str(table),
        routing_batch_alpha=0.8,
        budget_pricing={
            "small-m": {"input": 0.001, "output": 0.002},
            "medium-m": {"input": 0.003, "output": 0.006},
            "frontier-m": {"input": 0.01, "output": 0.03},
        },
    )
    policy = build_batch_policy(settings)
    assert policy is not None

    class _Audit:
        def log_event(self, event_type: str, payload: dict[str, object]) -> None:
            return None

    async def plan_fn(messages: list[dict[str, str]]) -> SwarmPlan:
        return SwarmPlan(
            goal="",
            orders=[SwarmOrder(order_id=f"o{i}", instruction=f"sub-task {i}") for i in range(4)],
            rationale="r",
        )

    supervisor = SkynetSupervisor(
        plan_fn=plan_fn,
        audit=_Audit(),  # type: ignore[arg-type]
        settings=settings,
        batch_policy=policy,
    )
    plan = await supervisor.plan("big goal")
    assigned = [o.context["routing_model"] for o in plan.orders]
    # Accuracy floor 0.8 is met by the cheap tier (p = 10/12 ≈ 0.83): all small.
    assert assigned == ["small-m"] * 4

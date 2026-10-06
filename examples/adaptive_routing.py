"""Adaptive model routing demo with fakes (Phase AMR).

Runs offline — no API key, no LLM call:

1. Parity: with the shipped tier map, every agent keeps ``default_model``.
2. Cascade: ``coder`` starts on the small tier; two tool errors escalate it to
   medium, and cache affinity keeps it there on the next turn.
3. Floors: a critical-risk turn is clamped to frontier whatever the policy says.
4. Budget: over the soft cap, the budget-aware policy drops one tier.
5. Batch assignment: four Skynet-style sub-orders are assigned jointly under an
   accuracy floor; the cheap tier is enough, so all go to it.

Run::

    python examples/adaptive_routing.py
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from langchain_core.messages import HumanMessage

from prismal.budget.types import Budget, BudgetStatus, Usage
from prismal.core.config import Settings
from prismal.decisions.types import RiskTier
from prismal.routing.fanout import assign_fanout, build_batch_policy, fanout_features
from prismal.routing.policies.budget_aware import BudgetAwarePolicy
from prismal.routing.policies.static import StaticTierPolicy
from prismal.routing.select import clear_routing_run, record_outcome, select_model
from prismal.routing.types import RoutingOutcome, TaskFeatures

TIERS = """\
tiers: {small: anthropic/claude-haiku-4-5, medium: anthropic/claude-sonnet-4-5,
        frontier: anthropic/claude-opus-4-5}
agents: {coder: frontier, researcher: medium}
risk_floor: {high: medium, critical: frontier}
"""

PREDICTOR = """\
{"version": "demo", "cells": [
  {"intent": "_", "tier": "small", "successes": 9, "trials": 10},
  {"intent": "_", "tier": "frontier", "successes": 10, "trials": 10}]}
"""


def _state(sid: str, text: str = "fix the failing test", **routing: object) -> dict[str, object]:
    return {
        "session_id": sid,
        "messages": [HumanMessage(content=text)],
        "metadata": {"routing": dict(routing)} if routing else {},
    }


def main() -> None:
    work = Path(tempfile.mkdtemp(prefix="amr-demo-"))
    tiers = work / "tiers.yaml"
    tiers.write_text(TIERS, encoding="utf-8")

    print("1. Parity map (shipped config/routing_tiers.yaml)")
    parity = Settings(default_model="claude-sonnet-4-5")
    static = StaticTierPolicy(settings=parity)
    for agent in ("coder", "file_manager", "researcher"):
        features = TaskFeatures(agent, None, 100, True, False)
        print(f"   {agent:13s} -> {static.select(features).model_ref}")

    print("2. Cascade + affinity")
    cascade = Settings(
        adaptive_routing_enabled=True,
        routing_mode="enforce",
        routing_policy="cascade",
        routing_tier_models=str(tiers),
    )
    state = _state("demo-cascade")
    first = select_model(state, "coder", settings=cascade)
    escalated = record_outcome(state, "coder", RoutingOutcome(success=True, tool_errors=2))
    state["messages"].append(HumanMessage(content="next request"))  # type: ignore[union-attr]
    later = select_model(state, "coder", settings=cascade)
    print(f"   turn 1: {first.tier} ({first.model_ref})")
    print(f"   escalation: {escalated.reason if escalated else None}")
    print(f"   turn 2 (affinity): {later.tier} ({later.model_ref})")
    clear_routing_run(state)

    print("3. Security floor")
    critical = _state("demo-floor", risk_tier="critical")
    choice = select_model(critical, "researcher", settings=cascade)
    print(f"   critical researcher -> {choice.tier} ({choice.reason})")
    clear_routing_run(critical)

    print("4. Budget soft cap")
    budget = BudgetAwarePolicy(StaticTierPolicy(settings=cascade), settings=cascade)
    status = BudgetStatus(False, True, False, "tokens", Usage(), Budget(max_tokens=1000))
    features = TaskFeatures("coder", None, 100, True, False, risk_tier=RiskTier.LOW)
    print(f"   coder over soft cap -> {budget.select(features, budget=status).tier}")

    print("5. Batch assignment (Skynet round)")
    predictor = work / "predictor.json"
    predictor.write_text(PREDICTOR, encoding="utf-8")
    batch = cascade.model_copy(
        update={
            "routing_policy": "batch",
            "routing_predictor_path": str(predictor),
            "budget_pricing": {
                "anthropic/claude-haiku-4-5": {"input": 0.001, "output": 0.005},
                "anthropic/claude-sonnet-4-5": {"input": 0.003, "output": 0.015},
                "anthropic/claude-opus-4-5": {"input": 0.005, "output": 0.025},
            },
        }
    )
    items = [(f"ord-{i}", fanout_features("skynet_worker", f"sub-task {i}")) for i in range(4)]
    choices = assign_fanout(items, build_batch_policy(batch), batch) or {}
    for item_id, c in choices.items():
        print(f"   {item_id} -> {c.tier} ({c.model_ref})")


if __name__ == "__main__":
    main()

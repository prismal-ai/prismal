"""MoA proposers via batch assignment, aggregator static (Phase AMR — SPEC-AMR-INT-002)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from prismal.agents.patterns.mixture_of_agents import MixtureOfAgents
from prismal.core.config import Settings
from prismal.routing.types import ModelChoice, ModelTier

_REG = "prismal.agents.patterns.mixture_of_agents.ProviderRegistry"


class _Assignment:
    def __init__(self, choices: dict[str, ModelChoice]) -> None:
        self.choices = choices


class _Policy:
    def __init__(self) -> None:
        self.items: list[Any] = []

    def assign(self, items: Any, **_: Any) -> _Assignment:
        self.items = list(items)
        return _Assignment(
            {
                it.item_id: ModelChoice(
                    model_ref=f"cheap-{n}", tier=ModelTier.SMALL, reason="batch", policy="batch"
                )
                for n, it in enumerate(self.items)
            }
        )


def _registry() -> tuple[MagicMock, list[str]]:
    used: list[str] = []
    registry = MagicMock()

    def get_llm(model: str | None = None, **_: Any) -> MagicMock:
        used.append(str(model))
        llm = MagicMock()
        resp = MagicMock()
        resp.content = f"out:{model}"
        llm.ainvoke = AsyncMock(return_value=resp)
        return llm

    registry.get_llm.side_effect = get_llm
    return registry, used


async def _run(settings: Settings, policy: _Policy | None) -> list[str]:
    registry, used = _registry()
    moa = MixtureOfAgents(
        ["p-a", "p-b"], aggregator_model="agg-m", settings=settings, batch_policy=policy
    )
    with patch(_REG, return_value=registry):
        await moa.generate("question", {"session_id": "s"})
    return used


async def test_proposers_assigned_aggregator_static() -> None:
    policy = _Policy()
    used = await _run(Settings(adaptive_routing_enabled=True, routing_mode="enforce"), policy)
    assert [it.item_id for it in policy.items] == ["proposer-0", "proposer-1"]
    assert sorted(used[:2]) == ["cheap-0", "cheap-1"]
    assert used[-1] == "agg-m"


async def test_routing_off_keeps_configured_proposers() -> None:
    policy = _Policy()
    used = await _run(Settings(), policy)
    assert policy.items == []
    assert sorted(used[:2]) == ["p-a", "p-b"]


async def test_shadow_keeps_configured_proposers() -> None:
    used = await _run(Settings(adaptive_routing_enabled=True), _Policy())
    assert sorted(used[:2]) == ["p-a", "p-b"]


async def test_mixture_node_threads_batch_policy_only_when_available() -> None:
    from langchain_core.messages import HumanMessage

    from prismal.agents.patterns import nodes

    seen: list[dict[str, Any]] = []

    class _Res:
        final_answer = "a"
        providers_used: list[str] = []

    def factory(**kwargs: Any) -> Any:
        seen.append(kwargs)
        m = MagicMock()
        m.generate = AsyncMock(return_value=_Res())
        return m

    state = {"messages": [HumanMessage(content="q")], "metadata": {}}
    sentinel = object()
    with patch.object(nodes, "build_batch_policy", return_value=None):
        await nodes.make_mixture_node(moa_factory=factory, proposer_models=["a"])(state)
    with patch.object(nodes, "build_batch_policy", return_value=sentinel):
        await nodes.make_mixture_node(moa_factory=factory, proposer_models=["a"])(state)
    assert seen[0] == {"proposer_models": ["a"]}
    assert seen[1] == {"proposer_models": ["a"], "batch_policy": sentinel}

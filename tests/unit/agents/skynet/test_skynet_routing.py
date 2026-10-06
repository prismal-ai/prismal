"""Skynet batch model assignment (Phase AMR — SPEC-AMR-INT-002, AMR6-02)."""

from __future__ import annotations

from typing import Any

from prismal.agents.skynet.supervisor import SkynetSupervisor
from prismal.agents.skynet.types import SwarmOrder, SwarmPlan
from prismal.agents.skynet.worker import SwarmWorker
from prismal.core.config import Settings
from prismal.routing.types import ModelChoice, ModelTier


class _Audit:
    def log_event(self, event_type: str, payload: dict[str, object]) -> None:
        return None


class _Assignment:
    def __init__(self, choices: dict[str, ModelChoice]) -> None:
        self.choices = choices


class _BatchPolicy:
    def __init__(self, *, boom: bool = False) -> None:
        self.boom = boom
        self.seen: list[Any] = []

    def assign(self, items: Any, **_: Any) -> _Assignment:
        if self.boom:
            raise RuntimeError("solver exploded")
        self.seen = list(items)
        return _Assignment(
            {
                it.item_id: ModelChoice(
                    model_ref=f"m-{it.item_id}",
                    tier=ModelTier.SMALL,
                    reason="batch",
                    policy="batch",
                )
                for it in items
            }
        )


def _orders(n: int) -> list[SwarmOrder]:
    return [SwarmOrder(order_id=f"ord-{i}", instruction=f"task {i}") for i in range(n)]


def _supervisor(settings: Settings, policy: _BatchPolicy | None) -> SkynetSupervisor:
    async def plan_fn(messages: list[dict[str, str]]) -> SwarmPlan:
        return SwarmPlan(goal="", orders=_orders(3), rationale="r")

    return SkynetSupervisor(
        plan_fn=plan_fn,
        audit=_Audit(),
        settings=settings,
        batch_policy=policy,  # type: ignore[arg-type]
    )


_ON = {"adaptive_routing_enabled": True, "routing_mode": "enforce"}


async def test_round_assigned_through_batch_policy() -> None:
    policy = _BatchPolicy()
    plan = await _supervisor(Settings(**_ON), policy).plan("goal")
    assert [it.item_id for it in policy.seen] == ["ord-0", "ord-1", "ord-2"]
    assert all(it.features.agent == "skynet_worker" for it in policy.seen)
    assert [o.context["routing_model"] for o in plan.orders] == ["m-ord-0", "m-ord-1", "m-ord-2"]


async def test_disabled_routing_leaves_orders_untouched() -> None:
    policy = _BatchPolicy()
    plan = await _supervisor(Settings(), policy).plan("goal")
    assert policy.seen == []
    assert all("routing_model" not in o.context for o in plan.orders)


async def test_shadow_mode_records_but_does_not_assign() -> None:
    policy = _BatchPolicy()
    settings = Settings(adaptive_routing_enabled=True, routing_mode="shadow")
    plan = await _supervisor(settings, policy).plan("goal")
    assert len(policy.seen) == 3
    assert all("routing_model" not in o.context for o in plan.orders)
    assert [o.context.get("routing_shadow_model") for o in plan.orders] == [
        "m-ord-0",
        "m-ord-1",
        "m-ord-2",
    ]


async def test_solver_failure_is_fail_open() -> None:
    plan = await _supervisor(Settings(**_ON), _BatchPolicy(boom=True)).plan("goal")
    assert all("routing_model" not in o.context for o in plan.orders)


async def test_worker_uses_assigned_model_when_enforced() -> None:
    seen: list[str | None] = []

    class _W(SwarmWorker):
        async def _invoke_default(self, messages: Any, *, model: str | None = None) -> Any:
            seen.append(model)

            class _R:
                content = "done"

            return _R()

    order = SwarmOrder(order_id="o", instruction="x", context={"routing_model": "cheap-m"})
    await _W(settings=Settings(**_ON)).execute(order)
    await _W(settings=Settings(skynet_worker_model="legacy-m")).execute(order)
    assert seen == ["cheap-m", "legacy-m"]


def test_builder_threads_batch_policy_from_settings() -> None:
    from unittest.mock import patch

    from prismal.agents.subgraphs.skynet import builder

    sentinel = object()
    with (
        patch.object(builder, "build_batch_policy", return_value=sentinel) as build,
        patch.object(builder, "SkynetSupervisor") as sup,
    ):
        builder.build_skynet_subgraph(Settings())
    build.assert_called_once()
    assert sup.call_args.kwargs["batch_policy"] is sentinel


async def test_replan_strips_stale_routing_model_when_unassigned() -> None:
    stale = [SwarmOrder(order_id="o", instruction="x", context={"routing_model": "old-m"})]
    sup = _supervisor(Settings(**_ON), _BatchPolicy(boom=True))
    plan = await sup.plan("goal", round=2, unmet=stale)
    assert "routing_model" not in plan.orders[0].context

"""Unit tests for the Skynet S+ supervisor extensions (SPEC-SP-SUP-001).

Role-aware ``plan()`` (specialists on/off) and injected shared ``CostMeter``.
All backends are faked — no LLM, no network.
"""

from __future__ import annotations

import pytest

from prismal.agents.skynet.roles import RoleRegistry, SpecialistRole
from prismal.agents.skynet.supervisor import SkynetSupervisor
from prismal.agents.skynet.types import SwarmOrder, SwarmPlan
from prismal.core.config import Settings


def _registry() -> RoleRegistry:
    return RoleRegistry(
        {
            "researcher": SpecialistRole(name="researcher", model="model-r"),
            "coder": SpecialistRole(name="coder", model="model-c"),
        }
    )


def _make_plan_fn(roles: list[str]):
    async def plan_fn(_messages: list[dict[str, str]]) -> SwarmPlan:
        orders = [
            SwarmOrder(order_id=f"ord-{i}", instruction=f"task {i}", role=role)
            for i, role in enumerate(roles, start=1)
        ]
        return SwarmPlan(goal="", orders=orders, rationale="split")

    return plan_fn


async def test_plan_assigns_roles_when_enabled() -> None:
    """Specialists enabled: known roles survive; an unknown tag falls back to worker."""
    settings = Settings(_env_file=None, skynet_specialists_enabled=True)  # type: ignore[call-arg]
    supervisor = SkynetSupervisor(
        plan_fn=_make_plan_fn(["researcher", "coder", "bogus"]),
        role_registry=_registry(),
        settings=settings,
    )

    plan = await supervisor.plan("do the work")

    assert [o.role for o in plan.orders] == ["researcher", "coder", "worker"]


async def test_plan_all_worker_when_disabled() -> None:
    """Specialists disabled: every order.role is coerced to 'worker' (Phase-S)."""
    settings = Settings(_env_file=None, skynet_specialists_enabled=False)  # type: ignore[call-arg]
    supervisor = SkynetSupervisor(
        plan_fn=_make_plan_fn(["researcher", "coder"]),
        role_registry=_registry(),
        settings=settings,
    )

    plan = await supervisor.plan("do the work")

    assert [o.role for o in plan.orders] == ["worker", "worker"]


async def test_plan_prompt_lists_known_roles_when_enabled() -> None:
    """The default planner prompt is told the registry's known roles when enabled."""

    class SpyPromptBuilder:
        def __init__(self) -> None:
            self.system: str = ""

        def build(self, *, system: str, user: str) -> list[dict[str, str]]:
            self.system = system
            return [{"role": "system", "content": system}, {"role": "user", "content": user}]

    spy = SpyPromptBuilder()
    settings = Settings(_env_file=None, skynet_specialists_enabled=True)  # type: ignore[call-arg]
    supervisor = SkynetSupervisor(
        plan_fn=_make_plan_fn(["researcher"]),
        role_registry=_registry(),
        prompt_builder=spy,  # type: ignore[arg-type]
        settings=settings,
    )

    await supervisor.plan("do the work")

    assert "researcher" in spy.system
    assert "coder" in spy.system


# ── SP3: shared meter + truthful budget ──────────────────────────────────────


def test_supervisor_accepts_injected_meter() -> None:
    """An injected shared meter is used verbatim; else the supervisor builds one."""
    from prismal.budget.meter import CostMeter

    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    shared = CostMeter(settings=settings)
    supervisor = SkynetSupervisor(meter=shared, settings=settings)
    assert supervisor.meter is shared

    # No meter injected → the supervisor still exposes its own (Phase S).
    default = SkynetSupervisor(settings=settings)
    assert default.meter is not shared


def test_budget_counts_worker_tokens() -> None:
    """Worker tokens recorded into the shared meter make enforce_token_budget truthful."""
    from prismal.budget.meter import CostMeter
    from prismal.budget.types import Usage
    from prismal.core.exceptions import SkynetBudgetExceeded

    settings = Settings(_env_file=None, skynet_token_budget=40)  # type: ignore[call-arg]
    shared = CostMeter(settings=settings)
    supervisor = SkynetSupervisor(meter=shared, settings=settings)

    # Below budget: no raise.
    supervisor.enforce_token_budget()

    # Simulate a worker recording 42 tokens into the SAME meter.
    shared.record(Usage(prompt_tokens=30, completion_tokens=12, calls=1))

    with pytest.raises(SkynetBudgetExceeded) as excinfo:
        supervisor.enforce_token_budget()
    assert excinfo.value.used == 42
    assert excinfo.value.limit == 40

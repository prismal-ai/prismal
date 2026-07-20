"""Unit tests for the Skynet S+ worker extensions (SPEC-SP-WRK-001).

Per-role model/persona/capabilities resolution (S+1). Metering (S+2) and remote
delegation (S+3) are covered in later phases. All backends faked — no LLM, no
network.
"""

from __future__ import annotations

from typing import Any

import pytest

from prismal.agents.skynet.roles import RoleRegistry, SpecialistRole
from prismal.agents.skynet.types import SwarmOrder
from prismal.agents.skynet.worker import SwarmWorker
from prismal.core.config import Settings


class SpyToolProvider:
    """ToolProviderPort spy recording the capabilities it was asked to resolve."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, list[str] | None]] = []

    def get_tools(self, *, agent_name: str, capabilities: list[str] | None = None) -> list[Any]:
        self.calls.append((agent_name, capabilities))
        return []


class SpyPromptBuilder:
    """SecurePromptBuilder spy capturing the system prompt it built."""

    def __init__(self) -> None:
        self.system: str = ""

    def build(self, *, system: str, user: str) -> list[dict[str, str]]:
        self.system = system
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]


class _FakeResponse:
    def __init__(self, content: str, usage_metadata: dict[str, int] | None = None) -> None:
        self.content = content
        self.usage_metadata = usage_metadata


class MeteringProviderRegistry:
    """ProviderRegistry stand-in whose LLM returns a fixed usage_metadata."""

    def __init__(self, *, settings: Any = None) -> None:
        self._settings = settings

    def get_llm(self, *, model: str | None = None) -> Any:
        class _LLM:
            async def ainvoke(self, _messages: list[Any]) -> _FakeResponse:
                return _FakeResponse(
                    "done", usage_metadata={"input_tokens": 30, "output_tokens": 12}
                )

        return _LLM()


class SpyProviderRegistry:
    """ProviderRegistry stand-in recording the model each get_llm requested."""

    models: list[str | None] = []

    def __init__(self, *, settings: Any = None) -> None:
        self._settings = settings

    def get_llm(self, *, model: str | None = None) -> Any:
        SpyProviderRegistry.models.append(model)

        class _LLM:
            async def ainvoke(self, _messages: list[Any]) -> _FakeResponse:
                return _FakeResponse("done")

        return _LLM()


@pytest.fixture(autouse=True)
def _reset_spy() -> None:
    SpyProviderRegistry.models = []


async def test_worker_uses_role_model_persona_and_caps(monkeypatch: pytest.MonkeyPatch) -> None:
    """Specialists on: worker resolves the role's model, persona, and capabilities."""
    monkeypatch.setattr("prismal.providers.registry.ProviderRegistry", SpyProviderRegistry)

    registry = RoleRegistry(
        {
            "researcher": SpecialistRole(
                name="researcher",
                model="model-researcher",
                capabilities=["research", "web"],
                persona="You are a meticulous research specialist.",
            )
        }
    )
    tools = SpyToolProvider()
    prompt = SpyPromptBuilder()
    settings = Settings(_env_file=None, skynet_specialists_enabled=True)  # type: ignore[call-arg]
    worker = SwarmWorker(
        role_registry=registry,
        tool_provider=tools,
        prompt_builder=prompt,  # type: ignore[arg-type]
        settings=settings,
    )

    result = await worker.execute(
        SwarmOrder(order_id="ord-1", instruction="find sources", role="researcher")
    )

    assert result.success is True
    # Capabilities from the role, not [order.role].
    assert tools.calls == [("skynet_worker", ["research", "web"])]
    # Persona is embedded in the system prompt.
    assert "meticulous research specialist" in prompt.system
    # The role's model was requested from the provider.
    assert SpyProviderRegistry.models == ["model-researcher"]


async def test_two_roles_resolve_two_models(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two roles with distinct models resolve to two distinct models."""
    monkeypatch.setattr("prismal.providers.registry.ProviderRegistry", SpyProviderRegistry)

    registry = RoleRegistry(
        {
            "researcher": SpecialistRole(name="researcher", model="model-r"),
            "coder": SpecialistRole(name="coder", model="model-c"),
        }
    )
    settings = Settings(_env_file=None, skynet_specialists_enabled=True)  # type: ignore[call-arg]
    worker = SwarmWorker(role_registry=registry, settings=settings)

    await worker.execute(SwarmOrder(order_id="ord-1", instruction="a", role="researcher"))
    await worker.execute(SwarmOrder(order_id="ord-2", instruction="b", role="coder"))

    assert SpyProviderRegistry.models == ["model-r", "model-c"]


async def test_worker_role_matches_phase_s(monkeypatch: pytest.MonkeyPatch) -> None:
    """Specialists off: role 'worker' is byte-for-byte Phase S — caps=[role], no persona."""
    monkeypatch.setattr("prismal.providers.registry.ProviderRegistry", SpyProviderRegistry)

    from prismal.agents.skynet.worker import _WORKER_SYSTEM

    tools = SpyToolProvider()
    prompt = SpyPromptBuilder()
    # A registry is present but specialists are OFF — the Phase-S path is used.
    registry = RoleRegistry({"researcher": SpecialistRole(name="researcher", model="model-r")})
    settings = Settings(_env_file=None, skynet_specialists_enabled=False)  # type: ignore[call-arg]
    worker = SwarmWorker(
        role_registry=registry,
        tool_provider=tools,
        prompt_builder=prompt,  # type: ignore[arg-type]
        settings=settings,
    )

    result = await worker.execute(SwarmOrder(order_id="ord-1", instruction="do", role="worker"))

    assert result.success is True
    # Phase-S capability resolution: [order.role].
    assert tools.calls == [("skynet_worker", ["worker"])]
    # No persona: the system prompt starts with the stock worker system text.
    assert prompt.system.startswith(_WORKER_SYSTEM)
    # No per-role model override → default (empty skynet_worker_model → None).
    assert SpyProviderRegistry.models == [None]


# ── SP3: metering + budget guard ─────────────────────────────────────────────


async def test_worker_records_usage_into_shared_meter(monkeypatch: pytest.MonkeyPatch) -> None:
    """The worker records its response into the injected shared meter (S+2)."""
    monkeypatch.setattr("prismal.providers.registry.ProviderRegistry", MeteringProviderRegistry)

    from prismal.budget.meter import CostMeter

    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    meter = CostMeter(settings=settings)
    worker = SwarmWorker(meter=meter, settings=settings)

    result = await worker.execute(SwarmOrder(order_id="ord-1", instruction="do"))

    assert result.success is True
    # Worker tokens landed in the shared meter …
    assert meter.usage.total_tokens == 42
    assert meter.usage.calls == 1
    # … and the per-worker usage is populated on the result.
    assert result.usage.total_tokens == 42


async def test_worker_no_meter_leaves_usage_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """No meter injected → WorkerResult.usage stays empty (default path)."""
    monkeypatch.setattr("prismal.providers.registry.ProviderRegistry", MeteringProviderRegistry)

    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    worker = SwarmWorker(settings=settings)

    result = await worker.execute(SwarmOrder(order_id="ord-1", instruction="do"))

    from prismal.budget.types import Usage

    assert result.usage == Usage()


class FakeGuard:
    """budget_guard_fn stand-in: soft returns False; hard raises."""

    def __init__(self, *, hard: bool = False, within: bool = True) -> None:
        self.hard = hard
        self.within = within
        self.calls = 0

    async def __call__(self, _ctx: dict[str, Any]) -> bool:
        self.calls += 1
        if self.hard:
            from prismal.core.exceptions import SkynetBudgetExceeded

            raise SkynetBudgetExceeded(used=100, limit=50)
        return self.within


async def test_budget_guard_soft_degrades_hard_raises() -> None:
    """A soft cap degrades the order; a hard cap raises SkynetBudgetExceeded (S+2)."""
    from prismal.core.exceptions import SkynetBudgetExceeded

    async def worker_fn(_messages: list[dict[str, str]]) -> str:
        return "should not run under a soft cap"

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    # Soft: guard returns False → the worker degrades (no LLM call), success=False.
    soft = FakeGuard(within=False)
    worker = SwarmWorker(worker_fn=worker_fn, budget_guard_fn=soft, settings=settings)
    result = await worker.execute(SwarmOrder(order_id="ord-1", instruction="do"))
    assert result.success is False
    assert soft.calls == 1
    assert "budget" in (result.error or "").lower()

    # Hard: guard raises → propagates out of the node (fan-out stops).
    hard = FakeGuard(hard=True)
    worker = SwarmWorker(worker_fn=worker_fn, budget_guard_fn=hard, settings=settings)
    with pytest.raises(SkynetBudgetExceeded):
        await worker.execute(SwarmOrder(order_id="ord-2", instruction="do"))


# ── SP4: remote workers over A2A ─────────────────────────────────────────────


def _remote_registry() -> RoleRegistry:
    return RoleRegistry(
        {
            "legal_review": SpecialistRole(
                name="legal_review",
                remote_agent="https://legal.example.com/.well-known/agent-card.json",
            )
        }
    )


async def test_remote_role_delegates_via_send_fn() -> None:
    """A remote-bound role routes to send_fn when remote workers are enabled (S+3)."""
    calls: list[tuple[str, str]] = []

    async def send_fn(role: Any, order: Any) -> str:
        calls.append((role.name, order.order_id))
        return "remote answer"

    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        skynet_specialists_enabled=True,
        skynet_remote_workers_enabled=True,
        a2a_enabled=True,
    )
    worker = SwarmWorker(
        role_registry=_remote_registry(),
        send_fn=send_fn,
        settings=settings,
    )

    result = await worker.execute(
        SwarmOrder(order_id="ord-1", instruction="review", role="legal_review")
    )

    assert result.success is True
    assert result.remote is True
    assert result.output == "remote answer"
    assert calls == [("legal_review", "ord-1")]


async def test_remote_failure_contained() -> None:
    """A send_fn raising A2AAgentUnavailable yields a contained failure, remote=True."""
    from prismal.core.exceptions import A2AAgentUnavailable

    async def send_fn(_role: Any, _order: Any) -> str:
        raise A2AAgentUnavailable("agent", "unreachable")

    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        skynet_specialists_enabled=True,
        skynet_remote_workers_enabled=True,
        a2a_enabled=True,
    )
    worker = SwarmWorker(
        role_registry=_remote_registry(),
        send_fn=send_fn,
        settings=settings,
    )

    result = await worker.execute(
        SwarmOrder(order_id="ord-1", instruction="review", role="legal_review")
    )

    assert result.success is False
    assert result.remote is True
    assert "unreachable" in (result.error or "")


async def test_remote_disabled_degrades_local(monkeypatch: pytest.MonkeyPatch) -> None:
    """With remote disabled, a remote-bound role degrades to local + a warning."""
    monkeypatch.setattr("prismal.providers.registry.ProviderRegistry", SpyProviderRegistry)

    from structlog.testing import capture_logs

    sent = False

    async def send_fn(_role: Any, _order: Any) -> str:
        nonlocal sent
        sent = True
        return "remote answer"

    # Remote workers OFF (a2a on) → the send_fn must NOT be called.
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        skynet_specialists_enabled=True,
        skynet_remote_workers_enabled=False,
        a2a_enabled=True,
    )
    worker = SwarmWorker(
        role_registry=_remote_registry(),
        send_fn=send_fn,
        settings=settings,
    )

    with capture_logs() as cap_logs:
        result = await worker.execute(
            SwarmOrder(order_id="ord-1", instruction="review", role="legal_review")
        )

    assert result.success is True
    assert result.remote is False  # ran locally
    assert sent is False
    assert any(log.get("event") == "skynet.remote_disabled" for log in cap_logs)

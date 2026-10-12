"""build_runtime memory composition (Phase MEM — MemoryService in RuntimeContext, TEN-001)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from prismal.composition import build_runtime, build_test_runtime
from prismal.core.config import Settings
from prismal.core.exceptions import RuntimeCompositionError
from prismal.memory.v2.adapters.in_memory import InMemoryBackend
from prismal.memory.v2.ports import MemoryPort
from prismal.memory.v2.service import MemoryService

from .test_composition import _reset_global_provider, patched_builders  # noqa: F401

pytestmark = [pytest.mark.unit, pytest.mark.usefixtures("patched_builders")]


def _settings(**kw: Any) -> Settings:
    base: dict[str, Any] = {"memory_v2_enabled": True, "memory_v2_backend": "memory"}
    base.update(kw)
    return Settings(**base)


async def test_none_when_disabled() -> None:
    ctx = await build_runtime(Settings(), org_id="acme", mode="context")
    assert ctx.memory is None
    await ctx.aclose()


async def test_enabled_composes_tenant_service() -> None:
    ctx = await build_runtime(_settings(), org_id="acme", mode="context")
    assert isinstance(ctx.memory, MemoryService)
    assert isinstance(ctx.memory, MemoryPort)
    assert ctx.memory.org_id == "acme"
    await ctx.aclose()


async def test_parallel_tenants_get_isolated_services() -> None:
    a = await build_runtime(_settings(), org_id="acme", mode="context")
    b = await build_runtime(_settings(), org_id="globex", mode="context")
    assert a.memory is not None and b.memory is not None
    assert a.memory is not b.memory
    assert a.memory.backend is not b.memory.backend
    await a.aclose()
    await b.aclose()


async def test_enabled_without_org_fails_closed() -> None:
    with pytest.raises(RuntimeCompositionError) as exc:
        await build_runtime(_settings(), mode="context")
    assert exc.value.port == "memory"


async def test_injected_backend_and_graph_win(tmp_path: Path) -> None:
    from prismal.memory.v2.graph.sqlite import SqliteMemoryGraph

    backend = InMemoryBackend()
    graph = SqliteMemoryGraph(tmp_path / "g.db")
    ctx = await build_runtime(
        _settings(memory_graph_backend="graphiti"),
        org_id="acme",
        mode="context",
        memory_backend=backend,
        memory_graph=graph,
    )
    assert ctx.memory is not None
    assert ctx.memory.backend is backend
    assert ctx.memory.graph is graph
    await ctx.aclose()


async def test_graphiti_without_injected_graph_fails_composition() -> None:
    with pytest.raises(RuntimeCompositionError):
        await build_runtime(
            _settings(memory_graph_backend="graphiti"), org_id="acme", mode="context"
        )


def test_test_runtime_accepts_memory() -> None:
    svc = MemoryService(org_id="acme", backend=InMemoryBackend(), settings=_settings())
    ctx = build_test_runtime(org_id="acme", memory=svc)
    assert ctx.memory is svc
    assert build_test_runtime().memory is None


async def test_decision_runtime_reaches_memory_through_the_cascade_bridge(tmp_path: Path) -> None:
    from prismal.memory.v2.decision_bridge import CascadeDecisionPort

    ctx = await build_runtime(
        _settings(
            decisions_enabled=True,
            decision_backend_order=["rule"],
            decision_calibration_dir=str(tmp_path),
        ),
        org_id="acme",
        mode="context",
    )
    assert ctx.memory is not None
    assert isinstance(ctx.memory.decisions, CascadeDecisionPort)
    await ctx.aclose()


@pytest.mark.parametrize(
    ("memory_mode", "gate", "expected"),
    [("shadow", "decision", "shadow"), ("enforce", "off", "enforce")],
)
async def test_cascade_bridge_mode_follows_memory_mode(
    tmp_path: Path, memory_mode: str, gate: str, expected: str
) -> None:
    # Review LOW10: shadow-mode memory decisions must not be recorded as enforced.
    from prismal.decisions.types import DecisionMode
    from prismal.memory.v2.decision_bridge import CascadeDecisionPort

    ctx = await build_runtime(
        _settings(
            memory_mode=memory_mode,
            memory_write_gate=gate,
            decisions_enabled=True,
            decision_backend_order=["rule"],
            decision_calibration_dir=str(tmp_path),
        ),
        org_id="acme",
        mode="context",
    )
    assert ctx.memory is not None
    assert isinstance(ctx.memory.decisions, CascadeDecisionPort)
    assert ctx.memory.decisions.mode is DecisionMode(expected)
    await ctx.aclose()


async def test_memory_hooks_reach_the_service_and_aclose_closes_the_graph() -> None:
    from prismal.memory.v2.service import MemoryHooks
    from prismal.memory.v2.types import MemoryType

    closed: list[bool] = []

    class Graph:
        async def upsert_node(self, record: Any) -> None: ...
        async def add_edge(self, edge: Any, *, scope: Any) -> None: ...
        async def invalidate_edge(self, *a: Any, **k: Any) -> None: ...
        async def neighbors(self, *a: Any, **k: Any) -> list[Any]:
            return []

        def close(self) -> None:
            closed.append(True)

    async def classify(candidate: Any) -> MemoryType:
        return MemoryType.SEMANTIC

    ctx = await build_runtime(
        _settings(memory_write_gate="llm"),
        org_id="acme",
        mode="context",
        memory_graph=Graph(),  # type: ignore[arg-type]
        memory_hooks=MemoryHooks(llm_classifier=classify),
    )
    assert ctx.memory is not None
    await ctx.aclose()
    assert closed == [True]


async def test_llm_gate_without_hooks_fails_composition() -> None:
    with pytest.raises(RuntimeCompositionError) as exc:
        await build_runtime(_settings(memory_write_gate="llm"), org_id="acme", mode="context")
    assert exc.value.port == "memory"


def test_bind_org_binds_the_tenant_memory_service_for_agent_nodes() -> None:
    from prismal.memory.v2.agent_prompt import get_memory_service

    svc = MemoryService(org_id="acme", backend=InMemoryBackend(), settings=_settings())
    ctx = build_test_runtime(org_id="acme", memory=svc)

    with ctx.bind_org():
        assert get_memory_service() is svc
    assert get_memory_service() is None

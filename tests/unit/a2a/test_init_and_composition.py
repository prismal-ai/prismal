"""Public exports + composition-root wiring for A2A (Phase I — SPEC-A2A-009)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from prismal.agents.extension import CompositeToolProvider, McpToolProvider, StubToolProvider
from prismal.composition.runtime import build_runtime
from prismal.core.config import Settings

pytestmark = pytest.mark.unit


class _FakeEmbeddings:
    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[1.0] for _ in texts]

    async def aembed_query(self, text: str) -> list[float]:
        return [1.0]


class _FakeAudit:
    def log_event(self, event_type: str, payload: dict[str, Any]) -> None: ...

    def log_node(
        self, *, node_name: str, session_id: str, status: str, state_hash: str, duration_ms: float
    ) -> None: ...

    def log_media(
        self, event: str, sha256: str, modality: str, size_bytes: int, duration_s: float | None
    ) -> None: ...


@pytest.fixture
def patched_builders(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch the heavy sub-builders with fast fakes (mirror composition tests)."""
    mcp_manager = MagicMock()
    mcp_manager.close = AsyncMock()

    def _make_provider(*_a: Any, **_k: Any) -> CompositeToolProvider:
        return CompositeToolProvider([McpToolProvider(mcp_manager), StubToolProvider()])

    ckpt = MagicMock()
    ckpt.aclose = AsyncMock()

    monkeypatch.setattr(
        "prismal.agents.extension.providers.build_default_tool_provider",
        AsyncMock(side_effect=_make_provider),
    )
    monkeypatch.setattr(
        "prismal.rag.embeddings.EmbeddingsFactory.create",
        MagicMock(return_value=_FakeEmbeddings()),
    )
    monkeypatch.setattr("prismal.agents.graph.build_checkpointer", AsyncMock(return_value=ckpt))
    monkeypatch.setattr("prismal.security.AuditLogger", MagicMock(return_value=_FakeAudit()))


@pytest.fixture(autouse=True)
def _reset_global_provider() -> Any:
    import prismal.agents.tool_registry as tr

    tr._provider = None
    yield
    tr._provider = None


class TestPublicExports:
    def test_reexports(self) -> None:
        import prismal.a2a as a2a

        for name in (
            "AgentCard",
            "AgentSkill",
            "A2ATask",
            "A2AMessage",
            "A2AArtifact",
            "A2APart",
            "A2AAuth",
            "build_agent_card",
            "A2AClient",
            "A2AConnectionManager",
            "A2AAgentNode",
            "A2AToolProvider",
            "A2AServerHandler",
            "AuthContext",
        ):
            assert hasattr(a2a, name), name


class _FakeGraph:
    async def ainvoke(self, state: dict[str, Any], config: Any = None) -> dict[str, Any]:
        return {"messages": state["messages"]}


@pytest.mark.usefixtures("patched_builders")
class TestCompositionWiring:
    async def test_disabled_leaves_handler_none(self) -> None:
        ctx = await build_runtime(Settings(), mode="context")
        assert ctx.a2a_handler is None
        await ctx.aclose()

    async def test_inbound_builds_handler_when_graph_supplied(self) -> None:
        settings = Settings(a2a_enabled=True, a2a_inbound_enabled=True)
        ctx = await build_runtime(settings, mode="context", graph=_FakeGraph())
        from prismal.a2a.server import A2AServerHandler

        assert isinstance(ctx.a2a_handler, A2AServerHandler)
        await ctx.aclose()

    async def test_outbound_composes_a2a_tools(self) -> None:
        from prismal.a2a.types import AgentCard, AgentSkill

        card = AgentCard(
            name="billing",
            description="d",
            url="https://billing.acme/a2a",
            version="1.0.0",
            skills=[AgentSkill(id="invoice", name="Invoice", description="d")],
        )
        settings = Settings(
            a2a_enabled=True, a2a_outbound_enabled=True, a2a_outbound_allowlist=["*.acme"]
        )
        ctx = await build_runtime(settings, mode="context", a2a_agents=[card])
        tools = ctx.tool_provider.get_tools(agent_name="researcher")
        assert any(t.name == "a2a__billing__invoice" for t in tools)
        await ctx.aclose()

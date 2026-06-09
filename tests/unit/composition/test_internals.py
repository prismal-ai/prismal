"""Coverage of composition internals: provider, closers, doubles, failures."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from prismal.agents.extension.providers import (
    CompositeToolProvider,
    FakeToolProvider,
    McpToolProvider,
    StubToolProvider,
)
from prismal.composition import VectorStoreProvider, build_runtime, build_test_runtime
from prismal.composition.runtime import (
    _checkpointer_closer,
    _mcp_closer,
)
from prismal.core.config import Settings
from prismal.core.exceptions import RuntimeCompositionError
from prismal.rag.vector_store_factory import VectorStoreFactory

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _reset_global_provider() -> Any:
    import prismal.agents.tool_registry as tr

    tr._provider = None
    yield
    tr._provider = None


# ── VectorStoreProvider (real factory path, R4) ───────────────────────────────


class TestVectorStoreProvider:
    def test_get_store_applies_tenant_via_factory(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen: list[str] = []
        store = MagicMock(name="store")

        def fake_create(settings: Any, collection_name: str) -> Any:
            seen.append(collection_name)
            return store

        monkeypatch.setattr(VectorStoreFactory, "create", fake_create)
        provider = VectorStoreProvider(Settings(), org_id="acme")
        result = provider.get_store("docs")
        assert result is store
        assert seen == ["docs_acme"]  # collection_for applied

    def test_get_store_uses_collection_base_when_unnamed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: list[str] = []
        monkeypatch.setattr(
            VectorStoreFactory,
            "create",
            lambda settings, collection_name: seen.append(collection_name) or MagicMock(),
        )
        VectorStoreProvider(Settings(), collection_base="kb").get_store()
        assert seen == ["kb"]

    @pytest.mark.asyncio
    async def test_aclose_closes_built_stores_async_and_sync(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async_store = MagicMock()
        async_store.aclose = AsyncMock()
        sync_store = SimpleNamespace(close=MagicMock())
        stores = iter([async_store, sync_store])
        monkeypatch.setattr(
            VectorStoreFactory, "create", lambda settings, collection_name: next(stores)
        )
        provider = VectorStoreProvider(Settings())
        provider.get_store("a")
        provider.get_store("b")
        await provider.aclose()
        async_store.aclose.assert_awaited()
        sync_store.close.assert_called_once()
        assert provider._built == []


# ── closers ───────────────────────────────────────────────────────────────────


class TestClosers:
    def test_mcp_closer_single_provider(self) -> None:
        mgr = MagicMock()
        mgr.close = AsyncMock()
        assert _mcp_closer(McpToolProvider(mgr)) is mgr.close

    def test_mcp_closer_none_without_mcp(self) -> None:
        assert _mcp_closer(FakeToolProvider({})) is None
        composite = CompositeToolProvider([StubToolProvider()])
        assert _mcp_closer(composite) is None

    @pytest.mark.asyncio
    async def test_checkpointer_closer_prefers_aclose(self) -> None:
        ckpt = SimpleNamespace(aclose=AsyncMock())
        await _checkpointer_closer(ckpt)()
        ckpt.aclose.assert_awaited()

    @pytest.mark.asyncio
    async def test_checkpointer_closer_async_conn_fallback(self) -> None:
        conn = MagicMock()
        conn.close = AsyncMock()
        await _checkpointer_closer(SimpleNamespace(conn=conn))()
        conn.close.assert_awaited()

    @pytest.mark.asyncio
    async def test_checkpointer_closer_sync_conn_fallback(self) -> None:
        conn = MagicMock()
        conn.close = MagicMock()  # sync close
        await _checkpointer_closer(SimpleNamespace(conn=conn))()
        conn.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_checkpointer_closer_no_op_without_close(self) -> None:
        # No aclose, no conn -> must not raise.
        await _checkpointer_closer(SimpleNamespace())()


# ── build_test_runtime doubles are callable ──────────────────────────────────


class TestTestRuntimeDoubles:
    @pytest.mark.asyncio
    async def test_doubles_are_callable(self) -> None:
        ctx = build_test_runtime()
        assert await ctx.embeddings.aembed_query("hi") == [2.0]
        assert await ctx.embeddings.aembed_documents(["ab", "x"]) == [[2.0], [1.0]]
        assert await ctx.checkpointer.aget({}) is None
        assert await ctx.checkpointer.aput({"k": 1}, None, {}) == {"k": 1}
        collected = [item async for item in ctx.checkpointer.alist(None)]
        assert collected == []
        # Audit doubles accept calls and discard.
        ctx.audit.log_event("e", {})
        ctx.audit.log_node(
            node_name="n", session_id="s", status="ok", state_hash="h", duration_ms=1.0
        )
        ctx.audit.log_media("e", "h", "image", 1, None)


# ── build_runtime failure branches per port ──────────────────────────────────


@pytest.fixture
def fast_builders(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    mgr = MagicMock()
    mgr.close = AsyncMock()
    monkeypatch.setattr(
        "prismal.agents.extension.providers.build_default_tool_provider",
        AsyncMock(return_value=CompositeToolProvider([McpToolProvider(mgr), StubToolProvider()])),
    )
    monkeypatch.setattr(
        "prismal.rag.embeddings.EmbeddingsFactory.create", MagicMock(return_value=MagicMock())
    )
    ckpt = MagicMock()
    ckpt.aclose = AsyncMock()
    monkeypatch.setattr("prismal.agents.graph.build_checkpointer", AsyncMock(return_value=ckpt))
    monkeypatch.setattr("prismal.security.AuditLogger", MagicMock())
    return {"mgr": mgr, "ckpt": ckpt}


@pytest.mark.asyncio
async def test_failure_tool_provider(
    fast_builders: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "prismal.agents.extension.providers.build_default_tool_provider",
        AsyncMock(side_effect=RuntimeError("down")),
    )
    with pytest.raises(RuntimeCompositionError) as ei:
        await build_runtime(Settings(), mode="context")
    assert ei.value.port == "tool_provider"


@pytest.mark.asyncio
async def test_failure_checkpointer_swallows_teardown_errors(
    fast_builders: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    # MCP closer raises during teardown -> must be swallowed, original error wins.
    fast_builders["mgr"].close = AsyncMock(side_effect=RuntimeError("teardown boom"))
    monkeypatch.setattr(
        "prismal.agents.graph.build_checkpointer", AsyncMock(side_effect=RuntimeError("no db"))
    )
    with pytest.raises(RuntimeCompositionError) as ei:
        await build_runtime(Settings(), mode="context")
    assert ei.value.port == "checkpointer"
    fast_builders["mgr"].close.assert_awaited()


@pytest.mark.asyncio
async def test_failure_audit(
    fast_builders: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "prismal.security.AuditLogger", MagicMock(side_effect=RuntimeError("audit fail"))
    )
    with pytest.raises(RuntimeCompositionError) as ei:
        await build_runtime(Settings(), mode="context")
    assert ei.value.port == "audit"


@pytest.mark.asyncio
async def test_aclose_swallows_closer_errors(fast_builders: dict[str, Any]) -> None:
    ctx = await build_runtime(Settings(), mode="context")
    # Make a registered closer raise; aclose must swallow and still complete.
    fast_builders["mgr"].close = AsyncMock(side_effect=RuntimeError("late boom"))
    ctx._closers.append(fast_builders["mgr"].close)
    await ctx.aclose()
    assert ctx._closed is True

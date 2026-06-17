"""Tests for ``build_runtime`` / ``RuntimeContext`` (Phase R — SPEC-CR-001..004)."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from prismal.agents.extension.ports import (
    AuditPort,
    CheckpointPort,
    EmbeddingsPort,
    ToolProviderPort,
    VectorStoreProviderPort,
)
from prismal.agents.extension.providers import (
    CompositeToolProvider,
    FakeToolProvider,
    McpToolProvider,
    StubToolProvider,
)
from prismal.composition import (
    RuntimeConfig,
    RuntimeContext,
    VectorStoreProvider,
    build_runtime,
    build_test_runtime,
)
from prismal.core.config import Settings
from prismal.core.exceptions import RuntimeCompositionError

pytestmark = pytest.mark.unit


# ── lightweight fakes so build_runtime stays fast (no model load, no I/O) ─────


class _FakeEmbeddings:
    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[1.0] for _ in texts]

    async def aembed_query(self, text: str) -> list[float]:
        return [1.0]


class _FakeAudit:
    """Real ``AuditPort`` conformer (MagicMock no longer satisfies Protocols)."""

    def log_event(self, event_type: str, payload: dict[str, Any]) -> None: ...

    def log_node(
        self, *, node_name: str, session_id: str, status: str, state_hash: str, duration_ms: float
    ) -> None: ...

    def log_media(
        self, event: str, sha256: str, modality: str, size_bytes: int, duration_s: float | None
    ) -> None: ...


def _fake_checkpointer() -> Any:
    ckpt = MagicMock()
    ckpt.aget = AsyncMock(return_value=None)
    ckpt.aput = AsyncMock(return_value={})
    ckpt.alist = MagicMock()
    ckpt.aclose = AsyncMock()
    return ckpt


@pytest.fixture
def patched_builders(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Patch the heavy sub-builders with fast fakes; expose the mocks."""
    mcp_manager = MagicMock()
    mcp_manager.close = AsyncMock()

    def _make_provider(*_a: Any, **_k: Any) -> CompositeToolProvider:
        # Real composite with a McpToolProvider so the MCP closer path runs.
        return CompositeToolProvider([McpToolProvider(mcp_manager), StubToolProvider()])

    tp_mock = AsyncMock(side_effect=_make_provider)
    emb_create = MagicMock(return_value=_FakeEmbeddings())
    ckpt = _fake_checkpointer()
    ckpt_mock = AsyncMock(return_value=ckpt)
    audit_cls = MagicMock(name="AuditLogger", return_value=_FakeAudit())

    monkeypatch.setattr("prismal.agents.extension.providers.build_default_tool_provider", tp_mock)
    monkeypatch.setattr("prismal.rag.embeddings.EmbeddingsFactory.create", emb_create)
    monkeypatch.setattr("prismal.agents.graph.build_checkpointer", ckpt_mock)
    monkeypatch.setattr("prismal.security.AuditLogger", audit_cls)

    return {
        "mcp_manager": mcp_manager,
        "tp_mock": tp_mock,
        "emb_create": emb_create,
        "ckpt": ckpt,
        "ckpt_mock": ckpt_mock,
        "audit_cls": audit_cls,
    }


@pytest.fixture(autouse=True)
def _reset_global_provider() -> Any:
    """Clear the injected global tool provider before/after each test."""
    import prismal.agents.tool_registry as tr

    tr._provider = None
    yield
    tr._provider = None


# ── R1/R2: composition produces five non-null ports ──────────────────────────


@pytest.mark.asyncio
async def test_build_runtime_composes_five_ports(patched_builders: dict[str, Any]) -> None:
    ctx = await build_runtime(Settings(), mode="context")
    assert isinstance(ctx, RuntimeContext)
    assert isinstance(ctx.tool_provider, ToolProviderPort)
    assert isinstance(ctx.vector_store_provider, VectorStoreProviderPort)
    assert isinstance(ctx.embeddings, EmbeddingsPort)
    assert isinstance(ctx.checkpointer, CheckpointPort)
    assert isinstance(ctx.audit, AuditPort)
    assert isinstance(ctx.config, RuntimeConfig)
    await ctx.aclose()


@pytest.mark.asyncio
async def test_non_duplication_uses_y_z_builders(patched_builders: dict[str, Any]) -> None:
    # DD-CR-001: build_runtime ORCHESTRATES the Y/Z builders, never reimplements.
    ctx = await build_runtime(Settings(), mode="context")
    assert patched_builders["tp_mock"].await_count == 1  # Phase Y builder used
    assert patched_builders["emb_create"].call_count == 1
    assert patched_builders["ckpt_mock"].await_count == 1
    # Phase Z: the vector store comes from the factory via the provider.
    assert isinstance(ctx.vector_store_provider, VectorStoreProvider)
    await ctx.aclose()


# ── R5: modes ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_global_mode_injects_singleton(patched_builders: dict[str, Any]) -> None:
    from prismal.agents.tool_registry import get_tool_provider

    ctx = await build_runtime(Settings(), mode="global")
    assert get_tool_provider() is ctx.tool_provider
    await ctx.aclose()


@pytest.mark.asyncio
async def test_context_mode_does_not_touch_globals(patched_builders: dict[str, Any]) -> None:
    from prismal.agents.tool_registry import get_tool_provider

    ctx = await build_runtime(Settings(), mode="context")
    assert get_tool_provider() is None
    await ctx.aclose()


@pytest.mark.asyncio
async def test_mode_defaults_to_settings_runtime_mode(patched_builders: dict[str, Any]) -> None:
    from prismal.agents.tool_registry import get_tool_provider

    ctx = await build_runtime(Settings(runtime_mode="context"))
    assert ctx.config.runtime_mode == "context"
    assert get_tool_provider() is None
    await ctx.aclose()


# ── R4: tenant resolution ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tenant_collection_resolution(patched_builders: dict[str, Any]) -> None:
    ctx = await build_runtime(Settings(), org_id="acme", mode="context")
    assert ctx.org_id == "acme"
    assert ctx.config.collection_name == "default_acme"
    assert ctx.vector_store_provider.org_id == "acme"
    await ctx.aclose()


# ── R6: lifecycle ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_aclose_closes_mcp_and_checkpointer(patched_builders: dict[str, Any]) -> None:
    ctx = await build_runtime(Settings(), mode="context")
    await ctx.aclose()
    patched_builders["mcp_manager"].close.assert_awaited()
    patched_builders["ckpt"].aclose.assert_awaited()


@pytest.mark.asyncio
async def test_aclose_is_idempotent(patched_builders: dict[str, Any]) -> None:
    ctx = await build_runtime(Settings(), mode="context")
    await ctx.aclose()
    await ctx.aclose()  # must not raise nor double-close
    assert patched_builders["mcp_manager"].close.await_count == 1


@pytest.mark.asyncio
async def test_async_context_manager(patched_builders: dict[str, Any]) -> None:
    async with await build_runtime(Settings(), mode="context") as ctx:
        assert isinstance(ctx, RuntimeContext)
    patched_builders["mcp_manager"].close.assert_awaited()


# ── R2: failure teardown ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_failure_raises_and_tears_down(
    patched_builders: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "prismal.rag.embeddings.EmbeddingsFactory.create",
        MagicMock(side_effect=RuntimeError("boom")),
    )
    with pytest.raises(RuntimeCompositionError) as ei:
        await build_runtime(Settings(), mode="context")
    assert ei.value.port == "embeddings"
    # Already-created tool provider must have been torn down.
    patched_builders["mcp_manager"].close.assert_awaited()


# ── isolation: parallel tenants do not share state ───────────────────────────


@pytest.mark.asyncio
async def test_context_isolation_parallel(patched_builders: dict[str, Any]) -> None:
    a, b = await asyncio.gather(
        build_runtime(Settings(), org_id="acme", mode="context"),
        build_runtime(Settings(), org_id="globex", mode="context"),
    )
    assert a.tool_provider is not b.tool_provider
    assert a.config.collection_name != b.config.collection_name
    assert a.config.collection_name == "default_acme"
    assert b.config.collection_name == "default_globex"
    await a.aclose()
    await b.aclose()


# ── R8 / SPEC-CR-004: test runtime with fakes ────────────────────────────────


class TestBuildTestRuntime:
    def test_five_ports_with_fakes(self) -> None:
        ctx = build_test_runtime()
        assert isinstance(ctx.tool_provider, ToolProviderPort)
        assert isinstance(ctx.vector_store_provider, VectorStoreProviderPort)
        assert isinstance(ctx.embeddings, EmbeddingsPort)
        assert isinstance(ctx.checkpointer, CheckpointPort)
        assert isinstance(ctx.audit, AuditPort)

    def test_fixed_store_short_circuits_factory(self) -> None:
        from prismal.rag.vector_store_factory import FakeVectorStore

        store = FakeVectorStore()
        ctx = build_test_runtime(vector_store=store, org_id="acme")
        # Same fixed store returned regardless of collection name.
        assert ctx.vector_store_provider.get_store("docs") is store
        assert ctx.vector_store_provider.get_store("other") is store

    def test_tenant_collection_name(self) -> None:
        ctx = build_test_runtime(org_id="acme")
        assert ctx.config.collection_name == "default_acme"

    def test_custom_tool_provider(self) -> None:
        tp = FakeToolProvider({})
        ctx = build_test_runtime(tool_provider=tp)
        assert ctx.tool_provider is tp

    @pytest.mark.asyncio
    async def test_aclose_is_noop(self) -> None:
        ctx = build_test_runtime()
        await ctx.aclose()  # no closers registered, must not raise
        async with ctx:
            pass


# ── backward-compat: Phase Y/Z injection still works without build_runtime ───


def test_backward_compat_set_tool_provider_standalone() -> None:
    from prismal.agents.tool_registry import get_tool_provider, set_tool_provider

    tp = FakeToolProvider({})
    set_tool_provider(tp)
    assert get_tool_provider() is tp


def test_backward_compat_vector_store_factory_standalone() -> None:
    from prismal.rag.vector_store_factory import FakeVectorStore

    store = FakeVectorStore()
    assert store.collection_name == "fake"


# ── Identity composition (Phase IDN — ID6-04) ────────────────────────────────


@pytest.mark.asyncio
async def test_identity_ports_none_when_disabled(patched_builders: dict[str, Any]) -> None:
    ctx = await build_runtime(Settings(), mode="context")
    assert ctx.identity_provider is None
    assert ctx.credential_vault is None
    assert ctx.policy_engine is None
    await ctx.aclose()


@pytest.mark.asyncio
async def test_identity_ports_composed_when_enabled(patched_builders: dict[str, Any]) -> None:
    from prismal.agents.extension.ports import CredentialVaultPort, IdentityPort, PolicyPort

    ctx = await build_runtime(Settings(identity_enabled=True), mode="context")
    assert isinstance(ctx.identity_provider, IdentityPort)
    assert isinstance(ctx.credential_vault, CredentialVaultPort)
    assert isinstance(ctx.policy_engine, PolicyPort)
    await ctx.aclose()

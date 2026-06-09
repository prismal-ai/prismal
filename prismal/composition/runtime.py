"""Runtime composition root (Phase R — SPEC-CR-001..008).

A single composition *facade* that assembles every core port from ``settings``
plus an optional tenant (``org_id``): tool provider (Phase Y), vector store
(Phase Z), embeddings, checkpointer, and audit. The host (``prismal-server``)
calls :func:`build_runtime` once in its lifespan; ``prismal-dashboard`` reads the
same config sources (see :mod:`prismal.composition.config_sources`).

Guiding principle — **orchestrate, do not reimplement**: ``build_runtime`` calls
``build_default_tool_provider`` (Y), :class:`VectorStoreFactory` (Z),
``EmbeddingsFactory``, ``build_checkpointer``, and ``AuditLogger``; it never
duplicates their logic.

Two modes (``settings.runtime_mode``):

* **global** — injects the tool provider as a process singleton
  (``set_tool_provider``); the graph picks it up with no extra wiring.
* **context** — keeps every port inside the returned :class:`RuntimeContext` for
  per-session / per-tenant use, leaving the process globals untouched.

The feature is **additive and opt-in**: callers that keep using
``set_tool_provider`` / ``VectorStoreFactory`` directly are unaffected.

Phase Z note: Phase Z ships a *factory* (``VectorStoreFactory``) rather than a
process singleton or a graph-bound provider, so the vector store is always
carried in the :class:`RuntimeContext` via :class:`VectorStoreProvider` (there is
no ``set_vector_store_provider`` global to inject in global mode). Consumers that
need a tenant store call ``ctx.vector_store_provider.get_store(...)``.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from prismal.composition.config_sources import (
    DEFAULT_COLLECTION_BASE,
    apply_org_overrides,
    collection_for,
    resolve_vector_store,
)
from prismal.core.exceptions import RuntimeCompositionError
from prismal.core.logging import get_logger

if TYPE_CHECKING:
    from pathlib import Path
    from types import TracebackType

    from prismal.agents.extension.ports import (
        AuditPort,
        CheckpointPort,
        EmbeddingsPort,
        ToolProviderPort,
        VectorStorePort,
    )
    from prismal.core.config import Settings

logger = get_logger("prismal.composition")

#: A teardown callback — sync or async; run by ``RuntimeContext.aclose``.
type Closer = Callable[[], Awaitable[None] | None]


# ── Vector store provider (Phase Z is factory-based; SPEC-CR-002) ─────────────


class VectorStoreProvider:
    """Build :class:`VectorStorePort` instances per logical collection (R4).

    Wraps :class:`~prismal.rag.vector_store_factory.VectorStoreFactory`, applying
    the runtime's ``org_id`` to every collection via :func:`collection_for` so a
    tenant's RAG and memory collections are isolated and consistently named.

    Conforms to :class:`~prismal.agents.extension.ports.VectorStoreProviderPort`.

    Args:
        settings: Effective (per-tenant) settings.
        org_id: Tenant id applied as a collection suffix (``None`` = single tenant).
        collection_base: Default logical collection when ``get_store`` is called
            without an explicit name.
        fixed: Test escape hatch — when set, every ``get_store`` returns it
            verbatim (used by :func:`build_test_runtime` with ``FakeVectorStore``).
    """

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        org_id: str | None = None,
        collection_base: str = DEFAULT_COLLECTION_BASE,
        fixed: VectorStorePort | None = None,
    ) -> None:
        self._settings = settings
        self._org_id = org_id
        self._collection_base = collection_base
        self._fixed = fixed
        self._built: list[VectorStorePort] = []

    @property
    def org_id(self) -> str | None:
        """The tenant this provider scopes collections to."""
        return self._org_id

    def get_store(self, collection_name: str | None = None) -> VectorStorePort:
        """Return a vector store for *collection_name* (tenant-suffixed).

        When ``collection_name`` is ``None`` the provider's ``collection_base``
        is used. The effective name is ``collection_for(base, org_id)``.
        """
        if self._fixed is not None:
            return self._fixed
        from prismal.rag.vector_store_factory import VectorStoreFactory

        base = collection_name if collection_name is not None else self._collection_base
        resolved = collection_for(base, self._org_id)
        store = VectorStoreFactory.create(self._settings, resolved)
        self._built.append(store)
        return store

    async def aclose(self) -> None:
        """Best-effort close of any built stores (server backends may hold conns)."""
        for store in self._built:
            closer = getattr(store, "aclose", None) or getattr(store, "close", None)
            if closer is None:
                continue
            try:
                result = closer()
                if inspect.isawaitable(result):
                    await result
            except Exception as exc:  # pragma: no cover - defensive teardown
                logger.warning("composition.vstore_close_error", error=str(exc))
        self._built.clear()


# ── Config / Context value objects (SPEC-CR-001, SPEC-CR-002) ─────────────────


@dataclass(frozen=True)
class RuntimeConfig:
    """Immutable, resolved view of a runtime's config (optionally per tenant).

    Sensitive fields (DSNs, API keys) are intentionally **not** included in
    cleartext — they stay referenced from ``settings`` (SPEC-CR-001).
    """

    org_id: str | None
    runtime_mode: Literal["global", "context"]
    mcp_config_path: Path | None
    vector_store_backend: str
    collection_base: str
    collection_name: str


@dataclass
class RuntimeContext:
    """Groups the composed ports for one runtime (SPEC-CR-002).

    A pure container — it adds no business behavior; the RAG/agent patterns keep
    consuming the ports directly. Provides a coordinated :meth:`aclose` and works
    as an async context manager.
    """

    config: RuntimeConfig
    tool_provider: ToolProviderPort
    vector_store_provider: VectorStoreProvider
    embeddings: EmbeddingsPort
    checkpointer: CheckpointPort
    audit: AuditPort
    org_id: str | None = None
    _closers: list[Closer] = field(default_factory=list, repr=False)
    _closed: bool = field(default=False, repr=False)

    async def aclose(self) -> None:
        """Release composed resources (MCP, vector store, checkpointer).

        Idempotent: safe to call more than once, never raises on already-closed
        resources. Closers run in reverse registration order (LIFO).
        """
        if self._closed:
            return
        self._closed = True
        for closer in reversed(self._closers):
            try:
                result = closer()
                if inspect.isawaitable(result):
                    await result
            except Exception as exc:  # pragma: no cover - defensive teardown
                logger.warning("composition.teardown_error", error=str(exc))
        logger.info(
            "composition.runtime_teardown",
            mode=self.config.runtime_mode,
            org_id=self.org_id,
        )

    async def __aenter__(self) -> RuntimeContext:
        """Enter the async context, returning self."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Exit the async context, tearing the runtime down."""
        await self.aclose()


# ── Composition root (SPEC-CR-003) ───────────────────────────────────────────


def _mcp_closer(tool_provider: ToolProviderPort) -> Closer | None:
    """Return an async closer for the MCP manager inside *tool_provider*, if any."""
    from prismal.agents.extension.providers import CompositeToolProvider, McpToolProvider

    candidates = (
        tool_provider.providers
        if isinstance(tool_provider, CompositeToolProvider)
        else (tool_provider,)
    )
    for provider in candidates:
        if isinstance(provider, McpToolProvider):
            manager = provider._manager  # sibling adapter teardown
            return manager.close
    return None


def _checkpointer_closer(checkpointer: Any) -> Closer:
    """Return a best-effort async closer for *checkpointer* (sqlite conn / aclose)."""

    async def _close() -> None:
        aclose = getattr(checkpointer, "aclose", None)
        if callable(aclose):
            result = aclose()
            if inspect.isawaitable(result):
                await result
            return
        conn = getattr(checkpointer, "conn", None)
        if conn is not None and hasattr(conn, "close"):
            result = conn.close()
            if inspect.isawaitable(result):
                await result

    return _close


async def _run_closers(closers: list[Closer]) -> None:
    """Run teardown closers, swallowing errors (used on composition failure)."""
    for closer in reversed(closers):
        try:
            result = closer()
            if inspect.isawaitable(result):
                await result
        except Exception as exc:  # pragma: no cover - defensive teardown
            logger.warning("composition.partial_teardown_error", error=str(exc))


async def build_runtime(
    settings: Settings | None = None,
    *,
    org_id: str | None = None,
    overrides: dict[str, Any] | None = None,
    mode: Literal["global", "context"] | None = None,
    collection_base: str = DEFAULT_COLLECTION_BASE,
    mcp_config_path: Path | None = None,
) -> RuntimeContext:
    """Compose every core port in one call (SPEC-CR-003).

    Reuses the Phase Y/Z builders and the existing factories; on any sub-port
    failure it tears down whatever was already created and raises
    :class:`~prismal.core.exceptions.RuntimeCompositionError`.

    Args:
        settings: Base settings. ``None`` resolves via ``get_settings()``.
        org_id: Tenant id; isolates vector collections (RAG + memory) per tenant.
        overrides: Per-tenant settings overrides (applied as a validated copy).
        mode: ``"global"`` injects singletons; ``"context"`` keeps ports in the
            returned context. ``None`` uses ``settings.runtime_mode``.
        collection_base: Default logical collection for the vector store provider.
        mcp_config_path: Override for the MCP server catalogue.

    Returns:
        A :class:`RuntimeContext` with five non-null ports; usable as
        ``async with``.
    """
    from prismal.core.config import get_settings

    base_settings = settings if settings is not None else get_settings()
    eff = apply_org_overrides(base_settings, org_id, overrides)
    resolved_mode: Literal["global", "context"] = mode if mode is not None else eff.runtime_mode

    backend, _ = resolve_vector_store(eff, org_id)
    cfg = RuntimeConfig(
        org_id=org_id,
        runtime_mode=resolved_mode,
        mcp_config_path=mcp_config_path,
        vector_store_backend=backend,
        collection_base=collection_base,
        collection_name=collection_for(collection_base, org_id),
    )

    closers: list[Closer] = []
    try:
        # 1. Tools (Phase Y) — the only async sub-builder (connects MCP).
        try:
            from prismal.agents.extension.providers import build_default_tool_provider

            tool_provider = await build_default_tool_provider(eff, mcp_config_path=mcp_config_path)
        except Exception as exc:
            raise RuntimeCompositionError("tool_provider", str(exc)) from exc
        mcp_closer = _mcp_closer(tool_provider)
        if mcp_closer is not None:
            closers.append(mcp_closer)

        # 2. Vector store (Phase Z) — factory-backed provider, tenant-scoped.
        try:
            vstore_provider = VectorStoreProvider(
                eff, org_id=org_id, collection_base=collection_base
            )
        except Exception as exc:
            raise RuntimeCompositionError("vector_store", str(exc)) from exc
        closers.append(vstore_provider.aclose)

        # 3. Embeddings.
        try:
            from prismal.rag.embeddings import EmbeddingsFactory

            embeddings = EmbeddingsFactory.create(eff)
        except Exception as exc:
            raise RuntimeCompositionError("embeddings", str(exc)) from exc

        # 4. Checkpointer.
        try:
            from prismal.agents.graph import build_checkpointer

            checkpointer = await build_checkpointer(eff.db_url or None)
        except Exception as exc:
            raise RuntimeCompositionError("checkpointer", str(exc)) from exc
        closers.append(_checkpointer_closer(checkpointer))

        # 5. Audit.
        try:
            from prismal.security import AuditLogger

            audit = AuditLogger()
        except Exception as exc:
            raise RuntimeCompositionError("audit", str(exc)) from exc

        # 6. Mode: global injects the singleton; context leaves globals untouched.
        if resolved_mode == "global":
            from prismal.agents.tool_registry import set_tool_provider

            set_tool_provider(tool_provider)
    except Exception:
        await _run_closers(closers)
        raise

    ctx = RuntimeContext(
        config=cfg,
        tool_provider=tool_provider,
        vector_store_provider=vstore_provider,
        embeddings=embeddings,
        checkpointer=checkpointer,
        audit=audit,
        org_id=org_id,
    )
    ctx._closers = closers
    logger.info(
        "composition.runtime_built",
        mode=resolved_mode,
        org_id=org_id,
        vector_store_backend=backend,
        collection=cfg.collection_name,
    )
    return ctx


# ── Test runtime (SPEC-CR-004) ───────────────────────────────────────────────


class _FakeEmbeddings:
    """Deterministic, I/O-free ``EmbeddingsPort`` double for tests."""

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        """Return a one-dim embedding (text length) per text."""
        return [[float(len(t))] for t in texts]

    async def aembed_query(self, text: str) -> list[float]:
        """Return a one-dim embedding (text length)."""
        return [float(len(text))]


class _FakeCheckpointer:
    """No-op ``CheckpointPort`` double for tests."""

    async def aget(self, config: dict[str, Any]) -> Any | None:
        """Return ``None`` (no stored checkpoint)."""
        del config
        return None

    async def aput(
        self, config: dict[str, Any], checkpoint: Any, metadata: dict[str, Any], *args: Any
    ) -> Any:
        """Echo back *config* (no persistence)."""
        del checkpoint, metadata, args
        return config

    async def alist(self, config: dict[str, Any] | None, *args: Any, **kwargs: Any) -> Any:
        """Yield nothing."""
        del config, args, kwargs
        return
        yield  # pragma: no cover - makes this an async generator


class _FakeAudit:
    """No-op ``AuditPort`` double for tests."""

    def log_event(self, event_type: str, payload: dict[str, Any]) -> None:
        """Discard the event."""

    def log_node(
        self, *, node_name: str, session_id: str, status: str, state_hash: str, duration_ms: float
    ) -> None:
        """Discard the node record."""

    def log_media(
        self, event: str, sha256: str, modality: str, size_bytes: int, duration_s: float | None
    ) -> None:
        """Discard the media record."""


def build_test_runtime(
    *,
    tool_provider: ToolProviderPort | None = None,
    vector_store: VectorStorePort | None = None,
    embeddings: EmbeddingsPort | None = None,
    org_id: str | None = None,
    collection_base: str = DEFAULT_COLLECTION_BASE,
) -> RuntimeContext:
    """Compose a deterministic ``RuntimeContext`` with fakes (SPEC-CR-004).

    No I/O, no connections, no global injection; :meth:`RuntimeContext.aclose`
    is effectively a no-op. Defaults: ``FakeToolProvider`` (Y), ``FakeVectorStore``
    (Z), and in-module embeddings/checkpointer/audit doubles.
    """
    from prismal.agents.extension.providers import FakeToolProvider
    from prismal.rag.vector_store_factory import FakeVectorStore

    tp: ToolProviderPort = tool_provider if tool_provider is not None else FakeToolProvider({})
    store: VectorStorePort = vector_store if vector_store is not None else FakeVectorStore()
    # The fixed store short-circuits the factory, so the provider never touches
    # settings — passing None keeps the test runtime fully I/O-free.
    provider = VectorStoreProvider(
        None, org_id=org_id, collection_base=collection_base, fixed=store
    )

    cfg = RuntimeConfig(
        org_id=org_id,
        runtime_mode="context",
        mcp_config_path=None,
        vector_store_backend="fake",
        collection_base=collection_base,
        collection_name=collection_for(collection_base, org_id),
    )
    return RuntimeContext(
        config=cfg,
        tool_provider=tp,
        vector_store_provider=provider,
        embeddings=embeddings if embeddings is not None else _FakeEmbeddings(),
        checkpointer=_FakeCheckpointer(),
        audit=_FakeAudit(),
        org_id=org_id,
    )


__all__ = [
    "RuntimeConfig",
    "RuntimeContext",
    "VectorStoreProvider",
    "build_runtime",
    "build_test_runtime",
]

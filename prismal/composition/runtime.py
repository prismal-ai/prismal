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
from contextlib import contextmanager
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
    from collections.abc import Iterator
    from datetime import datetime
    from pathlib import Path
    from types import TracebackType

    from prismal.agents.extension.ports import (
        AuditPort,
        CheckpointPort,
        CredentialVaultPort,
        EmbeddingsPort,
        IdentityPort,
        ObservabilityPort,
        PolicyPort,
        ToolProviderPort,
        VectorStorePort,
    )
    from prismal.composition.host_surface import (
        A2ARouteSpec,
        AuditChainStatus,
        AuditPageView,
        BudgetStatusView,
        DecisionsReportView,
        QuotaWindowView,
        RoutingReportView,
    )
    from prismal.core.config import Settings
    from prismal.core.config_source import ConfigSourcePort
    from prismal.decisions.runtime import DecisionRuntime
    from prismal.memory.v2.ports import MemoryBackendPort, MemoryGraphPort
    from prismal.memory.v2.service import MemoryHooks, MemoryService
    from prismal.providers.gateways.router import GatewayRouter

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
    # Agent identity & access governance (Phase IDN) — None unless identity_enabled.
    identity_provider: IdentityPort | None = None
    credential_vault: CredentialVaultPort | None = None
    policy_engine: PolicyPort | None = None
    # A2A interop (Phase I) — the inbound handler the host mounts; None unless
    # a2a_enabled + a2a_inbound_enabled and a graph was supplied to build_runtime.
    a2a_handler: Any | None = None
    # Observability integration (Phase OBS) — None unless observability_enabled.
    observability: ObservabilityPort | None = None
    # LLM Gateway Providers (Phase GW) — None unless llm_gateways_enabled.
    gateways: GatewayRouter | None = None
    # Typed Decision Models (Phase DEC) — None unless decisions_enabled.
    decisions: DecisionRuntime | None = None
    # Agentic memory (Phase MEM) — one tenant's MemoryService; None unless memory_v2_enabled.
    memory: MemoryService | None = None
    # Host read surface (HRS) — the runtime's effective settings (None ⇒ process).
    settings: Settings | None = field(default=None, repr=False)
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

    # ── Host read surface (HRS — SPEC-HRS-*; read-only, never raise) ─────────
    # Thin delegates to prismal.composition.host_surface. Each one is scoped to
    # ``self.org_id``, never mutates engine state, and on any internal failure
    # returns its documented fallback and increments
    # ``prismal.host_surface_errors_total{method}`` (SPEC-HRS-SUR-002).

    @contextmanager
    def bind_org(self) -> Iterator[None]:
        """Attribute the DEC/AMR report counters emitted in this block to ``self.org_id``.

        Wraps :func:`prismal.monitoring.host_stats.use_org`; a host wraps each
        graph run with it so :meth:`decisions_report` / :meth:`routing_report` of
        this tenant see the run. ``org_id=None`` binds "no tenant". Sync, nests,
        and restores the previous binding on exit (also on error). It also binds
        :attr:`memory` so agent nodes can recall it (Phase MEM,
        :func:`prismal.memory.v2.agent_prompt.use_memory_service`).
        """
        from prismal.memory.v2.agent_prompt import use_memory_service
        from prismal.monitoring.host_stats import use_org

        with use_org(self.org_id), use_memory_service(self.memory):
            yield

    def budget_status(self, *, thread_id: str | None = None) -> BudgetStatusView:
        """Budget verdict for the tenant / a thread's run (SPEC-HRS-BUD-001)."""
        from prismal.composition import host_surface as hs

        try:
            return hs.budget_status(
                hs.resolve_settings(self.settings), org_id=self.org_id, thread_id=thread_id
            )
        except Exception as exc:
            _surface_error("budget_status", exc)
            return hs.BudgetStatusView(
                enabled=False,
                scope="turn",
                status="unknown",
                breached_dimension=None,
                soft_ratio=None,
                hard_cap=None,
                limits=None,
                consumed=None,
                remaining=None,
            )

    async def quota_windows(self) -> list[QuotaWindowView]:
        """Gateway quota windows; a failing gateway is omitted (SPEC-HRS-QTA-001)."""
        from prismal.composition import host_surface as hs

        try:
            return await hs.quota_windows(self.gateways, on_error=_surface_error)
        except Exception as exc:
            _surface_error("quota_windows", exc)
            return []

    def audit_page(
        self, *, page: int = 1, page_size: int = 50, since: datetime | None = None
    ) -> AuditPageView:
        """Newest-first, tenant-scoped, hash-first audit page (SPEC-HRS-AUD-001)."""
        from prismal.composition import host_surface as hs

        try:
            return hs.audit_page(
                self.audit, org_id=self.org_id, page=page, page_size=page_size, since=since
            )
        except Exception as exc:
            _surface_error("audit_page", exc)
            return hs.AuditPageView(
                page=max(1, page), page_size=min(200, max(1, page_size)), total=None, entries=()
            )

    def verify_audit_chain(self, *, max_records: int = 10_000) -> AuditChainStatus:
        """Recompute the audit hash chain (SPEC-HRS-AUD-002)."""
        from prismal.composition import host_surface as hs

        try:
            return hs.verify_audit_chain(self.audit, max_records=max_records)
        except Exception as exc:
            _surface_error("verify_audit_chain", exc)
            return hs.AuditChainStatus(status="unverified", checked=0, first_broken_seq=None)

    def decisions_report(
        self, *, since: datetime | None = None, until: datetime | None = None
    ) -> DecisionsReportView:
        """DEC shadow agreement + outcome counts per integration (SPEC-HRS-DEC-001)."""
        from prismal.composition import host_surface as hs

        try:
            return hs.decisions_report(
                hs.resolve_settings(self.settings),
                decisions=self.decisions,
                org_id=self.org_id,
                since=since,
                until=until,
            )
        except Exception as exc:
            _surface_error("decisions_report", exc)
            return hs.DecisionsReportView(enabled=False, integrations=())

    def routing_report(
        self, *, since: datetime | None = None, until: datetime | None = None
    ) -> RoutingReportView:
        """AMR decisions / escalations / savings per routing policy (SPEC-HRS-RTE-001)."""
        from prismal.composition import host_surface as hs

        try:
            return hs.routing_report(
                hs.resolve_settings(self.settings), org_id=self.org_id, since=since, until=until
            )
        except Exception as exc:
            _surface_error("routing_report", exc)
            return hs.RoutingReportView(enabled=False, mode="shadow", policies=())

    def a2a_routes(self, *, protocol_version: Literal["0.3", "1.0"] | None = None) -> A2ARouteSpec:
        """Tenant-scoped A2A route shape matching the advertised card (SPEC-HRS-A2A-001).

        ``settings.a2a_base_url.rstrip("/") + rpc_path`` equals the URL the Agent Card
        for ``org_id`` advertises; both protocol versions share one dual-stack endpoint.
        """
        from prismal.a2a.card import a2a_route_paths
        from prismal.composition import host_surface as hs

        settings = hs.resolve_settings(self.settings)
        card_path, rpc_path = a2a_route_paths(
            settings,
            org_id=self.org_id,
            protocol_version=protocol_version or settings.a2a_protocol_version,
        )
        return hs.A2ARouteSpec(
            card_path=card_path, rpc_path=rpc_path, tenant_scoped=bool(self.org_id)
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


def _settings_from_source(
    config_source: ConfigSourcePort, overrides: dict[str, Any] | None
) -> Settings:
    """Per-runtime settings from *config_source* chained over the process source.

    Pure: builds through ``build_settings`` (ContextVar-scoped source) and never
    touches the global source or ``os.environ`` (SPEC-HRS-CMP-001).
    """
    from prismal.core.config import build_settings
    from prismal.core.config_source import (
        ChainedConfigSource,
        EnvConfigSource,
        get_config_source,
    )

    process = get_config_source() or EnvConfigSource()
    tenant = build_settings(ChainedConfigSource([config_source, process]))
    return tenant.model_copy(update=overrides) if overrides else tenant


def _surface_error(method: str, exc: BaseException) -> None:
    """Count + log a host-surface read failure (SPEC-HRS-SUR-002). Never raises."""
    try:
        from prismal.monitoring.otel import OTelManager

        OTelManager().increment_counter("host_surface_errors", attributes={"method": method})
        logger.warning("composition.host_surface_error", method=method, error=type(exc).__name__)
    except Exception:  # pragma: no cover - observability never breaks a read
        return


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


def build_identity_ports(
    settings: Settings,
) -> tuple[IdentityPort, CredentialVaultPort, PolicyPort]:
    """Compose the identity ports from settings (Phase IDN — SPEC-IDN-CMP-001).

    Maps ``identity_provider`` → Local/Oidc provider, ``identity_vault`` →
    Env/File vault, and builds a :class:`PolicyEngine` from the identity policy
    file. When ``hardening_enabled`` the Phase H ``ToolPolicyEngine`` is wired as
    the delegate (tool-call rules flow through it; no duplication). Pure
    in-memory composition — no I/O, no global state.
    """
    from prismal.identity.policy import PolicyEngine, load_identity_policies
    from prismal.identity.provider import LocalIdentityProvider, OidcIdentityProvider
    from prismal.identity.vault import EnvVault, FileVault

    provider: IdentityPort = (
        OidcIdentityProvider(settings)
        if settings.identity_provider == "oidc"
        else LocalIdentityProvider(settings)
    )
    vault: CredentialVaultPort = (
        FileVault(settings) if settings.identity_vault == "file" else EnvVault(settings)
    )

    tool_policy = None
    if settings.hardening_enabled:
        from prismal.security.tool_policy import ToolPolicyEngine, load_tool_policies

        tool_policy = ToolPolicyEngine(
            load_tool_policies(settings.tool_policy_path), settings=settings
        )
    policy: PolicyPort = PolicyEngine(
        load_identity_policies(settings.identity_policy_path),
        tool_policy=tool_policy,
        settings=settings,
    )
    return provider, vault, policy


async def _compose_a2a(
    settings: Settings,
    tool_provider: ToolProviderPort,
    *,
    graph: Any | None,
    a2a_agents: list[Any] | None,
    closers: list[Closer],
    org_id: str | None = None,
) -> tuple[ToolProviderPort, Any | None]:
    """Wire A2A into the runtime (SPEC-A2A-009): outbound tools + inbound handler.

    Returns the (possibly wrapped) tool provider and the inbound handler (or
    ``None``). Deferred imports keep ``prismal.a2a`` (``[a2a]`` extra) out of the
    base path when A2A is disabled.
    """
    handler: Any | None = None

    if settings.a2a_outbound_enabled and a2a_agents:
        from prismal.a2a.client import A2AConnectionManager
        from prismal.a2a.provider import A2AToolProvider
        from prismal.agents.extension.providers import CompositeToolProvider

        # The OAuth vault partition is the local tenant, never a remote-card value.
        manager = A2AConnectionManager(
            allowlist=list(settings.a2a_outbound_allowlist),
            strict=settings.a2a_strict,
            local_org_id=org_id,
            redirect_uri=settings.a2a_oauth_redirect_uri,
        )
        a2a_provider = A2AToolProvider(a2a_agents, manager=manager)
        await a2a_provider.prepare()
        closers.append(manager.aclose)
        if isinstance(tool_provider, CompositeToolProvider):
            tool_provider = CompositeToolProvider([a2a_provider, *tool_provider.providers])
        else:
            tool_provider = CompositeToolProvider([a2a_provider, tool_provider])

    if settings.a2a_inbound_enabled and graph is not None:
        from prismal.a2a.server import A2AServerHandler

        handler = A2AServerHandler(graph, settings=settings)
    elif settings.a2a_inbound_enabled:
        logger.warning("composition.a2a_inbound_no_graph")

    return tool_provider, handler


async def build_runtime(
    settings: Settings | None = None,
    *,
    org_id: str | None = None,
    config_source: ConfigSourcePort | None = None,
    overrides: dict[str, Any] | None = None,
    mode: Literal["global", "context"] | None = None,
    collection_base: str = DEFAULT_COLLECTION_BASE,
    mcp_config_path: Path | None = None,
    graph: Any | None = None,
    a2a_agents: list[Any] | None = None,
    gateway_vault: Any | None = None,
    decision_vault: Any | None = None,
    memory_backend: MemoryBackendPort | None = None,
    memory_graph: MemoryGraphPort | None = None,
    memory_hooks: MemoryHooks | None = None,
) -> RuntimeContext:
    """Compose every core port in one call (SPEC-CR-003).

    Reuses the Phase Y/Z builders and the existing factories; on any sub-port
    failure it tears down whatever was already created and raises
    :class:`~prismal.core.exceptions.RuntimeCompositionError`.

    Args:
        settings: Base settings. ``None`` resolves via ``get_settings()``.
        org_id: Tenant id; isolates vector collections (RAG + memory) per tenant.
        config_source: Per-tenant config source (HRS — SPEC-HRS-CMP-001). When
            given, this runtime's settings are built from
            ``ChainedConfigSource([config_source, <process source>])`` (first
            wins; the process source is the injected global one or the default
            ``EnvConfigSource``) and *settings* is ignored; neither the global
            source nor ``os.environ`` is mutated. ``None`` ⇒ unchanged behaviour.
        overrides: Per-tenant settings overrides (applied as a validated copy).
        mode: ``"global"`` injects singletons; ``"context"`` keeps ports in the
            returned context. ``None`` uses ``settings.runtime_mode``.
        collection_base: Default logical collection for the vector store provider.
        mcp_config_path: Override for the MCP server catalogue.
        graph: Optional compiled graph; when A2A inbound is enabled it is wrapped
            in an :class:`~prismal.a2a.server.A2AServerHandler` exposed on the
            returned context as ``a2a_handler`` (the host mounts its routes).
        a2a_agents: Optional remote A2A agents (Agent Card URLs or ``AgentCard``
            objects) to expose as tools when A2A outbound is enabled.
        gateway_vault: Credential vault for tenant gateway keys (Phase GW,
            ``<auth_ref>@<org_id>``). ``None`` uses the identity vault when
            ``identity_enabled``, else an ``EnvVault`` over the config source.
        decision_vault: Credential vault for decision backend keys (Phase DEC,
            ``jev_api_key@<org_id>``). ``None`` falls back to the identity vault,
            then to the injected settings (no ``os.environ`` reads).
        memory_backend: Phase MEM — storage adapter for this tenant's
            ``MemoryService`` (default: selected by ``memory_v2_backend``).
        memory_graph: Phase MEM — temporal graph; required when
            ``memory_graph_backend="graphiti"`` (never built implicitly).
        memory_hooks: Phase MEM — host callables (LLM classifier, summarizer,
            batch executor, playbook curator); required for the ``llm`` gate.

    Returns:
        A :class:`RuntimeContext` with five non-null ports; usable as
        ``async with``.
    """
    if config_source is not None:
        eff = _settings_from_source(config_source, overrides)
    else:
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

            # Widen to the port: _compose_a2a (Phase I) may wrap it in a new
            # CompositeToolProvider, so the variable must hold any ToolProviderPort.
            tool_provider: ToolProviderPort = await build_default_tool_provider(
                eff, mcp_config_path=mcp_config_path
            )
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

        # 6. Identity & access governance (Phase IDN) — opt-in, in-memory ports.
        identity_provider: IdentityPort | None = None
        credential_vault: CredentialVaultPort | None = None
        policy_engine: PolicyPort | None = None
        if eff.identity_enabled:
            try:
                identity_provider, credential_vault, policy_engine = build_identity_ports(eff)
            except Exception as exc:
                raise RuntimeCompositionError("identity", str(exc)) from exc

        # 6b. A2A interop (Phase I) — opt-in, additive. Outbound A2A skills join
        #     the tool provider; the inbound handler wraps the supplied graph.
        a2a_handler: Any | None = None
        if eff.a2a_enabled:
            try:
                tool_provider, a2a_handler = await _compose_a2a(
                    eff,
                    tool_provider,
                    graph=graph,
                    a2a_agents=a2a_agents,
                    closers=closers,
                    org_id=org_id,
                )
            except Exception as exc:
                raise RuntimeCompositionError("a2a", str(exc)) from exc

        # 6c. Observability (Phase OBS) — opt-in, additive. Thin wrapper over the
        #     existing OTel/Langfuse singletons; None unless observability_enabled.
        observability: ObservabilityPort | None = None
        if eff.observability_enabled:
            try:
                from prismal.monitoring.observability import DefaultObservabilityProvider

                observability = DefaultObservabilityProvider(settings=eff)
            except Exception as exc:
                raise RuntimeCompositionError("observability", str(exc)) from exc

        # 6d. LLM gateways (Phase GW) — opt-in. Tenant-scoped keys, startup gates
        #     run before first use (fail-closed), optional cost reconciler.
        gateways: GatewayRouter | None = None
        if eff.llm_gateways_enabled:
            try:
                gateways = await _compose_gateways(
                    eff,
                    org_id=org_id,
                    vault=gateway_vault or credential_vault,
                    global_mode=resolved_mode == "global",
                    closers=closers,
                )
            except Exception as exc:
                raise RuntimeCompositionError("gateways", str(exc)) from exc

        # 6e. Typed decisions (Phase DEC) — opt-in. Cascade tiers from settings,
        #     tenant-scoped keys; global mode installs the process runtime.
        decisions: DecisionRuntime | None = None
        if eff.decisions_enabled:
            try:
                decisions = await _compose_decisions(
                    eff,
                    org_id=org_id,
                    vault=decision_vault or credential_vault,
                    audit=audit,
                    global_mode=resolved_mode == "global",
                    closers=closers,
                )
            except Exception as exc:
                raise RuntimeCompositionError("decisions", str(exc)) from exc

        # 6f. Agentic memory (Phase MEM) — opt-in, one MemoryService per tenant
        #     (SPEC-MEM-TEN-001): requires an org_id (fail-closed); injected
        #     backend/graph win (the Graphiti graph is never built implicitly).
        memory: MemoryService | None = None
        if eff.memory_v2_enabled:
            try:
                from prismal.decisions.types import DecisionMode
                from prismal.memory.v2.decision_bridge import CascadeDecisionPort
                from prismal.memory.v2.service import build_memory_service

                memory = build_memory_service(
                    eff,
                    org_id=org_id,
                    backend=memory_backend,
                    graph=memory_graph,
                    decisions=(
                        CascadeDecisionPort(
                            decisions.cascade,
                            org_id=org_id,
                            mode=DecisionMode(eff.memory_mode),
                        )
                        if decisions is not None
                        else None
                    ),
                    audit=audit,
                    hooks=memory_hooks,
                )
            except Exception as exc:
                raise RuntimeCompositionError("memory", str(exc)) from exc
            closers.append(memory.aclose)

        # 7. Mode: global injects the singleton; context leaves globals untouched.
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
        identity_provider=identity_provider,
        credential_vault=credential_vault,
        policy_engine=policy_engine,
        a2a_handler=a2a_handler,
        observability=observability,
        gateways=gateways,
        decisions=decisions,
        memory=memory,
        settings=eff,
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


async def _compose_gateways(
    settings: Settings,
    *,
    org_id: str | None,
    vault: Any | None,
    global_mode: bool,
    closers: list[Closer],
) -> GatewayRouter | None:
    """Build, start and register the gateway router (SPEC-GW-COMP-001)."""
    from prismal.identity.vault import EnvVault
    from prismal.providers.gateways.router import (
        build_gateway_router,
        get_gateway_router,
        set_gateway_router,
        vault_secret_resolver,
    )

    resolver = vault_secret_resolver(
        vault if vault is not None else EnvVault(settings), org_id=org_id, settings=settings
    )
    router = build_gateway_router(settings, org_id=org_id, secret_resolver=resolver)
    if router is None:
        return None
    closers.append(router.aclose)
    await router.astartup()
    if settings.gateway_reconcile_enabled:
        from prismal.providers.gateways.reconcile import CostReconciler

        router.reconciler = CostReconciler(router, settings=settings)
        closers.append(router.reconciler.drain)
    if router.ledger is not None or any(
        (gw := router.get(name)) is not None and gw.profile.capabilities.has_quota_api
        for name in router.names()
    ):
        from prismal.providers.gateways.poller import GatewayQuotaPoller

        router.quota_poller = GatewayQuotaPoller(router)
        router.quota_poller.start()
        closers.append(router.quota_poller.aclose)
    if global_mode:
        set_gateway_router(router)

        def _uninstall() -> None:
            if get_gateway_router() is router:
                set_gateway_router(None)

        closers.append(_uninstall)
    return router


async def _compose_decisions(
    settings: Settings,
    *,
    org_id: str | None,
    vault: Any | None,
    audit: AuditPort,
    global_mode: bool,
    closers: list[Closer],
) -> DecisionRuntime:
    """Build the decision cascade + runtime (Phase DEC); warm Laya when preloading."""
    from prismal.decisions.cascade import DecisionCascade
    from prismal.decisions.runtime import (
        DecisionRuntime,
        get_decision_runtime,
        set_decision_runtime,
    )

    cascade = DecisionCascade.from_settings(settings, audit=audit, vault=vault, org_id=org_id)
    runtime = DecisionRuntime(cascade=cascade, settings=settings)
    closers.append(runtime.aclose)
    if settings.laya_preload:
        for backend in cascade.backends:
            warmup = getattr(backend, "warmup", None)
            if warmup is not None and not backend.name.startswith("ollama:"):
                await warmup()
    await runtime.warmup()  # Ollama sub-tiers (OLL-006): flag-gated, fail-open
    if global_mode:
        set_decision_runtime(runtime)

        def _uninstall() -> None:
            if get_decision_runtime() is runtime:
                set_decision_runtime(None)

        closers.append(_uninstall)
    return runtime


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
    observability: ObservabilityPort | None = None,
    org_id: str | None = None,
    collection_base: str = DEFAULT_COLLECTION_BASE,
    settings: Settings | None = None,
    memory: MemoryService | None = None,
) -> RuntimeContext:
    """Compose a deterministic ``RuntimeContext`` with fakes (SPEC-CR-004).

    No I/O, no connections, no global injection; :meth:`RuntimeContext.aclose`
    is effectively a no-op. Defaults: ``FakeToolProvider`` (Y), ``FakeVectorStore``
    (Z), and in-module embeddings/checkpointer/audit doubles. ``observability``
    defaults to ``None`` (parity with the other opt-in ports); pass a
    ``FakeObservabilityProvider`` to exercise the observability port in tests.
    ``settings`` (HRS) is exposed as ``ctx.settings`` for the host read methods;
    ``None`` makes them fall back to the process settings.
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
        observability=observability,
        memory=memory,
        settings=settings,
    )


__all__ = [
    "RuntimeConfig",
    "RuntimeContext",
    "VectorStoreProvider",
    "build_identity_ports",
    "build_runtime",
    "build_test_runtime",
]

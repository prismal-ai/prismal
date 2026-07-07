"""Formal hexagonal ports for the extension surface (SPEC-EXT-006).

These ``Protocol`` types declare the structural interfaces prismal depends on.
Existing implementations conform without modification; users can substitute
their own (Redis checkpointer, Splunk audit sink, …) by satisfying the same
shape — no base class or registration required.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping, Sequence
    from typing import Literal

    from langchain_core.documents import Document
    from langchain_core.tools import BaseTool
    from pydantic import SecretStr

    from prismal.budget.types import Usage
    from prismal.identity.types import (
        DID,
        AgentIdentity,
        Credential,
        PolicyDecision,
        Scope,
    )
    from prismal.monitoring.observability_types import (
        DatasetFormat,
        RunSummary,
        ToolCallRecord,
    )


@runtime_checkable
class CheckpointPort(Protocol):
    """Graph-state persistence.

    Conforming implementations: ``langgraph.checkpoint.sqlite.aio.AsyncSqliteSaver``,
    ``langgraph.checkpoint.postgres.aio.AsyncPostgresSaver``.
    """

    async def aget(self, config: dict[str, Any]) -> Any | None:
        pass

    async def aput(
        self, config: dict[str, Any], checkpoint: Any, metadata: dict[str, Any], *args: Any
    ) -> Any:
        pass

    def alist(self, config: dict[str, Any] | None, *args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        pass


@runtime_checkable
class AuditPort(Protocol):
    """Append-only audit log.

    Conforming implementation: :class:`prismal.security.AuditLogger`. Users can
    forward audit to Splunk/Datadog/CloudTrail by satisfying this shape.
    """

    def log_event(self, event_type: str, payload: dict[str, Any]) -> None:
        pass

    def log_node(
        self,
        *,
        node_name: str,
        session_id: str,
        status: str,
        state_hash: str,
        duration_ms: float,
    ) -> None:
        pass

    def log_media(
        self,
        event: str,
        sha256: str,
        modality: str,
        size_bytes: int,
        duration_s: float | None,
    ) -> None:
        pass


@runtime_checkable
class EmbeddingsPort(Protocol):
    """Embeddings provider.

    Conforming implementations: any ``langchain_core.embeddings.Embeddings``.
    """

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        pass

    async def aembed_query(self, text: str) -> list[float]:
        pass


@runtime_checkable
class ToolPort(Protocol):
    """Executable tool.

    Conforming implementation: ``langchain_core.tools.BaseTool``.
    """

    name: str
    description: str

    async def ainvoke(self, args: Any, *posargs: Any, **kwargs: Any) -> Any:
        pass


@runtime_checkable
class ToolProviderPort(Protocol):
    """Tool source resolvable per agent and capability, at runtime (SPEC-TPI-001).

    Conforming implementations: ``McpToolProvider``, ``SkillToolProvider``,
    ``StubToolProvider``, ``CompositeToolProvider``, ``FakeToolProvider`` and
    any host-supplied provider. The core only calls :meth:`get_tools`; it never
    constructs providers — the host composes and injects them.

    ``get_tools`` is sync and must not raise on a degraded source: it returns
    whatever it can (an empty list at minimum). ``agent_name`` lets a provider
    select tools per agent (providers that don't need it ignore it);
    ``capabilities`` is the Fase E filter — ``None`` means no filter.
    ``phase`` is the Fase LH dynamic tool-gating hint (SPEC-LH-GAT-001) — ``None``
    (default) means no phase-based narrowing, reproducing pre-LH behavior
    exactly. Conforming implementations that do not support phase-based
    narrowing MAY ignore the argument; core call sites never assume a provider
    honours it and fail open when a provider's ``get_tools`` does not accept
    the keyword at all (DD-LH-006).

    Returned tools are ``langchain_core.tools.BaseTool`` instances (the
    concrete type that conforms to :class:`ToolPort`).
    """

    def get_tools(
        self,
        *,
        agent_name: str,
        capabilities: list[str] | None = None,
        phase: str | None = None,
    ) -> list[BaseTool]:
        pass


@runtime_checkable
class VectorStorePort(Protocol):
    """Interchangeable vector store (SPEC-VS-001).

    Conforming implementations: ``ChromaVectorStore`` (default),
    ``LanceDBVectorStore``, ``SqliteVecVectorStore``, ``QdrantVectorStore``,
    ``PgVectorStore``, ``FakeVectorStore``. The core only invokes these methods;
    it never constructs a backend — :class:`prismal.rag.vector_store_factory.VectorStoreFactory`
    selects and builds the concrete adapter from ``settings.vector_store_backend``.

    Score contract (SPEC-VS-002): :meth:`similarity_search` returns
    ``(Document, score)`` tuples with ``score ∈ [0, 1]``, **higher = more
    relevant**. Adapters whose native metric is a distance convert it; Chroma
    (cosine ``[0, 1]``) is the reference. The port *defines* the contract; each
    adapter *implements* the normalization (see ``rag/stores/_normalize.py``).
    """

    @property
    def collection_name(self) -> str: ...

    def add_documents(self, documents: list[Document]) -> list[str]:
        """Index *documents* and return the backend-assigned IDs."""
        ...

    def similarity_search(self, query: str, k: int = 5) -> list[tuple[Document, float]]:
        """Return the top-*k* ``(Document, score)`` with ``score ∈ [0, 1]`` (SPEC-VS-002)."""
        ...

    def delete_by_source(self, source: str) -> None:
        """Delete documents where ``metadata["source"] == source`` (best-effort)."""
        ...

    def delete_collection(self) -> None:
        """Delete the entire collection."""
        ...


@runtime_checkable
class VectorStoreProviderPort(Protocol):
    """Source of :class:`VectorStorePort` instances, resolvable per collection.

    The composition root (Phase R) injects a provider that builds a vector store
    for a given logical collection, applying per-tenant naming. Mirrors
    :class:`ToolProviderPort` for the vector-store side: the core asks for a
    store rather than constructing a backend.

    Conforming implementation: ``prismal.composition.VectorStoreProvider`` (wraps
    :class:`prismal.rag.vector_store_factory.VectorStoreFactory`). Phase Z ships a
    factory rather than a process singleton, so the provider is always carried in
    the :class:`~prismal.composition.RuntimeContext`.

    ``get_store`` resolves the effective collection name (``collection_for`` with
    the runtime's ``org_id``) and returns a backend adapter conforming to
    :class:`VectorStorePort`.
    """

    @property
    def org_id(self) -> str | None: ...

    def get_store(self, collection_name: str | None = None) -> VectorStorePort:
        """Return a vector store for *collection_name* (tenant-suffixed)."""
        ...


@runtime_checkable
class IdentityPort(Protocol):
    """Per-agent identity issuer/resolver/verifier (SPEC-IDN-PRV-001).

    Conforming implementations: ``LocalIdentityProvider`` (did:key, offline),
    ``OidcIdentityProvider`` (Entra/Okta adapter), ``FakeIdentityProvider``
    (tests). The core only calls these methods; ``build_runtime`` composes and
    injects the concrete provider per ``org_id``.
    """

    def issue(
        self, *, agent_name: str, org_id: str | None = None, scopes: Sequence[Scope] = ()
    ) -> AgentIdentity:
        """Mint a verifiable identity for ``agent_name`` within ``org_id``."""
        ...

    def resolve(self, did: DID) -> AgentIdentity | None:
        """Return the identity for ``did`` or ``None`` if unknown."""
        ...

    def verify(self, did: DID) -> bool:
        """Return ``True`` if ``did`` is currently valid (not revoked/expired)."""
        ...

    def revoke(self, did: DID) -> None:
        """Invalidate ``did`` so subsequent :meth:`verify` calls fail."""
        ...


@runtime_checkable
class CredentialVaultPort(Protocol):
    """Scoped secret resolver (SPEC-IDN-VLT-001).

    Conforming implementations: ``EnvVault`` (via the injected ``ConfigSourcePort``;
    never reads ``os.environ`` directly), ``FileVault`` (encrypted), ``FakeVault``
    (tests). The resolved :class:`~prismal.identity.types.Credential` value is used
    only at the action boundary and redacted from audit; only the opaque
    ``credential_ref`` travels in identity metadata.
    """

    def resolve(self, credential_ref: str, *, scopes: Sequence[Scope] = ()) -> Credential:
        """Resolve ``credential_ref`` to a :class:`Credential`; out-of-scope → ``ScopeError``."""
        ...

    def store(self, ref: str, value: SecretStr, *, scopes: Sequence[Scope] = ()) -> None:
        """Persist a secret under ``ref`` with the given scopes."""
        ...


@runtime_checkable
class PolicyPort(Protocol):
    """Identity-aware authorization decision (SPEC-IDN-POL-001).

    Conforming implementation: :class:`prismal.identity.policy.PolicyEngine`,
    which delegates ``(agent, tool, args)`` rules to the Phase H
    ``ToolPolicyEngine`` and layers identity scopes + resource matching on top.
    Evaluation is sync, O(1), and must not raise on the hot path.
    """

    def allow(
        self,
        *,
        identity: AgentIdentity,
        action: str,
        resource: str,
        context: Mapping[str, Any] | None = None,
    ) -> PolicyDecision:
        """Decide whether ``identity`` may perform ``action`` on ``resource``."""
        ...


@runtime_checkable
class ObservabilityPort(Protocol):
    """Queryable surface over a run's telemetry — backend-agnostic (SPEC-OBS-PRT-001).

    Conforming implementations: ``DefaultObservabilityProvider`` (thin wrapper over
    the existing ``OTelManager``/``LangfuseManager`` singletons), ``FakeObservabilityProvider``
    (tests), and any future first-party store or host-supplied adapter (e.g. one
    ``prismal-dashboard`` implements directly over its own persistence). The core
    only calls these methods; it never renders them (no web UI belongs here — see
    PLAN.md's scope boundary).

    ``record_node`` and ``record_score`` are sync and must not raise on the hot
    path (fail-open: a backend error is logged, never propagated into the graph).
    """

    def record_node(
        self,
        *,
        run_id: str,
        node_name: str,
        session_id: str,
        status: Literal["ok", "error"],
        duration_ms: float,
        tool_calls: Sequence[ToolCallRecord] = (),
        usage: Usage | None = None,
    ) -> None:
        """Record one node's execution (span + its tool calls + LLM usage, if any)."""
        ...

    def record_score(
        self,
        *,
        run_id: str,
        name: str,
        value: float,
        comment: str = "",
        source: Literal["human", "llm_judge", "system"] = "system",
    ) -> None:
        """Attach a named score/feedback annotation to *run_id* (SPEC-OBS-PAR-002).

        Usable by a human reviewer or the eval-harness's LLM-judge
        (``prismal.eval.judges``) to close the "feedback/score annotation" gap.
        Never raises; an unknown ``run_id`` is a no-op (logged at debug level).
        """
        ...

    def get_run_summary(self, run_id: str) -> RunSummary | None:
        """Return the queryable snapshot for *run_id*, or ``None`` if unknown/evicted.

        Best-effort by contract (SPEC-OBS-PRT-001 / ARCHITECTURE.md DD-OBS-006) —
        implementations are not required to persist beyond process lifetime.
        """
        ...

    def export_dataset(
        self,
        run_ids: Sequence[str],
        *,
        fmt: DatasetFormat,
    ) -> list[dict[str, Any]]:
        """Export the given runs as evaluation-dataset records for *fmt*.

        Never raises; runs with no summary are skipped. See SPEC-OBS-PAR-003 for
        the per-format record shape.
        """
        ...


def conforms_to(obj: Any, port: type) -> bool:
    """Return ``True`` if ``obj`` structurally satisfies ``port``.

    ``port`` must be a ``@runtime_checkable`` Protocol. Returns ``False`` for
    non-conforming objects rather than raising.
    """
    try:
        return isinstance(obj, port)
    except TypeError:
        return False


__all__ = [
    "AuditPort",
    "CheckpointPort",
    "CredentialVaultPort",
    "EmbeddingsPort",
    "IdentityPort",
    "ObservabilityPort",
    "PolicyPort",
    "ToolPort",
    "ToolProviderPort",
    "VectorStorePort",
    "VectorStoreProviderPort",
    "conforms_to",
]

"""Formal hexagonal ports for the extension surface (SPEC-EXT-006).

These ``Protocol`` types declare the structural interfaces prismal depends on.
Existing implementations conform without modification; users can substitute
their own (Redis checkpointer, Splunk audit sink, …) by satisfying the same
shape — no base class or registration required.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from langchain_core.documents import Document
    from langchain_core.tools import BaseTool


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

    Returned tools are ``langchain_core.tools.BaseTool`` instances (the
    concrete type that conforms to :class:`ToolPort`).
    """

    def get_tools(
        self,
        *,
        agent_name: str,
        capabilities: list[str] | None = None,
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
    "EmbeddingsPort",
    "ToolPort",
    "ToolProviderPort",
    "VectorStorePort",
    "conforms_to",
]

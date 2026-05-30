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


@runtime_checkable
class CheckpointPort(Protocol):
    """Graph-state persistence.

    Conforming implementations: ``langgraph.checkpoint.sqlite.aio.AsyncSqliteSaver``,
    ``langgraph.checkpoint.postgres.aio.AsyncPostgresSaver``.
    """

    async def aget(self, config: dict[str, Any]) -> Any | None: ...

    async def aput(
        self, config: dict[str, Any], checkpoint: Any, metadata: dict[str, Any], *args: Any
    ) -> Any: ...

    def alist(
        self, config: dict[str, Any] | None, *args: Any, **kwargs: Any
    ) -> AsyncIterator[Any]: ...


@runtime_checkable
class AuditPort(Protocol):
    """Append-only audit log.

    Conforming implementation: :class:`prismal.security.AuditLogger`. Users can
    forward audit to Splunk/Datadog/CloudTrail by satisfying this shape.
    """

    def log_event(self, event_type: str, payload: dict[str, Any]) -> None: ...

    def log_node(
        self,
        *,
        node_name: str,
        session_id: str,
        status: str,
        state_hash: str,
        duration_ms: float,
    ) -> None: ...

    def log_media(
        self,
        event: str,
        sha256: str,
        modality: str,
        size_bytes: int,
        duration_s: float | None,
    ) -> None: ...


@runtime_checkable
class EmbeddingsPort(Protocol):
    """Embeddings provider.

    Conforming implementations: any ``langchain_core.embeddings.Embeddings``.
    """

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]: ...

    async def aembed_query(self, text: str) -> list[float]: ...


@runtime_checkable
class ToolPort(Protocol):
    """Executable tool.

    Conforming implementation: ``langchain_core.tools.BaseTool``.
    """

    name: str
    description: str

    async def ainvoke(self, args: Any, *posargs: Any, **kwargs: Any) -> Any: ...


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
    "conforms_to",
]

"""SubgraphRegistry: central in-memory registry for dynamic subgraphs.

Subgraphs are registered at startup (or at runtime via the REST/CLI API)
and injected as nodes into the main LangGraph supervisor graph.

Usage::

    registry = SubgraphRegistry.get_instance()
    await registry.register("dev_pipeline", definition)
    names = registry.list()         # ["dev_pipeline"]
    defn  = registry.get("dev_pipeline")
    await registry.unregister("dev_pipeline")
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable  # noqa: TC003
from typing import Any

import structlog
from pydantic import BaseModel, Field


class SubgraphDefinition(BaseModel):
    """Definition of a dynamic subgraph.

    Phase 34 added the ``send_edges`` field, which lets a subgraph declare
    map-reduce fan-out edges built on top of LangGraph's ``Send`` primitive.
    Each entry pairs a source node with a dispatcher callable (typically
    produced by
    :func:`~lightagent.agents.patterns.parallel.make_parallel_dispatcher`)
    that returns either a list of ``Send`` objects or a fallback node name.
    """

    name: str = Field(..., description="Unique subgraph identifier")
    description: str = Field(..., description="Human-readable purpose")
    entry_point: str = Field(..., description="Name of the entry node")
    nodes: dict[str, Callable[..., Any]] = Field(
        ..., description="Mapping node_name → async node function"
    )
    edges: list[tuple[str, str]] = Field(
        default_factory=list, description="List of (from, to) node pairs"
    )
    conditional_edges: dict[str, Callable[..., str]] = Field(
        default_factory=dict,
        description="source_node → routing function returning next node name",
    )
    send_edges: list[tuple[str, Callable[..., Any]]] = Field(
        default_factory=list,
        description=(
            "Phase 34: list of (source_node, dispatcher_fn) pairs. Each "
            "dispatcher returns ``list[Send]`` for parallel fan-out or a "
            "string node name for the empty/disabled case."
        ),
    )

    model_config = {"arbitrary_types_allowed": True}


class SubgraphRegistry:
    """In-memory registry for dynamic LangGraph subgraphs.

    Thread-safe via ``asyncio.Lock``.  Use :meth:`get_instance` to obtain
    the process-wide singleton.

    Example::

        registry = SubgraphRegistry.get_instance()
        await registry.register("my_pipe", definition)
    """

    _instance: SubgraphRegistry | None = None

    def __init__(self) -> None:
        """Initialise an empty registry."""
        self._subgraphs: dict[str, SubgraphDefinition] = {}
        self._lock: asyncio.Lock = asyncio.Lock()
        self._log = structlog.get_logger("lightagent.subgraphs.registry")

    @classmethod
    def get_instance(cls) -> SubgraphRegistry:
        """Return the process-wide singleton.

        Returns:
            The shared :class:`SubgraphRegistry` instance.
        """
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    async def register(self, name: str, definition: SubgraphDefinition) -> None:
        """Register a new subgraph definition.

        Args:
            name: Unique identifier for the subgraph.
            definition: The subgraph definition to store.

        Raises:
            ValueError: If ``name`` is already registered.
        """
        async with self._lock:
            if name in self._subgraphs:
                raise ValueError(f"Subgraph '{name}' already registered")
            self._subgraphs[name] = definition
            self._log.info("subgraph.registered", name=name)

    async def unregister(self, name: str) -> None:
        """Remove a registered subgraph.

        Args:
            name: Identifier of the subgraph to remove.

        Raises:
            KeyError: If ``name`` is not registered.
        """
        async with self._lock:
            if name not in self._subgraphs:
                raise KeyError(f"Subgraph '{name}' not found")
            del self._subgraphs[name]
            self._log.info("subgraph.unregistered", name=name)

    def list(self) -> list[str]:
        """Return names of all registered subgraphs.

        Returns:
            Sorted list of registered subgraph names.
        """
        return sorted(self._subgraphs.keys())

    def get(self, name: str) -> SubgraphDefinition | None:
        """Retrieve a subgraph definition by name.

        Args:
            name: Identifier to look up.

        Returns:
            The :class:`SubgraphDefinition`, or ``None`` if not found.
        """
        return self._subgraphs.get(name)

    def get_all(self) -> dict[str, SubgraphDefinition]:
        """Return a snapshot of all registered subgraphs.

        Returns:
            Dict mapping name → SubgraphDefinition.
        """
        return dict(self._subgraphs)


__all__ = ["SubgraphDefinition", "SubgraphRegistry"]

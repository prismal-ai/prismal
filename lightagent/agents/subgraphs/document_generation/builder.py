"""Builder for the document_generation subgraph (C2).

Linear pipeline::

    planner → researcher → writer → editor → formatter

Each node reads from and writes to ``state["metadata"]["document_generation"]``.
The formatter also appends an ``AIMessage`` with the final document so the
result is visible to the caller.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from lightagent.agents.subgraphs.document_generation.editor_node import (
    make_editor_node,
)
from lightagent.agents.subgraphs.document_generation.formatter_node import (
    make_formatter_node,
)
from lightagent.agents.subgraphs.document_generation.planner_node import (
    make_planner_node,
)
from lightagent.agents.subgraphs.document_generation.researcher_node import (
    make_researcher_node,
)
from lightagent.agents.subgraphs.document_generation.writer_node import (
    make_writer_node,
)
from lightagent.agents.subgraphs.registry import SubgraphDefinition
from lightagent.core.logging import get_logger

if TYPE_CHECKING:
    from lightagent.core.config import Settings

logger = get_logger("lightagent.subgraphs.document_generation.builder")

_NAME = "document_generation"
_DESCRIPTION = "Document generation pipeline: planner → researcher → writer → editor → formatter."


def build_document_generation_subgraph(
    llm: Any | None = None,
    rag_engine: Any | None = None,
    format: Literal["markdown", "plain", "html"] = "markdown",
    settings: Settings | None = None,
) -> SubgraphDefinition:
    """Build the document_generation :class:`SubgraphDefinition`.

    Args:
        llm: Pre-built LLM handle used by planner/researcher/writer/editor.
            ``None`` resolves via :class:`ProviderRegistry`.
        rag_engine: Optional RAG engine used by the researcher. ``None``
            means research is LLM-only.
        format: Output format for the final document.
        settings: Reserved for future auto-wiring.

    Returns:
        :class:`SubgraphDefinition` with 5 nodes and linear edges.
    """
    del settings  # reserved

    if llm is None:
        from lightagent.providers.registry import ProviderRegistry

        llm = ProviderRegistry().get_llm()

    planner = make_planner_node(llm=llm)
    researcher = make_researcher_node(llm=llm, rag_engine=rag_engine)
    writer = make_writer_node(llm=llm)
    editor = make_editor_node(llm=llm)
    formatter = make_formatter_node(format=format)

    definition = SubgraphDefinition(
        name=_NAME,
        description=_DESCRIPTION,
        entry_point="planner",
        nodes={
            "planner": planner,
            "researcher": researcher,
            "writer": writer,
            "editor": editor,
            "formatter": formatter,
        },
        edges=[
            ("planner", "researcher"),
            ("researcher", "writer"),
            ("writer", "editor"),
            ("editor", "formatter"),
        ],
    )
    logger.info(
        "document_generation_subgraph_built",
        nodes=list(definition.nodes.keys()),
        format=format,
    )
    return definition


async def register_document_generation(
    llm: Any | None = None,
    rag_engine: Any | None = None,
    format: Literal["markdown", "plain", "html"] = "markdown",
    settings: Settings | None = None,
) -> None:
    """Register the document_generation subgraph (idempotent)."""
    from lightagent.agents.subgraphs.registry import SubgraphRegistry

    registry = SubgraphRegistry.get_instance()
    if registry.get(_NAME) is not None:
        logger.info("document_generation.already_registered")
        return
    definition = build_document_generation_subgraph(
        llm=llm, rag_engine=rag_engine, format=format, settings=settings
    )
    await registry.register(_NAME, definition)
    logger.info("document_generation.registered")


__all__ = ["build_document_generation_subgraph", "register_document_generation"]

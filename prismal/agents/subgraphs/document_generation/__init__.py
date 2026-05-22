"""Document-generation subgraph (C2).

Pipeline: planner → researcher → writer → editor → formatter.

Public entry points::

    from prismal.agents.subgraphs.document_generation import (
        build_document_generation_subgraph,
        DocumentGenerationError,
    )
"""

from __future__ import annotations

from prismal.agents.subgraphs.document_generation.builder import (
    build_document_generation_subgraph,
)
from prismal.agents.subgraphs.document_generation.editor_node import (
    make_editor_node,
)
from prismal.agents.subgraphs.document_generation.formatter_node import (
    make_formatter_node,
)
from prismal.agents.subgraphs.document_generation.planner_node import (
    make_planner_node,
)
from prismal.agents.subgraphs.document_generation.researcher_node import (
    make_researcher_node,
)
from prismal.agents.subgraphs.document_generation.writer_node import (
    make_writer_node,
)
from prismal.core.exceptions import DocumentGenerationError

__all__ = [
    "DocumentGenerationError",
    "build_document_generation_subgraph",
    "make_editor_node",
    "make_formatter_node",
    "make_planner_node",
    "make_researcher_node",
    "make_writer_node",
]

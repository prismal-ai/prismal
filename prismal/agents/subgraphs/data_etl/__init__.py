"""Data ETL subgraph (C3).

Pipeline: extractor → validator → transformer → loader → auditor.

Public entry points::

    from prismal.agents.subgraphs.data_etl import (
        build_data_etl_subgraph,
        DataETLError,
    )
"""

from __future__ import annotations

from prismal.agents.subgraphs.data_etl.auditor_node import make_auditor_node
from prismal.agents.subgraphs.data_etl.builder import build_data_etl_subgraph
from prismal.agents.subgraphs.data_etl.extractor_node import make_extractor_node
from prismal.agents.subgraphs.data_etl.loader_node import make_loader_node
from prismal.agents.subgraphs.data_etl.transformer_node import make_transformer_node
from prismal.agents.subgraphs.data_etl.validator_node import (
    make_etl_branching_gate,
    make_validator_node,
)
from prismal.core.exceptions import DataETLError

__all__ = [
    "DataETLError",
    "build_data_etl_subgraph",
    "make_auditor_node",
    "make_etl_branching_gate",
    "make_extractor_node",
    "make_loader_node",
    "make_transformer_node",
    "make_validator_node",
]

"""Print the Mermaid diagram of every graph-based architecture (offline).

Run::

    python examples/visualize_graphs.py

``to_mermaid`` needs no network. In a notebook, swap it for ``visualize(...)``
to render an inline PNG (falling back to this same Mermaid text when no renderer
is available), or ``save_graph_image(obj, "graph.png")`` to write a file.
"""

from __future__ import annotations

from prismal.agents.subgraphs.code_review.builder import build_code_review_subgraph
from prismal.agents.subgraphs.customer_service.builder import (
    build_customer_service_subgraph,
)
from prismal.agents.subgraphs.data_etl.builder import build_data_etl_subgraph
from prismal.agents.subgraphs.debate_consensus.builder import (
    build_debate_consensus_subgraph,
)
from prismal.agents.subgraphs.document_generation.builder import (
    build_document_generation_subgraph,
)
from prismal.agents.subgraphs.multimodal_pipeline import build_multimodal_subgraph
from prismal.langgraph import to_mermaid

# Each entry is (label, SubgraphDefinition). All builders run offline.
SUBGRAPHS = [
    ("customer_service", build_customer_service_subgraph()),
    ("document_generation", build_document_generation_subgraph()),
    ("data_etl", build_data_etl_subgraph()),
    ("code_review", build_code_review_subgraph()),
    ("debate_consensus", build_debate_consensus_subgraph()),
    ("multimodal_pipeline", build_multimodal_subgraph()),
]


def main() -> None:
    for label, definition in SUBGRAPHS:
        print(f"\n{'=' * 70}\n{label}\n{'=' * 70}")
        # Equivalent to definition.to_mermaid().
        print(to_mermaid(definition))

    print(f"\n{'=' * 70}")
    print("Main supervisor graph:")
    print("  from prismal.agents.graph import visualize_supervisor_graph")
    print("  visualize_supervisor_graph()   # builds + draws the compiled graph")


if __name__ == "__main__":
    main()

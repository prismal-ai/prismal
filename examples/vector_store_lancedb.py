"""Swap the vector-store backend by configuration (Phase Z).

Demonstrates ``VectorStoreFactory`` selecting an **embedded** backend with no
HTTP server. The same RAG/memory consumers work unchanged — only
``settings.vector_store_backend`` differs. This example uses LanceDB; set the
backend to ``"sqlite_vec"`` for the in-process SQLite option, or ``"chroma"``
(the default) to keep current behaviour.

Run::

    pip install 'prismal[lancedb]'
    python examples/vector_store_lancedb.py

Every adapter conforms to ``VectorStorePort`` and returns ``(Document, score)``
with ``score ∈ [0, 1]`` (higher = more relevant), so downstream ranking is
backend-agnostic (SPEC-VS-002).
"""

from __future__ import annotations

from langchain_core.documents import Document

from prismal.agents.extension import VectorStorePort, conforms_to
from prismal.core.config import Settings
from prismal.core.exceptions import VectorStoreBackendUnavailable
from prismal.rag.vector_store_factory import VectorStoreFactory


def main() -> None:
    """Build an embedded LanceDB store, index a few docs, and search."""
    # Configuration-only backend swap — no code change in any consumer.
    settings = Settings(
        vector_store_backend="lancedb",
        vector_store_path="data/db/vectors",
    )

    try:
        store: VectorStorePort = VectorStoreFactory.create(settings, collection_name="demo")
    except VectorStoreBackendUnavailable as exc:
        # The base install does not ship LanceDB; the error guides to the extra.
        print(exc)
        print("Install it with: pip install 'prismal[lancedb]'")
        return

    assert conforms_to(store, VectorStorePort)

    store.add_documents(
        [
            Document(page_content="Prismal is an agent framework.", metadata={"source": "intro"}),
            Document(page_content="LanceDB is an embedded vector DB.", metadata={"source": "db"}),
            Document(
                page_content="Cosine similarity ranks relevance.", metadata={"source": "math"}
            ),
        ]
    )

    for doc, score in store.similarity_search("What is Prismal?", k=3):
        print(f"{score:.3f}  {doc.metadata['source']:8}  {doc.page_content}")


if __name__ == "__main__":
    main()

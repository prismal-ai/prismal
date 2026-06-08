"""Backward-compatible shim for the ChromaDB vector store (Phase Z).

The implementation moved to :mod:`prismal.rag.stores.chroma` when Phase Z
introduced the interchangeable :class:`~prismal.agents.extension.ports.VectorStorePort`.
This module re-exports the public names so existing imports keep working::

    from prismal.rag.vector_store import ChromaVectorStore, ChromaStoreError

New code should select a backend through
:class:`prismal.rag.vector_store_factory.VectorStoreFactory` and type against
:class:`~prismal.agents.extension.ports.VectorStorePort` instead of importing a
concrete adapter.
"""

from __future__ import annotations

from prismal.rag.stores.chroma import ChromaStoreError, ChromaVectorStore

__all__ = ["ChromaStoreError", "ChromaVectorStore"]

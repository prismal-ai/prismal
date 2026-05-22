"""LightAgent RAG (Retrieval-Augmented Generation) package.

Public API surface for the complete RAG system:

- :class:`~lightagent.rag.loaders.DocumentProcessorFactory` — loads documents
  from disk (PDF, DOCX, TXT, MD, CSV, JSON) using the appropriate LangChain
  loader.
- :data:`~lightagent.rag.loaders.SUPPORTED_EXTENSIONS` — frozenset of supported
  file extension strings.
- :class:`~lightagent.rag.loaders.UnsupportedDocumentTypeError` — raised when a
  file has an unsupported extension.
- :class:`~lightagent.rag.embeddings.EmbeddingsFactory` — creates the correct
  LangChain embeddings implementation based on ``settings.embeddings_model``.
- :class:`~lightagent.rag.vector_store.ChromaVectorStore` — thin wrapper around
  ChromaDB for document indexing and similarity search.
- :class:`~lightagent.rag.vector_store.ChromaStoreError` — raised on ChromaDB
  operation failures.
- :class:`~lightagent.rag.crag.CRAGPipeline` — Corrective RAG pipeline
  (retrieve → grade → filter → decide → generate).
- :class:`~lightagent.rag.crag.RetrievedChunk` — a graded document chunk with
  source, chunk_id, relevance_score, and content fields.
- :class:`~lightagent.rag.crag.CRAGResult` — result produced by the CRAG
  pipeline (answer, sources, used_web_fallback).
- :class:`~lightagent.rag.engine.RAGEngine` — high-level entry point composing
  all of the above into a single, ergonomic interface.

Typical usage::

    from pathlib import Path
    from prismal.rag import RAGEngine

    engine = RAGEngine(collection_name="docs")
    engine.index_directory(Path("data/documents/"))
    chunks = engine.search("What is LightAgent?", k=5)
    for chunk in chunks:
        print(chunk.source, chunk.relevance_score, chunk.content[:80])
"""

from __future__ import annotations

from prismal.rag.adaptive import AdaptiveRAGEngine, AdaptiveResult, QueryType
from prismal.rag.crag import CRAGPipeline, CRAGResult, RetrievedChunk
from prismal.rag.embeddings import EmbeddingsFactory
from prismal.rag.engine import RAGEngine
from prismal.rag.fusion import FusionResult, RAGFusionEngine, reciprocal_rank_fusion
from prismal.rag.hierarchical import (
    HierarchicalRAGEngine,
    HierarchicalSearchResult,
    ParentChunk,
)
from prismal.rag.hybrid import HybridSearchEngine
from prismal.rag.hyde import HyDEResult, HyDERetriever
from prismal.rag.loaders import (
    SUPPORTED_EXTENSIONS,
    DocumentProcessorFactory,
    UnsupportedDocumentTypeError,
)
from prismal.rag.multi_vector import MultiVectorRAGEngine, MultiVectorResult
from prismal.rag.self_rag import (
    RetrievalDecision,
    SelfRAGPipeline,
    SelfRAGResult,
    SupportedDecision,
)
from prismal.rag.vector_store import ChromaStoreError, ChromaVectorStore

__all__ = [
    "SUPPORTED_EXTENSIONS",
    "AdaptiveRAGEngine",
    "AdaptiveResult",
    "CRAGPipeline",
    "CRAGResult",
    "ChromaStoreError",
    "ChromaVectorStore",
    "DocumentProcessorFactory",
    "EmbeddingsFactory",
    "FusionResult",
    "HierarchicalRAGEngine",
    "HierarchicalSearchResult",
    "HyDEResult",
    "HyDERetriever",
    "HybridSearchEngine",
    "MultiVectorRAGEngine",
    "MultiVectorResult",
    "ParentChunk",
    "QueryType",
    "RAGEngine",
    "RAGFusionEngine",
    "RetrievalDecision",
    "RetrievedChunk",
    "SelfRAGPipeline",
    "SelfRAGResult",
    "SupportedDecision",
    "UnsupportedDocumentTypeError",
    "reciprocal_rank_fusion",
]

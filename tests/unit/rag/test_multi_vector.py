"""Unit tests for MultiVectorRAGEngine (prismal.rag.multi_vector).

Multi-Vector RAG indexes each document under multiple vector representations
(original chunk + LLM-generated summary + LLM-generated hypothetical
questions). At query time, similarity hits on any representation bubble up to
the same original doc; duplicates are merged.

External dependencies (LLM, loaders, vector store) are mocked.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from prismal.core.exceptions import MultiVectorError, RAGError
from prismal.rag.multi_vector import MultiVectorRAGEngine, MultiVectorResult

PROVIDER_REGISTRY_PATH = "prismal.rag.multi_vector.ProviderRegistry"
LOADER_PATH = "prismal.rag.multi_vector.DocumentProcessorFactory"


# ── Helpers ───────────────────────────────────────────────────────────────────


def _llm_response(text: str) -> MagicMock:
    r = MagicMock()
    r.content = text
    return r


def _doc(content: str, source: str = "file.txt") -> MagicMock:
    d = MagicMock()
    d.page_content = content
    d.metadata = {"source": source}
    return d


def _hit(content: str, doc_id: str, representation: str, source: str = "file.txt"):
    d = MagicMock()
    d.page_content = content
    d.metadata = {
        "source": source,
        "doc_id": doc_id,
        "representation": representation,
        "chunk_id": f"{doc_id}-{representation}",
    }
    return d


# ── Dataclass / exception ─────────────────────────────────────────────────────


def test_multi_vector_result_is_instantiable() -> None:
    r = MultiVectorResult(chunks=[], matched_representations={})
    assert r.chunks == []
    assert r.matched_representations == {}


def test_multi_vector_error_is_ragerror_subclass() -> None:
    assert issubclass(MultiVectorError, RAGError)


# ── Constructor ───────────────────────────────────────────────────────────────


def test_init_stores_config() -> None:
    store = MagicMock()
    with patch(PROVIDER_REGISTRY_PATH) as reg:
        reg.return_value.get_llm.return_value = MagicMock()
        engine = MultiVectorRAGEngine(vector_store=store, n_questions=5, settings=MagicMock())
    assert engine._store is store  # noqa: SLF001
    assert engine._n_questions == 5  # noqa: SLF001


def test_init_uses_default_n_questions() -> None:
    with patch(PROVIDER_REGISTRY_PATH) as reg:
        reg.return_value.get_llm.return_value = MagicMock()
        engine = MultiVectorRAGEngine(vector_store=MagicMock(), settings=MagicMock())
    assert engine._n_questions == 3  # noqa: SLF001


# ── index_document ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_index_document_calls_delete_by_source_first() -> None:
    store = MagicMock()
    doc = _doc("some content", "file.txt")

    llm = MagicMock()
    # One summary + one numbered question list per chunk.
    llm.ainvoke = AsyncMock(
        side_effect=[
            _llm_response("Summary of the chunk."),
            _llm_response("1. Q1\n2. Q2\n3. Q3"),
        ]
    )

    with (
        patch(LOADER_PATH) as mock_factory_cls,
        patch(PROVIDER_REGISTRY_PATH) as reg,
    ):
        mock_factory_cls.return_value.load.return_value = [doc]
        reg.return_value.get_llm.return_value = llm
        engine = MultiVectorRAGEngine(vector_store=store, settings=MagicMock())
        await engine.index_document(Path("file.txt"))

    store.delete_by_source.assert_called_once_with("file.txt")


@pytest.mark.asyncio
async def test_index_document_writes_summary_chunk_and_questions() -> None:
    store = MagicMock()
    doc = _doc("original content here", "file.txt")
    llm = MagicMock()
    llm.ainvoke = AsyncMock(
        side_effect=[
            _llm_response("summary text"),
            _llm_response("1. first question\n2. second question\n3. third question"),
        ]
    )
    with (
        patch(LOADER_PATH) as mock_factory_cls,
        patch(PROVIDER_REGISTRY_PATH) as reg,
    ):
        mock_factory_cls.return_value.load.return_value = [doc]
        reg.return_value.get_llm.return_value = llm
        engine = MultiVectorRAGEngine(vector_store=store, n_questions=3, settings=MagicMock())
        n_docs, n_vectors = await engine.index_document(Path("file.txt"))

    called_docs = store.add_documents.call_args[0][0]
    representations = [d.metadata["representation"] for d in called_docs]
    # 1 chunk + 1 summary + 3 questions = 5 vectors for 1 doc
    assert representations.count("chunk") == 1
    assert representations.count("summary") == 1
    assert representations.count("question") == 3
    # All share the same doc_id
    doc_ids = {d.metadata["doc_id"] for d in called_docs}
    assert len(doc_ids) == 1
    assert n_docs == 1
    assert n_vectors == 5


@pytest.mark.asyncio
async def test_index_document_continues_when_llm_partially_fails() -> None:
    """If the LLM fails on summary or questions, chunk-only indexing still happens."""
    store = MagicMock()
    doc = _doc("content", "file.txt")
    llm = MagicMock()
    # Summary fails; questions succeed.
    llm.ainvoke = AsyncMock(
        side_effect=[
            RuntimeError("summary boom"),
            _llm_response("1. q1\n2. q2"),
        ]
    )
    with (
        patch(LOADER_PATH) as mock_factory_cls,
        patch(PROVIDER_REGISTRY_PATH) as reg,
    ):
        mock_factory_cls.return_value.load.return_value = [doc]
        reg.return_value.get_llm.return_value = llm
        engine = MultiVectorRAGEngine(vector_store=store, n_questions=2, settings=MagicMock())
        n_docs, n_vectors = await engine.index_document(Path("file.txt"))

    # chunk + 2 questions = 3 vectors (summary was dropped)
    assert n_docs == 1
    assert n_vectors == 3


# ── search() ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_search_deduplicates_by_doc_id() -> None:
    """A summary hit and a chunk hit from the same doc produce ONE result."""
    store = MagicMock()
    store.similarity_search.return_value = [
        (_hit("summary text", "d0", "summary"), 0.95),
        (_hit("chunk text", "d0", "chunk"), 0.80),
        (_hit("other doc chunk", "d1", "chunk"), 0.70),
    ]
    with patch(PROVIDER_REGISTRY_PATH) as reg:
        reg.return_value.get_llm.return_value = MagicMock()
        engine = MultiVectorRAGEngine(vector_store=store, settings=MagicMock())
        result = await engine.search("q", k=5)

    doc_ids = [c.chunk_id.split("-")[0] for c in result.chunks]
    assert sorted(doc_ids) == ["d0", "d1"]
    # Best score for d0 wins (summary with 0.95)
    d0_chunk = next(c for c in result.chunks if c.chunk_id.startswith("d0"))
    assert d0_chunk.relevance_score == 0.95


@pytest.mark.asyncio
async def test_search_reports_which_representations_matched() -> None:
    store = MagicMock()
    store.similarity_search.return_value = [
        (_hit("x", "d0", "summary"), 0.9),
        (_hit("y", "d0", "chunk"), 0.8),
        (_hit("z", "d0", "question"), 0.7),
    ]
    with patch(PROVIDER_REGISTRY_PATH) as reg:
        reg.return_value.get_llm.return_value = MagicMock()
        engine = MultiVectorRAGEngine(vector_store=store, settings=MagicMock())
        result = await engine.search("q", k=5)

    assert result.matched_representations == {"d0": ["summary", "chunk", "question"]}


@pytest.mark.asyncio
async def test_search_returns_at_most_k_unique_docs() -> None:
    store = MagicMock()
    store.similarity_search.return_value = [
        (_hit(f"c{i}", f"d{i}", "chunk"), 0.9 - 0.01 * i) for i in range(10)
    ]
    with patch(PROVIDER_REGISTRY_PATH) as reg:
        reg.return_value.get_llm.return_value = MagicMock()
        engine = MultiVectorRAGEngine(vector_store=store, settings=MagicMock())
        result = await engine.search("q", k=3)
    assert len(result.chunks) == 3


@pytest.mark.asyncio
async def test_search_skips_hits_missing_doc_id_metadata() -> None:
    """Legacy hits without doc_id are dropped (logged) — no crash."""
    bad = MagicMock()
    bad.page_content = "orphan"
    bad.metadata = {"source": "f.txt"}  # no doc_id
    store = MagicMock()
    store.similarity_search.return_value = [(bad, 0.9)]
    with patch(PROVIDER_REGISTRY_PATH) as reg:
        reg.return_value.get_llm.return_value = MagicMock()
        engine = MultiVectorRAGEngine(vector_store=store, settings=MagicMock())
        result = await engine.search("q", k=5)
    assert result.chunks == []


@pytest.mark.asyncio
async def test_search_finds_docs_via_hypothetical_question_only() -> None:
    """A query can land on a 'question' vector of a doc whose chunk wouldn't rank."""
    store = MagicMock()
    # Only the question representation scored well; its content is the question text.
    store.similarity_search.return_value = [
        (_hit("What is LangGraph?", "d42", "question"), 0.92),
    ]
    with patch(PROVIDER_REGISTRY_PATH) as reg:
        reg.return_value.get_llm.return_value = MagicMock()
        engine = MultiVectorRAGEngine(vector_store=store, settings=MagicMock())
        result = await engine.search("LangGraph explanation", k=3)

    assert len(result.chunks) == 1
    assert result.chunks[0].content == "What is LangGraph?"
    assert result.matched_representations == {"d42": ["question"]}

"""Wiring of the RAG context budget into the RAG engines.

(Phase TKE — SPEC-TKE-RAG-001, TASKS TKE8-04 / TKE8-05)

Enabled ⇒ engines ask the store for ``rag_retrieve_k`` and hand back only the
chunks that fit ``rag_context_budget_tokens``. Disabled ⇒ identical store calls
and results to the pre-TKE behaviour. No network, no live LLM.
"""

from __future__ import annotations

import sys
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.documents import Document

from prismal.core.config import Settings
from prismal.rag.context_budget import (
    apply_context_budget,
    context_budget_active,
    effective_k,
)
from prismal.rag.crag import RetrievedChunk
from prismal.rag.vector_store_factory import FakeVectorStore
from prismal.utils.token_estimate import estimate_tokens

QUERY = "what is the revenue trend"

PROSE = (
    "The quarterly report shows that revenue grew steadily across every region. "
    "Analysts attribute the increase to stronger demand and improved logistics. "
    "Management expects the trend to continue through the next fiscal year as well."
)
CODE = "\n".join(
    [
        "def compute(x):",
        "    total = 0",
        "    for i in range(x):",
        "        total += i",
        "    return total",
        "class Foo:",
        "    pass",
        "import os",
    ]
    * 4
)


class RecordingStore(FakeVectorStore):
    """FakeVectorStore that records the ``k`` of every search."""

    def __init__(self, docs: dict[str, list[tuple[Document, float]]]) -> None:
        super().__init__(docs)
        self.calls: list[tuple[str, int]] = []

    def similarity_search(self, query: str, k: int = 5) -> list[tuple[Document, float]]:
        self.calls.append((query, k))
        return super().similarity_search(query, k=k)


def _docs(n: int, chars: int = 400, query: str = QUERY) -> dict[str, list[tuple[Document, float]]]:
    """*n* docs of ~``chars/4`` tokens each, scores descending."""
    results = [
        (
            Document(
                page_content=f"{i:03d} " + "w" * (chars - 4),
                metadata={"source": f"doc{i}.txt", "chunk_id": str(i)},
            ),
            round(1.0 - i * 0.01, 4),
        )
        for i in range(n)
    ]
    return {query: results}


def _enabled(**kw: Any) -> Settings:
    base: dict[str, Any] = {
        "token_efficiency_enabled": True,
        "rag_context_budget_enabled": True,
    }
    base.update(kw)
    return Settings(**base)


def _tokens(chunks: list[RetrievedChunk]) -> int:
    return sum(estimate_tokens(c.content) for c in chunks)


# ── Helpers ───────────────────────────────────────────────────────────────────


def test_context_budget_active_requires_both_real_booleans() -> None:
    assert context_budget_active(_enabled()) is True
    assert context_budget_active(Settings()) is False
    assert context_budget_active(Settings(rag_context_budget_enabled=True)) is False
    assert context_budget_active(Settings(token_efficiency_enabled=True)) is False
    # MagicMock settings (used throughout legacy tests) never count as enabled.
    assert context_budget_active(MagicMock()) is False
    assert context_budget_active(None) is False


def test_effective_k() -> None:
    assert effective_k(5, Settings()) == 5
    assert effective_k(5, MagicMock()) == 5
    assert effective_k(5, _enabled()) == 20
    assert effective_k(5, _enabled(rag_retrieve_k=12)) == 12


def test_apply_context_budget_disabled_returns_same_chunks() -> None:
    chunks = [RetrievedChunk("s", str(i), 0.5, "x" * 4000) for i in range(3)]
    out = apply_context_budget(QUERY, chunks, Settings())
    assert out == chunks
    assert apply_context_budget(QUERY, chunks, MagicMock()) == chunks


def test_apply_context_budget_enabled_fits_budget() -> None:
    chunks = [RetrievedChunk("s", str(i), 1 - i / 10, "x" * 400) for i in range(10)]
    out = apply_context_budget(QUERY, chunks, _enabled(rag_context_budget_tokens=350))
    assert _tokens(out) <= 350
    assert [c.chunk_id for c in out] == ["0", "1", "2"]


def test_rerank_extra_missing_falls_back_without_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "sentence_transformers", None)
    chunks = [
        RetrievedChunk("s", "low", 0.1, "x" * 400),
        RetrievedChunk("s", "high", 0.9, "x" * 400),
    ]
    settings = _enabled(rag_rerank_enabled=True, rag_context_budget_tokens=100)
    out = apply_context_budget(QUERY, chunks, settings)
    assert [c.chunk_id for c in out] == ["high"]


class _RecordingCompressor:
    instances: list[_RecordingCompressor] = []

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.seen: list[str] = []
        _RecordingCompressor.instances.append(self)

    def compress(self, text: str, *, target_tokens: int) -> str:
        self.seen.append(text)
        return "compressed prose"


def test_compression_only_on_prose(monkeypatch: pytest.MonkeyPatch) -> None:
    import prismal.providers.compression as compression

    _RecordingCompressor.instances = []
    monkeypatch.setattr(compression, "PassageCompressor", _RecordingCompressor)
    chunks = [
        RetrievedChunk("s", "prose", 0.9, PROSE),
        RetrievedChunk("s", "code", 0.8, CODE),
    ]
    settings = _enabled(rag_passage_compression_enabled=True, rag_context_budget_tokens=10_000)
    out = apply_context_budget(QUERY, chunks, settings)

    assert len(_RecordingCompressor.instances) == 1
    assert _RecordingCompressor.instances[0].seen == [PROSE]
    by_id = {c.chunk_id: c.content for c in out}
    assert by_id["prose"] == "compressed prose"
    assert by_id["code"] == CODE


def test_compressor_not_built_when_compression_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import prismal.providers.compression as compression

    _RecordingCompressor.instances = []
    monkeypatch.setattr(compression, "PassageCompressor", _RecordingCompressor)
    chunks = [RetrievedChunk("s", "prose", 0.9, PROSE)]
    out = apply_context_budget(QUERY, chunks, _enabled(rag_context_budget_tokens=10_000))
    assert _RecordingCompressor.instances == []
    assert out[0].content == PROSE


# ── RAGEngine ─────────────────────────────────────────────────────────────────


def _engine(settings: Any, store: RecordingStore) -> Any:
    from prismal.rag.engine import RAGEngine

    with patch("prismal.rag.engine.VectorStoreFactory") as factory:
        factory.create.return_value = store
        return RAGEngine(collection_name="t", settings=settings)


def test_engine_search_enabled_retrieves_retrieve_k_and_fits_budget() -> None:
    store = RecordingStore(_docs(30))
    engine = _engine(_enabled(rag_context_budget_tokens=500), store)

    chunks = engine.search(QUERY, k=5)

    assert store.calls == [(QUERY, 20)]
    assert chunks
    assert _tokens(chunks) <= 500
    assert [c.chunk_id for c in chunks] == ["0", "1", "2", "3", "4"]


def test_engine_search_enabled_honours_custom_retrieve_k() -> None:
    store = RecordingStore(_docs(30))
    engine = _engine(_enabled(rag_retrieve_k=7), store)
    engine.search(QUERY, k=5)
    assert store.calls == [(QUERY, 7)]


@pytest.mark.parametrize(
    "make_settings",
    [
        Settings,
        lambda: Settings(rag_context_budget_enabled=True),  # master off
        MagicMock,
    ],
    ids=["default", "master-off", "mock"],
)
def test_engine_search_disabled_is_unchanged(make_settings: Any) -> None:
    store = RecordingStore(_docs(30))
    engine = _engine(make_settings(), store)

    chunks = engine.search(QUERY, k=5)

    assert store.calls == [(QUERY, 5)]
    expected = [
        RetrievedChunk(
            source=doc.metadata["source"],
            chunk_id=doc.metadata["chunk_id"],
            relevance_score=score,
            content=doc.page_content,
        )
        for doc, score in _docs(30)[QUERY][:5]
    ]
    assert chunks == expected


# ── CRAG pipeline ─────────────────────────────────────────────────────────────


def _mock_llm() -> MagicMock:
    def _resp(*_a: Any, **_kw: Any) -> MagicMock:
        r = MagicMock()
        r.content = "0.9"
        return r

    llm = MagicMock()
    llm.ainvoke = AsyncMock(side_effect=_resp)
    return llm


async def test_crag_enabled_retrieves_retrieve_k_and_budgets_context() -> None:
    from prismal.rag.crag import CRAGPipeline

    store = RecordingStore(_docs(30))
    llm = _mock_llm()
    with patch("prismal.rag.crag.ProviderRegistry") as reg:
        reg.return_value.get_llm.return_value = llm
        pipeline = CRAGPipeline(
            vector_store=store, settings=_enabled(rag_context_budget_tokens=300)
        )
        result = await pipeline.run(QUERY)

    assert store.calls == [(QUERY, 20)]
    assert not result.used_web_fallback
    assert 0 < len(result.sources) <= 3
    assert _tokens(result.sources) <= 300
    # Only budgeted chunks are graded: one grade call per source + one generate.
    assert llm.ainvoke.await_count == len(result.sources) + 1


async def test_crag_disabled_retrieves_five() -> None:
    from prismal.rag.crag import CRAGPipeline

    store = RecordingStore(_docs(30))
    llm = _mock_llm()
    with patch("prismal.rag.crag.ProviderRegistry") as reg:
        reg.return_value.get_llm.return_value = llm
        pipeline = CRAGPipeline(vector_store=store, settings=Settings())
        result = await pipeline.run(QUERY)

    assert store.calls == [(QUERY, 5)]
    assert len(result.sources) == 5
    assert llm.ainvoke.await_count == 6


# ── HyDE ──────────────────────────────────────────────────────────────────────


def _hyde(settings: Any, store: RecordingStore) -> Any:
    from prismal.rag.hyde import HyDERetriever

    llm = MagicMock()
    resp = MagicMock()
    resp.content = "HYPO"
    llm.ainvoke = AsyncMock(return_value=resp)
    emb = MagicMock()
    emb.embed_query = MagicMock(return_value=[0.1, 0.2])
    with (
        patch("prismal.rag.hyde.ProviderRegistry") as reg,
        patch("prismal.rag.hyde.EmbeddingsFactory") as ef,
    ):
        reg.return_value.get_llm.return_value = llm
        ef.create.return_value = emb
        return HyDERetriever(vector_store=store, settings=settings)


async def test_hyde_enabled_retrieves_retrieve_k_and_fits_budget() -> None:
    store = RecordingStore(_docs(30, query="HYPO"))
    retriever = _hyde(_enabled(rag_context_budget_tokens=250), store)
    result = await retriever.search(QUERY, k=5)
    assert store.calls == [("HYPO", 20)]
    assert result.chunks
    assert _tokens(result.chunks) <= 250


async def test_hyde_disabled_is_unchanged() -> None:
    store = RecordingStore(_docs(30, query="HYPO"))
    retriever = _hyde(Settings(), store)
    result = await retriever.search(QUERY, k=5)
    assert store.calls == [("HYPO", 5)]
    assert len(result.chunks) == 5


# ── Hybrid ────────────────────────────────────────────────────────────────────


def test_hybrid_semantic_only_enabled_fits_budget() -> None:
    from prismal.rag.hybrid import HybridSearchEngine

    store = RecordingStore(_docs(30))
    engine = HybridSearchEngine(
        vector_store=store, settings=_enabled(rag_context_budget_tokens=250)
    )
    results = engine.search(QUERY, k=5)
    assert store.calls == [(QUERY, 20)]
    assert results
    assert _tokens(results) <= 250


def test_hybrid_with_bm25_enabled_fits_budget() -> None:
    pytest.importorskip("rank_bm25")
    from prismal.rag.hybrid import HybridSearchEngine

    docs = _docs(30)
    store = RecordingStore(docs)
    engine = HybridSearchEngine(
        vector_store=store, settings=_enabled(rag_context_budget_tokens=250)
    )
    engine.build_index(
        [d.page_content for d, _ in docs[QUERY]],
        [f"{d.metadata['source']}:{d.metadata['chunk_id']}" for d, _ in docs[QUERY]],
    )
    results = engine.search("revenue trend 001", k=5)
    assert store.calls == [("revenue trend 001", 20)]
    assert _tokens(results) <= 250


def test_hybrid_disabled_is_unchanged() -> None:
    from prismal.rag.hybrid import HybridSearchEngine

    store = RecordingStore(_docs(30))
    engine = HybridSearchEngine(vector_store=store, settings=Settings())
    results = engine.search(QUERY, k=5)
    assert store.calls == [(QUERY, 5)]
    assert len(results) == 5

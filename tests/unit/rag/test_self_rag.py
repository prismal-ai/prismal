"""Unit tests for SelfRAGPipeline (lightagent.rag.self_rag).

Self-RAG decides whether to retrieve before generating, and self-assesses
the produced answer. Full pipeline::

    query
      -> _decide_retrieval -> RETRIEVE | NO_RETRIEVE
      -> (if RETRIEVE) delegate to CRAGPipeline
      -> generate / collect answer
      -> _assess_support -> SUPPORTED | PARTIALLY | UNSUPPORTED + utility 1-5
      -> SelfRAGResult

External components (LLM, CRAGPipeline, vector store) are mocked.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lightagent.core.exceptions import RAGError, SelfRAGError
from lightagent.rag.crag import CRAGResult, RetrievedChunk
from lightagent.rag.self_rag import (
    RetrievalDecision,
    SelfRAGPipeline,
    SelfRAGResult,
    SupportedDecision,
)

PROVIDER_REGISTRY_PATH = "lightagent.rag.self_rag.ProviderRegistry"


# ── Helpers ───────────────────────────────────────────────────────────────────


def _llm_response(text: str) -> MagicMock:
    r = MagicMock()
    r.content = text
    return r


def _mock_llm_with_scripted_responses(*texts: str) -> MagicMock:
    """Return an LLM whose ainvoke yields texts in order."""
    llm = MagicMock()
    llm.ainvoke = AsyncMock(side_effect=[_llm_response(t) for t in texts])
    return llm


def _mock_crag_returning(answer: str, sources: list[RetrievedChunk]) -> MagicMock:
    crag = MagicMock()
    crag.run = AsyncMock(
        return_value=CRAGResult(answer=answer, sources=sources, used_web_fallback=False)
    )
    return crag


# ── Enums + dataclass ─────────────────────────────────────────────────────────


def test_retrieval_decision_values() -> None:
    assert RetrievalDecision.RETRIEVE.value == "RETRIEVE"
    assert RetrievalDecision.NO_RETRIEVE.value == "NO_RETRIEVE"


def test_supported_decision_values() -> None:
    assert SupportedDecision.SUPPORTED.value == "SUPPORTED"
    assert SupportedDecision.PARTIALLY_SUPPORTED.value == "PARTIALLY_SUPPORTED"
    assert SupportedDecision.UNSUPPORTED.value == "UNSUPPORTED"


def test_self_rag_result_is_instantiable() -> None:
    chunk = RetrievedChunk(source="f", chunk_id="0", relevance_score=0.8, content="c")
    r = SelfRAGResult(
        answer="ans",
        retrieval_decision=RetrievalDecision.RETRIEVE,
        supported_decision=SupportedDecision.SUPPORTED,
        utility_score=4,
        sources=[chunk],
    )
    assert r.answer == "ans"
    assert r.retrieval_decision is RetrievalDecision.RETRIEVE
    assert r.supported_decision is SupportedDecision.SUPPORTED
    assert r.utility_score == 4
    assert r.sources == [chunk]
    assert r.used_fallback is False


def test_self_rag_error_inherits_from_ragerror() -> None:
    assert issubclass(SelfRAGError, RAGError)


# ── Constructor ───────────────────────────────────────────────────────────────


def test_init_uses_provided_crag_pipeline() -> None:
    store = MagicMock()
    crag = MagicMock()
    with patch(PROVIDER_REGISTRY_PATH) as reg:
        reg.return_value.get_llm.return_value = MagicMock()
        p = SelfRAGPipeline(vector_store=store, crag_pipeline=crag, settings=MagicMock())
    assert p._crag is crag  # noqa: SLF001


def test_init_builds_default_crag_when_none_provided() -> None:
    store = MagicMock()
    with (
        patch(PROVIDER_REGISTRY_PATH) as reg,
        patch("lightagent.rag.self_rag.CRAGPipeline") as mock_crag_cls,
    ):
        reg.return_value.get_llm.return_value = MagicMock()
        mock_crag_cls.return_value = "built-crag"
        p = SelfRAGPipeline(vector_store=store, settings=MagicMock())
    assert p._crag == "built-crag"  # noqa: SLF001
    mock_crag_cls.assert_called_once()


# ── _decide_retrieval ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_decide_retrieval_parses_retrieve_token() -> None:
    store = MagicMock()
    llm = _mock_llm_with_scripted_responses("RETRIEVE")
    with patch(PROVIDER_REGISTRY_PATH) as reg:
        reg.return_value.get_llm.return_value = llm
        p = SelfRAGPipeline(
            vector_store=store, crag_pipeline=MagicMock(), settings=MagicMock()
        )
        decision, fallback = await p._decide_retrieval("q")  # noqa: SLF001
    assert decision is RetrievalDecision.RETRIEVE
    assert fallback is False


@pytest.mark.asyncio
async def test_decide_retrieval_parses_no_retrieve_token() -> None:
    store = MagicMock()
    llm = _mock_llm_with_scripted_responses("NO_RETRIEVE")
    with patch(PROVIDER_REGISTRY_PATH) as reg:
        reg.return_value.get_llm.return_value = llm
        p = SelfRAGPipeline(
            vector_store=store, crag_pipeline=MagicMock(), settings=MagicMock()
        )
        decision, fallback = await p._decide_retrieval("q")  # noqa: SLF001
    assert decision is RetrievalDecision.NO_RETRIEVE
    assert fallback is False


@pytest.mark.asyncio
async def test_decide_retrieval_is_permissive_about_surrounding_text() -> None:
    """Token may appear anywhere in the response."""
    store = MagicMock()
    llm = _mock_llm_with_scripted_responses("My decision: RETRIEVE — need external facts.")
    with patch(PROVIDER_REGISTRY_PATH) as reg:
        reg.return_value.get_llm.return_value = llm
        p = SelfRAGPipeline(
            vector_store=store, crag_pipeline=MagicMock(), settings=MagicMock()
        )
        decision, _ = await p._decide_retrieval("q")  # noqa: SLF001
    assert decision is RetrievalDecision.RETRIEVE


@pytest.mark.asyncio
async def test_decide_retrieval_falls_back_to_retrieve_when_unparseable() -> None:
    """If no recognisable token, default to RETRIEVE (safer than hallucination).

    The fallback flag must be set so the caller can mark used_fallback=True.
    """
    store = MagicMock()
    llm = _mock_llm_with_scripted_responses("Sure, I can help with that.")
    with patch(PROVIDER_REGISTRY_PATH) as reg:
        reg.return_value.get_llm.return_value = llm
        p = SelfRAGPipeline(
            vector_store=store, crag_pipeline=MagicMock(), settings=MagicMock()
        )
        decision, fallback = await p._decide_retrieval("q")  # noqa: SLF001
    assert decision is RetrievalDecision.RETRIEVE
    assert fallback is True


@pytest.mark.asyncio
async def test_decide_retrieval_falls_back_when_llm_raises() -> None:
    store = MagicMock()
    llm = MagicMock()
    llm.ainvoke = AsyncMock(side_effect=RuntimeError("boom"))
    with patch(PROVIDER_REGISTRY_PATH) as reg:
        reg.return_value.get_llm.return_value = llm
        p = SelfRAGPipeline(
            vector_store=store, crag_pipeline=MagicMock(), settings=MagicMock()
        )
        decision, fallback = await p._decide_retrieval("q")  # noqa: SLF001
    assert decision is RetrievalDecision.RETRIEVE
    assert fallback is True


# ── _assess_support ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_assess_support_parses_supported_with_utility() -> None:
    store = MagicMock()
    llm = _mock_llm_with_scripted_responses("SUPPORTED Utility:5")
    with patch(PROVIDER_REGISTRY_PATH) as reg:
        reg.return_value.get_llm.return_value = llm
        p = SelfRAGPipeline(
            vector_store=store, crag_pipeline=MagicMock(), settings=MagicMock()
        )
        support, utility = await p._assess_support("q", "ans", [])  # noqa: SLF001
    assert support is SupportedDecision.SUPPORTED
    assert utility == 5


@pytest.mark.asyncio
async def test_assess_support_parses_partial() -> None:
    store = MagicMock()
    llm = _mock_llm_with_scripted_responses("PARTIALLY_SUPPORTED Utility:3")
    with patch(PROVIDER_REGISTRY_PATH) as reg:
        reg.return_value.get_llm.return_value = llm
        p = SelfRAGPipeline(
            vector_store=store, crag_pipeline=MagicMock(), settings=MagicMock()
        )
        support, utility = await p._assess_support("q", "ans", [])  # noqa: SLF001
    assert support is SupportedDecision.PARTIALLY_SUPPORTED
    assert utility == 3


@pytest.mark.asyncio
async def test_assess_support_unparseable_defaults_to_unsupported_utility_1() -> None:
    store = MagicMock()
    llm = _mock_llm_with_scripted_responses("I'm not sure")
    with patch(PROVIDER_REGISTRY_PATH) as reg:
        reg.return_value.get_llm.return_value = llm
        p = SelfRAGPipeline(
            vector_store=store, crag_pipeline=MagicMock(), settings=MagicMock()
        )
        support, utility = await p._assess_support("q", "ans", [])  # noqa: SLF001
    assert support is SupportedDecision.UNSUPPORTED
    assert utility == 1


@pytest.mark.asyncio
async def test_assess_support_clamps_utility_to_1_5_range() -> None:
    store = MagicMock()
    llm = _mock_llm_with_scripted_responses("SUPPORTED Utility:99")
    with patch(PROVIDER_REGISTRY_PATH) as reg:
        reg.return_value.get_llm.return_value = llm
        p = SelfRAGPipeline(
            vector_store=store, crag_pipeline=MagicMock(), settings=MagicMock()
        )
        _, utility = await p._assess_support("q", "ans", [])  # noqa: SLF001
    assert 1 <= utility <= 5


# ── run() orchestration ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_no_retrieve_path_skips_crag() -> None:
    """NO_RETRIEVE -> CRAG never called; answer generated directly."""
    store = MagicMock()
    crag = MagicMock()
    crag.run = AsyncMock()
    # 1) decision, 2) generation without context, 3) support assessment.
    llm = _mock_llm_with_scripted_responses(
        "NO_RETRIEVE",
        "2 + 2 is 4.",
        "SUPPORTED Utility:5",
    )
    with patch(PROVIDER_REGISTRY_PATH) as reg:
        reg.return_value.get_llm.return_value = llm
        p = SelfRAGPipeline(vector_store=store, crag_pipeline=crag, settings=MagicMock())
        result = await p.run("What is 2+2?")
    crag.run.assert_not_called()
    assert result.retrieval_decision is RetrievalDecision.NO_RETRIEVE
    assert result.sources == []
    assert result.answer == "2 + 2 is 4."
    assert result.used_fallback is False


@pytest.mark.asyncio
async def test_run_retrieve_path_uses_crag() -> None:
    store = MagicMock()
    chunk = RetrievedChunk(source="doc.txt", chunk_id="0", relevance_score=0.9, content="ctx")
    crag = _mock_crag_returning(answer="CRAG answer", sources=[chunk])
    # Decision + support assessment (no separate generation — CRAG already generated).
    llm = _mock_llm_with_scripted_responses("RETRIEVE", "SUPPORTED Utility:4")
    with patch(PROVIDER_REGISTRY_PATH) as reg:
        reg.return_value.get_llm.return_value = llm
        p = SelfRAGPipeline(vector_store=store, crag_pipeline=crag, settings=MagicMock())
        result = await p.run("Specific question about the corpus")
    crag.run.assert_called_once()
    assert result.retrieval_decision is RetrievalDecision.RETRIEVE
    assert result.answer == "CRAG answer"
    assert result.sources == [chunk]
    assert result.used_fallback is False


@pytest.mark.asyncio
async def test_run_sets_used_fallback_when_decision_unparseable() -> None:
    """Unparseable decision -> default to RETRIEVE AND set used_fallback=True."""
    store = MagicMock()
    crag = _mock_crag_returning(answer="CRAG answer", sources=[])
    llm = _mock_llm_with_scripted_responses("maybe?", "SUPPORTED Utility:3")
    with patch(PROVIDER_REGISTRY_PATH) as reg:
        reg.return_value.get_llm.return_value = llm
        p = SelfRAGPipeline(vector_store=store, crag_pipeline=crag, settings=MagicMock())
        result = await p.run("q")
    assert result.retrieval_decision is RetrievalDecision.RETRIEVE
    assert result.used_fallback is True


@pytest.mark.asyncio
async def test_run_raises_selfrag_error_when_generation_fails_on_no_retrieve() -> None:
    """NO_RETRIEVE path LLM failure during generation -> SelfRAGError."""
    store = MagicMock()
    llm = MagicMock()
    llm.ainvoke = AsyncMock(
        side_effect=[
            _llm_response("NO_RETRIEVE"),
            RuntimeError("generation boom"),
        ]
    )
    with patch(PROVIDER_REGISTRY_PATH) as reg:
        reg.return_value.get_llm.return_value = llm
        p = SelfRAGPipeline(
            vector_store=store, crag_pipeline=MagicMock(), settings=MagicMock()
        )
        with pytest.raises(SelfRAGError):
            await p.run("q")

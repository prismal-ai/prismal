"""Unit tests for CRAGPipeline (lightagent.rag.crag).

Tests follow the TDD spec from T-063.  ChromaVectorStore, ProviderRegistry,
and the LLM are all mocked so no real API calls or vector DB connections are
made.

Coverage targets:
- RetrievedChunk and CRAGResult dataclasses can be instantiated
- CRAGPipeline.__init__ stores the vector store and settings
- CRAGPipeline.run() calls similarity_search with query and k=5
- Grading: LLM is called once per retrieved document
- Filtering: chunks with score < 0.5 are excluded
- When all chunks are filtered out, web fallback is triggered
- When some chunks pass filtering, used_web_fallback=False
- Generation: LLM is called with a prompt containing the query and context
- The returned CRAGResult has answer, sources, used_web_fallback
- sources only contains chunks that passed the filter (or web fallback chunks)
- Each RetrievedChunk in sources has source, chunk_id, relevance_score, content
- LLM grading parse failure falls back to the similarity score
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lightagent.rag.crag import CRAGPipeline, CRAGResult, RetrievedChunk

# ── Paths for patching ─────────────────────────────────────────────────────────

PROVIDER_REGISTRY_PATH = "lightagent.rag.crag.ProviderRegistry"

# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_document(
    content: str = "doc content",
    source: str = "file.txt",
    chunk_id: str | None = None,
) -> MagicMock:
    """Return a mock Document with page_content and metadata."""
    doc = MagicMock()
    doc.page_content = content
    metadata: dict[str, str] = {"source": source}
    if chunk_id is not None:
        metadata["chunk_id"] = chunk_id
    doc.metadata = metadata
    return doc


def _make_llm_response(text: str) -> MagicMock:
    """Return a mock LLM response with .content set."""
    response = MagicMock()
    response.content = text
    return response


def _make_mock_llm(grade_responses: list[str], generate_response: str) -> AsyncMock:
    """Build an async LLM mock that returns grade_responses then generate_response."""
    llm = MagicMock()
    # ainvoke returns coroutines; configure side_effect list
    side_effects = [
        _make_llm_response(r) for r in grade_responses
    ] + [_make_llm_response(generate_response)]
    llm.ainvoke = AsyncMock(side_effect=side_effects)
    return llm


def _make_settings() -> MagicMock:
    """Return a minimal mock Settings object."""
    return MagicMock()


# ── Dataclass instantiation ───────────────────────────────────────────────────


def test_retrieved_chunk_can_be_instantiated() -> None:
    """RetrievedChunk must be instantiable with all four fields."""
    chunk = RetrievedChunk(
        source="file.txt",
        chunk_id="0",
        relevance_score=0.9,
        content="some content",
    )
    assert chunk.source == "file.txt"
    assert chunk.chunk_id == "0"
    assert chunk.relevance_score == 0.9
    assert chunk.content == "some content"


def test_crag_result_can_be_instantiated() -> None:
    """CRAGResult must be instantiable with answer, sources, and used_web_fallback."""
    chunk = RetrievedChunk(
        source="file.txt", chunk_id="0", relevance_score=0.8, content="content"
    )
    result = CRAGResult(
        answer="The answer",
        sources=[chunk],
        used_web_fallback=False,
    )
    assert result.answer == "The answer"
    assert result.sources == [chunk]
    assert result.used_web_fallback is False


@pytest.mark.asyncio
async def test_grade_score_clamped_above_1_0() -> None:
    """When LLM returns a value > 1.0, grade score must be clamped to 1.0."""
    doc = _make_document("content", "file.txt")
    mock_store = MagicMock()
    mock_store.similarity_search.return_value = [(doc, 0.9)]

    mock_llm = _make_mock_llm(
        grade_responses=["1.5"],
        generate_response="answer",
    )

    with patch(PROVIDER_REGISTRY_PATH) as mock_registry_cls:
        mock_registry_cls.return_value.get_llm.return_value = mock_llm
        pipeline = CRAGPipeline(vector_store=mock_store)
        result = await pipeline.run("query")

    assert len(result.sources) == 1
    assert result.sources[0].relevance_score == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_grade_score_clamped_below_0_0() -> None:
    """When LLM returns a value < 0.0, grade score must be clamped to 0.0."""
    doc = _make_document("content", "file.txt")
    mock_store = MagicMock()
    mock_store.similarity_search.return_value = [(doc, 0.9)]

    mock_llm = _make_mock_llm(
        grade_responses=["-0.2"],
        generate_response="answer",
    )

    with patch(PROVIDER_REGISTRY_PATH) as mock_registry_cls:
        mock_registry_cls.return_value.get_llm.return_value = mock_llm
        pipeline = CRAGPipeline(vector_store=mock_store)
        result = await pipeline.run("query")

    # Score clamped to 0.0 which is below the 0.5 threshold, so web fallback fires
    assert result.used_web_fallback is True
    assert result.sources[0].source == "web_fallback"


# ── CRAGPipeline.__init__ ─────────────────────────────────────────────────────


def test_init_stores_vector_store() -> None:
    """CRAGPipeline.__init__ must store the provided vector store."""
    mock_store = MagicMock()
    with patch(PROVIDER_REGISTRY_PATH):
        pipeline = CRAGPipeline(vector_store=mock_store)
    assert pipeline._store is mock_store


def test_init_stores_settings_when_provided() -> None:
    """CRAGPipeline.__init__ must store explicitly provided Settings."""
    mock_store = MagicMock()
    mock_settings = _make_settings()
    with patch(PROVIDER_REGISTRY_PATH):
        pipeline = CRAGPipeline(vector_store=mock_store, settings=mock_settings)
    assert pipeline._settings is mock_settings


def test_init_uses_get_settings_when_none() -> None:
    """CRAGPipeline.__init__(settings=None) must call get_settings() internally."""
    mock_store = MagicMock()
    fake_settings = _make_settings()
    with (
        patch(PROVIDER_REGISTRY_PATH),
        patch(
            "lightagent.rag.crag.get_settings", return_value=fake_settings
        ) as mock_gs,
    ):
        CRAGPipeline(vector_store=mock_store, settings=None)
    mock_gs.assert_called_once()


# ── Step 1: RETRIEVE ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_calls_similarity_search_with_query_and_k5() -> None:
    """run() must call similarity_search(query, k=5)."""
    mock_store = MagicMock()
    mock_store.similarity_search.return_value = []

    mock_llm = _make_mock_llm(grade_responses=[], generate_response="answer")

    with patch(PROVIDER_REGISTRY_PATH) as mock_registry_cls:
        mock_registry_cls.return_value.get_llm.return_value = mock_llm
        pipeline = CRAGPipeline(vector_store=mock_store)
        await pipeline.run("test query")

    mock_store.similarity_search.assert_called_once_with("test query", k=5)


@pytest.mark.asyncio
async def test_run_passes_query_string_to_similarity_search() -> None:
    """run() must forward the exact query string to similarity_search."""
    mock_store = MagicMock()
    mock_store.similarity_search.return_value = []

    mock_llm = _make_mock_llm(grade_responses=[], generate_response="answer")

    with patch(PROVIDER_REGISTRY_PATH) as mock_registry_cls:
        mock_registry_cls.return_value.get_llm.return_value = mock_llm
        pipeline = CRAGPipeline(vector_store=mock_store)
        await pipeline.run("what is RAG?")

    call_args = mock_store.similarity_search.call_args
    positional_match = call_args.args[0] == "what is RAG?"
    keyword_match = call_args.kwargs.get("query") == "what is RAG?"
    assert positional_match or keyword_match


# ── Step 2: GRADE ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_grading_llm_called_once_per_retrieved_document() -> None:
    """run() must call llm.ainvoke once per document retrieved."""
    doc1 = _make_document("content 1", "file1.txt")
    doc2 = _make_document("content 2", "file2.txt")
    mock_store = MagicMock()
    mock_store.similarity_search.return_value = [(doc1, 0.8), (doc2, 0.9)]

    # 2 grade calls + 1 generate call = 3 total
    mock_llm = _make_mock_llm(
        grade_responses=["0.8", "0.9"],
        generate_response="generated answer",
    )

    with patch(PROVIDER_REGISTRY_PATH) as mock_registry_cls:
        mock_registry_cls.return_value.get_llm.return_value = mock_llm
        pipeline = CRAGPipeline(vector_store=mock_store)
        await pipeline.run("query")

    # ainvoke called 2 times for grading + 1 for generation = 3
    assert mock_llm.ainvoke.call_count == 3


@pytest.mark.asyncio
async def test_grading_uses_query_and_document_content_in_prompt() -> None:
    """Grading prompt must contain both the query and the document content."""
    doc = _make_document("important content", "doc.txt")
    mock_store = MagicMock()
    mock_store.similarity_search.return_value = [(doc, 0.75)]

    mock_llm = _make_mock_llm(
        grade_responses=["0.8"],
        generate_response="answer",
    )

    with patch(PROVIDER_REGISTRY_PATH) as mock_registry_cls:
        mock_registry_cls.return_value.get_llm.return_value = mock_llm
        pipeline = CRAGPipeline(vector_store=mock_store)
        await pipeline.run("my query")

    # First call to ainvoke is for grading
    first_call_args = mock_llm.ainvoke.call_args_list[0]
    messages = first_call_args.args[0]
    # The messages should contain query and document content
    full_text = " ".join(str(m) for m in messages)
    assert "my query" in full_text
    assert "important content" in full_text


# ── Step 3: FILTER ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_chunks_below_threshold_are_excluded_from_sources() -> None:
    """Chunks with relevance_score < 0.5 must not appear in CRAGResult.sources."""
    doc_high = _make_document("high relevance", "high.txt")
    doc_low = _make_document("low relevance", "low.txt")
    mock_store = MagicMock()
    mock_store.similarity_search.return_value = [(doc_high, 0.9), (doc_low, 0.8)]

    # Grade: 0.8 passes, 0.3 fails
    mock_llm = _make_mock_llm(
        grade_responses=["0.8", "0.3"],
        generate_response="answer",
    )

    with patch(PROVIDER_REGISTRY_PATH) as mock_registry_cls:
        mock_registry_cls.return_value.get_llm.return_value = mock_llm
        pipeline = CRAGPipeline(vector_store=mock_store)
        result = await pipeline.run("query")

    assert len(result.sources) == 1
    assert result.sources[0].content == "high relevance"
    assert result.sources[0].relevance_score == pytest.approx(0.8)


@pytest.mark.asyncio
async def test_chunks_at_threshold_are_included() -> None:
    """Chunks with relevance_score == 0.5 must be included (inclusive threshold)."""
    doc = _make_document("exactly at threshold", "doc.txt")
    mock_store = MagicMock()
    mock_store.similarity_search.return_value = [(doc, 0.5)]

    mock_llm = _make_mock_llm(
        grade_responses=["0.5"],
        generate_response="answer",
    )

    with patch(PROVIDER_REGISTRY_PATH) as mock_registry_cls:
        mock_registry_cls.return_value.get_llm.return_value = mock_llm
        pipeline = CRAGPipeline(vector_store=mock_store)
        result = await pipeline.run("query")

    assert result.used_web_fallback is False
    assert len(result.sources) == 1
    assert result.sources[0].relevance_score == pytest.approx(0.5)


# ── Step 4: DECIDE ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_web_fallback_triggered_when_all_chunks_filtered() -> None:
    """When all chunks score below 0.5, used_web_fallback must be True."""
    doc = _make_document("irrelevant content", "file.txt")
    mock_store = MagicMock()
    mock_store.similarity_search.return_value = [(doc, 0.9)]

    # Grade returns below threshold
    mock_llm = _make_mock_llm(
        grade_responses=["0.2"],
        generate_response="fallback answer",
    )

    with patch(PROVIDER_REGISTRY_PATH) as mock_registry_cls:
        mock_registry_cls.return_value.get_llm.return_value = mock_llm
        pipeline = CRAGPipeline(vector_store=mock_store)
        result = await pipeline.run("query")

    assert result.used_web_fallback is True


@pytest.mark.asyncio
async def test_web_fallback_triggered_when_no_documents_retrieved() -> None:
    """When similarity_search returns empty, used_web_fallback must be True."""
    mock_store = MagicMock()
    mock_store.similarity_search.return_value = []

    mock_llm = _make_mock_llm(grade_responses=[], generate_response="answer")

    with patch(PROVIDER_REGISTRY_PATH) as mock_registry_cls:
        mock_registry_cls.return_value.get_llm.return_value = mock_llm
        pipeline = CRAGPipeline(vector_store=mock_store)
        result = await pipeline.run("query")

    assert result.used_web_fallback is True


@pytest.mark.asyncio
async def test_no_web_fallback_when_some_chunks_pass_filter() -> None:
    """When at least one chunk passes filtering, used_web_fallback must be False."""
    doc1 = _make_document("relevant", "good.txt")
    doc2 = _make_document("irrelevant", "bad.txt")
    mock_store = MagicMock()
    mock_store.similarity_search.return_value = [(doc1, 0.9), (doc2, 0.8)]

    mock_llm = _make_mock_llm(
        grade_responses=["0.9", "0.1"],
        generate_response="answer",
    )

    with patch(PROVIDER_REGISTRY_PATH) as mock_registry_cls:
        mock_registry_cls.return_value.get_llm.return_value = mock_llm
        pipeline = CRAGPipeline(vector_store=mock_store)
        result = await pipeline.run("query")

    assert result.used_web_fallback is False


@pytest.mark.asyncio
async def test_web_fallback_chunk_has_correct_source() -> None:
    """Web fallback chunk must have source='web_fallback'."""
    mock_store = MagicMock()
    mock_store.similarity_search.return_value = []

    mock_llm = _make_mock_llm(grade_responses=[], generate_response="answer")

    with patch(PROVIDER_REGISTRY_PATH) as mock_registry_cls:
        mock_registry_cls.return_value.get_llm.return_value = mock_llm
        pipeline = CRAGPipeline(vector_store=mock_store)
        result = await pipeline.run("my query")

    assert len(result.sources) == 1
    assert result.sources[0].source == "web_fallback"
    assert result.sources[0].chunk_id == "0"
    assert result.sources[0].relevance_score == pytest.approx(0.7)
    assert "stub" in result.sources[0].content


# ── Step 5: GENERATE ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_generate_llm_called_with_query_in_prompt() -> None:
    """Generation LLM call must include the original query in the prompt."""
    doc = _make_document("relevant content", "doc.txt")
    mock_store = MagicMock()
    mock_store.similarity_search.return_value = [(doc, 0.9)]

    mock_llm = _make_mock_llm(
        grade_responses=["0.9"],
        generate_response="the final answer",
    )

    with patch(PROVIDER_REGISTRY_PATH) as mock_registry_cls:
        mock_registry_cls.return_value.get_llm.return_value = mock_llm
        pipeline = CRAGPipeline(vector_store=mock_store)
        await pipeline.run("specific query string")

    # Last ainvoke call is the generation call
    last_call_args = mock_llm.ainvoke.call_args_list[-1]
    messages = last_call_args.args[0]
    full_text = " ".join(str(m) for m in messages)
    assert "specific query string" in full_text


@pytest.mark.asyncio
async def test_generate_llm_called_with_context_in_prompt() -> None:
    """Generation LLM call must include the filtered chunk contents."""
    doc = _make_document("the relevant document text", "doc.txt")
    mock_store = MagicMock()
    mock_store.similarity_search.return_value = [(doc, 0.9)]

    mock_llm = _make_mock_llm(
        grade_responses=["0.9"],
        generate_response="answer based on context",
    )

    with patch(PROVIDER_REGISTRY_PATH) as mock_registry_cls:
        mock_registry_cls.return_value.get_llm.return_value = mock_llm
        pipeline = CRAGPipeline(vector_store=mock_store)
        await pipeline.run("query")

    last_call_args = mock_llm.ainvoke.call_args_list[-1]
    messages = last_call_args.args[0]
    full_text = " ".join(str(m) for m in messages)
    assert "the relevant document text" in full_text


@pytest.mark.asyncio
async def test_answer_comes_from_llm_generate_response() -> None:
    """CRAGResult.answer must be the content returned by the generation LLM call."""
    doc = _make_document("content", "doc.txt")
    mock_store = MagicMock()
    mock_store.similarity_search.return_value = [(doc, 0.9)]

    mock_llm = _make_mock_llm(
        grade_responses=["0.9"],
        generate_response="this is the definitive answer",
    )

    with patch(PROVIDER_REGISTRY_PATH) as mock_registry_cls:
        mock_registry_cls.return_value.get_llm.return_value = mock_llm
        pipeline = CRAGPipeline(vector_store=mock_store)
        result = await pipeline.run("query")

    assert result.answer == "this is the definitive answer"


# ── CRAGResult structure ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_crag_result_has_answer_sources_and_used_web_fallback() -> None:
    """run() must return a CRAGResult with answer, sources, used_web_fallback."""
    mock_store = MagicMock()
    mock_store.similarity_search.return_value = []

    mock_llm = _make_mock_llm(grade_responses=[], generate_response="answer")

    with patch(PROVIDER_REGISTRY_PATH) as mock_registry_cls:
        mock_registry_cls.return_value.get_llm.return_value = mock_llm
        pipeline = CRAGPipeline(vector_store=mock_store)
        result = await pipeline.run("query")

    assert isinstance(result, CRAGResult)
    assert isinstance(result.answer, str)
    assert isinstance(result.sources, list)
    assert isinstance(result.used_web_fallback, bool)


@pytest.mark.asyncio
async def test_sources_only_contains_filtered_chunks() -> None:
    """sources must contain only chunks that passed the 0.5 filter."""
    doc1 = _make_document("passes", "good.txt")
    doc2 = _make_document("fails", "bad.txt")
    doc3 = _make_document("passes too", "also_good.txt")
    mock_store = MagicMock()
    mock_store.similarity_search.return_value = [
        (doc1, 0.9),
        (doc2, 0.8),
        (doc3, 0.7),
    ]

    mock_llm = _make_mock_llm(
        grade_responses=["0.9", "0.2", "0.7"],
        generate_response="answer",
    )

    with patch(PROVIDER_REGISTRY_PATH) as mock_registry_cls:
        mock_registry_cls.return_value.get_llm.return_value = mock_llm
        pipeline = CRAGPipeline(vector_store=mock_store)
        result = await pipeline.run("query")

    assert len(result.sources) == 2
    contents = [c.content for c in result.sources]
    assert "passes" in contents
    assert "passes too" in contents
    assert "fails" not in contents


@pytest.mark.asyncio
async def test_each_source_chunk_has_all_required_fields() -> None:
    """Each RetrievedChunk must have source, chunk_id, relevance_score, content."""
    doc = _make_document("doc content", "myfile.txt", chunk_id="chunk-42")
    mock_store = MagicMock()
    mock_store.similarity_search.return_value = [(doc, 0.8)]

    mock_llm = _make_mock_llm(
        grade_responses=["0.8"],
        generate_response="answer",
    )

    with patch(PROVIDER_REGISTRY_PATH) as mock_registry_cls:
        mock_registry_cls.return_value.get_llm.return_value = mock_llm
        pipeline = CRAGPipeline(vector_store=mock_store)
        result = await pipeline.run("query")

    assert len(result.sources) == 1
    chunk = result.sources[0]
    assert chunk.source == "myfile.txt"
    assert chunk.chunk_id == "chunk-42"
    assert chunk.relevance_score == pytest.approx(0.8)
    assert chunk.content == "doc content"


@pytest.mark.asyncio
async def test_chunk_id_falls_back_to_index_when_no_metadata() -> None:
    """chunk_id must fall back to the document index when metadata has no chunk_id."""
    doc = _make_document("content", "file.txt")
    # Ensure no chunk_id in metadata
    doc.metadata = {"source": "file.txt"}
    mock_store = MagicMock()
    mock_store.similarity_search.return_value = [(doc, 0.9)]

    mock_llm = _make_mock_llm(
        grade_responses=["0.9"],
        generate_response="answer",
    )

    with patch(PROVIDER_REGISTRY_PATH) as mock_registry_cls:
        mock_registry_cls.return_value.get_llm.return_value = mock_llm
        pipeline = CRAGPipeline(vector_store=mock_store)
        result = await pipeline.run("query")

    assert result.sources[0].chunk_id == "0"


@pytest.mark.asyncio
async def test_source_falls_back_to_unknown_when_no_metadata_source() -> None:
    """source must fall back to 'unknown' when document metadata has no 'source' key."""
    doc = _make_document("content", "file.txt")
    doc.metadata = {}  # no source
    mock_store = MagicMock()
    mock_store.similarity_search.return_value = [(doc, 0.9)]

    mock_llm = _make_mock_llm(
        grade_responses=["0.9"],
        generate_response="answer",
    )

    with patch(PROVIDER_REGISTRY_PATH) as mock_registry_cls:
        mock_registry_cls.return_value.get_llm.return_value = mock_llm
        pipeline = CRAGPipeline(vector_store=mock_store)
        result = await pipeline.run("query")

    assert result.sources[0].source == "unknown"


# ── LLM parse failure fallback ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_grading_parse_failure_falls_back_to_similarity_score() -> None:
    """When LLM grading returns non-float text, fall back to the similarity score."""
    doc = _make_document("content", "file.txt")
    mock_store = MagicMock()
    # similarity score is 0.75
    mock_store.similarity_search.return_value = [(doc, 0.75)]

    # LLM returns unparseable text
    mock_llm = _make_mock_llm(
        grade_responses=["not a number at all"],
        generate_response="answer",
    )

    with patch(PROVIDER_REGISTRY_PATH) as mock_registry_cls:
        mock_registry_cls.return_value.get_llm.return_value = mock_llm
        pipeline = CRAGPipeline(vector_store=mock_store)
        result = await pipeline.run("query")

    # score 0.75 >= 0.5, so chunk must be included
    assert result.used_web_fallback is False
    assert len(result.sources) == 1
    assert result.sources[0].relevance_score == pytest.approx(0.75)


@pytest.mark.asyncio
async def test_grading_parse_failure_low_score_triggers_web_fallback() -> None:
    """Parse failure + similarity score < 0.5 must trigger web fallback."""
    doc = _make_document("content", "file.txt")
    mock_store = MagicMock()
    # similarity score is 0.3 (below threshold)
    mock_store.similarity_search.return_value = [(doc, 0.3)]

    mock_llm = _make_mock_llm(
        grade_responses=["INVALID"],
        generate_response="answer",
    )

    with patch(PROVIDER_REGISTRY_PATH) as mock_registry_cls:
        mock_registry_cls.return_value.get_llm.return_value = mock_llm
        pipeline = CRAGPipeline(vector_store=mock_store)
        result = await pipeline.run("query")

    assert result.used_web_fallback is True

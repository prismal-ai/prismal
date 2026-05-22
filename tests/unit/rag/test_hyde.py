"""Unit tests for HyDERetriever (prismal.rag.hyde).

HyDE — Hypothetical Document Embeddings — generates a hypothetical document
that would answer the query, embeds that, and uses the resulting vector to
search the store. Improves recall on abstract / "why" questions.

All external dependencies (LLM, EmbeddingsFactory, ChromaVectorStore) are
mocked so no real API calls or DB connections are made.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from prismal.core.exceptions import HyDEError
from prismal.rag.crag import RetrievedChunk
from prismal.rag.hyde import HyDEResult, HyDERetriever

# ── Paths for patching ─────────────────────────────────────────────────────────

PROVIDER_REGISTRY_PATH = "prismal.rag.hyde.ProviderRegistry"
EMBEDDINGS_FACTORY_PATH = "prismal.rag.hyde.EmbeddingsFactory"


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
    response = MagicMock()
    response.content = text
    return response


def _make_mock_llm(hypothesis_text: str) -> MagicMock:
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=_make_llm_response(hypothesis_text))
    return llm


def _make_mock_embeddings(vector: list[float]) -> MagicMock:
    emb = MagicMock()
    emb.embed_query = MagicMock(return_value=vector)
    return emb


def _make_settings() -> MagicMock:
    return MagicMock()


# ── Dataclass ─────────────────────────────────────────────────────────────────


def test_hyde_result_is_instantiable() -> None:
    """HyDEResult must accept chunks, hypothesis, and hypothesis_embedding."""
    chunk = RetrievedChunk(source="f.txt", chunk_id="0", relevance_score=0.9, content="c")
    result = HyDEResult(
        chunks=[chunk],
        hypothesis="Hypothetical answer",
        hypothesis_embedding=[0.1, 0.2, 0.3],
    )
    assert result.chunks == [chunk]
    assert result.hypothesis == "Hypothetical answer"
    assert result.hypothesis_embedding == [0.1, 0.2, 0.3]


# ── Exception ─────────────────────────────────────────────────────────────────


def test_hyde_error_is_ragerror_subclass() -> None:
    """HyDEError must inherit from RAGError for hierarchical catching."""
    from prismal.core.exceptions import RAGError

    assert issubclass(HyDEError, RAGError)


# ── Constructor ───────────────────────────────────────────────────────────────


def test_init_stores_vector_store_and_settings() -> None:
    """Constructor keeps the passed-in vector store and settings."""
    store = MagicMock()
    settings = _make_settings()

    with (
        patch(PROVIDER_REGISTRY_PATH) as mock_registry_cls,
        patch(EMBEDDINGS_FACTORY_PATH) as mock_emb_cls,
    ):
        mock_registry_cls.return_value.get_llm.return_value = MagicMock()
        mock_emb_cls.create.return_value = MagicMock()
        retriever = HyDERetriever(vector_store=store, settings=settings)

    assert retriever._store is store  # noqa: SLF001
    assert retriever._settings is settings  # noqa: SLF001


def test_init_accepts_custom_hypothesis_prompt() -> None:
    """A custom hypothesis_prompt overrides the default."""
    store = MagicMock()
    custom = "My custom hypothesis prompt"

    with (
        patch(PROVIDER_REGISTRY_PATH) as mock_registry_cls,
        patch(EMBEDDINGS_FACTORY_PATH) as mock_emb_cls,
    ):
        mock_registry_cls.return_value.get_llm.return_value = MagicMock()
        mock_emb_cls.create.return_value = MagicMock()
        retriever = HyDERetriever(
            vector_store=store,
            settings=_make_settings(),
            hypothesis_prompt=custom,
        )

    assert retriever._hypothesis_prompt == custom  # noqa: SLF001


def test_init_uses_default_prompt_when_none() -> None:
    """When hypothesis_prompt is None, a non-empty default is used."""
    store = MagicMock()

    with (
        patch(PROVIDER_REGISTRY_PATH) as mock_registry_cls,
        patch(EMBEDDINGS_FACTORY_PATH) as mock_emb_cls,
    ):
        mock_registry_cls.return_value.get_llm.return_value = MagicMock()
        mock_emb_cls.create.return_value = MagicMock()
        retriever = HyDERetriever(vector_store=store, settings=_make_settings())

    assert retriever._hypothesis_prompt  # noqa: SLF001 -- truthy, non-empty


# ── search() happy path ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_search_returns_hyderesult_with_chunks_and_hypothesis() -> None:
    """search() must return HyDEResult with chunks, hypothesis, embedding."""
    doc = _make_document("A document about LangGraph.", "file.txt", "0")
    store = MagicMock()
    store.similarity_search.return_value = [(doc, 0.88)]

    llm = _make_mock_llm("LangGraph is a supervisor state machine framework.")
    embeddings = _make_mock_embeddings([0.1, 0.2, 0.3])

    with (
        patch(PROVIDER_REGISTRY_PATH) as mock_registry_cls,
        patch(EMBEDDINGS_FACTORY_PATH) as mock_emb_cls,
    ):
        mock_registry_cls.return_value.get_llm.return_value = llm
        mock_emb_cls.create.return_value = embeddings

        retriever = HyDERetriever(vector_store=store, settings=_make_settings())
        result = await retriever.search("Why LangGraph?", k=3)

    assert isinstance(result, HyDEResult)
    assert result.hypothesis == "LangGraph is a supervisor state machine framework."
    assert result.hypothesis_embedding == [0.1, 0.2, 0.3]
    assert len(result.chunks) == 1
    assert result.chunks[0].source == "file.txt"
    assert result.chunks[0].content == "A document about LangGraph."


@pytest.mark.asyncio
async def test_search_uses_hypothesis_text_to_query_store() -> None:
    """similarity_search must be called with the hypothesis, not the raw query."""
    store = MagicMock()
    store.similarity_search.return_value = []
    llm = _make_mock_llm("HYPOTHESIS TEXT")
    embeddings = _make_mock_embeddings([0.0])

    with (
        patch(PROVIDER_REGISTRY_PATH) as mock_registry_cls,
        patch(EMBEDDINGS_FACTORY_PATH) as mock_emb_cls,
    ):
        mock_registry_cls.return_value.get_llm.return_value = llm
        mock_emb_cls.create.return_value = embeddings

        retriever = HyDERetriever(vector_store=store, settings=_make_settings())
        await retriever.search("raw user query", k=7)

    args, kwargs = store.similarity_search.call_args
    query_arg = args[0] if args else kwargs.get("query")
    assert query_arg == "HYPOTHESIS TEXT"
    # k is forwarded
    k_arg = kwargs.get("k", args[1] if len(args) > 1 else None)
    assert k_arg == 7


@pytest.mark.asyncio
async def test_search_embeds_the_generated_hypothesis() -> None:
    """embed_query must be called with the hypothesis text."""
    store = MagicMock()
    store.similarity_search.return_value = []
    llm = _make_mock_llm("SOME HYPOTHESIS")
    embeddings = _make_mock_embeddings([0.5, 0.5])

    with (
        patch(PROVIDER_REGISTRY_PATH) as mock_registry_cls,
        patch(EMBEDDINGS_FACTORY_PATH) as mock_emb_cls,
    ):
        mock_registry_cls.return_value.get_llm.return_value = llm
        mock_emb_cls.create.return_value = embeddings

        retriever = HyDERetriever(vector_store=store, settings=_make_settings())
        await retriever.search("q", k=1)

    embeddings.embed_query.assert_called_once_with("SOME HYPOTHESIS")


@pytest.mark.asyncio
async def test_search_uses_secure_prompt_builder() -> None:
    """The hypothesis prompt must flow through SecurePromptBuilder."""
    store = MagicMock()
    store.similarity_search.return_value = []
    llm = _make_mock_llm("H")
    embeddings = _make_mock_embeddings([0.0])

    with (
        patch(PROVIDER_REGISTRY_PATH) as mock_registry_cls,
        patch(EMBEDDINGS_FACTORY_PATH) as mock_emb_cls,
        patch("prismal.rag.hyde.SecurePromptBuilder") as mock_builder_cls,
    ):
        mock_registry_cls.return_value.get_llm.return_value = llm
        mock_emb_cls.create.return_value = embeddings
        builder_instance = MagicMock()
        builder_instance.build.return_value = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "usr"},
        ]
        mock_builder_cls.return_value = builder_instance

        retriever = HyDERetriever(vector_store=store, settings=_make_settings())
        await retriever.search("user query text", k=1)

    assert builder_instance.build.called
    # user parameter must be the raw query (so it gets sanitised)
    _, kwargs = builder_instance.build.call_args
    assert kwargs["user"] == "user query text"


# ── search() error paths ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_search_raises_hydeerror_when_llm_fails() -> None:
    """If the LLM call fails, HyDEError is raised."""
    store = MagicMock()
    llm = MagicMock()
    llm.ainvoke = AsyncMock(side_effect=RuntimeError("boom"))
    embeddings = _make_mock_embeddings([0.0])

    with (
        patch(PROVIDER_REGISTRY_PATH) as mock_registry_cls,
        patch(EMBEDDINGS_FACTORY_PATH) as mock_emb_cls,
    ):
        mock_registry_cls.return_value.get_llm.return_value = llm
        mock_emb_cls.create.return_value = embeddings

        retriever = HyDERetriever(vector_store=store, settings=_make_settings())

        with pytest.raises(HyDEError):
            await retriever.search("q", k=1)


@pytest.mark.asyncio
async def test_search_raises_hydeerror_when_embedding_fails() -> None:
    """If embed_query raises, HyDEError is raised."""
    store = MagicMock()
    llm = _make_mock_llm("H")
    embeddings = MagicMock()
    embeddings.embed_query = MagicMock(side_effect=RuntimeError("embed boom"))

    with (
        patch(PROVIDER_REGISTRY_PATH) as mock_registry_cls,
        patch(EMBEDDINGS_FACTORY_PATH) as mock_emb_cls,
    ):
        mock_registry_cls.return_value.get_llm.return_value = llm
        mock_emb_cls.create.return_value = embeddings

        retriever = HyDERetriever(vector_store=store, settings=_make_settings())

        with pytest.raises(HyDEError):
            await retriever.search("q", k=1)


# ── RetrievedChunk conversion ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_search_converts_documents_to_retrieved_chunks() -> None:
    """Each (doc, score) tuple from the store becomes a RetrievedChunk."""
    docs = [
        (_make_document("c1", "s1.txt", "0"), 0.9),
        (_make_document("c2", "s2.txt", "1"), 0.7),
    ]
    store = MagicMock()
    store.similarity_search.return_value = docs
    llm = _make_mock_llm("H")
    embeddings = _make_mock_embeddings([0.0])

    with (
        patch(PROVIDER_REGISTRY_PATH) as mock_registry_cls,
        patch(EMBEDDINGS_FACTORY_PATH) as mock_emb_cls,
    ):
        mock_registry_cls.return_value.get_llm.return_value = llm
        mock_emb_cls.create.return_value = embeddings

        retriever = HyDERetriever(vector_store=store, settings=_make_settings())
        result = await retriever.search("q", k=2)

    assert len(result.chunks) == 2
    assert [c.chunk_id for c in result.chunks] == ["0", "1"]
    assert [c.source for c in result.chunks] == ["s1.txt", "s2.txt"]
    assert [c.relevance_score for c in result.chunks] == [0.9, 0.7]

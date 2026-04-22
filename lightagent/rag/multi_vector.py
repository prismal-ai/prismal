"""Multi-Vector RAG — index each document under multiple representations.

Implements task A6 of the advanced-architectures initiative. Each document
chunk is embedded three times:

1. **chunk**    — the original text.
2. **summary**  — an LLM-generated concise summary.
3. **question** — N LLM-generated hypothetical questions the chunk would answer.

All representations share a common ``doc_id`` so that a similarity hit on any
one surface bubbles back up to the same source document. Results are
deduplicated by ``doc_id`` at search time; the best-scoring representation
wins.

Gain: queries that look nothing like the document text (e.g. a question
phrasing) can still match via the question representation, which was
*designed* to look like a question.

Example::

    from lightagent.rag.multi_vector import MultiVectorRAGEngine
    from lightagent.rag.vector_store import ChromaVectorStore

    store = ChromaVectorStore(collection_name="multi")
    engine = MultiVectorRAGEngine(vector_store=store, n_questions=3)
    await engine.index_document(Path("notes.md"))
    result = await engine.search("How does X work?", k=5)
    print(result.matched_representations)  # {doc_id: ["question", ...]}
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from langchain_core.documents import Document
from langchain_core.messages import HumanMessage, SystemMessage

from lightagent.core.config import get_settings
from lightagent.core.logging import get_logger
from lightagent.monitoring.otel import OTelManager
from lightagent.providers.registry import ProviderRegistry
from lightagent.rag.crag import RetrievedChunk
from lightagent.rag.loaders import DocumentProcessorFactory
from lightagent.security.prompt_builder import SecurePromptBuilder

if TYPE_CHECKING:
    from pathlib import Path

    from lightagent.core.config import Settings
    from lightagent.rag.vector_store import ChromaVectorStore

logger = get_logger("lightagent.rag.multi_vector")

_SUMMARY_PROMPT = (
    "Write a concise summary (2-3 sentences) of the provided text. Do not add "
    "commentary or preamble — summary only."
)

_QUESTIONS_PROMPT = (
    "Generate exactly {n} distinct, self-contained questions that the provided "
    "text could answer. Output as a numbered list (1., 2., ...), one per line, "
    "nothing else."
)


@dataclass
class MultiVectorResult:
    """Result of :meth:`MultiVectorRAGEngine.search`.

    Attributes:
        chunks: One :class:`RetrievedChunk` per unique doc, best-scoring
            representation selected. Ordered by score descending.
        matched_representations: ``{doc_id: [representation_type, ...]}`` —
            which representation types (chunk / summary / question) produced a
            hit for each doc, in original score order.
    """

    chunks: list[RetrievedChunk]
    matched_representations: dict[str, list[str]] = field(default_factory=dict)


class MultiVectorRAGEngine:
    """RAG engine that indexes each chunk under multiple representations.

    Args:
        vector_store: Initialised :class:`ChromaVectorStore`.
        n_questions: Number of hypothetical questions to generate per chunk
            (default 3).
        settings: LightAgent settings. ``None`` resolves via
            :func:`~lightagent.core.config.get_settings`.
    """

    def __init__(
        self,
        vector_store: ChromaVectorStore,
        n_questions: int = 3,
        settings: Settings | None = None,
    ) -> None:
        self._store = vector_store
        self._n_questions = n_questions
        self._settings = settings if settings is not None else get_settings()
        self._llm = ProviderRegistry(settings=self._settings).get_llm()

    # ── Indexing ──────────────────────────────────────────────────────────────

    async def index_document(self, path: Path) -> tuple[int, int]:
        """Index *path* under chunk / summary / question representations.

        Behaves best-effort on partial LLM failures: if summary or question
        generation fails for a chunk, the original chunk representation is
        still indexed so the document is not lost.

        Args:
            path: Path to a supported document.

        Returns:
            ``(n_docs, n_vectors)`` — unique source docs seen and total
            vectors written to the store.
        """
        factory = DocumentProcessorFactory()
        docs = factory.load(path)
        source = str(path)
        logger.info("multi_vector_index_start", source=source, loaded_docs=len(docs))

        self._store.delete_by_source(source)

        all_vectors: list[Document] = []
        for doc in docs:
            doc_id = uuid.uuid4().hex
            content = doc.page_content
            # Always add the original chunk representation.
            all_vectors.append(
                self._make_vector_doc(
                    content=content,
                    source=source,
                    doc_id=doc_id,
                    representation="chunk",
                )
            )
            # Best-effort summary.
            summary = await self._generate_summary(content)
            if summary is not None:
                all_vectors.append(
                    self._make_vector_doc(
                        content=summary,
                        source=source,
                        doc_id=doc_id,
                        representation="summary",
                    )
                )
            # Best-effort hypothetical questions.
            questions = await self._generate_questions(content)
            for q in questions:
                all_vectors.append(
                    self._make_vector_doc(
                        content=q,
                        source=source,
                        doc_id=doc_id,
                        representation="question",
                    )
                )

        if all_vectors:
            self._store.add_documents(all_vectors)

        logger.info(
            "multi_vector_index_done",
            source=source,
            n_docs=len(docs),
            n_vectors=len(all_vectors),
        )
        return len(docs), len(all_vectors)

    # ── Retrieval ─────────────────────────────────────────────────────────────

    async def search(self, query: str, k: int = 5) -> MultiVectorResult:
        """Similarity search then deduplicate by ``doc_id``.

        Over-fetches ``k * 4`` hits so there is enough material to collapse
        down to ``k`` unique docs. The best-scoring representation per doc is
        returned; all matching representation types are surfaced in
        ``matched_representations`` for debugging / audit.
        """
        otel = OTelManager()
        with otel.start_span("multi_vector.search") as span:
            span.set_attribute("lightagent.query_len", len(query))
            span.set_attribute("lightagent.k", k)

            raw = self._store.similarity_search(query, k=k * 4)

            best_by_doc: dict[str, tuple[float, Document]] = {}
            matched: dict[str, list[str]] = {}

            for doc, score in raw:
                metadata = doc.metadata
                doc_id = metadata.get("doc_id")
                if not doc_id:
                    logger.info(
                        "multi_vector_hit_missing_doc_id",
                        chunk_id=metadata.get("chunk_id"),
                    )
                    continue
                representation = metadata.get("representation", "unknown")
                matched.setdefault(doc_id, []).append(representation)
                if doc_id not in best_by_doc or score > best_by_doc[doc_id][0]:
                    best_by_doc[doc_id] = (score, doc)

            ordered_ids = sorted(best_by_doc, key=lambda did: best_by_doc[did][0], reverse=True)[:k]

            chunks = [
                RetrievedChunk(
                    source=best_by_doc[did][1].metadata.get("source", "unknown"),
                    chunk_id=best_by_doc[did][1].metadata.get("chunk_id", did),
                    relevance_score=best_by_doc[did][0],
                    content=best_by_doc[did][1].page_content,
                )
                for did in ordered_ids
            ]
            # Trim matched_representations to the returned docs for clarity.
            matched = {did: matched[did] for did in ordered_ids}

            span.set_attribute("lightagent.num_unique_docs", len(chunks))
            otel.increment_counter("rag_queries")
            logger.info(
                "multi_vector_search_done",
                n_unique_docs=len(chunks),
                n_raw_hits=len(raw),
            )
            return MultiVectorResult(chunks=chunks, matched_representations=matched)

    # ── internals ─────────────────────────────────────────────────────────────

    def _make_vector_doc(
        self, content: str, source: str, doc_id: str, representation: str
    ) -> Document:
        chunk_id = f"{doc_id}-{representation}-{uuid.uuid4().hex[:8]}"
        return Document(
            page_content=content,
            metadata={
                "source": source,
                "doc_id": doc_id,
                "representation": representation,
                "chunk_id": chunk_id,
            },
        )

    async def _generate_summary(self, content: str) -> str | None:
        builder = SecurePromptBuilder()
        messages = builder.build(system=_SUMMARY_PROMPT, user=content)
        lc_messages = [
            SystemMessage(content=messages[0]["content"]),
            HumanMessage(content=messages[1]["content"]),
        ]
        try:
            response = await self._llm.ainvoke(lc_messages)
        except Exception as exc:
            logger.warning("multi_vector_summary_error", error=str(exc))
            return None
        return str(response.content).strip() or None

    async def _generate_questions(self, content: str) -> list[str]:
        if self._n_questions <= 0:
            return []
        builder = SecurePromptBuilder()
        messages = builder.build(
            system=_QUESTIONS_PROMPT.format(n=self._n_questions),
            user=content,
        )
        lc_messages = [
            SystemMessage(content=messages[0]["content"]),
            HumanMessage(content=messages[1]["content"]),
        ]
        try:
            response = await self._llm.ainvoke(lc_messages)
        except Exception as exc:
            logger.warning("multi_vector_questions_error", error=str(exc))
            return []
        raw = str(response.content)
        questions: list[str] = []
        for line in raw.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            match = re.match(r"^\d+[\.\)]\s*(.+)$", stripped)
            if match:
                questions.append(match.group(1).strip())
            else:
                questions.append(stripped)
        return questions[: self._n_questions]


__all__ = ["MultiVectorRAGEngine", "MultiVectorResult"]

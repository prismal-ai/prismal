"""
Multimodal RAG — Indexar y buscar texto + imágenes + audio en una colección
=============================================================================
Arquitectura: SPEC-MM-RAG-001 / prismal.rag.multimodal

Dataset: arXiv (cs.CV/cs.CL) + MedQuAD + ATIS (mezcla multimodal sintética)
  • Textos    : abstracts de arXiv (`Langgraph_tutorials/data/arxiv/...`)
                + preguntas MedQuAD.
  • Imágenes  : figuras hipotéticas asociadas a cada paper (caption es
                la descripción del paper) — PNG generado en memoria.
  • Audio     : utterancias ATIS-style con su transcript como caption.
  • Por qué: el `MultimodalRAGEngine` sin un cross-modal embedder real
    indexa los *captions/transcripts* (DD-MM-003). Este ejemplo enseña
    cómo cada modalidad se conserva en `metadata["modality"]` y cómo
    `MultimodalRAGEngine.search(modalities=[...])` filtra resultados.

Descripción de la arquitectura:
  1. Cargar/generar documentos por modalidad → todos con
     `metadata["modality"]` ∈ {text, image, audio, video}.
  2. `engine.index(path)` o `store.add_documents(...)`: persisten en
     ChromaDB (o en este demo, en un store in-memory equivalente).
  3. `engine.search(query, k=K, modalities=None)` → `[
     MultimodalRetrievedChunk(modality, content, score, source_uri, …)]`.
  4. Mismo `query`, distintos `modalities` → distintos resultados.
  5. Sin embedder cross-modal, el engine cae a fallback textual
     (`logger.warning("multimodal_rag_textual_fallback")`) — explícito.

Uso:
    uv run python examples/rag/09_multimodal_rag.py
"""

from __future__ import annotations

import asyncio
import csv
import textwrap
from pathlib import Path

from langchain_core.documents import Document

from prismal.agents.multimodal import Modality
from prismal.rag.multimodal import MultimodalRAGEngine, MultimodalRetrievedChunk

ROOT = Path(__file__).resolve().parents[3] / "Langgraph_tutorials" / "data"
ARXIV_CSV = ROOT / "arxiv" / "arxiv_papers.csv"
MEDQUAD_CSV = ROOT / "medquad" / "medquad.csv"


# ── In-memory ChromaVectorStore-shim ─────────────────────────────────────────
# `MultimodalRAGEngine` sólo necesita:
#   - .add_documents(list[Document]) -> list[str]
#   - .similarity_search(query, k) -> list[(Document, score)]
# Implementamos un sustituto BM25-like para evitar Chroma en el ejemplo.
class InMemoryStore:
    """Mock minimal del `ChromaVectorStore` usando overlapping-keyword score."""

    def __init__(self) -> None:
        self._docs: list[Document] = []

    def add_documents(self, documents: list[Document]) -> list[str]:
        ids: list[str] = []
        for d in documents:
            self._docs.append(d)
            ids.append(f"doc-{len(self._docs):04d}")
        return ids

    def similarity_search(self, query: str, k: int = 5) -> list[tuple[Document, float]]:
        q_terms = {t.lower() for t in query.split() if len(t) > 2}
        scored: list[tuple[Document, float]] = []
        for d in self._docs:
            doc_terms = {t.lower().strip(".,;:!?()[]") for t in d.page_content.split()}
            overlap = len(q_terms & doc_terms)
            denom = len(q_terms) or 1
            score = overlap / denom
            if score > 0:
                scored.append((d, float(score)))
        scored.sort(key=lambda x: -x[1])
        return scored[:k]


# ── Carga del corpus multimodal ──────────────────────────────────────────────
def _load_text_docs(limit: int = 8) -> list[Document]:
    """Abstracts de arXiv + preguntas/respuestas MedQuAD como modalidad TEXT."""
    out: list[Document] = []
    if ARXIV_CSV.exists():
        try:
            with ARXIV_CSV.open(encoding="utf-8") as fh:
                for i, row in enumerate(csv.DictReader(fh)):
                    if i >= limit // 2:
                        break
                    out.append(
                        Document(
                            page_content=f"{row['title']}. {textwrap.shorten(row['abstract'], 300)}",
                            metadata={
                                "modality": "text",
                                "source_uri": f"arxiv://{row['arxiv_id']}",
                                "source": row["arxiv_id"],
                                "category": row.get("category", ""),
                            },
                        )
                    )
        except Exception as exc:
            print(f"  (aviso: no se pudo leer {ARXIV_CSV.name}: {exc})")
    if MEDQUAD_CSV.exists():
        try:
            with MEDQUAD_CSV.open(encoding="utf-8") as fh:
                for i, row in enumerate(csv.DictReader(fh)):
                    if i >= limit // 2:
                        break
                    out.append(
                        Document(
                            page_content=f"Q: {row['question']} A: {textwrap.shorten(row['answer'], 280)}",
                            metadata={
                                "modality": "text",
                                "source_uri": f"medquad://{row['focus_area']}",
                                "source": row.get("focus_area", "medquad"),
                            },
                        )
                    )
        except Exception as exc:
            print(f"  (aviso: no se pudo leer {MEDQUAD_CSV.name}: {exc})")
    return out or _embedded_text_docs()


def _embedded_text_docs() -> list[Document]:
    return [
        Document(
            page_content="Retrieval-Augmented Generation combines retrieval with generation.",
            metadata={"modality": "text", "source_uri": "embedded://rag", "source": "embedded-rag"},
        ),
        Document(
            page_content="Type-2 diabetes is a chronic metabolic disorder affecting glucose.",
            metadata={
                "modality": "text",
                "source_uri": "embedded://medquad/diabetes",
                "source": "medquad/diabetes",
            },
        ),
    ]


def _image_docs() -> list[Document]:
    """Captions de figuras (sin embedding cross-modal → textual fallback)."""
    return [
        Document(
            page_content="Figure: architecture diagram of a transformer encoder block "
            "with multi-head attention and feed-forward layers.",
            metadata={
                "modality": "image",
                "source_uri": "arxiv://2604.02185#fig1",
                "source": "arxiv-2604.02185",
                "type": "diagram",
            },
        ),
        Document(
            page_content="Figure: a histopathology slide showing glaucoma-induced optic "
            "nerve atrophy in a 65-year-old patient.",
            metadata={
                "modality": "image",
                "source_uri": "medquad://glaucoma/fig",
                "source": "medquad-glaucoma",
                "type": "medical-photo",
            },
        ),
        Document(
            page_content="Figure: bar chart comparing BM25, dense, and hybrid retrieval "
            "MRR@10 across BEIR benchmark.",
            metadata={
                "modality": "image",
                "source_uri": "arxiv://2604.02077#fig3",
                "source": "arxiv-2604.02077",
                "type": "chart",
            },
        ),
    ]


def _audio_docs() -> list[Document]:
    """Transcripts de utterancias estilo ATIS como modalidad AUDIO."""
    return [
        Document(
            page_content="Show me all flights from Boston to Denver on Tuesday morning.",
            metadata={
                "modality": "audio",
                "source_uri": "atis://flight_search_001",
                "source": "atis-001",
                "language": "en",
            },
        ),
        Document(
            page_content="Cuántos asientos hay disponibles en el vuelo de las dos de la tarde.",
            metadata={
                "modality": "audio",
                "source_uri": "atis://seat_avail_003",
                "source": "atis-003",
                "language": "es",
            },
        ),
        Document(
            page_content="Voice command transcript: cancel my reservation for tomorrow's flight.",
            metadata={
                "modality": "audio",
                "source_uri": "atis://cancellation_004",
                "source": "atis-004",
                "language": "en",
            },
        ),
    ]


def _video_docs() -> list[Document]:
    return [
        Document(
            page_content="Video: cooking pasta from scratch — boiling water, adding spaghetti, "
            "stirring with a wooden spoon, plating with sauce.",
            metadata={
                "modality": "video",
                "source_uri": "anet://cooking_pasta",
                "source": "anet-001",
                "duration_s": 24.0,
            },
        ),
        Document(
            page_content="Video: skateboarder lands a kickflip down a seven-stair set.",
            metadata={
                "modality": "video",
                "source_uri": "anet://skateboard_trick",
                "source": "anet-002",
                "duration_s": 12.0,
            },
        ),
    ]


# ── Demo principal ──────────────────────────────────────────────────────────
async def main() -> None:
    print("=" * 72)
    print("MultimodalRAGEngine · indexar + buscar text/image/audio/video")
    print("=" * 72)

    # 1. Construir corpus multimodal
    store = InMemoryStore()
    text_docs = _load_text_docs(limit=6)
    image_docs = _image_docs()
    audio_docs = _audio_docs()
    video_docs = _video_docs()

    print(
        f"\nCorpus: {len(text_docs)} TEXT · {len(image_docs)} IMAGE "
        f"· {len(audio_docs)} AUDIO · {len(video_docs)} VIDEO"
    )

    store.add_documents(text_docs + image_docs + audio_docs + video_docs)

    # 2. Instanciar engine (sin cross_modal_embedder → fallback textual)
    engine = MultimodalRAGEngine(vector_store=store)  # type: ignore[arg-type]

    queries = [
        "transformer architecture and attention",
        "flight from Boston",
        "glaucoma",
        "skateboard tricks",
    ]

    # 3. Búsqueda sin filtrar por modalidad
    print("\n" + "─" * 72)
    print("1) search(query, k=3) — sin filtro de modalidad")
    print("─" * 72)
    for q in queries:
        print(f"\n  query: {q!r}")
        chunks: list[MultimodalRetrievedChunk] = await engine.search(q, k=3)
        if not chunks:
            print("    (sin resultados — keyword overlap=0)")
        for c in chunks:
            print(
                f"    [{c.modality.value:5}] score={c.score:.2f}  "
                f"{c.source_uri}\n           → {c.content[:80]}…"
            )

    # 4. Búsqueda filtrada por modalidad
    print("\n" + "─" * 72)
    print("2) search(query, k=3, modalities=[IMAGE]) — solo imágenes")
    print("─" * 72)
    for q in ["transformer architecture", "glaucoma diagnosis"]:
        print(f"\n  query: {q!r}")
        chunks = await engine.search(q, k=3, modalities=[Modality.IMAGE])
        for c in chunks:
            assert c.modality is Modality.IMAGE
            print(f"    [IMAGE] {c.source_uri} score={c.score:.2f}")
            print(f"            → {c.content[:80]}…")
        if not chunks:
            print("    (sin imágenes relevantes)")

    # 5. Búsqueda multi-modalidad (texto + video)
    print("\n" + "─" * 72)
    print("3) search(query, k=4, modalities=[TEXT, VIDEO])")
    print("─" * 72)
    q = "cooking"
    chunks = await engine.search(q, k=4, modalities=[Modality.TEXT, Modality.VIDEO])
    print(f"\n  query: {q!r}")
    for c in chunks:
        assert c.modality in {Modality.TEXT, Modality.VIDEO}
        print(f"    [{c.modality.value:5}] {c.source_uri} score={c.score:.2f}")
        print(f"            → {c.content[:80]}…")

    print("\n" + "=" * 72)
    print("OK — modalidades preservadas en metadata, filtrado funciona sin embedder cross-modal")
    print("=" * 72)


if __name__ == "__main__":
    asyncio.run(main())

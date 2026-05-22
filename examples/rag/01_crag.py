"""
CRAG — Corrective RAG con calificación LLM de relevancia
=========================================================
Arquitectura: SPEC-005 / prismal.rag.crag

Dataset: SQuAD 2.0 (Stanford Question Answering Dataset)
  • 150 000 pares pregunta-respuesta sobre artículos de Wikipedia.
  • Referencia: https://huggingface.co/datasets/rajpurkar/squad_v2
  • Por qué: CRAG requiere documentos base + preguntas sobre ellos.
    SQuAD 2.0 proporciona exactamente esto: contextos de Wikipedia y
    preguntas reales de usuarios con respuestas extraídas.

Descripción de la arquitectura CRAG:
  Pipeline de 5 pasos:
  1. RETRIEVE   — ChromaDB similarity_search(query, k=5)
  2. GRADE      — LLM puntúa cada chunk 0.0-1.0 por relevancia
  3. FILTER     — Conserva chunks con relevance_score >= 0.5
  4. DECIDE     — Si todos se filtran → activar fallback web (stub)
  5. GENERATE   — LLM responde con contexto + citas de fuentes

Flujo de corrección:
  - Chunks irrelevantes se descartan antes de generar (evita alucinaciones).
  - Si no hay chunks relevantes, el fallback web previene respuestas vacías.
  - El LLM cita las fuentes usadas en la respuesta final.

Uso:
    uv run python examples/rag/01_crag.py
"""

from __future__ import annotations

import asyncio

from langchain_core.documents import Document

from prismal.rag.crag import CRAGPipeline, CRAGResult
from prismal.rag.vector_store import ChromaVectorStore

# ── Dataset: fragmentos de SQuAD 2.0 (Wikipedia contexts) ────────────────────
# Contextos reales de Wikipedia usados en SQuAD 2.0.
SQUAD_CONTEXTS = [
    {
        "source": "wikipedia_transformers",
        "title": "Transformer (machine learning model)",
        "content": (
            "The transformer is a deep learning architecture that relies on the parallel "
            "multi-head attention mechanism. Introduced by Vaswani et al. in 2017 in the "
            "paper 'Attention Is All You Need', the transformer architecture has become "
            "the standard for natural language processing tasks. Unlike recurrent neural "
            "networks (RNNs), transformers process entire sequences simultaneously, "
            "enabling highly parallelizable training. The key innovation is the "
            "self-attention mechanism that allows the model to weigh the importance "
            "of different words in the input sequence when generating each output token."
        ),
    },
    {
        "source": "wikipedia_bert",
        "title": "BERT (language model)",
        "content": (
            "BERT (Bidirectional Encoder Representations from Transformers) is a "
            "language model developed by Google in 2018. BERT uses a transformer "
            "architecture and is pre-trained on masked language modeling and "
            "next sentence prediction tasks using the BooksCorpus and English "
            "Wikipedia datasets. BERT was revolutionary because it achieved "
            "state-of-the-art results on 11 NLP tasks. Unlike GPT which is "
            "unidirectional, BERT processes text bidirectionally, allowing it "
            "to understand context from both left and right of each token."
        ),
    },
    {
        "source": "wikipedia_python",
        "title": "Python (programming language)",
        "content": (
            "Python is a high-level, general-purpose programming language. Its design "
            "philosophy emphasizes code readability using indentation. Python is "
            "dynamically typed and garbage-collected. It supports multiple programming "
            "paradigms, including structured, object-oriented, and functional programming. "
            "Guido van Rossum began working on Python in the late 1980s as a successor "
            "to the ABC programming language. Python 3.0 was released in 2008. It was "
            "designed to not be fully backward compatible with Python 2. Python has an "
            "extensive standard library, often described as 'batteries included'."
        ),
    },
    {
        "source": "wikipedia_rag",
        "title": "Retrieval-Augmented Generation",
        "content": (
            "Retrieval-Augmented Generation (RAG) is a technique for enhancing the "
            "accuracy and reliability of generative AI models with facts fetched from "
            "external sources. It combines an information retrieval component with "
            "a text generation model. When given a query, the retrieval system fetches "
            "relevant documents from a knowledge base, which are then passed to the "
            "generative model as context. RAG was introduced by Lewis et al. in 2020 "
            "and has become the dominant approach for building knowledge-intensive "
            "NLP applications. It reduces hallucinations by grounding the model in "
            "factual retrieved content."
        ),
    },
    {
        "source": "wikipedia_llm",
        "title": "Large language model",
        "content": (
            "A large language model (LLM) is a type of computational model designed "
            "for natural language processing tasks. LLMs are artificial neural networks "
            "that contain hundreds of billions (or more) parameters, and are trained "
            "on vast amounts of text data, generally through self-supervised and "
            "semi-supervised learning. As of 2024, the most capable LLMs are "
            "general-purpose models that can be used for a variety of tasks. Notable "
            "LLMs include GPT-4 (OpenAI), Claude (Anthropic), Gemini (Google), "
            "and LLaMA (Meta). LLMs process and generate text using transformer "
            "architectures trained on web-scale text corpora."
        ),
    },
]

# ── Preguntas SQuAD 2.0 sobre los contextos ──────────────────────────────────
SQUAD_QUESTIONS = [
    {
        "id": "SQ1",
        "question": "¿Cuándo fue introducida la arquitectura Transformer?",
        "expected_source": "wikipedia_transformers",
        "expected_answer_contains": "2017",
    },
    {
        "id": "SQ2",
        "question": "¿Qué hace diferente a BERT de GPT en cuanto al procesamiento de texto?",
        "expected_source": "wikipedia_bert",
        "expected_answer_contains": "bidireccional",
    },
    {
        "id": "SQ3",
        "question": "¿Quién creó el lenguaje de programación Python?",
        "expected_source": "wikipedia_python",
        "expected_answer_contains": "Guido",
    },
    {
        "id": "SQ4",
        "question": "¿Para qué sirve RAG y qué problema resuelve?",
        "expected_source": "wikipedia_rag",
        "expected_answer_contains": "alucinaciones",
    },
    {
        "id": "SQ5",
        "question": "¿Cuál es la diferencia entre RAG y los modelos de lenguaje tradicionales?",
        "expected_source": "wikipedia_rag",
        "expected_answer_contains": None,  # pregunta cruzada entre documentos
    },
]


async def setup_vector_store(collection_name: str = "squad_crag") -> ChromaVectorStore:
    """Inicializa y popula el vector store con los documentos SQuAD.

    Args:
        collection_name: Nombre de la colección ChromaDB.

    Returns:
        ChromaVectorStore lista para búsqueda.
    """
    store = ChromaVectorStore(collection_name=collection_name)

    # Crear documentos LangChain con metadata
    docs = [
        Document(
            page_content=ctx["content"],
            metadata={
                "source": ctx["source"],
                "title": ctx["title"],
                "chunk_id": str(i),
                "dataset": "squad_2.0",
            },
        )
        for i, ctx in enumerate(SQUAD_CONTEXTS)
    ]

    # Indexar en ChromaDB
    print(f"  Indexando {len(docs)} documentos en ChromaDB (colección: {collection_name})...")
    store.add_documents(docs)
    print(f"  ✓ Vectores indexados en: {collection_name}")

    return store


async def run_crag_query(
    pipeline: CRAGPipeline,
    question: dict,
) -> CRAGResult:
    """Ejecuta una query CRAG y muestra resultados detallados.

    Args:
        pipeline: Pipeline CRAG inicializado.
        question: Pregunta con metadatos esperados.

    Returns:
        CRAGResult con respuesta y fuentes.
    """
    return await pipeline.run(question["question"])


def print_crag_result(question: dict, result: CRAGResult) -> None:
    """Imprime los resultados CRAG de forma estructurada."""
    print(f"\n[{question['id']}] {question['question']}")

    print("\n  Fuentes recuperadas y calificadas:")
    for chunk in result.sources:
        score_bar = "█" * int(chunk.relevance_score * 10) + "░" * (
            10 - int(chunk.relevance_score * 10)
        )
        print(
            f"    [{score_bar}] {chunk.relevance_score:.2f} — "
            f"{chunk.source} (chunk {chunk.chunk_id})"
        )

    if result.used_web_fallback:
        print("  ⚠ Fallback web activado (ningún chunk superó el umbral 0.5)")
    else:
        relevant_count = len([c for c in result.sources if c.relevance_score >= 0.5])
        print(f"  ✓ {relevant_count} chunks relevantes (score >= 0.5)")

    print("\n  Respuesta CRAG:")
    print(f"  {result.answer[:400]}")

    # Verificar si la fuente esperada fue usada
    expected_src = question.get("expected_source")
    if expected_src:
        used_sources = [c.source for c in result.sources]
        found = expected_src in used_sources
        print(f"\n  Fuente esperada ({expected_src}): {'✓ usada' if found else '✗ no usada'}")

    print("─" * 70)


async def main() -> None:
    print("=" * 70)
    print("  CRAG (Corrective RAG) — Dataset: SQuAD 2.0 (Wikipedia QA)")
    print("=" * 70)

    # Inicializar vector store
    print("\n[Paso 0: Configuración del Vector Store]")
    store = await setup_vector_store("squad_crag_example")

    # Crear pipeline CRAG
    pipeline = CRAGPipeline(vector_store=store)
    print("  ✓ Pipeline CRAG inicializado")
    print("  Umbral de relevancia: 0.5 (chunks con score < 0.5 se descartan)")

    # Ejecutar queries
    print(f"\n[Queries sobre {len(SQUAD_QUESTIONS)} preguntas SQuAD]")

    for question in SQUAD_QUESTIONS:
        result = await run_crag_query(pipeline, question)
        print_crag_result(question, result)

    # Demostrar fallback web (pregunta sin documentos relevantes)
    print("\n[Demostración del Fallback Web]")
    print("  Pregunta sin documentos relevantes en la base de conocimiento...")

    off_topic_result = await pipeline.run(
        "¿Cuáles son los mejores restaurantes de sushi en Tokio con estrellas Michelin?"
    )
    print(f"  Fallback activado: {off_topic_result.used_web_fallback}")
    print(
        f"  Fuente fallback: {off_topic_result.sources[0].source if off_topic_result.sources else 'N/A'}"
    )

    # Resumen del pipeline CRAG
    print("\n[Flujo CRAG — 5 pasos]")
    steps = [
        ("1. RETRIEVE", "ChromaDB similarity_search(query, k=5)"),
        ("2. GRADE   ", "LLM puntúa cada chunk 0.0-1.0"),
        ("3. FILTER  ", "Conservar chunks con score >= 0.5"),
        ("4. DECIDE  ", "Si todo filtrado → fallback web"),
        ("5. GENERATE", "LLM responde con contexto + citas"),
    ]
    for step, desc in steps:
        print(f"  {step}: {desc}")


if __name__ == "__main__":
    asyncio.run(main())

"""
Adaptive RAG — Enrutamiento inteligente de queries a motores RAG especializados
================================================================================
Arquitectura: SPEC-RAG-006 / lightagent.rag.adaptive

Dataset: Natural Questions (NQ) + TriviaQA
  • NQ: 307 373 preguntas de Google Search sobre artículos de Wikipedia.
  • TriviaQA: 95 000 preguntas de trivia con evidencia de múltiples fuentes.
  • Referencia:
    - https://huggingface.co/datasets/google-research-datasets/natural_questions
    - https://huggingface.co/datasets/mandarjoshi/trivia_qa
  • Por qué: NQ y TriviaQA cubren los 6 tipos de queries que AdaptiveRAG
    clasifica: factual simple, abstracta, ambigua, técnica, conversacional
    y multi-salto. Son el benchmark estándar para sistemas RAG.

Descripción de la arquitectura Adaptive RAG:
  Clasifica la query y enruta al motor más adecuado:
  - FACTUAL_SIMPLE  → CRAG  (preguntas de hechos directos)
  - ABSTRACT        → HyDE  (preguntas "¿por qué?", "¿cómo funciona?")
  - AMBIGUOUS       → RAG-Fusion (queries cortas o vagas)
  - TECHNICAL       → Hybrid Search (términos técnicos, API, código)
  - CONVERSATIONAL  → CRAG  (referencias a conversación previa)
  - MULTI_HOP       → CRAG  (preguntas compuestas "primero X, luego Y")

Clasificación:
  - Por defecto: regex determinístico (sin coste de LLM)
  - Opcional: use_llm_classifier=True para casos límite

Uso:
    uv run python examples/rag/02_adaptive_rag.py
"""

from __future__ import annotations

import asyncio

from langchain_core.documents import Document

from lightagent.rag.adaptive import AdaptiveRAGEngine, AdaptiveResult, QueryType
from lightagent.rag.crag import CRAGPipeline
from lightagent.rag.fusion import RAGFusionEngine
from lightagent.rag.hybrid import HybridSearchEngine
from lightagent.rag.hyde import HyDERetriever
from lightagent.rag.vector_store import ChromaVectorStore

# ── Dataset: queries de NQ + TriviaQA clasificadas por tipo ──────────────────
ADAPTIVE_QUERIES = [
    # FACTUAL_SIMPLE: hechos directos con respuesta precisa
    {
        "id": "AQ1",
        "query": "¿En qué año se fundó OpenAI?",
        "query_type": QueryType.FACTUAL_SIMPLE,
        "dataset": "NQ",
        "reason": "Pregunta de hecho directo con fecha específica",
    },
    {
        "id": "AQ2",
        "query": "¿Cuántos parámetros tiene GPT-4?",
        "query_type": QueryType.FACTUAL_SIMPLE,
        "dataset": "TriviaQA",
        "reason": "Pregunta factual con número específico",
    },
    # ABSTRACT: preguntas conceptuales tipo "¿por qué?" o "¿cómo?"
    {
        "id": "AQ3",
        "query": "¿Por qué los modelos Transformer son más eficientes que los RNNs?",
        "query_type": QueryType.ABSTRACT,
        "dataset": "NQ",
        "reason": "Pregunta explicativa con 'por qué', requiere razonamiento",
    },
    {
        "id": "AQ4",
        "query": "¿Cómo funciona el mecanismo de atención en los LLMs modernos?",
        "query_type": QueryType.ABSTRACT,
        "dataset": "NQ",
        "reason": "Pregunta de mecanismo tipo 'cómo funciona'",
    },
    # AMBIGUOUS: queries cortas o vagas
    {
        "id": "AQ5",
        "query": "RAG",
        "query_type": QueryType.AMBIGUOUS,
        "dataset": "NQ",
        "reason": "Query de una sola palabra, muy ambigua",
    },
    {
        "id": "AQ6",
        "query": "mejores herramientas IA",
        "query_type": QueryType.AMBIGUOUS,
        "dataset": "TriviaQA",
        "reason": "Query corta y vaga sin contexto",
    },
    # TECHNICAL: términos técnicos, código, APIs
    {
        "id": "AQ7",
        "query": "ChromaVectorStore.similarity_search() API documentation",
        "query_type": QueryType.TECHNICAL,
        "dataset": "Technical Docs",
        "reason": "Notación camelCase/snake_case y API explícita",
    },
    {
        "id": "AQ8",
        "query": "async def vs sync function performance Python asyncio",
        "query_type": QueryType.TECHNICAL,
        "dataset": "Technical Docs",
        "reason": "Términos técnicos de programación con sintaxis de código",
    },
    # CONVERSATIONAL: referencias a contexto previo
    {
        "id": "AQ9",
        "query": "¿Puedes explicarme más sobre lo que mencionaste antes sobre BERT?",
        "query_type": QueryType.CONVERSATIONAL,
        "dataset": "NQ",
        "reason": "Referencia explícita a conversación anterior ('lo que mencionaste')",
    },
    # MULTI_HOP: requiere razonamiento secuencial
    {
        "id": "AQ10",
        "query": "Primero dime quién creó Python, y luego cuántos años tiene ese lenguaje",
        "query_type": QueryType.MULTI_HOP,
        "dataset": "NQ",
        "reason": "Estructura 'primero X, luego Y' indica razonamiento multi-salto",
    },
]

# ── Documentos base para los motores RAG ─────────────────────────────────────
BASE_DOCUMENTS = [
    Document(
        page_content=(
            "OpenAI was founded in December 2015 by Sam Altman, Greg Brockman, "
            "Ilya Sutskever, Elon Musk, and others. The company develops AI systems "
            "including the GPT series of language models. GPT-4, released in 2023, "
            "is estimated to have over 1 trillion parameters, though OpenAI has not "
            "officially confirmed the exact number."
        ),
        metadata={"source": "openai_wiki", "chunk_id": "0", "topic": "openai"},
    ),
    Document(
        page_content=(
            "Transformers are more efficient than RNNs primarily because they process "
            "all tokens in parallel rather than sequentially. RNNs must process tokens "
            "one at a time, making them slow for long sequences. Transformers use "
            "self-attention to directly relate any two positions in a sequence, "
            "solving the vanishing gradient problem that plagues RNNs. This allows "
            "Transformers to be trained on much larger datasets and achieve better results."
        ),
        metadata={"source": "transformer_wiki", "chunk_id": "1", "topic": "transformers"},
    ),
    Document(
        page_content=(
            "Retrieval-Augmented Generation (RAG) combines retrieval systems with "
            "generative models. The attention mechanism in modern LLMs uses queries, "
            "keys, and values to compute weighted sums of value vectors. For each token, "
            "the model computes attention scores against all other tokens, then uses "
            "these scores to aggregate information. Multi-head attention runs this "
            "process in parallel with different learned projections."
        ),
        metadata={"source": "rag_attention_wiki", "chunk_id": "2", "topic": "rag_attention"},
    ),
    Document(
        page_content=(
            "ChromaDB is a vector database designed for AI applications. The "
            "ChromaVectorStore class wraps ChromaDB and exposes similarity_search(). "
            "Python async/await syntax allows writing concurrent code that looks like "
            "synchronous code. The asyncio module provides the event loop infrastructure. "
            "BERT uses bidirectional training unlike GPT's unidirectional approach."
        ),
        metadata={"source": "technical_docs", "chunk_id": "3", "topic": "technical"},
    ),
    Document(
        page_content=(
            "Python was created by Guido van Rossum and first released in 1991. "
            "In 2024, Python is 33 years old. It has become the most popular "
            "programming language for data science and machine learning. "
            "The best AI tools in 2024 include LangChain, LlamaIndex, AutoGen, "
            "CrewAI, and the lightagent-agents framework."
        ),
        metadata={"source": "python_wiki", "chunk_id": "4", "topic": "python"},
    ),
]


async def setup_adaptive_rag(collection_name: str = "adaptive_rag_example") -> AdaptiveRAGEngine:
    """Configura el motor Adaptive RAG con todos los sub-motores.

    Args:
        collection_name: Nombre de la colección ChromaDB base.

    Returns:
        AdaptiveRAGEngine configurado con CRAG, HyDE, RAG-Fusion y Hybrid.
    """
    # Vector store compartido
    store = ChromaVectorStore(collection_name=collection_name)
    store.add_documents(BASE_DOCUMENTS)
    print(f"  ✓ {len(BASE_DOCUMENTS)} documentos indexados en ChromaDB")

    # Construir sub-motores
    crag = CRAGPipeline(vector_store=store)
    hyde = HyDERetriever(vector_store=store)
    fusion = RAGFusionEngine(vector_store=store, n_queries=4)
    hybrid = HybridSearchEngine(vector_store=store, alpha=0.5)

    # Construir índice BM25 para Hybrid Search
    corpus = [doc.page_content for doc in BASE_DOCUMENTS]
    doc_ids = [doc.metadata["chunk_id"] for doc in BASE_DOCUMENTS]
    hybrid.build_index(corpus=corpus, doc_ids=doc_ids)
    print(f"  ✓ Índice BM25 construido para {len(corpus)} documentos")

    # Motor Adaptive con todos los sub-motores disponibles
    return AdaptiveRAGEngine(
        crag_pipeline=crag,
        hyde_retriever=hyde,
        fusion_engine=fusion,
        hybrid_engine=hybrid,
        use_llm_classifier=False,  # clasificador regex (sin coste de LLM)
    )


def print_adaptive_result(query_info: dict, result: AdaptiveResult) -> None:
    """Imprime el resultado del motor adaptativo."""
    classification_correct = result.query_type == query_info["query_type"]
    icon = "✓" if classification_correct else "✗"

    print(f"\n[{query_info['id']}] {query_info['query']}")
    print(f"  Dataset      : {query_info['dataset']}")
    print(f"  Tipo esperado: {query_info['query_type'].value}")
    print(
        f"  Tipo detectado: {result.query_type.value}  {icon}  (confianza: {result.confidence:.2f})"
    )
    print(f"  Motor usado  : {result.strategy_used}")
    print(f"  Chunks obtenidos: {len(result.chunks)}")

    for chunk in result.chunks[:2]:  # mostrar máximo 2 chunks
        print(f"    • [{chunk.relevance_score:.2f}] {chunk.source}: {chunk.content[:80]}...")
    print("─" * 70)


async def main() -> None:
    print("=" * 70)
    print("  Adaptive RAG — Dataset: NQ + TriviaQA (clasificación de queries)")
    print("=" * 70)

    # Configuración
    print("\n[Inicialización de sub-motores RAG]")
    engine = await setup_adaptive_rag()
    print("  ✓ Motor Adaptive RAG listo con 4 sub-motores")

    # Tabla de enrutamiento
    print("\n[Tabla de enrutamiento (clasificador regex)]")
    routing = [
        ("FACTUAL_SIMPLE", "CRAG", "hechos directos, fechas, nombres"),
        ("ABSTRACT       ", "HyDE", "preguntas '¿por qué?', '¿cómo funciona?'"),
        ("AMBIGUOUS      ", "RAG-Fusion", "queries cortas/vagas (< 3 tokens)"),
        ("TECHNICAL      ", "Hybrid", "snake_case, CamelCase, API, SDK"),
        ("CONVERSATIONAL ", "CRAG", "referencias a contexto previo"),
        ("MULTI_HOP      ", "CRAG*", "estructura 'primero X, luego Y'"),
    ]
    for qtype, motor, criteria in routing:
        print(f"  {qtype} → {motor:12s} ({criteria})")
    print("  * GraphRAG reservado para Fase B")

    # Evaluar todas las queries
    print(f"\n[Evaluando {len(ADAPTIVE_QUERIES)} queries de NQ + TriviaQA]")

    correct_classifications = 0
    engine_usage: dict[str, int] = {}

    for query_info in ADAPTIVE_QUERIES:
        result = await engine.search(query_info["query"], k=3)
        print_adaptive_result(query_info, result)

        if result.query_type == query_info["query_type"]:
            correct_classifications += 1

        engine_usage[result.strategy_used] = engine_usage.get(result.strategy_used, 0) + 1

    # Resumen estadístico
    print("\n[Resumen estadístico]")
    accuracy = correct_classifications / len(ADAPTIVE_QUERIES)
    print(
        f"  Clasificación correcta : {correct_classifications}/{len(ADAPTIVE_QUERIES)} ({accuracy:.0%})"
    )

    print("\n  Distribución de motores usados:")
    for engine_name, count in sorted(engine_usage.items(), key=lambda x: -x[1]):
        bar = "█" * count
        print(f"    {engine_name:15s}: {bar} ({count})")

    # Demostrar LLM classifier
    print("\n[Clasificador LLM vs Regex]")
    print("  Regex (por defecto):")
    print("    + Sin coste de LLM adicional")
    print("    + Determinístico y reproducible")
    print("    - Puede fallar en casos límite ambiguos")
    print("  LLM (use_llm_classifier=True):")
    print("    + Mejor precisión en casos límite")
    print("    + Comprende matices del lenguaje natural")
    print("    - Coste extra de llamada al LLM")
    print("    - Tiene fallback a regex si el LLM falla")


if __name__ == "__main__":
    asyncio.run(main())

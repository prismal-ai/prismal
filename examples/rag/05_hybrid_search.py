"""
Hybrid Search — BM25 léxico + semántico con fusión ponderada
=============================================================
Arquitectura: SPEC-RAG-004 / lightagent.rag.hybrid

Dataset: AG News (News Topic Classification)
  • 127 600 artículos de noticias de 4 categorías: World, Sports, Business, Science/Tech.
  • Referencia: https://huggingface.co/datasets/fancyzhx/ag_news
  • Por qué: Hybrid Search combina BM25 (coincidencia léxica exacta) con
    búsqueda semántica. Las noticias tienen términos técnicos y nombres propios
    que BM25 maneja bien (exactos), y temas temáticos que la semántica
    captura mejor. AG News es el benchmark estándar para retrieval en news.

Descripción de la arquitectura Hybrid Search:
  Fórmula: score(d) = α × semantic(d) + (1 - α) × bm25_norm(d)
  - BM25 (Okapi): TF-IDF probabilístico, excelente para keywords exactas
  - Semántico: embeddings densos, excelente para sinónimos y paráfrasis
  - α = 0.5 (por defecto): balance igual entre ambos
  - α = 1.0: solo semántico
  - α = 0.0: solo BM25

  Limitación: BM25Okapi in-memory; >100K docs → usar backend con índice invertido.

Experimentos incluidos:
  - Comparativa α=0.0, 0.3, 0.5, 0.7, 1.0 en el mismo corpus
  - Casos donde BM25 gana (keywords exactas, nombres propios)
  - Casos donde semántico gana (sinónimos, paráfrasis)

Uso:
    uv run python examples/rag/05_hybrid_search.py
"""

from __future__ import annotations

import asyncio

from langchain_core.documents import Document

from lightagent.rag.crag import RetrievedChunk
from lightagent.rag.hybrid import HybridSearchEngine
from lightagent.rag.vector_store import ChromaVectorStore

# ── Dataset: artículos AG News ────────────────────────────────────────────────
# Muestra representativa de 4 categorías de AG News.
AG_NEWS_ARTICLES = [
    # Categoría: Science/Technology
    {
        "id": "ANG001",
        "category": "Science/Tech",
        "title": "GPT-4 Achieves Human-Level Performance on Medical Licensing Exams",
        "text": (
            "OpenAI's GPT-4 language model has demonstrated human-level performance on "
            "the United States Medical Licensing Examination (USMLE), scoring above the "
            "passing threshold. The model achieves 86.7% accuracy on Step 1 questions "
            "without any medical fine-tuning. Researchers attribute this to GPT-4's "
            "broad pretraining on medical literature, textbooks, and clinical guidelines."
        ),
    },
    {
        "id": "ANG002",
        "category": "Science/Tech",
        "title": "Quantum Computing Milestone: IBM Reaches 1000-Qubit Processor",
        "text": (
            "IBM has unveiled its Condor quantum processor with 1,121 qubits, surpassing "
            "the 1000-qubit milestone. The processor uses superconducting transmon qubits "
            "operating at temperatures near absolute zero. IBM claims this represents a "
            "major step toward quantum advantage in optimization and simulation problems. "
            "The system achieves error rates below 0.3% per two-qubit gate operation."
        ),
    },
    # Categoría: Business
    {
        "id": "ANG003",
        "category": "Business",
        "title": "Microsoft Acquires AI Startup for $1.5 Billion in Strategic Move",
        "text": (
            "Microsoft Corporation announced the acquisition of an AI startup specializing "
            "in enterprise language models for $1.5 billion. The deal includes integration "
            "with Azure OpenAI Service. Analysts expect the acquisition to boost Microsoft's "
            "market share in enterprise AI solutions by 15% over the next fiscal year. "
            "The startup's 200 engineers will join Microsoft's AI division in Redmond."
        ),
    },
    {
        "id": "ANG004",
        "category": "Business",
        "title": "NVIDIA Stock Surges 40% as AI Chip Demand Exceeds Projections",
        "text": (
            "NVIDIA Corporation shares surged 40% in after-hours trading following "
            "quarterly results that showed GPU demand far exceeding Wall Street projections. "
            "Data center revenue reached $18.4 billion, driven by AI training workloads. "
            "CEO Jensen Huang stated that the company is accelerating H100 production "
            "to meet unprecedented demand from cloud providers and AI research labs."
        ),
    },
    # Categoría: Sports
    {
        "id": "ANG005",
        "category": "Sports",
        "title": "Barcelona FC Signs AI-Powered Training System for Player Performance",
        "text": (
            "FC Barcelona has implemented an AI-powered player performance analytics "
            "system developed by a Silicon Valley startup. The system uses computer vision "
            "and biomechanics analysis to optimize training loads and predict injury risk. "
            "The club reports a 23% reduction in soft tissue injuries since implementation. "
            "Manager Xavi Hernandez praised the system for providing data-driven insights."
        ),
    },
    # Categoría: World
    {
        "id": "ANG006",
        "category": "World",
        "title": "European Union Passes AI Act: Comprehensive Regulation for Artificial Intelligence",
        "text": (
            "The European Parliament has passed the EU AI Act, the world's first comprehensive "
            "legal framework for artificial intelligence. The regulation classifies AI systems "
            "by risk level: unacceptable, high, limited, and minimal risk. High-risk systems "
            "in healthcare, law enforcement, and critical infrastructure face strict compliance "
            "requirements. The Act prohibits real-time biometric surveillance in public spaces "
            "and social scoring systems. Companies face fines up to 7% of global revenue."
        ),
    },
    {
        "id": "ANG007",
        "category": "Science/Tech",
        "title": "LangChain and LlamaIndex Partnership Accelerates Enterprise RAG Adoption",
        "text": (
            "LangChain and LlamaIndex announced a technical partnership to standardize "
            "Retrieval-Augmented Generation (RAG) pipelines for enterprise deployments. "
            "The partnership introduces interoperability between both frameworks' document "
            "loaders, vector stores, and LLM integrations. Early adopters report 60% "
            "reduction in development time for production RAG systems. The collaboration "
            "targets the growing market of companies deploying domain-specific AI assistants."
        ),
    },
    {
        "id": "ANG008",
        "category": "Science/Tech",
        "title": "ChromaDB Releases Version 0.5 with Enhanced Filtering and MMR Support",
        "text": (
            "ChromaDB vector database released version 0.5.0 with improved metadata "
            "filtering, Maximal Marginal Relevance (MMR) support for diverse retrieval, "
            "and 3x faster query performance. The update supports hybrid search combining "
            "dense vector similarity with sparse BM25 retrieval. ChromaDB now handles "
            "collections with over 10 million embeddings efficiently. The release also "
            "includes native integration with LangChain and LlamaIndex frameworks."
        ),
    },
]

# ── Queries para comparativa BM25 vs Semántico ────────────────────────────────
HYBRID_QUERIES = [
    # BM25 gana: keywords exactas, nombres propios, términos técnicos
    {
        "id": "HQ1",
        "query": "NVIDIA H100 GPU data center revenue Jensen Huang",
        "type": "keyword_exact",
        "expected_source": "ANG004",
        "reason": "Nombres propios exactos → BM25 supera al semántico",
    },
    {
        "id": "HQ2",
        "query": "ChromaDB version 0.5 MMR metadata filtering BM25",
        "type": "keyword_exact",
        "expected_source": "ANG008",
        "reason": "Términos técnicos exactos + números de versión",
    },
    # Semántico gana: sinónimos, paráfrasis, conceptos
    {
        "id": "HQ3",
        "query": "Regulación gubernamental de sistemas inteligentes en Europa",
        "type": "semantic_paraphrase",
        "expected_source": "ANG006",
        "reason": "Paráfrasis de 'EU AI Act' — semántico captura el concepto",
    },
    {
        "id": "HQ4",
        "query": "rendimiento de deportistas mejorado con análisis de datos",
        "type": "semantic_paraphrase",
        "expected_source": "ANG005",
        "reason": "Paráfrasis de 'player performance analytics' — semántico",
    },
    # Hybrid gana: combinación de keyword + semántico
    {
        "id": "HQ5",
        "query": "RAG pipeline enterprise LangChain integration performance",
        "type": "hybrid_optimal",
        "expected_source": "ANG007",
        "reason": "Mix de keywords exactas (LangChain) + concepto (RAG pipeline)",
    },
    {
        "id": "HQ6",
        "query": "Microsoft billion dollar AI startup Azure cloud services",
        "type": "hybrid_optimal",
        "expected_source": "ANG003",
        "reason": "Nombres propios (Microsoft) + concepto (cloud AI services)",
    },
]


async def setup_hybrid(alpha: float = 0.5) -> HybridSearchEngine:
    """Configura el motor Hybrid Search con el corpus AG News.

    Args:
        alpha: Peso del componente semántico [0.0=BM25, 0.5=hybrid, 1.0=semántico].

    Returns:
        HybridSearchEngine configurado y listo para búsqueda.
    """
    store = ChromaVectorStore(collection_name=f"ag_news_hybrid_{alpha:.1f}")

    # Crear documentos
    docs = [
        Document(
            page_content=f"{art['title']}. {art['text']}",
            metadata={
                "source": art["id"],
                "category": art["category"],
                "title": art["title"],
                "chunk_id": art["id"],
                "dataset": "AG_News",
            },
        )
        for art in AG_NEWS_ARTICLES
    ]

    store.add_documents(docs)

    engine = HybridSearchEngine(
        vector_store=store,
        alpha=alpha,
    )

    # Construir índice BM25 con el mismo corpus
    corpus = [f"{art['title']}. {art['text']}" for art in AG_NEWS_ARTICLES]
    doc_ids = [art["id"] for art in AG_NEWS_ARTICLES]
    engine.build_index(corpus=corpus, doc_ids=doc_ids)

    return engine


def evaluate_retrieval(
    query_info: dict,
    chunks: list[RetrievedChunk],
    top_k: int = 3,
) -> bool:
    """Evalúa si el documento esperado está en los top-k resultados."""
    expected = query_info["expected_source"]
    retrieved_sources = [c.source for c in chunks[:top_k]]
    return expected in retrieved_sources


async def run_alpha_comparison(query_info: dict) -> dict[float, bool]:
    """Compara resultados con distintos valores de α para una query."""
    alphas = [0.0, 0.3, 0.5, 0.7, 1.0]
    results = {}

    for alpha in alphas:
        engine = await setup_hybrid(alpha)
        chunks = engine.search(query_info["query"], k=3)
        results[alpha] = evaluate_retrieval(query_info, chunks)

    return results


async def main() -> None:
    print("=" * 70)
    print("  Hybrid Search (BM25 + Semántico) — Dataset: AG News")
    print("=" * 70)

    print("\n[Fórmula de Hybrid Search]")
    print("  score(d) = α × semantic(d) + (1-α) × bm25_norm(d)")
    print("  α = 0.0 → solo BM25 (léxico exacto)")
    print("  α = 0.5 → balance igual (recomendado por defecto)")
    print("  α = 1.0 → solo semántico (embeddings densos)")

    # Configurar motor principal (α=0.5)
    print("\n[Inicialización con α=0.5]")
    engine = await setup_hybrid(alpha=0.5)
    print(f"  ✓ Índice BM25 y vector store listos ({len(AG_NEWS_ARTICLES)} artículos)")

    # Evaluar queries
    print(f"\n[Evaluando {len(HYBRID_QUERIES)} queries AG News]")

    for query_info in HYBRID_QUERIES:
        chunks = engine.search(query_info["query"], k=3)
        found = evaluate_retrieval(query_info, chunks, top_k=3)

        print(f"\n[{query_info['id']}] Tipo: {query_info['type']}")
        print(f"  Query   : {query_info['query']}")
        print(f"  Razón   : {query_info['reason']}")
        print(f"  Top-3 resultados:")
        for chunk in chunks[:3]:
            mark = "→" if chunk.source == query_info["expected_source"] else " "
            print(f"    {mark} [{chunk.relevance_score:.3f}] {chunk.source}")
        print(f"  Fuente esperada encontrada: {'✓' if found else '✗'}")

    # Experimento: comparativa de α
    print("\n[Experimento: efecto del parámetro α]")
    print("  Comparando α en 3 queries representativas...")
    print()

    alpha_labels = ["α=0.0\n(BM25)", "α=0.3", "α=0.5\n(default)", "α=0.7", "α=1.0\n(semántico)"]
    sample_queries = HYBRID_QUERIES[:3]  # 1 keyword, 1 semántico, 1 hybrid

    for query_info in sample_queries:
        alpha_results = await run_alpha_comparison(query_info)
        print(f"  [{query_info['id']}] {query_info['type']}")
        print(f"    Query: {query_info['query'][:60]}...")
        result_line = "    " + " | ".join(
            f"α={a:.1f}:{'✓' if ok else '✗'}"
            for a, ok in alpha_results.items()
        )
        print(result_line)
        print()

    # Conclusiones
    print("[Guía de selección de α]")
    guidelines = [
        (0.0, "Solo BM25", "Documentos legales, código fuente, búsqueda exacta"),
        (0.3, "BM25 dominante", "Corpora técnicos con vocabulario especializado"),
        (0.5, "Balance (default)", "Uso general — mejor compromiso"),
        (0.7, "Semántico dominante", "Texto conversacional, sinónimos frecuentes"),
        (1.0, "Solo semántico", "Búsqueda por concepto, cross-lingual retrieval"),
    ]
    for alpha, label, use_case in guidelines:
        print(f"  α={alpha:.1f} ({label:20s}): {use_case}")


if __name__ == "__main__":
    asyncio.run(main())

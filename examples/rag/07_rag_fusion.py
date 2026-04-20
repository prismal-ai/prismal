"""
RAG-Fusion — Múltiples queries + Reciprocal Rank Fusion (RRF)
=============================================================
Arquitectura: SPEC-RAG-002 / lightagent.rag.fusion

Dataset: BEIR (Benchmark for Evaluating IR Models)
  • 18 datasets de IR heterogéneos (TREC-COVID, HotpotQA, ArguAna, etc.)
  • Referencia: https://huggingface.co/datasets/BeIR/beir
  • Por qué: RAG-Fusion fue diseñado específicamente para mejorar el recall
    al generar múltiples variaciones de la query y fusionar sus rankings.
    BEIR es el benchmark estándar para evaluar mejoras en retrieval.
    Especialmente efectivo en ArguAna (argumentación) y TREC-COVID.

Descripción de la arquitectura RAG-Fusion:
  1. Genera N variaciones de la query original (el LLM parafrasea)
  2. Ejecuta N búsquedas en paralelo (una por variación)
  3. Fusiona los rankings con RRF (Reciprocal Rank Fusion):
     score(d) = Σ_{q in queries} 1 / (k + rank(d, q))
     donde k=60 es el parámetro estabilizador (Cormack et al. 2009)
  4. Los documentos que aparecen en múltiples rankings salen primero

  Beneficio: Mayor recall — un documento que no aparece con la query
  original puede aparecer con una de sus paráfrasis.

Uso:
    uv run python examples/rag/07_rag_fusion.py
"""

from __future__ import annotations

import asyncio

from langchain_core.documents import Document

from lightagent.rag.fusion import FusionResult, RAGFusionEngine, reciprocal_rank_fusion
from lightagent.rag.vector_store import ChromaVectorStore

# ── Dataset: documentos BEIR (TREC-COVID + ArguAna style) ────────────────────
# Documentos científicos y argumentativos representativos de BEIR.
BEIR_DOCUMENTS = [
    # TREC-COVID: papers sobre COVID-19
    {
        "source": "trec_covid_001",
        "corpus": "TREC-COVID",
        "text": (
            "Effectiveness of COVID-19 mRNA vaccines against the Omicron variant "
            "showed reduced neutralization but maintained protection against severe disease. "
            "Three-dose vaccination regimens provided 70% protection against hospitalization "
            "compared to 40% for two-dose schedules. Booster vaccination timing significantly "
            "impacts efficacy, with optimal boosting at 6-month intervals post-primary series."
        ),
    },
    {
        "source": "trec_covid_002",
        "corpus": "TREC-COVID",
        "text": (
            "Long COVID, also known as post-acute sequelae of SARS-CoV-2 (PASC), affects "
            "approximately 10-30% of infected individuals. Common symptoms include fatigue, "
            "cognitive impairment ('brain fog'), dyspnea, and post-exertional malaise. "
            "Pathophysiological mechanisms include viral persistence, immune dysregulation, "
            "microbiome disruption, and autoimmune responses. No FDA-approved treatments "
            "exist specifically for Long COVID as of 2024."
        ),
    },
    # ArguAna: argumentación científica
    {
        "source": "arguana_001",
        "corpus": "ArguAna",
        "text": (
            "Universal Basic Income (UBI) represents a transformative approach to social "
            "welfare that provides unconditional regular payments to all citizens. "
            "Proponents argue UBI eliminates poverty traps, reduces bureaucracy of means-tested "
            "welfare, and provides economic security during technological unemployment. "
            "Pilot programs in Finland and Kenya demonstrated improved mental health outcomes "
            "and maintained work incentives contrary to critics' predictions."
        ),
    },
    {
        "source": "arguana_002",
        "corpus": "ArguAna",
        "text": (
            "Artificial intelligence regulation is necessary to prevent monopolistic control "
            "of AI systems by a small number of corporations. Without regulation, AI capabilities "
            "concentrate power among those with computational resources, potentially exacerbating "
            "economic inequality. Regulatory frameworks like the EU AI Act establish risk-based "
            "oversight while preserving innovation. Open-source AI development offers an "
            "alternative path to democratizing AI access."
        ),
    },
    # HotpotQA: documentos de razonamiento multi-salto
    {
        "source": "hotpot_001",
        "corpus": "HotpotQA",
        "text": (
            "The attention mechanism in large language models scales quadratically with "
            "sequence length, creating computational bottlenecks for long documents. "
            "Flash Attention optimizes memory access patterns to achieve linear memory "
            "scaling. Sparse attention variants like Longformer and BigBird extend context "
            "windows to 4096+ tokens using sliding window and global attention patterns."
        ),
    },
    {
        "source": "hotpot_002",
        "corpus": "HotpotQA",
        "text": (
            "Vector databases enable semantic search by storing high-dimensional embeddings "
            "and performing approximate nearest neighbor (ANN) search. Algorithms like "
            "HNSW (Hierarchical Navigable Small World) and IVF (Inverted File Index) "
            "provide sub-linear query time. ChromaDB, Pinecone, Weaviate, and Qdrant "
            "are leading solutions for production RAG deployments requiring low-latency retrieval."
        ),
    },
    {
        "source": "hotpot_003",
        "corpus": "HotpotQA",
        "text": (
            "Retrieval-Augmented Generation improves large language model accuracy by "
            "providing relevant external context at inference time. The retrieval component "
            "uses dense passage retrieval (DPR) or sparse BM25 methods. Advanced RAG "
            "variants include Corrective RAG (CRAG), Self-RAG, RAG-Fusion, and HyDE. "
            "Each variant addresses different failure modes: relevance, hallucination, "
            "query ambiguity, and vocabulary mismatch respectively."
        ),
    },
    {
        "source": "fever_001",
        "corpus": "FEVER",
        "text": (
            "The Transformer architecture was introduced by Vaswani et al. in 2017 in the "
            "landmark paper 'Attention Is All You Need'. Prior to Transformers, sequence "
            "modeling relied on recurrent architectures (LSTM, GRU) that processed tokens "
            "sequentially. Transformers revolutionized NLP by enabling massive parallelization "
            "and direct attention between any two positions in the sequence."
        ),
    },
]

# ── Queries BEIR con múltiples formulaciones equivalentes ────────────────────
BEIR_QUERIES = [
    {
        "id": "BQ1",
        "original_query": "¿Cómo afecta el COVID prolongado al sistema cognitivo?",
        "corpus": "TREC-COVID",
        "expected_source": "trec_covid_002",
        "expected_queries_contain": ["Long COVID", "cognitive", "brain fog"],
    },
    {
        "id": "BQ2",
        "original_query": "ventajas de la renta básica universal para combatir la pobreza",
        "corpus": "ArguAna",
        "expected_source": "arguana_001",
        "expected_queries_contain": ["UBI", "welfare", "basic income"],
    },
    {
        "id": "BQ3",
        "original_query": "¿Qué bases de datos vectoriales se usan en producción para RAG?",
        "corpus": "HotpotQA",
        "expected_source": "hotpot_002",
        "expected_queries_contain": ["vector database", "ChromaDB", "Pinecone"],
    },
    {
        "id": "BQ4",
        "original_query": "arquitecturas RAG avanzadas más allá de RAG básico",
        "corpus": "HotpotQA",
        "expected_source": "hotpot_003",
        "expected_queries_contain": ["CRAG", "Self-RAG", "RAG-Fusion", "HyDE"],
    },
]


async def setup_rag_fusion(n_queries: int = 4) -> RAGFusionEngine:
    """Configura RAG-Fusion con el corpus BEIR."""
    store = ChromaVectorStore(collection_name="beir_rag_fusion")

    docs = [
        Document(
            page_content=doc["text"],
            metadata={
                "source": doc["source"],
                "corpus": doc["corpus"],
                "chunk_id": doc["source"],
                "dataset": "BEIR",
            },
        )
        for doc in BEIR_DOCUMENTS
    ]

    store.add_documents(docs)
    print(f"  ✓ {len(docs)} documentos BEIR indexados ({len(set(d['corpus'] for d in BEIR_DOCUMENTS))} corpora)")

    engine = RAGFusionEngine(
        vector_store=store,
        n_queries=n_queries,  # número de variaciones de query a generar
    )
    return engine


def print_fusion_result(query_info: dict, result: FusionResult) -> None:
    """Muestra el resultado con todas las queries generadas y el ranking RRF."""
    print(f"\n[{query_info['id']}] Query original:")
    print(f"  '{query_info['original_query']}'")

    print(f"\n  Queries generadas por RAG-Fusion ({len(result.queries)}):")
    for i, q in enumerate(result.queries, 1):
        print(f"    {i}. {q}")

    print(f"\n  Per-query results:")
    for i, (query, chunks) in enumerate(zip(result.queries, result.per_query_results)):
        top_sources = [c.source for c in chunks[:3]]
        print(f"    Q{i+1}: top-3 → {top_sources}")

    print(f"\n  Ranking fusionado (RRF score):")
    for chunk in result.chunks[:5]:
        expected_mark = "→ " if chunk.source == query_info["expected_source"] else "  "
        print(f"    {expected_mark}[{chunk.relevance_score:.4f}] {chunk.source}")

    # Verificar recall
    top3_sources = [c.source for c in result.chunks[:3]]
    found = query_info["expected_source"] in top3_sources
    print(f"\n  Fuente esperada en top-3: {'✓' if found else '✗'}")
    print("─" * 70)


def demo_rrf_formula() -> None:
    """Demuestra la fórmula RRF con un ejemplo concreto."""
    print("\n[Fórmula RRF — Reciprocal Rank Fusion]")
    print("  score(d) = Σ_{q in queries} 1 / (k + rank(d, q))")
    print("  k = 60 (parámetro estabilizador de Cormack et al. 2009)")
    print()

    # Ejemplo simulado
    k = 60
    print("  Ejemplo con 3 queries y 3 documentos:")
    print()
    print(f"  {'Doc':<12} {'Q1 rank':>8} {'Q2 rank':>8} {'Q3 rank':>8} {'RRF score':>12}")
    print("  " + "─" * 52)

    scenarios = [
        ("doc_A", 1, 2, 1),  # aparece alto en todas → ganador
        ("doc_B", 3, 1, 5),  # aparece en algunas
        ("doc_C", 10, 15, 2),  # solo fuerte en Q3
        ("doc_D", None, 3, None),  # solo en Q2
    ]

    for doc, r1, r2, r3 in scenarios:
        rrf = 0.0
        if r1: rrf += 1 / (k + r1)
        if r2: rrf += 1 / (k + r2)
        if r3: rrf += 1 / (k + r3)

        r1_str = str(r1) if r1 else "—"
        r2_str = str(r2) if r2 else "—"
        r3_str = str(r3) if r3 else "—"
        print(f"  {doc:<12} {r1_str:>8} {r2_str:>8} {r3_str:>8} {rrf:>12.6f}")

    print()
    print("  Observación: doc_A gana porque aparece primero en 2 de 3 queries.")
    print("  doc_C con rango 2 en Q3 supera a doc_D con rango 3 en Q2.")

    # Demostrar la función reciprocal_rank_fusion directamente
    from lightagent.rag.crag import RetrievedChunk
    sample_lists = [
        [RetrievedChunk("s1", "0", 0.9, "text1"), RetrievedChunk("s2", "1", 0.8, "text2")],
        [RetrievedChunk("s2", "1", 0.7, "text2"), RetrievedChunk("s1", "0", 0.6, "text1")],
    ]
    fused = reciprocal_rank_fusion(sample_lists, k=60)
    print(f"\n  reciprocal_rank_fusion() demo:")
    for chunk in fused:
        print(f"    {chunk.source}: RRF={chunk.relevance_score:.6f}")


async def main() -> None:
    print("=" * 70)
    print("  RAG-Fusion — Dataset: BEIR (TREC-COVID + ArguAna + HotpotQA)")
    print("=" * 70)

    # Demostrar la fórmula RRF
    demo_rrf_formula()

    # Configurar motor
    print("\n[Inicialización RAG-Fusion]")
    engine = await setup_rag_fusion(n_queries=4)
    print(f"  ✓ RAG-Fusion listo (generará 4 variaciones por query)")

    # Ejecutar queries BEIR
    print(f"\n[Ejecutando {len(BEIR_QUERIES)} queries BEIR]")

    correct_count = 0
    for query_info in BEIR_QUERIES:
        result = await engine.search(query_info["original_query"], k=5)
        print_fusion_result(query_info, result)

        top3 = [c.source for c in result.chunks[:3]]
        if query_info["expected_source"] in top3:
            correct_count += 1

    # Resumen
    recall_at_3 = correct_count / len(BEIR_QUERIES)
    print(f"\n[Resumen]")
    print(f"  Recall@3: {correct_count}/{len(BEIR_QUERIES)} ({recall_at_3:.0%})")

    print("\n[Ventajas de RAG-Fusion sobre RAG estándar]")
    print("  ✓ Mayor recall: un doc no encontrado con query A puede encontrarse con B")
    print("  ✓ Robustez: no depende de la formulación exacta del usuario")
    print("  ✓ Sin parámetros de ponderación: RRF no requiere calibración")
    print("  ✗ Coste: N+1 llamadas al LLM (1 para generar queries + N para búsqueda)")
    print("  ✗ Latencia: mayor que RAG estándar por las N búsquedas paralelas")


if __name__ == "__main__":
    asyncio.run(main())

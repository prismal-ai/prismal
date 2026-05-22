"""
HyDE — Hypothetical Document Embeddings para búsqueda abstracta
================================================================
Arquitectura: SPEC-RAG-003 / prismal.rag.hyde

Dataset: MS MARCO (Microsoft MAchine Reading COmprehension)
  • 1 millón de preguntas reales de usuarios de Bing con pasajes de respuesta.
  • Referencia: https://huggingface.co/datasets/microsoft/ms_marco
  • Por qué: MS MARCO contiene muchas preguntas abstractas tipo "¿por qué?"
    y "¿cómo?" donde hay un gap semántico entre la query y los documentos.
    HyDE cierra este gap generando un documento hipotético que se parece
    más al documento real que la query original.

Descripción de la arquitectura HyDE:
  En lugar de embebber la query directamente:
  1. El LLM genera un documento hipotético que respondería la query.
  2. Este documento hipotético se embebe (con mejor cobertura semántica).
  3. Se busca en ChromaDB usando el embedding del documento hipotético.

  Beneficio: El documento hipotético usa el vocabulario y estilo
  de los documentos del corpus, no el de las queries. Esto reduce
  el "vocabulary mismatch" en búsquedas abstractas.

Comparativa incluida:
  - RAG estándar (embedding de la query directamente) vs HyDE
  - Especialmente efectivo para preguntas abstractas/conceptuales

Uso:
    uv run python examples/rag/04_hyde.py
"""

from __future__ import annotations

import asyncio

from langchain_core.documents import Document

from prismal.rag.crag import CRAGPipeline
from prismal.rag.hyde import HyDEResult, HyDERetriever
from prismal.rag.vector_store import ChromaVectorStore

# ── Dataset: pasajes MS MARCO sobre IA y tecnología ──────────────────────────
MSMARCO_PASSAGES = [
    {
        "source": "msmarco_attention_mechanism",
        "passage": (
            "The attention mechanism in neural networks allows the model to focus on "
            "relevant parts of the input when producing each output token. Unlike "
            "traditional sequence-to-sequence models that compress all input information "
            "into a single fixed-size vector, attention maintains a dynamic context. "
            "The transformer attention computes similarity scores between all pairs of "
            "positions, enabling the model to directly attend to any position in the "
            "input sequence regardless of distance. This eliminates the information "
            "bottleneck that limited earlier RNN-based models."
        ),
    },
    {
        "source": "msmarco_gradient_descent",
        "passage": (
            "Gradient descent is the optimization algorithm used to train neural networks. "
            "It minimizes the loss function by iteratively adjusting model parameters in "
            "the direction of the negative gradient. Stochastic gradient descent (SGD) "
            "uses a random subset (mini-batch) of training examples for each update, "
            "making it computationally feasible for large datasets. Adam optimizer "
            "combines momentum (exponential moving average of gradients) with "
            "adaptive learning rates per parameter, typically outperforming vanilla SGD "
            "on deep learning tasks."
        ),
    },
    {
        "source": "msmarco_overfitting",
        "passage": (
            "Overfitting occurs when a model learns the training data too well, "
            "capturing noise and random fluctuations rather than the underlying pattern. "
            "An overfit model shows low training error but high test error. "
            "Regularization techniques prevent overfitting: L1 regularization adds "
            "absolute value of parameters to loss, promoting sparsity. L2 regularization "
            "adds squared parameters, shrinking weights toward zero. Dropout randomly "
            "deactivates neurons during training, acting as an ensemble method. "
            "Data augmentation artificially increases training set diversity."
        ),
    },
    {
        "source": "msmarco_transfer_learning",
        "passage": (
            "Transfer learning leverages knowledge gained from one task to improve "
            "performance on a related task. In NLP, large pre-trained language models "
            "like BERT and GPT are fine-tuned on domain-specific datasets, requiring "
            "significantly less labeled data. The pre-training phase captures general "
            "linguistic patterns, while fine-tuning specializes the model. This approach "
            "has democratized NLP by enabling small organizations to achieve state-of-the-art "
            "results without massive computational resources."
        ),
    },
    {
        "source": "msmarco_embedding_space",
        "passage": (
            "Word embeddings represent words as dense vectors in a continuous space "
            "where semantic similarity corresponds to geometric proximity. Word2Vec "
            "demonstrated that arithmetic operations on embeddings capture semantic "
            "relationships: king - man + woman = queen. Modern contextual embeddings "
            "from BERT produce different vectors for the same word in different contexts. "
            "Sentence transformers extend this to full sentence representations, "
            "enabling semantic search, duplicate detection, and textual entailment."
        ),
    },
    {
        "source": "msmarco_rag_benefits",
        "passage": (
            "Retrieval-Augmented Generation addresses the knowledge cutoff limitation "
            "of parametric language models by grounding responses in up-to-date documents. "
            "Instead of encoding all knowledge in model parameters, RAG retrieves relevant "
            "documents at inference time. This reduces hallucinations because the model "
            "explicitly uses factual sources. RAG also improves interpretability since "
            "retrieved chunks can be shown to users as citations. The tradeoff is "
            "increased latency from the retrieval step and dependency on retrieval quality."
        ),
    },
]

# ── Queries MS MARCO — abstractas vs. concretas ───────────────────────────────
MSMARCO_QUERIES = [
    # Queries ABSTRACTAS — HyDE debería ayudar
    {
        "id": "MQ1",
        "query": "¿Por qué los transformers son mejores que las RNNs para NLP?",
        "type": "abstract",
        "expected_source": "msmarco_attention_mechanism",
        "description": "Pregunta explicativa — HyDE genera documento hipotético con 'porque'",
    },
    {
        "id": "MQ2",
        "query": "¿Cómo previenen el sobreajuste las redes neuronales modernas?",
        "type": "abstract",
        "expected_source": "msmarco_overfitting",
        "description": "Pregunta de mecanismo — vocabulario diferente entre query y documento",
    },
    {
        "id": "MQ3",
        "query": "¿Por qué el transfer learning es tan poderoso para NLP?",
        "type": "abstract",
        "expected_source": "msmarco_transfer_learning",
        "description": "Pregunta de razonamiento — HyDE cierra gap semántico",
    },
    # Queries CONCRETAS — RAG estándar debería funcionar bien
    {
        "id": "MQ4",
        "query": "gradient descent optimizer Adam learning rate",
        "type": "concrete",
        "expected_source": "msmarco_gradient_descent",
        "description": "Query técnica con keywords exactas — RAG estándar funciona bien",
    },
    {
        "id": "MQ5",
        "query": "word embeddings semantic similarity word2vec",
        "type": "concrete",
        "expected_source": "msmarco_embedding_space",
        "description": "Keywords técnicas con match léxico directo",
    },
]


async def setup_hyde(collection_name: str = "msmarco_hyde") -> tuple[HyDERetriever, CRAGPipeline]:
    """Configura HyDE y CRAG estándar para comparativa.

    Returns:
        Tupla (HyDERetriever, CRAGPipeline) con el mismo vector store.
    """
    store = ChromaVectorStore(collection_name=collection_name)

    docs = [
        Document(
            page_content=p["passage"],
            metadata={
                "source": p["source"],
                "chunk_id": str(i),
                "dataset": "MS_MARCO",
            },
        )
        for i, p in enumerate(MSMARCO_PASSAGES)
    ]

    store.add_documents(docs)
    print(f"  ✓ {len(docs)} pasajes MS MARCO indexados")

    # Prompt personalizado para generar documentos hipotéticos en español
    hypothesis_prompt = (
        "Eres un experto en inteligencia artificial y machine learning. "
        "Escribe un párrafo técnico conciso (3-5 oraciones) que responda "
        "directamente la siguiente pregunta, como si fuera un extracto de "
        "un artículo académico o documentación técnica: {query}"
    )

    hyde_retriever = HyDERetriever(
        vector_store=store,
        hypothesis_prompt=hypothesis_prompt,
    )

    crag_pipeline = CRAGPipeline(vector_store=store)

    return hyde_retriever, crag_pipeline


def print_comparison(query_info: dict, hyde_result: HyDEResult, crag_chunks: list) -> None:
    """Compara resultados HyDE vs CRAG estándar."""
    print(f"\n[{query_info['id']}] Tipo: {query_info['type'].upper()}")
    print(f"  Query   : {query_info['query']}")
    print(f"  Propósito: {query_info['description']}")

    # Documento hipotético generado
    print("\n  📝 Documento hipotético generado por HyDE:")
    print(f"     {hyde_result.hypothesis[:200]}...")

    # Comparativa de chunks recuperados
    hyde_sources = [c.source for c in hyde_result.chunks[:3]]
    crag_sources = [c.source for c in crag_chunks[:3]]

    print("\n  Chunks recuperados:")
    print(f"    HyDE (hipotético): {hyde_sources}")
    print(f"    CRAG (query directa): {crag_sources}")

    # Verificar fuente esperada
    expected = query_info.get("expected_source")
    if expected:
        hyde_found = expected in hyde_sources
        crag_found = expected in crag_sources
        print(f"\n  Fuente esperada ({expected}):")
        print(f"    HyDE encontró: {'✓' if hyde_found else '✗'}")
        print(f"    CRAG encontró: {'✓' if crag_found else '✗'}")

        # Para queries abstractas, HyDE debería ganar
        if query_info["type"] == "abstract" and hyde_found and not crag_found:
            print("    → HyDE supera a CRAG en query abstracta ✓")
        elif query_info["type"] == "concrete" and crag_found:
            print("    → CRAG funciona bien para query concreta ✓")

    print("\n  Top-2 chunks HyDE:")
    for chunk in hyde_result.chunks[:2]:
        print(f"    [{chunk.relevance_score:.2f}] {chunk.source}: {chunk.content[:80]}...")
    print("─" * 70)


async def main() -> None:
    print("=" * 70)
    print("  HyDE (Hypothetical Document Embeddings) — Dataset: MS MARCO")
    print("=" * 70)

    # Inicialización
    print("\n[Inicialización]")
    hyde_retriever, crag_pipeline = await setup_hyde()
    print("  ✓ HyDE Retriever y CRAG Pipeline inicializados")

    # Explicación del flujo HyDE
    print("\n[Flujo HyDE vs RAG estándar]")
    print("  RAG estándar:")
    print("    Query → embed(query) → similarity_search → chunks")
    print()
    print("  HyDE:")
    print("    Query → LLM genera documento hipotético H")
    print("         → embed(H) → similarity_search → chunks")
    print("  ")
    print("  La clave: embed(H) ≈ embed(documento_real) >> embed(query)")

    # Comparativa
    print(f"\n[Comparativa HyDE vs CRAG en {len(MSMARCO_QUERIES)} queries MS MARCO]")

    hyde_wins = 0
    crag_wins = 0

    for query_info in MSMARCO_QUERIES:
        # Ejecutar HyDE
        hyde_result = await hyde_retriever.retrieve(query_info["query"], k=3)

        # Ejecutar CRAG para comparar
        crag_result = await crag_pipeline.run(query_info["query"])

        print_comparison(query_info, hyde_result, crag_result.sources)

        # Contar victorias (simplificado)
        expected = query_info.get("expected_source")
        if expected:
            hyde_found = expected in [c.source for c in hyde_result.chunks[:3]]
            crag_found = expected in [c.source for c in crag_result.sources[:3]]

            if query_info["type"] == "abstract":
                if hyde_found:
                    hyde_wins += 1
                elif crag_found:
                    crag_wins += 1
            else:
                if crag_found:
                    crag_wins += 1
                elif hyde_found:
                    hyde_wins += 1

    # Resumen
    print("\n[Resumen de la comparativa]")
    print(
        f"  Queries abstractas (HyDE esperado ganar): {sum(1 for q in MSMARCO_QUERIES if q['type'] == 'abstract')}"
    )
    print(
        f"  Queries concretas (CRAG esperado ganar) : {sum(1 for q in MSMARCO_QUERIES if q['type'] == 'concrete')}"
    )

    print("\n[¿Cuándo usar HyDE?]")
    recommendations = [
        ("✓ Usar HyDE", "Preguntas abstractas: '¿por qué?', '¿cómo funciona?'"),
        ("✓ Usar HyDE", "Vocabulario de query muy diferente al corpus"),
        ("✓ Usar HyDE", "Dominio técnico con jerga especializada"),
        ("✗ Evitar HyDE", "Queries con keywords exactas del corpus"),
        ("✗ Evitar HyDE", "Latencia crítica (HyDE añade 1 llamada al LLM)"),
        ("✗ Evitar HyDE", "Presupuesto de tokens limitado"),
    ]
    for status, case in recommendations:
        print(f"  {status}: {case}")

    # Mostrar embedding hipotético (primeros tokens)
    print("\n[Ejemplo de documento hipotético generado]")
    sample_result = await hyde_retriever.retrieve(
        "¿Por qué el self-attention supera al cross-attention en muchos casos?", k=2
    )
    print("  Query   : '¿Por qué el self-attention supera al cross-attention?'")
    print(f"  Hipótesis: {sample_result.hypothesis[:250]}...")
    print(f"  Embedding dim: {len(sample_result.hypothesis_embedding)} tokens hipotéticos")


if __name__ == "__main__":
    asyncio.run(main())

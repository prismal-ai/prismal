"""
Multi-Vector RAG — Tres representaciones por documento
======================================================
Arquitectura: SPEC-RAG-007 / prismal.rag.multi_vector

Dataset: ArXiv Papers (Machine Learning & AI)
  • 2.2+ millones de papers científicos de ArXiv en múltiples dominios.
  • Referencia: https://huggingface.co/datasets/ccdv/arxiv-summarization
  • Por qué: Los papers científicos tienen un mismatch natural entre la query
    del usuario y el contenido: el usuario puede preguntar con un concepto
    simple, pero el paper usa vocabulario técnico especializado.
    Multi-Vector RAG indexa el mismo documento bajo 3 representaciones
    (chunk, resumen, preguntas hipotéticas) para capturar diferentes
    ángulos de acceso al documento.

Descripción de la arquitectura Multi-Vector RAG:
  Para cada documento, genera e indexa 3 representaciones:
  1. Chunk    : Texto original fragmentado (~500 chars)
  2. Summary  : Resumen generado por LLM del chunk
  3. Questions: N preguntas hipotéticas que el chunk respondería

  Búsqueda:
  - Busca en las 3 representaciones simultáneamente
  - Dedup por doc_id (mismo documento bajo representaciones distintas)
  - Retorna el chunk original (no la representación que hizo match)

  Beneficio: Un documento puede ser encontrado por:
  - Su contenido exacto (chunk)
  - Un concepto resumido (summary)
  - Una pregunta específica que responde (questions)

Uso:
    uv run python examples/rag/08_multi_vector_rag.py
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from prismal.rag.multi_vector import MultiVectorRAGEngine, MultiVectorResult
from prismal.rag.vector_store import ChromaVectorStore

# ── Dataset: abstracts y fragmentos de papers de ArXiv ───────────────────────
# Papers representativos de ML/AI de ArXiv (2023-2024).
ARXIV_PAPERS = [
    {
        "arxiv_id": "2302.04761",
        "title": "Language Is Not All You Need: Aligning Perception with Language Models",
        "filename": "kosmos1_multimodal.txt",
        "content": (
            "We introduce KOSMOS-1, a Multimodal Large Language Model (MLLM) that can perceive "
            "general modalities, learn in context (i.e., few-shot), and follow instructions "
            "(i.e., zero-shot). Specifically, we train KOSMOS-1 from scratch on web-scale "
            "multimodal corpora, including arbitrarily interleaved text and images, image-caption "
            "pairs, and text data. We evaluate various settings, including zero-shot, few-shot, "
            "and multimodal chain-of-thought prompting. KOSMOS-1 achieves impressive performance "
            "on language understanding, generation, OCR-free NLP (e.g., FLAN tokens), "
            "perception-language tasks (e.g., image captioning, visual QA), speech recognition, "
            "and speech-to-text translation. "
            "A key capability of MLLMs is cross-modal transfer, where knowledge learned from one "
            "modality can be applied to another. KOSMOS-1 demonstrates this by achieving "
            "competitive results on visual commonsense reasoning tasks despite being trained "
            "primarily on language data. The model architecture uses a transformer backbone "
            "with vision encoders that process image patches as tokens alongside text tokens."
        ),
    },
    {
        "arxiv_id": "2307.09288",
        "title": "Llama 2: Open Foundation and Fine-Tuned Chat Models",
        "filename": "llama2_paper.txt",
        "content": (
            "In this work, we develop and release Llama 2, a collection of pretrained and "
            "fine-tuned large language models (LLMs) ranging in scale from 7 billion to 70 billion "
            "parameters. Our fine-tuned LLMs, called Llama 2-Chat, are optimized for dialogue use "
            "cases. Our models outperform open-source chat models on most benchmarks we tested, "
            "and based on our human evaluations for helpfulness and safety, may be a suitable "
            "substitute for closed-source models. We provide a detailed description of our "
            "approach to fine-tuning and safety improvements of Llama 2-Chat in order to enable "
            "the community to build on our work and contribute to the responsible development of LLMs. "
            "We used Reinforcement Learning from Human Feedback (RLHF) with Proximal Policy "
            "Optimization (PPO) and rejection sampling to align the models with human preferences. "
            "Ghost Attention (GAtt) was introduced to maintain instruction following in multi-turn "
            "conversations by conditioning on the system prompt throughout the dialogue."
        ),
    },
    {
        "arxiv_id": "2310.11511",
        "title": "Self-RAG: Learning to Retrieve, Generate, and Critique through Self-Reflection",
        "filename": "self_rag_paper.txt",
        "content": (
            "We introduce Self-RAG, a framework that trains an arbitrary LM to reflectively "
            "retrieve passages on demand, and generate and critique its own output. Unlike "
            "conventional RAG that always retrieves a fixed number of passages regardless of "
            "whether retrieval is necessary, Self-RAG trains a single arbitrary LM that "
            "adaptively retrieves passages on demand, and generates and reflects on retrieved "
            "passages and its own generations using special tokens, called reflection tokens. "
            "Generating reflection tokens makes the LM controllable during the inference phase, "
            "enabling it to tailor its behavior to diverse task requirements. Experiments show "
            "that Self-RAG (7B and 13B parameters) significantly outperforms state-of-the-art "
            "LLMs and retrieval-augmented models on various tasks including open-domain QA, "
            "reasoning, and fact verification. Self-RAG introduces four types of special tokens: "
            "[Retrieve], [IsREL], [IsSUP], and [IsUSE] for controlling retrieval, relevance "
            "grading, support assessment, and utility scoring respectively."
        ),
    },
    {
        "arxiv_id": "2310.06825",
        "title": "Mistral 7B",
        "filename": "mistral7b_paper.txt",
        "content": (
            "We introduce Mistral 7B, a 7-billion-parameter language model engineered for "
            "superior performance and efficiency. Mistral 7B outperforms the best open 13B model "
            "(Llama 2 13B) across all evaluated benchmarks, and the best released 34B model "
            "(Llama 1 34B) in reasoning, mathematics, and code generation. Our model leverages "
            "grouped-query attention (GQA) for faster inference, coupled with sliding window "
            "attention (SWA) to effectively handle sequences of arbitrary length with a reduced "
            "inference cost. We also provide a model fine-tuned to follow instructions, Mistral "
            "7B-Instruct, that surpasses Llama 2 13B-chat on both human and automated benchmarks. "
            "The sliding window attention with a window size of 4096 allows the model to process "
            "sequences up to 128K tokens during inference through a rolling buffer KV cache mechanism."
        ),
    },
    {
        "arxiv_id": "2401.00368",
        "title": "Improving Text Embeddings with Large Language Models",
        "filename": "llm_embeddings_paper.txt",
        "content": (
            "We introduce a novel and simple approach for obtaining high-quality text embeddings "
            "using only synthetic data and less than 1K training steps. Unlike prior work that "
            "trains on expensive human-labeled data or relies on complex training pipelines, "
            "our approach generates diverse synthetic data for hundreds of thousands of text "
            "embedding tasks across 93 languages. We then fine-tune an LLM on the synthetic "
            "data following standard contrastive learning with in-batch negatives. Experiments "
            "demonstrate that our method achieves strong performance on the Massive Text Embedding "
            "Benchmark (MTEB) and BEIR retrieval benchmarks. The key insight is that LLMs can "
            "generate high-quality diverse training examples by following simple prompts, "
            "eliminating the need for expensive human annotation in embedding model training."
        ),
    },
]

# ── Queries con distinto tipo de match ────────────────────────────────────────
MULTIVECTOR_QUERIES = [
    {
        "id": "MVQ1",
        "query": "¿Cómo maneja los LLMs multimodales la percepción visual?",
        "expected_source_prefix": "kosmos1",
        "match_type": "summary",
        "description": "Match por resumen (conceptual) — el texto usa vocabulario diferente",
    },
    {
        "id": "MVQ2",
        "query": "¿Qué técnica usa Llama 2 para el alineamiento con preferencias humanas?",
        "expected_source_prefix": "llama2",
        "match_type": "question",
        "description": "Match por pregunta hipotética — el paper responde exactamente esto",
    },
    {
        "id": "MVQ3",
        "query": "reflection tokens RETRIEVE IsSUP IsUSE special tokens self-reflection",
        "expected_source_prefix": "self_rag",
        "match_type": "chunk",
        "description": "Match por chunk directo — keywords técnicos exactos",
    },
    {
        "id": "MVQ4",
        "query": "¿Qué modelo de 7B supera a modelos de 13B en benchmarks de razonamiento?",
        "expected_source_prefix": "mistral",
        "match_type": "question",
        "description": "Pregunta directa sobre benchmark — match vía preguntas hipotéticas",
    },
    {
        "id": "MVQ5",
        "query": "entrenamiento de embeddings con datos sintéticos sin anotación humana",
        "expected_source_prefix": "llm_embeddings",
        "match_type": "summary",
        "description": "Concepto clave resumido — match vía resumen",
    },
]


async def setup_multivector_rag() -> MultiVectorRAGEngine:
    """Crea e inicializa el motor Multi-Vector RAG con papers de ArXiv.

    Returns:
        MultiVectorRAGEngine con los 5 papers indexados.
    """
    engine = MultiVectorRAGEngine(
        vector_store=ChromaVectorStore(collection_name="arxiv_multivector"),
        n_questions=3,  # generar 3 preguntas hipotéticas por chunk
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)

        for paper in ARXIV_PAPERS:
            paper_path = tmpdir_path / paper["filename"]
            # Incluir el título en el contenido para mejor contexto
            full_content = f"Title: {paper['title']}\n\nAbstract:\n{paper['content']}"
            paper_path.write_text(full_content, encoding="utf-8")
            await engine.index_document(paper_path)
            print(f"  ✓ Indexado: {paper['filename']} (3 representaciones)")

    return engine


def print_multivector_result(
    query_info: dict,
    result: MultiVectorResult,
) -> None:
    """Muestra los resultados Multi-Vector con qué representación hizo match."""
    expected_prefix = query_info["expected_source_prefix"]

    print(f"\n[{query_info['id']}] {query_info['query']}")
    print(f"  Tipo de match esperado: {query_info['match_type']}")
    print(f"  Descripción: {query_info['description']}")

    print("\n  Top-3 resultados:")
    for i, chunk in enumerate(result.chunks[:3], 1):
        expected = expected_prefix in chunk.source
        mark = "→" if expected else " "
        print(
            f"    {mark}[{i}] [{chunk.relevance_score:.3f}] {chunk.source}: {chunk.content[:80]}..."
        )

    # Mostrar qué representaciones hicieron match
    if result.matched_representations:
        print("\n  Representaciones que hicieron match:")
        for doc_id, representations in list(result.matched_representations.items())[:3]:
            print(f"    {doc_id}: {representations}")

    # Verificar si el documento esperado está en los resultados
    found = any(expected_prefix in c.source for c in result.chunks[:3])
    print(f"\n  Documento esperado encontrado: {'✓' if found else '✗'}")
    print("─" * 70)


async def main() -> None:
    print("=" * 70)
    print("  Multi-Vector RAG — Dataset: ArXiv Papers (ML/AI)")
    print("=" * 70)

    # Arquitectura
    print("\n[Arquitectura Multi-Vector]")
    print("  Para cada documento, se generan e indexan 3 representaciones:")
    print()
    print("  Documento")
    print("  ├── [Chunk]     Texto original fragmentado")
    print("  │                → Indexado con: embed(chunk_text)")
    print("  ├── [Summary]   Resumen LLM del chunk")
    print("  │                → Indexado con: embed(summary)")
    print("  └── [Questions] N preguntas hipotéticas que el chunk responde")
    print("                   → Indexado con: embed(question_i)")
    print()
    print("  Todos comparten el mismo doc_id → dedup en resultados")

    # Inicialización
    print("\n[Indexando papers de ArXiv]")
    print("  (Cada paper genera ~3 chunks × 3 representaciones = ~9 vectores)")
    engine = await setup_multivector_rag()
    total_vectors = len(ARXIV_PAPERS) * 3 * 3
    print(f"  ✓ ~{total_vectors} vectores indexados para {len(ARXIV_PAPERS)} papers")

    # Ejecutar queries
    print(f"\n[Ejecutando {len(MULTIVECTOR_QUERIES)} queries sobre papers ArXiv]")

    correct_count = 0
    match_type_stats: dict[str, int] = {"chunk": 0, "summary": 0, "question": 0}

    for query_info in MULTIVECTOR_QUERIES:
        result = await engine.search(query_info["query"], k=5)
        print_multivector_result(query_info, result)

        expected_prefix = query_info["expected_source_prefix"]
        if any(expected_prefix in c.source for c in result.chunks[:3]):
            correct_count += 1
            match_type_stats[query_info["match_type"]] = (
                match_type_stats.get(query_info["match_type"], 0) + 1
            )

    # Resumen
    recall = correct_count / len(MULTIVECTOR_QUERIES)
    print("\n[Resumen estadístico]")
    print(f"  Recall@3: {correct_count}/{len(MULTIVECTOR_QUERIES)} ({recall:.0%})")

    # Comparativa de representaciones
    print("\n[Distribución de tipos de match]")
    print("  En qué representaciones son más útiles:")
    for rep_type, desc in [
        ("chunk", "keywords técnicos exactos del paper"),
        ("summary", "conceptos y temas generales"),
        ("question", "preguntas directas sobre el contenido"),
    ]:
        count = match_type_stats.get(rep_type, 0)
        print(f"    {rep_type:10s}: {count} queries ← {desc}")

    # Comparativa Multi-Vector vs Chunk-only
    print("\n[Multi-Vector vs RAG estándar (solo chunks)]")
    comparison = [
        ("Multi-Vector", "Alta", "Alta", "3×"),
        ("RAG estándar", "Media", "Media", "1×"),
    ]
    print(f"  {'Método':<15} {'Recall':<8} {'Precisión':<10} {'Coste indexación':>18}")
    print("  " + "─" * 53)
    for method, recall_q, precision, cost in comparison:
        print(f"  {method:<15} {recall_q:<8} {precision:<10} {cost:>18}")

    print("\n[Cuándo usar Multi-Vector RAG]")
    print("  ✓ Documentos técnicos con vocabulario especializado")
    print("  ✓ Usuarios que preguntan con diferentes estilos/idiomas")
    print("  ✓ Corpus de papers científicos o documentación compleja")
    print("  ✗ Corpus muy grande (3× vectores = 3× coste de indexación)")
    print("  ✗ Documentos simples/cortos donde chunk único es suficiente")


if __name__ == "__main__":
    asyncio.run(main())

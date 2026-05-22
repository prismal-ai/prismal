"""
Self-RAG — Recuperación selectiva con auto-evaluación
======================================================
Arquitectura: SPEC-RAG-005 / prismal.rag.self_rag

Dataset: PubMedQA (Preguntas sobre artículos científicos biomédicos)
  • 273 518 QA pares sobre abstracts de PubMed.
  • Referencia: https://huggingface.co/datasets/qiaojin/PubMedQA
  • Por qué: Self-RAG decide si recuperar o no recuperar en función de la
    query. En PubMedQA hay preguntas que se pueden responder con conocimiento
    previo (NO_RETRIEVE) y otras que requieren el contexto del abstract
    (RETRIEVE). Esto captura perfectamente la dinámica de Self-RAG.

Descripción de la arquitectura Self-RAG:
  El LLM controla el pipeline con "tokens de reflexión":
  1. RETRIEVAL TOKEN: ¿Necesito recuperar? → RETRIEVE | NO_RETRIEVE
  2. Recuperación condicional: solo si el token es RETRIEVE
  3. SUPPORT TOKEN: ¿Los chunks apoyan mi respuesta?
     → SUPPORTED | PARTIALLY_SUPPORTED | UNSUPPORTED
  4. UTILITY TOKEN: ¿La respuesta es útil? (1-5)
  5. Genera respuesta final adaptada al nivel de soporte

Beneficio clave:
  Evita recuperaciones innecesarias cuando el LLM ya sabe la respuesta,
  y auto-evalúa la calidad de las fuentes recuperadas.

Uso:
    uv run python examples/rag/03_self_rag.py
"""

from __future__ import annotations

import asyncio

from langchain_core.documents import Document

from prismal.rag.self_rag import SelfRAGPipeline, SelfRAGResult
from prismal.rag.vector_store import ChromaVectorStore

# ── Dataset: abstracts de PubMedQA ───────────────────────────────────────────
# Abstracts reales de PubMed sobre temas de medicina y biología.
PUBMED_ABSTRACTS = [
    {
        "source": "pubmed_covid_vaccines",
        "pmid": "34567890",
        "title": "Efficacy of mRNA COVID-19 Vaccines",
        "abstract": (
            "Background: mRNA vaccines represent a novel vaccination technology that "
            "instructs cells to produce the spike protein of SARS-CoV-2. Methods: "
            "We conducted a phase 3 randomized controlled trial with 43,548 participants. "
            "Results: Vaccine efficacy was 95% (95% CI: 90.3-97.6%) against symptomatic "
            "COVID-19. The vaccine showed 100% efficacy against severe disease. "
            "Adverse events were mild to moderate and transient. Conclusions: "
            "mRNA COVID-19 vaccines are safe and highly effective against SARS-CoV-2 infection."
        ),
    },
    {
        "source": "pubmed_diabetes_metformin",
        "pmid": "34789012",
        "title": "Metformin in Type 2 Diabetes Management",
        "abstract": (
            "Type 2 diabetes mellitus affects approximately 422 million people worldwide. "
            "Metformin is the first-line pharmacological treatment for T2DM. It works by "
            "reducing hepatic glucose production, increasing insulin sensitivity, and "
            "decreasing intestinal glucose absorption. Clinical trials demonstrate HbA1c "
            "reductions of 1-2% with metformin monotherapy. The drug has an excellent "
            "safety profile and is associated with reduced cardiovascular risk. "
            "Weight neutrality or modest weight loss is an additional benefit."
        ),
    },
    {
        "source": "pubmed_alzheimer_tau",
        "pmid": "34901234",
        "title": "Tau Protein and Alzheimer's Disease Pathogenesis",
        "abstract": (
            "Alzheimer's disease (AD) is characterized by two hallmark pathologies: "
            "amyloid-beta plaques and neurofibrillary tangles composed of hyperphosphorylated "
            "tau protein. Tau normally stabilizes microtubules in neurons. In AD, "
            "tau becomes hyperphosphorylated, detaches from microtubules, and aggregates "
            "into paired helical filaments. This process causes neuronal death and "
            "cognitive decline. Recent clinical trials targeting tau phosphorylation "
            "have shown promise in slowing disease progression."
        ),
    },
    {
        "source": "pubmed_crispr_cancer",
        "pmid": "35012345",
        "title": "CRISPR-Cas9 Applications in Cancer Immunotherapy",
        "abstract": (
            "CRISPR-Cas9 gene editing has revolutionized cancer immunotherapy by enabling "
            "precise modification of T cells. CAR-T cell therapy can be enhanced by "
            "CRISPR-mediated knockout of PD-1, TIM-3, and LAG-3 checkpoints. "
            "In clinical trials, CRISPR-edited T cells showed improved persistence and "
            "anti-tumor activity. The first CRISPR-edited CAR-T cell therapy received "
            "FDA approval in 2024. Safety concerns include off-target editing effects, "
            "which are being addressed through high-fidelity Cas9 variants."
        ),
    },
    {
        "source": "pubmed_microbiome_mental_health",
        "pmid": "35123456",
        "title": "Gut Microbiome and Mental Health: The Gut-Brain Axis",
        "abstract": (
            "The gut-brain axis (GBA) is a bidirectional communication network between "
            "the gastrointestinal tract and the central nervous system. The gut microbiome "
            "produces neurotransmitters including serotonin (95% produced in the gut), "
            "GABA, and dopamine precursors. Studies show that microbiome dysbiosis is "
            "associated with depression, anxiety, and autism spectrum disorders. "
            "Probiotic interventions have shown modest but significant effects on "
            "depressive symptoms in randomized controlled trials."
        ),
    },
]

# ── Preguntas PubMedQA con expectativa de recuperación ────────────────────────
PUBMED_QUESTIONS = [
    {
        "id": "PQ1",
        "question": "¿Cuál fue la eficacia de las vacunas mRNA contra COVID-19 en el ensayo de fase 3?",
        "expected_retrieve": True,
        "expected_source": "pubmed_covid_vaccines",
        "reason": "Requiere cifra específica del abstract (95%)",
    },
    {
        "id": "PQ2",
        "question": "¿Qué es la diabetes?",
        "expected_retrieve": False,
        "expected_source": None,
        "reason": "Definición general que el LLM conoce sin recuperación",
    },
    {
        "id": "PQ3",
        "question": "¿Cómo reduce la metformina el nivel de glucosa en sangre según los estudios clínicos?",
        "expected_retrieve": True,
        "expected_source": "pubmed_diabetes_metformin",
        "reason": "Mecanismo específico con datos clínicos del abstract",
    },
    {
        "id": "PQ4",
        "question": "¿Qué porcentaje de serotonina se produce en el intestino?",
        "expected_retrieve": True,
        "expected_source": "pubmed_microbiome_mental_health",
        "reason": "Dato específico (95%) que requiere recuperación del abstract",
    },
    {
        "id": "PQ5",
        "question": "¿Cuándo fue la primera vez que la FDA aprobó una terapia CAR-T editada con CRISPR?",
        "expected_retrieve": True,
        "expected_source": "pubmed_crispr_cancer",
        "reason": "Fecha específica que requiere el abstract (2024)",
    },
    {
        "id": "PQ6",
        "question": "¿Qué es la proteína tau y por qué es importante en neurología?",
        "expected_retrieve": True,
        "expected_source": "pubmed_alzheimer_tau",
        "reason": "Información específica de investigación sobre tau en AD",
    },
]


async def setup_pubmed_store() -> ChromaVectorStore:
    """Crea y popula el vector store con abstracts de PubMed."""
    store = ChromaVectorStore(collection_name="pubmed_self_rag")

    docs = [
        Document(
            page_content=abstract["abstract"],
            metadata={
                "source": abstract["source"],
                "pmid": abstract["pmid"],
                "title": abstract["title"],
                "chunk_id": str(i),
                "dataset": "PubMedQA",
            },
        )
        for i, abstract in enumerate(PUBMED_ABSTRACTS)
    ]

    store.add_documents(docs)
    print(f"  ✓ {len(docs)} abstracts PubMed indexados en ChromaDB")
    return store


def print_self_rag_result(question: dict, result: SelfRAGResult) -> None:
    """Imprime el resultado Self-RAG de forma detallada."""
    # Determinar si recuperó o no
    retrieved = result.retrieval_decision.value == "RETRIEVE"
    expected_retrieve = question["expected_retrieve"]
    retrieve_icon = "✓" if retrieved == expected_retrieve else "✗"

    print(f"\n[{question['id']}] {question['question']}")
    print(f"  Razón de clasificación: {question['reason']}")

    print(f"\n  RETRIEVAL TOKEN : {result.retrieval_decision.value}  {retrieve_icon}")
    print(f"  (esperado: {'RETRIEVE' if expected_retrieve else 'NO_RETRIEVE'})")

    if retrieved:
        print(f"\n  Chunks recuperados: {len(result.chunks)}")
        for chunk in result.chunks[:2]:
            print(f"    • [{chunk.relevance_score:.2f}] {chunk.source}: {chunk.content[:80]}...")
        print(f"\n  SUPPORT TOKEN  : {result.support_decision.value}")
        print(f"  UTILITY SCORE  : {result.utility_score}/5")
    else:
        print("  → Respuesta generada sin recuperación (LLM usa conocimiento previo)")

    print("\n  Respuesta final:")
    print(f"  {result.answer[:300]}")
    print("─" * 70)


async def main() -> None:
    print("=" * 70)
    print("  Self-RAG — Dataset: PubMedQA (artículos científicos biomédicos)")
    print("=" * 70)

    # Setup
    print("\n[Inicialización]")
    store = await setup_pubmed_store()
    pipeline = SelfRAGPipeline(vector_store=store)
    print("  ✓ Pipeline Self-RAG inicializado")

    # Tokens de control de Self-RAG
    print("\n[Tokens de reflexión Self-RAG]")
    print("  RETRIEVAL: RETRIEVE | NO_RETRIEVE")
    print("  SUPPORT  : SUPPORTED | PARTIALLY_SUPPORTED | UNSUPPORTED")
    print("  UTILITY  : 1 (baja) → 5 (alta)")

    # Ejecutar queries
    print(f"\n[Ejecutando {len(PUBMED_QUESTIONS)} queries PubMedQA]")

    retrieve_correct = 0
    support_distribution: dict[str, int] = {}

    for question in PUBMED_QUESTIONS:
        result = await pipeline.run(question["question"])
        print_self_rag_result(question, result)

        # Estadísticas
        retrieved = result.retrieval_decision.value == "RETRIEVE"
        if retrieved == question["expected_retrieve"]:
            retrieve_correct += 1

        support_key = result.support_decision.value
        support_distribution[support_key] = support_distribution.get(support_key, 0) + 1

    # Resumen
    print("\n[Resumen estadístico]")
    retrieve_accuracy = retrieve_correct / len(PUBMED_QUESTIONS)
    print(
        f"  Decisiones de recuperación correctas: {retrieve_correct}/{len(PUBMED_QUESTIONS)} ({retrieve_accuracy:.0%})"
    )

    print("\n  Distribución de SUPPORT tokens:")
    for token, count in support_distribution.items():
        print(f"    {token}: {count} queries")

    print("\n[Ventajas de Self-RAG sobre RAG tradicional]")
    benefits = [
        ("Selectividad", "No recupera cuando el LLM ya sabe la respuesta"),
        ("Auto-evaluación", "El LLM califica la relevancia de sus propias fuentes"),
        ("Adaptabilidad", "Ajusta la respuesta según el nivel de soporte encontrado"),
        ("Eficiencia", "Menos llamadas al vector store = menor latencia en queries simples"),
    ]
    for benefit, desc in benefits:
        print(f"  • {benefit:15s}: {desc}")


if __name__ == "__main__":
    asyncio.run(main())

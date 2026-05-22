"""
Mixture of Agents (MoA) — QA Médica con múltiples modelos
==========================================================
Patrón: SPEC-PAT-006 / prismal.agents.patterns.mixture_of_agents

Dataset: MedQA (USMLE Medical Board Questions)
  • 12 723 preguntas del examen USMLE estilo opción múltiple.
  • Referencia: https://huggingface.co/datasets/GBaker/MedQA-USMLE-4-options
  • Por qué: MoA brilla cuando distintos modelos tienen "puntos ciegos"
    diferentes. En medicina, la diversidad de perspectivas reduce errores
    clínicos. El agregador sintetiza lo mejor de cada propositor.

Descripción del patrón:
  - Propositores: N modelos responden la pregunta de forma independiente
    y en paralelo (diferentes proveedores/modelos aportan perspectivas distintas).
  - Agregador: Un modelo de mayor capacidad sintetiza las respuestas en
    una única respuesta cohesiva y mejora la precisión colectiva.
  - Tolerancia a fallos: si algunos propositores fallan, el resto
    continúa; solo falla si TODOS los propositores fallan.
  - Multi-capa: n_aggregator_layers > 1 permite refinamientos iterativos.

Uso:
    uv run python examples/patterns/05_mixture_of_agents.py
"""

from __future__ import annotations

import asyncio

from prismal.agents.patterns.mixture_of_agents import MixtureOfAgents, MoAResult

# ── Dataset: preguntas MedQA USMLE ───────────────────────────────────────────
# Muestra representativa de preguntas de nivel USMLE Step 1/2.
MEDQA_SAMPLES = [
    {
        "id": "MQ1",
        "category": "Farmacología",
        "question": (
            "Un paciente de 65 años con fibrilación auricular paroxística está "
            "tomando warfarina. Se le prescribe amoxicilina por una infección "
            "del tracto urinario. ¿Cuál es el efecto más probable sobre el INR?"
        ),
        "options": {
            "A": "INR disminuirá significativamente",
            "B": "INR aumentará por reducción de la flora intestinal productora de vitamina K",
            "C": "INR no cambiará",
            "D": "INR disminuirá por inducción enzimática",
        },
        "correct": "B",
    },
    {
        "id": "MQ2",
        "category": "Fisiopatología",
        "question": (
            "Una mujer de 45 años presenta fatiga, aumento de peso, intolerancia "
            "al frío y estreñimiento. El análisis muestra TSH elevada y T4 libre "
            "baja. ¿Cuál es el diagnóstico más probable?"
        ),
        "options": {
            "A": "Hipertiroidismo primario",
            "B": "Hipotiroidismo primario",
            "C": "Hipotiroidismo secundario",
            "D": "Síndrome de Cushing",
        },
        "correct": "B",
    },
    {
        "id": "MQ3",
        "category": "Microbiología",
        "question": (
            "Un niño de 5 años presenta fiebre, exantema vesicular pruriginoso "
            "que comienza en el tronco y se extiende a la periferia, y el signo "
            "de Koebner positivo. ¿Cuál es el agente etiológico más probable?"
        ),
        "options": {
            "A": "Virus del sarampión (Morbillivirus)",
            "B": "Virus varicela-zóster (VZV)",
            "C": "Parvovirus B19",
            "D": "Streptococcus pyogenes",
        },
        "correct": "B",
    },
]


def format_question(sample: dict) -> str:
    """Formatea una pregunta MedQA para el LLM."""
    options_text = "\n".join(f"  {k}) {v}" for k, v in sample["options"].items())
    return (
        f"Pregunta de medicina ({sample['category']}):\n\n"
        f"{sample['question']}\n\n"
        f"Opciones:\n{options_text}\n\n"
        "Por favor, razona tu respuesta paso a paso y elige la opción correcta."
    )


async def run_moa(sample: dict, models: list[str], aggregator: str) -> MoAResult:
    """Ejecuta Mixture of Agents en una pregunta MedQA.

    Args:
        sample: Pregunta con opciones y respuesta correcta.
        models: Lista de modelos propositores.
        aggregator: Modelo agregador.

    Returns:
        MoAResult con la respuesta final sintetizada.
    """
    moa = MixtureOfAgents(
        proposer_models=models,
        aggregator_model=aggregator,
        n_aggregator_layers=1,  # una capa de agregación
    )

    return await moa.generate(
        query=format_question(sample),
        state={"messages": [], "metadata": {"category": sample["category"]}},
    )


async def main() -> None:
    print("=" * 70)
    print("  Mixture of Agents — Dataset: MedQA USMLE (QA Médica)")
    print("=" * 70)

    # Configuración de modelos (ajustar según proveedores disponibles)
    # MoA es más potente con modelos DIFERENTES (distintos sesgos)
    proposer_models = [
        "claude-sonnet-4-6",  # Anthropic
        "claude-sonnet-4-6",  # En producción: usar gpt-4o, gemini-1.5-pro
        "claude-sonnet-4-6",  # En producción: usar modelos médicos especializados
    ]
    aggregator_model = "claude-sonnet-4-6"  # En producción: claude-opus-4-6

    print(f"\n  Propositores: {len(proposer_models)} modelos paralelos")
    print(f"  Agregador   : {aggregator_model}")
    print("  Capas MoA   : 1")
    print()

    correct_count = 0

    for sample in MEDQA_SAMPLES:
        print(f"[{sample['id']}] Categoría: {sample['category']}")
        print(f"  Pregunta: {sample['question'][:80]}...")
        print(f"  Respuesta correcta: {sample['correct']}) {sample['options'][sample['correct']]}")

        result = await run_moa(sample, proposer_models, aggregator_model)

        print("\n  Respuesta MoA:")
        print(f"  {result.final_answer[:300]}")

        print("\n  Métricas MoA:")
        print(f"    Propositores exitosos : {len(result.layer_outputs[0])}/{len(proposer_models)}")
        print(f"    Capas completadas     : {len(result.layer_outputs)}")
        print(f"    Proveedores usados    : {result.providers_used}")

        # Verificar si la respuesta correcta aparece en la respuesta final
        correct_letter = sample["correct"]
        if correct_letter in result.final_answer:
            print(f"    ✓ La opción correcta ({correct_letter}) está en la respuesta")
            correct_count += 1
        else:
            print(f"    ✗ La opción correcta ({correct_letter}) no se detectó claramente")

        print()
        print("-" * 70)

    print(f"\n  Precisión MoA: {correct_count}/{len(MEDQA_SAMPLES)} preguntas")
    print(
        "\n  [Nota] En producción, usar modelos diferentes como propositores "
        "maximiza la diversidad y la precisión colectiva (Wang et al. 2024)."
    )

    # Demostrar tolerancia a fallos
    print("\n[Tolerancia a fallos de propositores]")
    print("  Si 1 de 3 propositores falla → MoA continúa con los 2 restantes")
    print("  Si 2 de 3 propositores fallan → MoA continúa con el 1 restante")
    print("  Si 3 de 3 propositores fallan → MoAError (fallo total)")

    # Demostrar MoA multi-capa
    print("\n[MoA Multi-capa (n_aggregator_layers=2)]")
    moa_multilayer = MixtureOfAgents(
        proposer_models=proposer_models[:2],
        aggregator_model=aggregator_model,
        n_aggregator_layers=2,
    )
    sample = MEDQA_SAMPLES[0]
    result_ml = await moa_multilayer.generate(
        query=format_question(sample),
        state={},
    )
    print(f"  Capa 1 propositores : {len(result_ml.layer_outputs[0])} respuestas")
    if len(result_ml.layer_outputs) > 1:
        print(f"  Capa 2 refinamiento : {len(result_ml.layer_outputs[1])} respuestas")
    print(f"  Respuesta final     : {result_ml.final_answer[:150]}...")


if __name__ == "__main__":
    asyncio.run(main())

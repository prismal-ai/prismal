"""
Reflection Loop — Mejora iterativa de artículos técnicos
=========================================================
Patrón: SPEC-035 / lightagent.agents.patterns.reflection

Dataset: Writing Prompts + prompts de documentación técnica
  • FairytaleQA / Writing Prompts de Reddit (r/WritingPrompts).
  • Referencia: https://huggingface.co/datasets/euclaise/writingprompts
  • Por qué: El patrón Reflection requiere un dominio donde la calidad
    es gradualmente mejorable: la escritura técnica y los artículos
    son ideales porque hay criterios objetivos (claridad, precisión,
    completitud) que guían las iteraciones de refinamiento.

Descripción del patrón:
  1. generate_fn produce un borrador.
  2. critique_fn evalúa el borrador y devuelve (feedback, score ∈ [0,1]).
  3. Si score >= threshold → retorna el borrador.
  4. Si no → generate_fn recibe el borrador anterior y la crítica para refinar.
  5. Máximo max_iterations iteraciones; retorna el mejor borrador observado.

También demuestra el decorador @with_reflection para nodos de LangGraph.

Uso:
    uv run python examples/patterns/06_reflection_loop.py
"""

from __future__ import annotations

import asyncio

from langchain_core.messages import HumanMessage, SystemMessage

from lightagent.agents.patterns.reflection import reflection_loop
from lightagent.agents.state import AgentState, initial_state
from lightagent.core.config import get_settings
from lightagent.providers.registry import ProviderRegistry

# ── Dataset: prompts de escritura técnica ────────────────────────────────────
WRITING_PROMPTS = [
    {
        "id": "WP1",
        "topic": "Explicación de Transformers para desarrolladores",
        "instruction": (
            "Escribe una explicación de 3-4 párrafos de la arquitectura Transformer "
            "para un desarrollador con experiencia en Python pero sin experiencia "
            "en deep learning. Usa analogías concretas."
        ),
        "evaluation_criteria": [
            "analogías concretas y comprensibles",
            "sin jerga matemática sin explicar",
            "mención de atención y codificador/decodificador",
            "ejemplos de aplicación práctica",
        ],
    },
    {
        "id": "WP2",
        "topic": "Guía de async/await en Python",
        "instruction": (
            "Escribe una guía concisa de async/await en Python para un programador "
            "que viene de JavaScript. Explica las diferencias clave y los errores "
            "más comunes. Incluye al menos un ejemplo de código."
        ),
        "evaluation_criteria": [
            "comparación explícita con JavaScript",
            "ejemplo de código funcional",
            "errores comunes mencionados",
            "asyncio.run() o loop.run_until_complete() incluido",
        ],
    },
    {
        "id": "WP3",
        "topic": "Introducción a RAG para product managers",
        "instruction": (
            "Escribe una explicación de Retrieval-Augmented Generation (RAG) "
            "para un product manager sin conocimiento técnico. Explica qué es, "
            "para qué sirve, y cuándo usarlo vs fine-tuning. Máximo 300 palabras."
        ),
        "evaluation_criteria": [
            "sin jerga técnica excesiva",
            "comparación RAG vs fine-tuning",
            "casos de uso concretos",
            "longitud <= 300 palabras",
        ],
    },
]


# ── Callables del loop de reflexión ──────────────────────────────────────────


async def generate_article(
    state: AgentState,
    previous_draft: str | None = None,
    critique: str | None = None,
) -> str:
    """Genera o mejora un artículo técnico.

    Si se proporcionan previous_draft y critique, el LLM refina el borrador.
    De lo contrario, genera desde cero.

    Args:
        state: AgentState con 'instruction' en metadata.
        previous_draft: Borrador anterior (si hay iteración previa).
        critique: Feedback de la evaluación anterior.

    Returns:
        Nuevo borrador como string.
    """
    settings = get_settings()
    llm = ProviderRegistry(settings=settings).get_llm()

    instruction = state.get("metadata", {}).get("instruction", "Escribe sobre IA")

    if previous_draft and critique:
        system = (
            "Eres un escritor técnico experto. Tu borrador anterior fue evaluado "
            "y recibió el siguiente feedback. Reescribe el artículo incorporando "
            "las mejoras sugeridas. Mantén lo que ya funciona bien."
        )
        user = (
            f"Instrucción original: {instruction}\n\n"
            f"Tu borrador anterior:\n{previous_draft}\n\n"
            f"Feedback recibido:\n{critique}\n\n"
            "Por favor, reescribe el artículo mejorando los puntos señalados."
        )
    else:
        system = (
            "Eres un escritor técnico experto en tecnología y programación. "
            "Escribe artículos claros, concisos y útiles para el público objetivo."
        )
        user = f"Instrucción: {instruction}"

    response = await llm.ainvoke(
        [SystemMessage(content=system), HumanMessage(content=user)]
    )
    return str(response.content).strip()


async def critique_article(
    draft: str,
    state: AgentState,
) -> tuple[str, float]:
    """Evalúa la calidad de un borrador técnico.

    Comprueba los criterios definidos en state["metadata"]["criteria"].

    Args:
        draft: Texto del borrador a evaluar.
        state: AgentState con 'criteria' en metadata.

    Returns:
        Tupla (feedback: str, score: float in [0,1]).
    """
    settings = get_settings()
    llm = ProviderRegistry(settings=settings).get_llm()

    criteria = state.get("metadata", {}).get("criteria", [])
    criteria_text = "\n".join(f"  - {c}" for c in criteria)

    system = (
        "Eres un editor técnico experto. Evalúa el texto según los criterios "
        "dados. Proporciona:\n"
        "1. Una puntuación del 0.0 al 1.0 (en la PRIMERA línea, solo el número).\n"
        "2. Feedback específico y accionable sobre qué mejorar.\n"
        "Sé estricto: una puntuación de 0.9+ solo si el texto es excelente."
    )
    user = (
        f"Criterios de evaluación:\n{criteria_text}\n\n"
        f"Texto a evaluar:\n{draft}\n\n"
        "Formato de respuesta:\n"
        "[score entre 0.0 y 1.0]\n"
        "[feedback detallado]"
    )

    response = await llm.ainvoke(
        [SystemMessage(content=system), HumanMessage(content=user)]
    )

    raw = str(response.content).strip()
    lines = raw.split("\n", 1)

    # Parsear puntuación
    try:
        score = float(lines[0].strip())
        score = max(0.0, min(1.0, score))
    except ValueError:
        # Fallback: buscar el primer número flotante
        import re
        numbers = re.findall(r"\b0\.\d+\b|\b1\.0\b", raw)
        score = float(numbers[0]) if numbers else 0.5

    feedback = lines[1].strip() if len(lines) > 1 else raw

    return feedback, score


# ── Función principal ─────────────────────────────────────────────────────────


async def run_reflection(prompt: dict, threshold: float = 0.85) -> None:
    """Ejecuta el loop de reflexión para un prompt de escritura.

    Args:
        prompt: Diccionario con topic, instruction y evaluation_criteria.
        threshold: Puntuación mínima para aceptar el borrador.
    """
    print(f"\n[{prompt['id']}] Tema: {prompt['topic']}")
    print(f"  Umbral de calidad: {threshold}")
    print(f"  Criterios: {len(prompt['evaluation_criteria'])} criterios")

    # Preparar el estado
    state: AgentState = initial_state()
    state["metadata"]["instruction"] = prompt["instruction"]
    state["metadata"]["criteria"] = prompt["evaluation_criteria"]
    state["metadata"]["topic"] = prompt["topic"]

    # Ejecutar el loop de reflexión
    best_draft, final_score = await reflection_loop(
        generate_fn=generate_article,
        critique_fn=critique_article,
        state=state,
        threshold=threshold,
        max_iterations=3,
    )

    print(f"\n  Score final: {final_score:.3f}")
    print(f"  Borrador final ({len(best_draft)} chars):")
    print(f"  {'─' * 60}")
    # Mostrar primeras 400 caracteres del borrador
    preview = best_draft[:400]
    for line in preview.split("\n"):
        print(f"  {line}")
    if len(best_draft) > 400:
        print(f"  ... [{len(best_draft) - 400} chars más]")


async def main() -> None:
    print("=" * 70)
    print("  Reflection Loop — Dataset: Writing Prompts (escritura técnica)")
    print("=" * 70)

    # Ejecutar con distintos umbrales para mostrar el efecto
    configs = [
        (WRITING_PROMPTS[0], 0.80),  # Umbral moderado → 1-2 iteraciones
        (WRITING_PROMPTS[1], 0.88),  # Umbral exigente → 2-3 iteraciones
        (WRITING_PROMPTS[2], 0.75),  # Umbral más permisivo → 1 iteración
    ]

    for prompt, threshold in configs:
        await run_reflection(prompt, threshold=threshold)
        print("─" * 70)

    # Demostración del decorador @with_reflection
    print("\n[Decorador @with_reflection para nodos LangGraph]")
    print("  Uso:")
    print("    from lightagent.agents.patterns.reflection import with_reflection")
    print()
    print("    @with_reflection(threshold=0.85, max_iterations=2, critique_fn=my_critic)")
    print("    async def writer_node(state: AgentState) -> str:")
    print("        # Tu lógica de generación aquí")
    print("        return await llm.generate(state)")
    print()
    print("  El decorador:")
    print("    1. Envuelve la función del nodo con reflection_loop")
    print("    2. Almacena reflection_score en state['metadata'][node_name]")
    print("    3. Es compatible con cualquier nodo de subgraph de LangGraph")

    print("\n[Resumen del patrón Reflection Loop]")
    print("  Iteración 1: borrador inicial                 → score bajo")
    print("  Iteración 2: refinado con feedback específico → score medio")
    print("  Iteración 3: refinado nuevamente              → score >= threshold")
    print("  Retorna: el borrador con mayor score observado")


if __name__ == "__main__":
    asyncio.run(main())

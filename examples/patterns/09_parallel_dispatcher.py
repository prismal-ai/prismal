"""
Parallel Dispatcher (Map-Reduce) — Investigación paralela de temas
===================================================================
Patrón: SPEC / lightagent.agents.patterns.parallel

Dataset: FEVER (Fact Extraction and VERification)
  • 185 445 afirmaciones sobre Wikipedia para verificar.
  • Referencia: https://huggingface.co/datasets/fever/fever
  • Por qué: El dispatcher paralelo es perfecto para verificar múltiples
    afirmaciones independientes en paralelo (fan-out), luego agregar
    todos los resultados (fan-in). FEVER tiene exactamente afirmaciones
    independientes que se pueden distribuir entre workers.

Descripción del patrón:
  make_parallel_dispatcher crea una función compatible con LangGraph:
  1. Fan-out: envía cada tarea a worker_node vía LangGraph Send()
  2. Fan-in: todos los workers terminan y sus resultados se agregan
  3. Controlado por settings.parallel_max_workers (cap de seguridad)
  4. Desactivable globalmente con settings.parallel_enabled = False

También demuestra asyncio.gather directo para paralelismo fuera de LangGraph.

Uso:
    uv run python examples/patterns/09_parallel_dispatcher.py
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from lightagent.agents.patterns.parallel import make_parallel_dispatcher
from lightagent.core.config import get_settings
from lightagent.providers.registry import ProviderRegistry

# ── Dataset: afirmaciones FEVER para verificación ─────────────────────────────
# Muestra de afirmaciones FEVER (SUPPORTS / REFUTES / NOT ENOUGH INFO)
FEVER_CLAIMS = [
    {
        "id": "FC001",
        "claim": "Python fue creado por Guido van Rossum y lanzado por primera vez en 1991.",
        "expected_label": "SUPPORTS",
        "topic": "python_history",
    },
    {
        "id": "FC002",
        "claim": "La Torre Eiffel está ubicada en Berlín, Alemania.",
        "expected_label": "REFUTES",
        "topic": "geography",
    },
    {
        "id": "FC003",
        "claim": "El modelo GPT-4 fue desarrollado por Google DeepMind.",
        "expected_label": "REFUTES",
        "topic": "ai_models",
    },
    {
        "id": "FC004",
        "claim": "LangChain es un framework de código abierto para construir aplicaciones con LLMs.",
        "expected_label": "SUPPORTS",
        "topic": "ai_frameworks",
    },
    {
        "id": "FC005",
        "claim": "El lenguaje Rust fue diseñado con énfasis en rendimiento y seguridad de memoria.",
        "expected_label": "SUPPORTS",
        "topic": "programming_languages",
    },
    {
        "id": "FC006",
        "claim": "ChromaDB es una base de datos relacional diseñada para transacciones bancarias.",
        "expected_label": "REFUTES",
        "topic": "vector_databases",
    },
]

# ── Worker: verificación de una sola afirmación ───────────────────────────────


async def verify_single_claim(claim_task: dict[str, Any]) -> dict[str, Any]:
    """Verifica una sola afirmación FEVER con el LLM.

    Este es el worker que se ejecuta en paralelo para cada tarea.

    Args:
        claim_task: Diccionario con 'claim', 'id' y metadatos.

    Returns:
        Resultado de verificación con label y justificación.
    """
    settings = get_settings()
    llm = ProviderRegistry(settings=settings).get_llm()

    claim = claim_task["claim"]

    system_prompt = (
        "Eres un verificador de hechos. Analiza la afirmación proporcionada y "
        "clasifícala como:\n"
        "- SUPPORTS: la afirmación es verdadera según tu conocimiento\n"
        "- REFUTES: la afirmación es falsa según tu conocimiento\n"
        "- NOT_ENOUGH_INFO: no tienes información suficiente\n\n"
        "Responde en el formato:\n"
        "LABEL: [SUPPORTS|REFUTES|NOT_ENOUGH_INFO]\n"
        "JUSTIFICACIÓN: [breve explicación]"
    )

    response = await llm.ainvoke(
        [
            SystemMessage(content=system_prompt),
            HumanMessage(content=f"Afirmación: {claim}"),
        ]
    )

    raw = str(response.content).strip()
    lines = raw.split("\n")

    # Parsear respuesta
    label = "NOT_ENOUGH_INFO"
    justification = raw

    for line in lines:
        if line.startswith("LABEL:"):
            label_part = line.replace("LABEL:", "").strip()
            if "SUPPORTS" in label_part:
                label = "SUPPORTS"
            elif "REFUTES" in label_part:
                label = "REFUTES"
            else:
                label = "NOT_ENOUGH_INFO"
        elif line.startswith("JUSTIFICACIÓN:"):
            justification = line.replace("JUSTIFICACIÓN:", "").strip()

    return {
        "claim_id": claim_task["id"],
        "claim": claim,
        "predicted_label": label,
        "expected_label": claim_task["expected_label"],
        "justification": justification,
        "correct": label == claim_task["expected_label"],
    }


# ── Demostración de paralelismo con asyncio.gather ────────────────────────────


async def run_parallel_verification(
    claims: list[dict],
    max_workers: int = 6,
) -> list[dict[str, Any]]:
    """Ejecuta verificación en paralelo usando asyncio.gather.

    Demuestra el patrón fan-out/fan-in sin necesidad de LangGraph.
    En un grafo LangGraph completo, se usaría make_parallel_dispatcher.

    Args:
        claims: Lista de afirmaciones a verificar.
        max_workers: Máximo de workers concurrentes.

    Returns:
        Lista de resultados de verificación.
    """
    # Limitar concurrencia con semáforo
    semaphore = asyncio.Semaphore(max_workers)

    async def bounded_verify(claim: dict) -> dict[str, Any]:
        async with semaphore:
            return await verify_single_claim(claim)

    # Fan-out: todas las tareas en paralelo (limitado por semáforo)
    results = await asyncio.gather(
        *[bounded_verify(claim) for claim in claims],
        return_exceptions=True,
    )

    # Fan-in: filtrar errores y retornar resultados exitosos
    valid_results = []
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            valid_results.append(
                {
                    "claim_id": claims[i]["id"],
                    "error": str(result),
                    "correct": False,
                }
            )
        else:
            valid_results.append(result)

    return valid_results


# ── Demostración de make_parallel_dispatcher ──────────────────────────────────


def demo_langgraph_dispatcher() -> None:
    """Muestra cómo usar make_parallel_dispatcher en un grafo LangGraph."""
    print("\n[make_parallel_dispatcher — Uso en LangGraph]")

    # En un grafo LangGraph real, el dispatcher se crearía así:
    dispatcher = make_parallel_dispatcher(
        tasks_field="research_tasks",  # campo del estado con las tareas
        worker_node="claim_verifier",  # nodo worker del grafo
        max_workers=6,  # cap de workers concurrentes
        on_empty="__end__",  # routing cuando no hay tareas
        task_key="_task",  # key para inyectar la tarea al worker
    )

    print("  Dispatcher creado con configuración:")
    print("    tasks_field : 'research_tasks'")
    print("    worker_node : 'claim_verifier'")
    print("    max_workers : 6")
    print()

    # Simular estado del grafo con tareas pendientes
    mock_state = {
        "research_tasks": [
            {"id": "T1", "query": "What is Python?"},
            {"id": "T2", "query": "What is LangChain?"},
            {"id": "T3", "query": "What is ChromaDB?"},
        ],
        "messages": [],
        "metadata": {},
    }

    # El dispatcher retorna una lista de Send() para LangGraph
    dispatch_result = dispatcher(mock_state)

    print(f"  Estado con {len(mock_state['research_tasks'])} tareas:")
    if isinstance(dispatch_result, list):
        print(f"  → {len(dispatch_result)} Send() emitidos (ejecución paralela)")
        for send in dispatch_result[:3]:
            print(f"    • Send('{send.node}', tarea={send.arg.get('_task', {}).get('id', '?')})")
    else:
        print(f"  → Routing a: {dispatch_result}")

    # Estado vacío → routing a on_empty
    empty_state = {**mock_state, "research_tasks": []}
    empty_result = dispatcher(empty_state)
    print(f"\n  Estado sin tareas → routing a: {empty_result!r} (on_empty)")


# ── Función principal ─────────────────────────────────────────────────────────


async def main() -> None:
    print("=" * 70)
    print("  Parallel Dispatcher — Dataset: FEVER (Verificación de hechos)")
    print("=" * 70)

    # Mostrar configuración
    settings = get_settings()
    max_w = getattr(settings, "parallel_max_workers", 10)
    parallel_enabled = getattr(settings, "parallel_enabled", True)
    print(f"\n  parallel_enabled    : {parallel_enabled}")
    print(f"  parallel_max_workers: {max_w}")
    print(f"  Afirmaciones a verificar: {len(FEVER_CLAIMS)}")

    # ── Benchmark: secuencial vs paralelo ─────────────────────────────────
    print("\n[Benchmark: Secuencial vs Paralelo]")

    # Paralelo
    print(f"\n  Ejecutando {len(FEVER_CLAIMS)} verificaciones en paralelo...")
    t0 = time.perf_counter()
    results_parallel = await run_parallel_verification(FEVER_CLAIMS, max_workers=6)
    t_parallel = time.perf_counter() - t0

    # Resultados
    print("\n  Resultados de verificación:")
    print(f"  {'ID':<8} {'Label esperado':<20} {'Predicción':<20} {'OK?':>4}")
    print("  " + "─" * 55)
    for r in results_parallel:
        if "error" in r:
            print(f"  {r['claim_id']:<8} {'ERROR':<20} {str(r.get('error', ''))[:18]:<20} {'✗':>4}")
        else:
            ok = "✓" if r["correct"] else "✗"
            print(
                f"  {r['claim_id']:<8} {r['expected_label']:<20} {r['predicted_label']:<20} {ok:>4}"
            )

    # Métricas
    correct = sum(1 for r in results_parallel if r.get("correct", False))
    accuracy = correct / len(results_parallel) if results_parallel else 0
    errors = sum(1 for r in results_parallel if "error" in r)

    print(f"\n  Tiempo paralelo  : {t_parallel:.2f}s")
    print(f"  Afirmaciones     : {len(results_parallel)}")
    print(f"  Correctas        : {correct}/{len(results_parallel)} ({accuracy:.1%})")
    if errors:
        print(f"  Errores          : {errors} (tolerados por asyncio.gather)")

    # Speedup teórico
    assumed_seq_time = t_parallel * min(6, len(FEVER_CLAIMS))
    speedup = assumed_seq_time / t_parallel if t_parallel > 0 else 0
    print(f"  Speedup estimado : ~{speedup:.1f}x respecto a secuencial")

    # ── Demostración de make_parallel_dispatcher ──────────────────────────
    demo_langgraph_dispatcher()

    # ── Justificaciones de muestra ────────────────────────────────────────
    print("\n[Justificaciones de muestra]")
    for r in results_parallel[:3]:
        if "justification" in r:
            print(f"  {r['claim_id']}: {r['justification'][:100]}...")


if __name__ == "__main__":
    asyncio.run(main())

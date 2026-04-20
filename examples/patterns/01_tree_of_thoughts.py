"""
Tree of Thoughts (ToT) — Resolución de problemas matemáticos GSM8K
====================================================================
Patrón: SPEC-PAT-001 / lightagent.agents.patterns.tree_of_thoughts

Dataset: GSM8K (Grade School Math 8K)
  • 8 500 problemas de matemáticas de nivel primaria/secundaria con solución paso a paso.
  • Referencia: https://huggingface.co/datasets/openai/gsm8k
  • Por qué: ToT brilla en razonamiento multi-paso donde hay que explorar y podar
    ramas de solución; GSM8K exige exactamente eso.

Descripción del patrón:
  ToT construye un árbol de pensamientos (pasos de solución). En cada nodo,
  generate_fn propone N candidatos; evaluate_fn los puntúa. La estrategia de
  búsqueda (beam / BFS / DFS) decide qué ramas conservar. Se detiene en cuanto
  cualquier pensamiento alcanza el umbral de calidad o se agota la profundidad.

Uso:
    uv run python examples/patterns/01_tree_of_thoughts.py
"""

from __future__ import annotations

import asyncio
import os
import re

from langchain_core.messages import HumanMessage, SystemMessage

from lightagent.agents.patterns.tree_of_thoughts import ToTResult, tree_of_thoughts
from lightagent.core.config import get_settings
from lightagent.providers.registry import ProviderRegistry

# ── Dataset: subconjunto fijo de GSM8K (sin dependencia de red) ───────────────
# Muestra representativa extraída de openai/gsm8k (train split).
GSM8K_SAMPLES = [
    {
        "question": (
            "Janet's ducks lay 16 eggs per day. She eats three for breakfast every "
            "morning and bakes muffins for her friends every day with four. She sells "
            "the remainder at the farmers' market daily for $2 per fresh duck egg. "
            "How much in dollars does she make every day at the farmers' market?"
        ),
        "answer": "18",
    },
    {
        "question": (
            "A robe takes 2 bolts of blue fiber and half that much white fiber. "
            "How many bolts in total does it take?"
        ),
        "answer": "3",
    },
    {
        "question": (
            "Josh decides to try flipping a house. He buys a house for $80,000 and "
            "then puts in $50,000 in repairs. This increased the value of the house "
            "by 150%. How much profit did he make?"
        ),
        "answer": "70000",
    },
]


# ── Callables requeridos por tree_of_thoughts ─────────────────────────────────


async def generate_solution_steps(
    problem: str,
    state: dict,
    path_so_far: list,
) -> list[str]:
    """Genera N pasos candidatos de solución para el problema matemático.

    Args:
        problem: Enunciado del problema (o paso parcial acumulado).
        state: Estado opaco (contiene el enunciado original en state["question"]).
        path_so_far: Pensamientos anteriores en el camino actual.

    Returns:
        Lista de textos con distintos enfoques de solución.
    """
    settings = get_settings()
    llm = ProviderRegistry(settings=settings).get_llm()

    context = ""
    if path_so_far and len(path_so_far) > 1:
        # Incluir el camino previo como contexto de refinamiento
        prev = "\n".join(f"  Paso {i}: {t.content[:200]}" for i, t in enumerate(path_so_far[1:], 1))
        context = f"\n\nPasos previos explorados:\n{prev}"

    system_prompt = (
        "Eres un experto en matemáticas. Dado un problema, propón 3 enfoques "
        "diferentes y concisos para resolverlo paso a paso. "
        "Devuelve exactamente 3 enfoques separados por '|||'. "
        "Cada enfoque debe ser autocontenido e incluir el resultado numérico final."
    )
    user_prompt = f"Problema: {state.get('question', problem)}{context}"

    response = await llm.ainvoke(
        [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt),
        ]
    )

    raw = str(response.content).strip()
    # Separar los 3 candidatos por el delimitador
    candidates = [c.strip() for c in raw.split("|||") if c.strip()]
    # Garantizar al menos 1 candidato
    if not candidates:
        candidates = [raw]
    return candidates[:3]  # máximo 3


async def evaluate_solution(thought_text: str, state: dict) -> float:
    """Puntúa un pensamiento de solución en [0, 1].

    Criterios:
    - Contiene un número final claramente identificable → +0.4
    - Ese número coincide con la respuesta esperada → +0.5
    - La solución tiene razonamiento explícito (>50 chars) → +0.1

    Args:
        thought_text: Texto del pensamiento a evaluar.
        state: Contiene 'answer' con la solución esperada.

    Returns:
        Puntuación en [0.0, 1.0].
    """
    score = 0.0
    expected = state.get("answer", "").strip()

    # Extraer números del texto
    numbers = re.findall(r"\b\d+(?:[.,]\d+)?\b", thought_text)
    if numbers:
        score += 0.4
        # Comprobar si la respuesta esperada está entre los números encontrados
        normalized_expected = expected.replace(",", "").replace(".", "")
        for n in numbers:
            normalized_n = n.replace(",", "").replace(".", "")
            if normalized_n == normalized_expected:
                score += 0.5
                break

    # Calidad del razonamiento: longitud razonable
    if len(thought_text) > 50:
        score += 0.1

    return min(1.0, score)


# ── Función principal ─────────────────────────────────────────────────────────


async def solve_with_tot(sample: dict, strategy: str = "beam") -> ToTResult:
    """Resuelve un problema GSM8K usando Tree of Thoughts.

    Args:
        sample: Diccionario con 'question' y 'answer'.
        strategy: Estrategia de búsqueda: 'beam', 'bfs' o 'dfs'.

    Returns:
        ToTResult con el mejor pensamiento y el camino completo.
    """
    state = {
        "question": sample["question"],
        "answer": sample["answer"],
        "messages": [],
        "metadata": {},
    }

    result = await tree_of_thoughts(
        problem=sample["question"],
        generate_fn=generate_solution_steps,
        evaluate_fn=evaluate_solution,
        state=state,
        breadth=3,        # 3 candidatos por nodo
        depth=3,          # máximo 3 niveles de profundidad
        beam_size=2,      # retener top-2 en búsqueda beam
        threshold=0.9,    # detener si algún pensamiento puntúa >= 0.9
        search_strategy=strategy,  # type: ignore[arg-type]
    )
    return result


async def main() -> None:
    print("=" * 70)
    print("  Tree of Thoughts — Dataset: GSM8K (Grade School Math)")
    print("=" * 70)

    for i, sample in enumerate(GSM8K_SAMPLES, 1):
        print(f"\n[Problema {i}]")
        print(f"  Pregunta : {sample['question'][:80]}...")
        print(f"  Respuesta esperada: {sample['answer']}")

        # Probar con estrategia beam (por defecto)
        result = await solve_with_tot(sample, strategy="beam")

        print(f"\n  Mejor pensamiento (score={result.best_thought.score:.2f}):")
        print(f"    {result.best_thought.content[:300]}")
        print(f"  Total pensamientos generados: {result.total_thoughts_generated}")
        print(f"  Profundidad del mejor camino: {len(result.best_path) - 1}")

        # Mostrar el camino completo
        print("  Camino de razonamiento:")
        for step in result.best_path:
            prefix = "  ROOT" if step.depth == 0 else f"  D{step.depth}"
            print(f"    {prefix} [score={step.score:.2f}] {step.content[:100]}...")

        print("-" * 70)

    # Comparación de estrategias en el primer problema
    print("\n[Comparativa de estrategias — Problema 1]")
    for strat in ["beam", "dfs", "bfs"]:
        r = await solve_with_tot(GSM8K_SAMPLES[0], strategy=strat)
        print(
            f"  {strat:5s}: score={r.best_thought.score:.2f}  "
            f"pensamientos={r.total_thoughts_generated}"
        )


if __name__ == "__main__":
    asyncio.run(main())

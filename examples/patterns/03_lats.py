"""
LATS — Language Agent Tree Search (MCTS sobre acciones de agente)
=================================================================
Patrón: SPEC-PAT-004 / lightagent.agents.patterns.lats

Dataset: WebArena / tareas de planificación de texto
  • Conjunto de tareas de navegación y planificación basado en texto.
  • Para este ejemplo usamos tareas de planificación sintéticas que simulan
    el espacio de acción de un agente de búsqueda de información.
  • Referencia inspirada en: https://arxiv.org/abs/2307.13854 (WebArena)
  • Por qué: LATS/MCTS es ideal para espacios de acción discretos donde hay
    que balancear exploración y explotación (UCB1). Las tareas de planificación
    multi-paso capturan exactamente esta dinámica.

Descripción del patrón:
  LATSAgent implementa MCTS sobre acciones de agente:
  1. Select   — UCB1 para elegir el nodo hoja más prometedor
  2. Expand   — generar acciones candidatas con action_generator
  3. Simulate — puntuar el estado resultante con reward_fn
  4. Backprop — propagar recompensa al árbol

Callables necesarios:
  - action_generator(state, tools) → lista de acciones candidatas
  - transition_fn(state, action) → nuevo estado
  - reward_fn(state) → float [0, 1]

Uso:
    uv run python examples/patterns/03_lats.py
"""

from __future__ import annotations

import asyncio
from typing import Any

from lightagent.agents.patterns.lats import LATSAgent, LATSResult

# ── Dataset: tareas de planificación de texto ─────────────────────────────────
# Tareas sintéticas que simulan WebArena / ALFWorld style:
# el agente debe navegar por un espacio de acciones para alcanzar un objetivo.

PLANNING_TASKS = [
    {
        "id": "T1",
        "goal": "Encontrar el precio del vuelo más barato entre Madrid y Nueva York para el 15 de enero",
        "initial_state": {
            "location": "home",
            "info_gathered": [],
            "steps_taken": 0,
            "goal": "find_cheapest_flight_MAD_NYC",
        },
        "terminal_keyword": "precio",
    },
    {
        "id": "T2",
        "goal": "Reservar una mesa para 4 personas en un restaurante italiano en Barcelona con menos de 3 pasos",
        "initial_state": {
            "location": "search_engine",
            "info_gathered": [],
            "steps_taken": 0,
            "goal": "book_restaurant_Barcelona",
        },
        "terminal_keyword": "reserva confirmada",
    },
    {
        "id": "T3",
        "goal": "Investigar los 3 mejores frameworks de agentes IA en 2024 y crear un resumen comparativo",
        "initial_state": {
            "location": "search_engine",
            "info_gathered": [],
            "steps_taken": 0,
            "goal": "research_ai_agent_frameworks",
        },
        "terminal_keyword": "comparativa",
    },
]

# ── Definición de herramientas disponibles ────────────────────────────────────
AVAILABLE_TOOLS = [
    {"name": "search_web", "description": "Buscar información en internet"},
    {"name": "open_url", "description": "Abrir una URL específica"},
    {"name": "extract_info", "description": "Extraer información de una página"},
    {"name": "compare_options", "description": "Comparar múltiples opciones"},
    {"name": "make_selection", "description": "Seleccionar la mejor opción"},
    {"name": "confirm_action", "description": "Confirmar y ejecutar una acción"},
    {"name": "summarize", "description": "Resumir información recopilada"},
]


# ── Callables del agente ──────────────────────────────────────────────────────


async def action_generator(state: dict[str, Any], tools: list[dict]) -> list[str]:
    """Genera acciones candidatas dado el estado actual.

    En producción esto llamaría al LLM. Aquí usamos heurísticas basadas
    en el estado para simular el espacio de acción de WebArena.

    Args:
        state: Estado actual del agente.
        tools: Herramientas disponibles.

    Returns:
        Lista de acciones candidatas como strings.
    """
    steps = state.get("steps_taken", 0)
    goal = state.get("goal", "")
    info = state.get("info_gathered", [])

    # Simular generación de acciones contextuales
    if steps == 0:
        return [
            f"search_web(query='{goal}')",
            f"search_web(query='{goal} precio mejor opción')",
            f"search_web(query='comparativa {goal} 2024')",
        ]
    if steps == 1 and not info:
        return [
            "extract_info(source='primer resultado')",
            "extract_info(source='segundo resultado')",
            "open_url(url='resultado más relevante')",
        ]
    if steps == 2:
        return [
            "compare_options(results='todos los resultados')",
            "summarize(content='información recopilada')",
            "make_selection(criterion='mejor valor')",
        ]
    return [
        "confirm_action(selection='opción elegida')",
        "summarize(content='resultado final con precio/reserva/comparativa')",
    ]


async def transition_fn(state: dict[str, Any], action: str) -> dict[str, Any]:
    """Aplica una acción al estado y devuelve el nuevo estado.

    Simula la transición de estado de un agente de planificación.

    Args:
        state: Estado actual.
        action: Acción a ejecutar.

    Returns:
        Nuevo estado tras ejecutar la acción.
    """
    new_state = dict(state)
    new_info = list(state.get("info_gathered", []))

    # Simular efectos de cada herramienta
    if "search_web" in action:
        new_info.append(f"resultados_búsqueda:{action[:50]}")
        new_state["location"] = "search_results"
    elif "extract_info" in action:
        new_info.append("información_extraída:precio_comparativa_opciones")
        new_state["location"] = "info_page"
    elif "compare_options" in action:
        new_info.append("comparativa:opción_A_mejor_precio_opción_B_mejor_servicio")
        new_state["location"] = "comparison_view"
    elif "summarize" in action:
        goal = state.get("goal", "")
        if "flight" in goal:
            new_info.append("resumen:precio_vuelo_más_barato_450EUR_aerolínea_X")
        elif "restaurant" in goal:
            new_info.append("resumen:reserva_confirmada_restaurante_Italiano_20:00")
        elif "frameworks" in goal:
            new_info.append("resumen:comparativa_LangGraph_vs_AutoGen_vs_CrewAI")
    elif "confirm_action" in action:
        new_info.append("acción_confirmada:tarea_completada")
        new_state["location"] = "confirmation"

    new_state["info_gathered"] = new_info
    new_state["steps_taken"] = state.get("steps_taken", 0) + 1
    new_state["last_action"] = action
    return new_state


async def reward_fn(state: dict[str, Any]) -> float:
    """Puntúa el estado actual en [0, 1].

    Criterios de recompensa:
    - Información útil acumulada (+0.2 por ítem relevante)
    - Pasos eficientes (menos pasos → mejor recompensa)
    - Tarea completada (bonus +0.5)

    Args:
        state: Estado a evaluar.

    Returns:
        Recompensa en [0.0, 1.0].
    """
    info = state.get("info_gathered", [])
    steps = state.get("steps_taken", 0)
    state.get("goal", "")

    score = 0.0

    # Recompensa por información relevante acumulada
    relevant_keywords = {"precio", "comparativa", "confirmada", "resumen", "opciones"}
    for item in info:
        if any(kw in item.lower() for kw in relevant_keywords):
            score += 0.2

    # Penalización por demasiados pasos (eficiencia)
    if steps > 5:
        score -= 0.1 * (steps - 5)

    # Bonus por completar la tarea
    terminal_markers = {"confirmada", "precio_", "comparativa_"}
    for item in info:
        if any(m in item for m in terminal_markers) and "resumen" in item:
            score += 0.5
            break

    return max(0.0, min(1.0, score))


# ── Función principal ─────────────────────────────────────────────────────────


async def run_lats_task(task: dict) -> LATSResult:
    """Ejecuta LATS en una tarea de planificación.

    Args:
        task: Diccionario con goal, initial_state y metadatos.

    Returns:
        LATSResult con la mejor secuencia de acciones encontrada.
    """
    agent = LATSAgent(
        tools=AVAILABLE_TOOLS,
        reward_fn=reward_fn,
        action_generator=action_generator,
        transition_fn=transition_fn,
        max_simulations=30,  # simulaciones MCTS
        exploration_constant=1.41,  # √2 (Auer et al. 2002)
        max_depth=5,  # profundidad máxima del árbol
        timeout_seconds=30.0,  # timeout de seguridad
        terminal_reward=0.95,  # umbral de terminación anticipada
    )

    return await agent.search(
        initial_state=task["initial_state"],
        goal=task["goal"],
    )


async def main() -> None:
    print("=" * 70)
    print("  LATS (MCTS) — Dataset: Tareas de planificación WebArena-style")
    print("=" * 70)

    for task in PLANNING_TASKS:
        print(f"\n[Tarea {task['id']}]")
        print(f"  Objetivo: {task['goal']}")

        result = await run_lats_task(task)

        print(f"\n  Simulaciones: {result.total_simulations}")
        print(f"  Nodos explorados: {result.nodes_explored}")
        print(f"  Mejor recompensa: {result.best_reward:.3f}")
        print(f"  Tarea completada: {'Sí' if result.goal_reached else 'No'}")

        print("\n  Mejor secuencia de acciones:")
        for j, action in enumerate(result.best_action_sequence, 1):
            print(f"    {j}. {action}")

        if result.best_state:
            info = result.best_state.get("info_gathered", [])
            print(f"\n  Información recopilada ({len(info)} ítems):")
            for item in info:
                print(f"    • {item}")

        print("-" * 70)

    print("\n[UCB1 en acción]")
    print("  El coeficiente de exploración C=√2 balancea:")
    print("  • Explotación: nodos con alta recompensa promedio (Q/N)")
    print("  • Exploración: nodos poco visitados (√(ln N_parent / N))")
    print("  Resultado: cobertura eficiente del espacio de acciones")


if __name__ == "__main__":
    asyncio.run(main())

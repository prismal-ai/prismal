"""
LLM-Compiler — Generación de informes de investigación como DAG paralelo
=========================================================================
Patrón: SPEC-PAT-005 / lightagent.agents.patterns.llm_compiler

Dataset: HotpotQA (preguntas de razonamiento multi-salto)
  • 113 000 preguntas que requieren razonar sobre múltiples documentos.
  • Referencia: https://huggingface.co/datasets/hotpot_qa
  • Por qué: LLMCompiler descompone metas complejas en un DAG de tareas que
    se ejecutan en paralelo. HotpotQA exige exactamente este tipo de
    razonamiento distribuido (buscar A y B, luego sintetizar con A+B).

Descripción del patrón:
  LLMCompiler convierte una meta en un DAG de tareas (TaskNode), valida
  el grafo con el algoritmo de Kahn, calcula ondas de ejecución topológicas
  y ejecuta cada onda concurrentemente con asyncio.gather.
  Si una tarea falla, re-planifica hasta max_replanning veces.

Callables necesarios:
  - plan_fn(goal, state, previous_results) → lista de TaskNode
  - tool_executor(task, prior_outputs) → output de la tarea
  - joiner(goal, completed_tasks) → respuesta final

Uso:
    uv run python examples/patterns/04_llm_compiler.py
"""

from __future__ import annotations

import asyncio
from typing import Any

from lightagent.agents.patterns.llm_compiler import (
    CompilerResult,
    LLMCompiler,
    TaskNode,
)

# ── Dataset: preguntas HotpotQA ───────────────────────────────────────────────
# Subconjunto representativo que requiere razonamiento multi-salto.
HOTPOTQA_SAMPLES = [
    {
        "id": "Q1",
        "question": (
            "¿Cuál es la capital del país donde se inventó el lenguaje de "
            "programación Python, y cuántos habitantes tiene esa ciudad?"
        ),
        "expected_subtasks": ["buscar_origen_python", "buscar_capital", "buscar_poblacion"],
    },
    {
        "id": "Q2",
        "question": (
            "¿Quién fundó la empresa que desarrolló el modelo GPT-4, y en qué "
            "año se fundó esa empresa?"
        ),
        "expected_subtasks": ["buscar_empresa_gpt4", "buscar_fundador", "buscar_año_fundacion"],
    },
    {
        "id": "Q3",
        "question": (
            "Compara LangChain y LlamaIndex: ¿cuál tiene más estrellas en GitHub "
            "y cuál tiene mayor adopción en empresas Fortune 500?"
        ),
        "expected_subtasks": [
            "buscar_langchain_github",
            "buscar_llamaindex_github",
            "buscar_adopcion_empresas",
            "comparar_frameworks",
        ],
    },
]

# ── Simulación de herramientas de investigación ───────────────────────────────
# En producción estas herramientas llamarían APIs reales (web search, Wikipedia, etc.)

_MOCK_TOOL_RESULTS: dict[str, str] = {
    "search": "Resultados de búsqueda: información relevante encontrada",
    "wikipedia": "Wikipedia: artículo con información detallada",
    "web_scrape": "Página web: datos estructurados extraídos",
    "aggregate": "Agregación: síntesis de múltiples fuentes",
    "compare": "Comparativa: análisis lado a lado completado",
    "calculate": "Cálculo: resultado numérico obtenido",
}


# ── Callables del compilador ──────────────────────────────────────────────────


async def plan_fn(
    goal: str,
    state: Any,
    previous_results: dict[str, Any] | None = None,
) -> list[TaskNode]:
    """Descompone una meta en un DAG de tareas.

    En producción, un LLM generaría el plan. Aquí usamos heurísticas
    basadas en keywords para crear planes representativos.

    Args:
        goal: Meta a alcanzar.
        state: Estado actual del agente.
        previous_results: Resultados de un plan fallido anterior (para re-planificar).

    Returns:
        Lista de TaskNode formando un DAG válido.
    """
    # Re-planificación: si hay fallos previos, añadir tareas de verificación
    extra_verify = []
    if previous_results:
        failed = [k for k, v in previous_results.items() if v is None]
        if failed:
            extra_verify = [
                TaskNode(
                    id="T_verify",
                    description=f"Verificar y recuperar tareas fallidas: {failed}",
                    tool="web_scrape",
                    args={"query": f"alternativa para {failed}"},
                    depends_on=[],
                )
            ]

    # Plan heurístico basado en la pregunta
    if "python" in goal.lower() or "lenguaje" in goal.lower():
        tasks = [
            TaskNode(
                id="T1",
                description="Buscar el origen del lenguaje Python",
                tool="search",
                args={"query": "Python programming language origin country creator"},
                depends_on=[],
            ),
            TaskNode(
                id="T2",
                description="Identificar el país y su capital",
                tool="wikipedia",
                args={"query": "Países Bajos capital Amsterdam", "context": "$T1.output"},
                depends_on=["T1"],
            ),
            TaskNode(
                id="T3",
                description="Buscar población de la capital",
                tool="search",
                args={"query": "Amsterdam population 2024", "context": "$T2.output"},
                depends_on=["T2"],
            ),
        ]
    elif "gpt" in goal.lower() or "openai" in goal.lower():
        tasks = [
            TaskNode(
                id="T1",
                description="Buscar qué empresa desarrolló GPT-4",
                tool="search",
                args={"query": "GPT-4 developer company"},
                depends_on=[],
            ),
            TaskNode(
                id="T2",
                description="Buscar el fundador de OpenAI",
                tool="wikipedia",
                args={"query": "OpenAI founders", "context": "$T1.output"},
                depends_on=["T1"],
            ),
            TaskNode(
                id="T3",
                description="Buscar el año de fundación",
                tool="search",
                args={"query": "OpenAI founding year", "context": "$T1.output"},
                depends_on=["T1"],
            ),
            TaskNode(
                id="T4",
                description="Agregar fundador y año",
                tool="aggregate",
                args={"sources": ["$T2.output", "$T3.output"]},
                depends_on=["T2", "T3"],
            ),
        ]
    else:
        # Plan genérico para comparativas
        tasks = [
            TaskNode(
                id="T1",
                description="Recopilar datos de LangChain en GitHub",
                tool="web_scrape",
                args={"url": "https://github.com/langchain-ai/langchain"},
                depends_on=[],
            ),
            TaskNode(
                id="T2",
                description="Recopilar datos de LlamaIndex en GitHub",
                tool="web_scrape",
                args={"url": "https://github.com/run-llama/llama_index"},
                depends_on=[],
            ),
            TaskNode(
                id="T3",
                description="Buscar adopción empresarial de ambos frameworks",
                tool="search",
                args={"query": "LangChain vs LlamaIndex enterprise adoption Fortune 500"},
                depends_on=[],
            ),
            TaskNode(
                id="T4",
                description="Comparar los tres resultados",
                tool="compare",
                args={
                    "langchain_data": "$T1.output",
                    "llamaindex_data": "$T2.output",
                    "enterprise_data": "$T3.output",
                },
                depends_on=["T1", "T2", "T3"],
            ),
        ]

    return extra_verify + tasks


async def tool_executor(
    task: TaskNode,
    prior_outputs: dict[str, Any],
) -> str:
    """Ejecuta una tarea y devuelve su output.

    En producción invocaría herramientas reales (web search, APIs, etc.).
    Aquí simula la ejecución con resultados mock realistas.

    Args:
        task: La tarea a ejecutar.
        prior_outputs: Outputs de tareas previas (por ID).

    Returns:
        Output de la tarea como string.
    """
    # Simular latencia de herramienta (operaciones reales tomarían más tiempo)
    await asyncio.sleep(0.05)

    tool_result = _MOCK_TOOL_RESULTS.get(task.tool, "resultado no disponible")

    # Enriquecer con contexto de dependencias
    if prior_outputs:
        context_ids = ", ".join(prior_outputs.keys())
        return f"[{task.tool}] {task.description} → {tool_result} (usando contexto: {context_ids})"

    return f"[{task.tool}] {task.description} → {tool_result}"


async def joiner(goal: str, completed_tasks: list[TaskNode]) -> str:
    """Sintetiza los resultados de todas las tareas en la respuesta final.

    Args:
        goal: Meta original.
        completed_tasks: Todas las tareas completadas con sus outputs.

    Returns:
        Respuesta final sintetizada.
    """
    outputs = [
        f"  • [{t.id}] {t.description}: {str(t.output)[:100]}"
        for t in completed_tasks
        if t.output is not None
    ]
    outputs_str = "\n".join(outputs)

    return (
        f"Respuesta sintetizada para: '{goal}'\n"
        f"Basada en {len(completed_tasks)} tareas completadas:\n"
        f"{outputs_str}"
    )


# ── Función principal ─────────────────────────────────────────────────────────


async def run_compiler(sample: dict) -> CompilerResult:
    """Ejecuta el LLMCompiler para una pregunta HotpotQA.

    Args:
        sample: Pregunta con metadatos.

    Returns:
        CompilerResult con el plan ejecutado y la respuesta final.
    """
    compiler = LLMCompiler(
        tools=[{"name": t} for t in _MOCK_TOOL_RESULTS.keys()],
        plan_fn=plan_fn,
        tool_executor=tool_executor,
        joiner=joiner,
        max_replanning=2,
    )

    result = await compiler.compile_and_run(
        goal=sample["question"],
        state={"messages": [], "metadata": {}},
    )
    return result


async def main() -> None:
    print("=" * 70)
    print("  LLM-Compiler — Dataset: HotpotQA (razonamiento multi-salto)")
    print("=" * 70)

    for sample in HOTPOTQA_SAMPLES:
        print(f"\n[{sample['id']}] {sample['question']}")
        print(f"  Subtareas esperadas: {sample['expected_subtasks']}")

        result = await run_compiler(sample)

        # Mostrar el plan como DAG
        print(f"\n  Plan DAG ({len(result.plan.tasks)} tareas):")
        for t in result.plan.tasks:
            deps = f" → depende de {t.depends_on}" if t.depends_on else " (raíz)"
            print(f"    {t.id}: [{t.tool}] {t.description}{deps}")

        # Mostrar ondas de ejecución (concurrencia)
        waves = result.plan.execution_waves
        print(f"\n  Ondas de ejecución ({len(waves)} ondas paralelas):")
        for i, wave in enumerate(waves, 1):
            print(f"    Onda {i}: {wave}  ← ejecutadas simultáneamente")

        # Resultados
        print(f"\n  Estado de tareas:")
        for t in result.plan.tasks:
            status_icon = "✓" if t.status == "completed" else "✗"
            print(f"    {status_icon} {t.id} [{t.status}]")

        print(f"\n  Re-planificaciones: {result.replanning_count}")
        print(f"\n  Respuesta final:")
        print(f"  {result.final_answer[:300]}")
        print("-" * 70)

    # Demostrar validación de DAG
    print("\n[Validación de DAG con algoritmo de Kahn]")
    compiler = LLMCompiler(
        tools=[],
        plan_fn=plan_fn,
        tool_executor=tool_executor,
        joiner=joiner,
    )
    valid_plan_tasks = [
        TaskNode(id="A", description="task A", tool="search", args={}, depends_on=[]),
        TaskNode(id="B", description="task B", tool="search", args={}, depends_on=["A"]),
        TaskNode(id="C", description="task C", tool="search", args={}, depends_on=["A"]),
        TaskNode(id="D", description="task D", tool="aggregate", args={}, depends_on=["B", "C"]),
    ]
    from lightagent.agents.patterns.llm_compiler import CompilerPlan
    plan = CompilerPlan(tasks=valid_plan_tasks, goal="test", is_valid=False, execution_waves=[])
    is_valid = compiler.validate_dag(plan)
    waves = compiler.compute_execution_waves(valid_plan_tasks)
    print(f"  DAG A→B,C→D  es válido: {is_valid}")
    print(f"  Ondas: {waves}")


if __name__ == "__main__":
    asyncio.run(main())

"""
Dev Pipeline Subgraph — Pipeline completo de desarrollo de software ágil
=========================================================================
Subgraph: prismal.agents.subgraphs.dev_pipeline

Dataset: GitHub Issues + Feature Requests (proyectos open-source reales)
  • Referencia inspirada en GitHub REST API issues:
    https://docs.github.com/en/rest/issues
  • Proyectos usados como referencia: FastAPI, Pydantic, LangChain, Typer
  • Por qué: Las GitHub Issues representan el artefacto de entrada natural
    para un pipeline de desarrollo: contienen el requisito (PO), permiten
    inferir la arquitectura (Architect), generan código (Developer), tests
    unitarios (UnitTest), QA, y revisión (Reviewer) — exactamente los 6
    agentes del Dev Pipeline.

Descripción del subgraph Dev Pipeline:
  po_agent → architect_agent → developer_agent → unit_test_agent
  → qa_agent → reviewer_agent → [quality gates]

  Gates (4):
  1. Test gate       : tests pasando → continúa; fallando → retest (max 3)
  2. Review gate     : reviewer_score >= 0.8 → approval seed; < 0.8 → developer
  3. HITL seed       : escribe artifact + risk_level en metadata
  4. HITL gate       : approve → END; reject/changes → developer (bypass si hitl=False)

  Paralelismo:
  - Los tests unitarios se ejecutan en paralelo por módulo (module_dispatcher)
  - dev_unit_tester_node corre en paralelo para cada módulo
  - dev_test_aggregator_node recolecta todos los resultados

Agentes:
  po_agent        — Define requisitos, criterios de aceptación, user stories
  architect_agent — Diseña la arquitectura, interfaces, estructura de módulos
  developer_agent — Escribe la implementación en código
  unit_test_agent — Escribe tests unitarios para el código generado
  qa_agent        — QA, tests de integración, regresión
  reviewer_agent  — Code review, calidad, puntuación >= 0.8 para aprobar

Uso:
    uv run python examples/subgraphs/03_dev_pipeline.py
"""

from __future__ import annotations

import asyncio

from langchain_core.messages import HumanMessage

from prismal.agents.state import initial_state

# Importar el subgraph con manejo de dependencias opcionales
try:
    from prismal.agents.subgraphs.dev_pipeline.builder import (
        build_dev_pipeline_subgraph,
        register_dev_pipeline,
    )

    DEV_PIPELINE_AVAILABLE = True
except ImportError:
    DEV_PIPELINE_AVAILABLE = False

# ── Dataset: GitHub Issues de proyectos open-source ──────────────────────────
# Issues reales/inspiradas de FastAPI, Pydantic y LangChain.
GITHUB_ISSUES = [
    {
        "id": "GH-001",
        "repo": "prismal",
        "title": "Add rate limiting middleware for LLM provider calls",
        "labels": ["enhancement", "providers", "good-first-issue"],
        "priority": "high",
        "description": (
            "We need a rate limiting mechanism for LLM API calls to prevent "
            "hitting provider rate limits (tokens/min and requests/min). "
            "The solution should:\n"
            "- Support configurable limits per provider (Anthropic, OpenAI, etc.)\n"
            "- Implement token bucket or sliding window algorithm\n"
            "- Retry with exponential backoff on 429 errors\n"
            "- Expose metrics: current_rate, tokens_remaining, wait_time\n"
            "- Be async-native and thread-safe\n"
            "- Integrate with the existing ProviderRegistry\n\n"
            "Acceptance criteria:\n"
            "- Unit tests with >90% coverage\n"
            "- No breaking changes to existing ProviderRegistry API\n"
            "- Type-annotated with mypy strict compliance\n"
            "- Documentation in docstrings"
        ),
        "complexity": "medium",
        "estimated_hours": 8,
    },
    {
        "id": "GH-002",
        "repo": "prismal",
        "title": "Implement async context manager for AgentState checkpointing",
        "labels": ["enhancement", "core", "async"],
        "priority": "medium",
        "description": (
            "Currently, checkpointing requires manual calls to SqliteSaver. "
            "We should add an async context manager that automatically saves "
            "state at the beginning and end of each agent turn.\n\n"
            "Requirements:\n"
            "- `async with checkpoint_context(thread_id) as state:` pattern\n"
            "- Auto-save on __aexit__ (even on exception)\n"
            "- Support both SQLite and PostgreSQL backends\n"
            "- Compatible with LangGraph's existing checkpointer protocol\n"
            "- Performance: <5ms overhead per turn\n\n"
            "Acceptance criteria:\n"
            "- Integration tests with both backends\n"
            "- No memory leaks (context manager must release connections)\n"
            "- Works with Python 3.13 asyncio event loop"
        ),
        "complexity": "medium",
        "estimated_hours": 6,
    },
    {
        "id": "GH-003",
        "repo": "prismal",
        "title": "Create CLI tool for testing agent pipelines interactively",
        "labels": ["enhancement", "cli", "dx"],
        "priority": "low",
        "description": (
            "Developers need a way to test agent pipelines from the terminal "
            "without writing Python scripts. Implement a `prismal run` CLI "
            "command using Typer.\n\n"
            "Features:\n"
            "- `prismal run <pipeline> --input 'user message'`\n"
            "- `prismal run --stream` for token-by-token output\n"
            "- `prismal list-pipelines` to enumerate registered subgraphs\n"
            "- JSON output mode: `--format json`\n"
            "- Thread ID support: `--thread-id my-session`\n\n"
            "Tech stack: Typer + Rich for beautiful output\n\n"
            "Acceptance criteria:\n"
            "- Works on Linux, macOS, Windows\n"
            "- Tests using Click test runner\n"
            "- `--help` for all commands"
        ),
        "complexity": "low",
        "estimated_hours": 4,
    },
]

# ── Configuración del pipeline ────────────────────────────────────────────────


def format_issue_as_task(issue: dict) -> str:
    """Convierte una GitHub Issue en una instrucción para el Dev Pipeline."""
    return (
        f"Issue #{issue['id']}: {issue['title']}\n"
        f"Repositorio: {issue['repo']}\n"
        f"Prioridad: {issue['priority']} | Complejidad: {issue['complexity']}\n"
        f"Labels: {', '.join(issue['labels'])}\n\n"
        f"Descripción completa:\n{issue['description']}\n\n"
        f"Tiempo estimado: {issue['estimated_hours']} horas\n\n"
        "Desarrolla este feature siguiendo el pipeline completo:\n"
        "1. PO: define historias de usuario y criterios de aceptación\n"
        "2. Architect: diseña la arquitectura y las interfaces\n"
        "3. Developer: implementa el código en Python\n"
        "4. UnitTest: escribe tests unitarios con pytest\n"
        "5. QA: verifica cobertura y casos edge\n"
        "6. Reviewer: revisa calidad, seguridad y estilo"
    )


async def run_dev_pipeline_real(issue: dict) -> dict:
    """Ejecuta el Dev Pipeline real con el subgraph compilado."""
    await register_dev_pipeline()
    subgraph_def = build_dev_pipeline_subgraph()

    from prismal.agents.subgraphs.factory import SubgraphFactory

    compiled = SubgraphFactory.compile(subgraph_def)

    state = initial_state()
    state["messages"] = [HumanMessage(content=format_issue_as_task(issue))]
    state["metadata"] = {
        "issue_id": issue["id"],
        "priority": issue["priority"],
        "hitl_enabled": False,  # desactivar aprobación humana en el ejemplo
        "dev_pipeline": {
            "iteration": 0,
            "review_attempts": 0,
        },
    }

    config = {
        "configurable": {
            "thread_id": f"dev_{issue['id']}",
            "recursion_limit": 25,
        }
    }

    return await compiled.ainvoke(state, config=config)


def simulate_dev_pipeline(issue: dict) -> None:
    """Simula la ejecución del Dev Pipeline mostrando la salida esperada."""
    print("\n  [Nodo 1: PO Agent]")
    print(f"    Analizando issue #{issue['id']}...")
    print("    → User Stories definidas:")
    print(f"      AS a developer, I WANT {issue['title'].lower()}")
    print("      SO THAT the system handles rate limits gracefully")
    print(
        f"    → Criterios de aceptación extraídos: {len(issue['description'].split(chr(10)))} líneas"
    )

    print("\n  [Nodo 2: Architect Agent]")
    print(f"    Diseñando arquitectura para: {issue['title'][:50]}...")
    arch_patterns = {
        "rate limiting": "Token Bucket + CircuitBreaker pattern",
        "context manager": "AsyncContextManager + Repository pattern",
        "CLI tool": "Command pattern + Observer (streaming)",
    }
    pattern = next(
        (v for k, v in arch_patterns.items() if k in issue["title"].lower()),
        "Adapter + Factory pattern",
    )
    print(f"    → Patrón arquitectural: {pattern}")
    print("    → Módulos identificados: 3-4 módulos")
    print("    → Interfaces definidas: Protocol classes con type hints")

    print("\n  [Nodo 3: Developer Agent]")
    print("    Implementando código...")
    complexity_map = {"high": "~250 líneas", "medium": "~150 líneas", "low": "~80 líneas"}
    lines = complexity_map.get(issue["complexity"], "~120 líneas")
    print(f"    → Código generado: {lines} de Python")
    print("    → Type annotations: 100% (mypy strict)")
    print("    → Docstrings: Google style en todas las funciones públicas")

    print("\n  [Nodo 4: Unit Test Agent]")
    cov = {"high": 94, "medium": 91, "low": 88}[issue["complexity"]]
    print("    Escribiendo tests con pytest + pytest-asyncio...")
    print(f"    → Tests generados: {cov // 10 + 2} test cases")
    print(f"    → Cobertura estimada: {cov}%")
    print("    → Mock de LLM providers: sí (sin llamadas reales)")

    print("\n  [Nodo 4b: Parallel Unit Tester] (módulos en paralelo)")
    n_modules = {"high": 4, "medium": 3, "low": 2}[issue["complexity"]]
    print(f"    module_dispatcher → {n_modules} workers en paralelo")
    for i in range(1, n_modules + 1):
        print(f"      Worker {i}: tests del módulo_{i} → PASS ✓")
    print(f"    dev_test_aggregator → {n_modules}/{n_modules} módulos OK")

    print("\n  [Test Gate]")
    print("    → Todos los tests pasan ✓ → avanza a QA")

    print("\n  [Nodo 5: QA Agent]")
    print("    Tests de integración y edge cases...")
    print("    → Tests de integración: PASS ✓")
    print("    → Edge cases probados: inputs vacíos, timeouts, errores de red")
    print("    → Regresión con suite completa: PASS ✓")

    print("\n  [Nodo 6: Reviewer Agent]")
    review_score = {"high": 0.88, "medium": 0.85, "low": 0.92}[issue["complexity"]]
    print("    Code review con checklist de calidad...")
    print(f"    → Puntuación de calidad: {review_score:.2f}/1.0")
    passed = review_score >= 0.8
    print(f"    → Review gate (>= 0.8): {'PASS ✓' if passed else 'FAIL ✗ → vuelve a Developer'}")

    print("\n  [Review Gate → HITL Approval Seed]")
    print("    Artefacto guardado en metadata['dev_pipeline']['code_artifact']")
    print(f"    Risk level: {'HIGH' if issue['priority'] == 'high' else 'MEDIUM'}")

    print("\n  [HITL Gate] (hitl_enabled=False → bypass automático)")
    print("    → Aprobación automática → END ✓")

    print(f"\n  Pipeline completado para issue #{issue['id']}")


async def main() -> None:
    print("=" * 70)
    print("  Dev Pipeline Subgraph — Dataset: GitHub Issues (prismal)")
    print("=" * 70)

    # Mostrar arquitectura del subgraph
    print("\n[Arquitectura del Dev Pipeline]")
    nodes = [
        ("po_agent        ", "Define requisitos, user stories, criterios de aceptación"),
        ("architect_agent ", "Diseña arquitectura, interfaces, estructura de módulos"),
        ("developer_agent ", "Escribe implementación Python (puede iterar máx 3×)"),
        ("unit_test_agent ", "Escribe tests pytest con cobertura objetivo >90%"),
        ("[Parallel Tests] ", "module_dispatcher → N workers → aggregator"),
        ("[Test Gate]      ", "¿Todos los tests pasan? SÍ → QA | NO → unit_test (max 3×)"),
        ("qa_agent        ", "Tests integración, edge cases, regresión"),
        ("reviewer_agent  ", "Code review: score >= 0.8 → approval | < 0.8 → developer"),
        ("[Review Gate]   ", "¿score >= 0.8? SÍ → HITL seed | NO → developer"),
        ("[HITL Seed]     ", "Escribe artefacto + risk_level en metadata"),
        ("[HITL Gate]     ", "approve → END | reject → developer | bypass si hitl=False"),
    ]
    for node, desc in nodes:
        print(f"  {node}: {desc}")

    print(f"\n[Issues a desarrollar: {len(GITHUB_ISSUES)}]")
    for issue in GITHUB_ISSUES:
        print(f"  #{issue['id']}: {issue['title'][:55]}... [{issue['priority'].upper()}]")

    # Ejecutar el pipeline para cada issue
    print("\n" + "─" * 70)
    for issue in GITHUB_ISSUES:
        print(f"\n{'═' * 70}")
        print(f"  Feature: {issue['title']}")
        print(
            f"  Issue: #{issue['id']} | Prioridad: {issue['priority']} | Complejidad: {issue['complexity']}"
        )
        print(f"  Estimación: {issue['estimated_hours']}h")
        print(f"{'─' * 70}")

        if DEV_PIPELINE_AVAILABLE:
            try:
                final_state = await run_dev_pipeline_real(issue)
                messages = final_state.get("messages", [])
                pipeline_meta = final_state.get("metadata", {}).get("dev_pipeline", {})

                print("\n  [Resultado real del pipeline]")
                if pipeline_meta:
                    print(f"    Iteraciones de desarrollo : {pipeline_meta.get('iteration', 1)}")
                    print(
                        f"    Score de revisión        : {pipeline_meta.get('review_result', {}).get('score', 'N/A')}"
                    )
                    print(
                        f"    Artefacto generado       : {bool(pipeline_meta.get('code_artifact'))}"
                    )

                if messages:
                    last_msg = str(messages[-1].content)
                    print("\n  Output final:")
                    print(f"  {last_msg[:400]}")

            except Exception as exc:
                print(f"  [Modo simulado — razón: {type(exc).__name__}]")
                simulate_dev_pipeline(issue)
        else:
            print("  [Modo simulado — subgraph no disponible]")
            simulate_dev_pipeline(issue)

    # Resumen y comparativa con desarrollo manual
    print(f"\n{'═' * 70}")
    print("  COMPARATIVA: Dev Pipeline vs Desarrollo Manual")
    print(f"{'═' * 70}")

    total_hours = sum(i["estimated_hours"] for i in GITHUB_ISSUES)
    print(f"\n  {'Fase':<25} {'Manual':>10} {'Dev Pipeline':>14}")
    print("  " + "─" * 52)
    phases = [
        ("Definición de requisitos", "2h/feature", "automático (PO Agent)"),
        ("Diseño arquitectura     ", "3h/feature", "automático (Arch Agent)"),
        ("Implementación          ", f"{total_hours}h total", "paralelo + iterativo"),
        ("Tests unitarios         ", "2h/feature", "automático (UT Agent)"),
        ("QA / Integración        ", "1h/feature", "automático (QA Agent)"),
        ("Code review             ", "1h/feature", "score automático >= 0.8"),
    ]
    for phase, manual, pipeline in phases:
        print(f"  {phase}: {manual:>10} → {pipeline}")

    print("\n  Quality Gates garantizan:")
    print("    ✓ Tests siempre pasan antes de QA")
    print("    ✓ Code review score >= 0.8 antes de aprobar")
    print("    ✓ Aprobación humana opcional (HITL) para cambios críticos")
    print("    ✓ Máximo 3 iteraciones por gate (anti-bucle infinito)")

    print("\n[Configuración HITL]")
    print("  hitl_enabled=True  → pausa y espera aprobación humana")
    print("  hitl_enabled=False → bypass automático (CI/CD mode)")
    print("  risk_level HIGH/MEDIUM/LOW → visible en el payload del interrupt")


if __name__ == "__main__":
    asyncio.run(main())

"""
Analysis Orchestrator — Orquestador Jerárquico de Dominio Analítico
====================================================================

Dataset:  Business Intelligence Center (custom)
          6 tareas analíticas de un centro de inteligencia de negocio
          que cubren los 4 agentes hoja del dominio: data_analyst,
          ml_pipeline, dev_pipeline y financial_analyst.

Patrón:   Orquestador jerárquico de dominio (SPEC-042 Phase 40)
          ┌─────────────────────────────────────────────────────┐
          │  Jerarquía completa (PRISMAL_HIERARCHICAL_MODE)  │
          │                                                     │
          │  root_supervisor                                    │
          │       │                                             │
          │  analysis_orchestrator ◄── entry point             │
          │       │                                             │
          │  analysis_supervisor  (domain LLM router)          │
          │  ┌────┼────────────────────┐                       │
          │  ▼    ▼         ▼          ▼                       │
          │  data_analyst  ml_pipeline  dev_pipeline            │
          │  financial_analyst                                  │
          │       │ (todos retornan al supervisor)              │
          │  analysis_supervisor → END                          │
          └─────────────────────────────────────────────────────┘

Diferencia vs un subgraph plano:
  - El orquestador NO ejecuta tareas: sólo enruta.
  - El supervisor de dominio usa el LLM para decidir el agente hoja.
  - Una vez un agente hoja responde, el supervisor cierra el turno (END).
  - El contador de iteraciones por dominio evita bucles runaway (cap=8).

Modos:
  1. demo_simulation()      — simula el enrutamiento sin LLM (determinístico)
  2. demo_comparison()      — compara enrutamiento vs enrutamiento manual
  3. demo_real_langgraph()  — grafo real con stubs y MemorySaver
  4. demo_hierarchy()       — visualiza la jerarquía completa de 3 niveles

Uso:
  uv run python examples/subgraphs/10_analysis_orchestrator.py
"""

from __future__ import annotations

import asyncio
import textwrap
from dataclasses import dataclass, field
from typing import Any

# ──────────────────────────────────────────────────────────────────────────────
# Dataset — tareas de Business Intelligence
# ──────────────────────────────────────────────────────────────────────────────


@dataclass
class AnalyticsTask:
    id: str
    request: str
    domain: str  # data_analyst | ml_pipeline | dev_pipeline | financial_analyst
    complexity: str  # LOW | MEDIUM | HIGH
    expected_output: str
    context: str


TASKS: list[AnalyticsTask] = [
    AnalyticsTask(
        id="TASK-01",
        request="Analiza las ventas del Q3 2025 por región y producto. "
        "Necesito un ranking de las 5 regiones con mayor crecimiento YoY.",
        domain="data_analyst",
        complexity="MEDIUM",
        expected_output="DataFrame con ventas por región, crecimiento YoY y ranking",
        context="Tabla `sales` con columnas: date, region, product, amount, units_sold",
    ),
    AnalyticsTask(
        id="TASK-02",
        request="Entrena un modelo de predicción de churn para usuarios premium. "
        "Usa los últimos 12 meses de actividad y exporta el pipeline a MLflow.",
        domain="ml_pipeline",
        complexity="HIGH",
        expected_output="Modelo XGBoost + LightGBM; AUC-ROC objetivo >= 0.85",
        context="Dataset: 45 000 usuarios premium con 22 features de comportamiento",
    ),
    AnalyticsTask(
        id="TASK-03",
        request="Implementa el endpoint REST /api/v2/reports en FastAPI. "
        "Debe aceptar filtros por fecha y región, con paginación y cache Redis.",
        domain="dev_pipeline",
        complexity="HIGH",
        expected_output="Código FastAPI + tests unitarios + documentación OpenAPI",
        context="Arquitectura existente: FastAPI 0.111, SQLAlchemy 2.0, Redis 7",
    ),
    AnalyticsTask(
        id="TASK-04",
        request="Analiza NVDA y TSLA: señales técnicas (RSI, MACD, Bollinger Bands) "
        "y fundamentales (P/E, EPS growth). Dame una recomendación BUY/HOLD/SELL.",
        domain="financial_analyst",
        complexity="HIGH",
        expected_output="Reporte técnico+fundamental con recomendación y score de riesgo",
        context="Datos Yahoo Finance; ventana 6 meses; cartera con exposición actual 8%",
    ),
    AnalyticsTask(
        id="TASK-05",
        request="¿Cuántos usuarios activos únicos tuvimos en los últimos 30 días? "
        "Segmenta por plan (free/premium/enterprise) y calcula el ARPU.",
        domain="data_analyst",
        complexity="LOW",
        expected_output="Métricas DAU/MAU por segmento y ARPU mensual",
        context="Tabla `user_events` con user_id, event_type, timestamp, plan_type",
    ),
    AnalyticsTask(
        id="TASK-06",
        request="Refactoriza el módulo de autenticación (auth/jwt.py). "
        "Elimina las deprecation warnings de PyJWT 2.9 y añade soporte PKCE.",
        domain="dev_pipeline",
        complexity="MEDIUM",
        expected_output="Código refactorizado + tests de regresión + PR description",
        context="JWT actual: HS256; migrar a RS256 con rotación de claves cada 24h",
    ),
]

# ──────────────────────────────────────────────────────────────────────────────
# Stubs de los agentes hoja (simulación sin LLM)
# ──────────────────────────────────────────────────────────────────────────────

BOLD = "\033[1m"
RESET = "\033[0m"
CYAN = "\033[96m"
GREEN = "\033[92m"
PURPLE = "\033[95m"
ORANGE = "\033[93m"
RED = "\033[91m"
DIM = "\033[2m"

AGENT_COLORS = {
    "data_analyst": CYAN,
    "ml_pipeline": PURPLE,
    "dev_pipeline": GREEN,
    "financial_analyst": ORANGE,
}

AGENT_ICONS = {
    "data_analyst": "📊",
    "ml_pipeline": "🤖",
    "dev_pipeline": "⚙️ ",
    "financial_analyst": "📈",
}


@dataclass
class AgentResult:
    agent: str
    task_id: str
    summary: str
    artifacts: list[str]
    metrics: dict[str, Any] = field(default_factory=dict)


def data_analyst_stub(task: AnalyticsTask) -> AgentResult:
    """Simula data_analyst: queries SQL/DataFrame y análisis estadístico."""
    if "ventas" in task.request.lower() or "q3" in task.request.lower():
        return AgentResult(
            agent="data_analyst",
            task_id=task.id,
            summary="Query ejecutada sobre `sales`. Top regiones YoY: Norte (+34%), "
            "Sur (+28%), Centro (+21%), Este (+15%), Oeste (+9%).",
            artifacts=["sales_q3_2025_regional.parquet", "ranking_yoy_growth.csv"],
            metrics={"rows_processed": 2_847_391, "query_time_ms": 312, "cache_hit": False},
        )
    return AgentResult(
        agent="data_analyst",
        task_id=task.id,
        summary="DAU/MAU calculados: Free=12 450, Premium=3 280, Enterprise=890. "
        "ARPU: Free=$0.82, Premium=$45.20, Enterprise=$380.50.",
        artifacts=["user_metrics_30d.csv", "arpu_by_plan.json"],
        metrics={"rows_processed": 4_120_000, "query_time_ms": 489, "cache_hit": True},
    )


def ml_pipeline_stub(task: AnalyticsTask) -> AgentResult:
    """Simula ml_pipeline: entrenamiento, evaluación y exportación."""
    return AgentResult(
        agent="ml_pipeline",
        task_id=task.id,
        summary="Pipeline completado: XGBoost AUC-ROC=0.887, LightGBM AUC-ROC=0.891. "
        "Top features: dias_sin_login, sesiones_7d, upgrades_historicos. "
        "Modelo exportado a MLflow run_id=a3f9c12e.",
        artifacts=[
            "churn_model_xgb.pkl",
            "churn_model_lgbm.pkl",
            "feature_importance.png",
            "shap_summary.html",
            "mlflow://run/a3f9c12e",
        ],
        metrics={
            "auc_roc": 0.891,
            "precision": 0.834,
            "recall": 0.792,
            "f1": 0.812,
            "train_samples": 36_000,
            "test_samples": 9_000,
        },
    )


def dev_pipeline_stub(task: AnalyticsTask) -> AgentResult:
    """Simula dev_pipeline: PO → Architect → Developer → QA → Reviewer."""
    if "endpoint" in task.request.lower() or "fastapi" in task.request.lower():
        return AgentResult(
            agent="dev_pipeline",
            task_id=task.id,
            summary="PO definió user stories. Architect diseñó: Router FastAPI + "
            "ReportService + CacheLayer (Redis TTL=300s). Developer implementó "
            "415 líneas. QA: 28 tests unitarios (100% pass), 4 tests de integración. "
            "Reviewer: score=0.91 — APROBADO.",
            artifacts=[
                "api/v2/reports.py",
                "api/v2/schemas.py",
                "tests/test_reports_endpoint.py",
                "docs/openapi_reports.yaml",
            ],
            metrics={"review_score": 0.91, "test_coverage": 0.94, "loc": 415},
        )
    return AgentResult(
        agent="dev_pipeline",
        task_id=task.id,
        summary="Refactorización completada: migrado HS256 → RS256, eliminadas 7 "
        "DeprecationWarnings de PyJWT 2.9, implementado PKCE flow. "
        "22 tests de regresión en verde. Reviewer: score=0.88 — APROBADO.",
        artifacts=[
            "auth/jwt.py",
            "auth/pkce.py",
            "tests/test_auth_regression.py",
            "docs/auth_migration_rs256.md",
        ],
        metrics={"review_score": 0.88, "test_coverage": 0.97, "warnings_fixed": 7},
    )


def financial_analyst_stub(task: AnalyticsTask) -> AgentResult:
    """Simula financial_analyst: análisis técnico + fundamental + riesgo."""
    return AgentResult(
        agent="financial_analyst",
        task_id=task.id,
        summary="NVDA: RSI=68 (approaching overbought), MACD bullish cross, BB upper. "
        "P/E=38.2x, EPS growth +112% YoY → BUY (score 0.78). "
        "TSLA: RSI=44 (neutral), MACD bearish, BB mid. "
        "P/E=72.1x, EPS growth -8% YoY → HOLD (score 0.51). "
        "Riesgo de cartera combinado: MEDIUM (VaR 95%=3.2%).",
        artifacts=[
            "NVDA_technical_report.pdf",
            "TSLA_technical_report.pdf",
            "portfolio_risk_assessment.json",
            "recommendation_summary.md",
        ],
        metrics={
            "NVDA_score": 0.78,
            "TSLA_score": 0.51,
            "portfolio_var_95": 0.032,
            "recommendation": {"NVDA": "BUY", "TSLA": "HOLD"},
        },
    )


# ──────────────────────────────────────────────────────────────────────────────
# Simulador del orquestador jerárquico
# ──────────────────────────────────────────────────────────────────────────────

# Reglas de routing determinístico (replica la lógica LLM del domain_supervisor)
_ROUTING_RULES: list[tuple[list[str], str]] = [
    # financial_analyst keywords
    (
        [
            "rsi",
            "macd",
            "bollinger",
            "p/e",
            "eps",
            "nvda",
            "tsla",
            "aapl",
            "acción",
            "bursát",
            "bolsa",
            "buy",
            "sell",
            "hold",
            "técnico",
            "fundamental",
            "forex",
            "cripto",
            "crypto",
            "mercado financiero",
            "stock",
        ],
        "financial_analyst",
    ),
    # ml_pipeline keywords
    (
        [
            "entrena",
            "modelo",
            "ml",
            "machine learning",
            "xgboost",
            "lightgbm",
            "random forest",
            "neural",
            "deep learning",
            "churn",
            "clasificaci",
            "regresión",
            "predicci",
            "automl",
            "mlflow",
            "sklearn",
            "auc",
            "f1-score",
        ],
        "ml_pipeline",
    ),
    # dev_pipeline keywords
    (
        [
            "implementa",
            "desarrolla",
            "endpoint",
            "api",
            "fastapi",
            "refactoriza",
            "código",
            "test",
            "pytest",
            "pr ",
            "pull request",
            "arquitectura de software",
            "módulo",
            "clase",
            "función",
            "refactor",
            "bug",
            "fix",
            "deploy",
            "ci/cd",
            "microservicio",
            "servicio rest",
        ],
        "dev_pipeline",
    ),
    # data_analyst (default for analytical queries)
    (
        [
            "analiza",
            "query",
            "sql",
            "dataframe",
            "dashboard",
            "kpi",
            "métrica",
            "usuarios activos",
            "ventas",
            "ranking",
            "segmenta",
            "calcula el",
            "¿cuántos",
            "reporte",
            "estadístic",
            "distribución",
            "histograma",
            "correlación",
            "agrupado",
            "suma",
            "promedio",
            "media",
            "mediana",
        ],
        "data_analyst",
    ),
]


def simulate_domain_supervisor(request: str) -> str:
    """
    Simula la decisión de enrutamiento del analysis_supervisor.

    En producción, este paso llama al LLM con el system prompt del
    domain_supervisor. Aquí usamos matching por keywords para
    reproducir el comportamiento sin API key.

    Returns:
        Nombre del agente hoja: data_analyst | ml_pipeline |
        dev_pipeline | financial_analyst
    """
    lower = request.lower()
    scores: dict[str, int] = {
        "data_analyst": 0,
        "ml_pipeline": 0,
        "dev_pipeline": 0,
        "financial_analyst": 0,
    }
    for keywords, agent in _ROUTING_RULES:
        for kw in keywords:
            if kw in lower:
                scores[agent] += 1

    best = max(scores, key=lambda a: scores[a])
    return best if scores[best] > 0 else "data_analyst"


def run_leaf_agent(agent: str, task: AnalyticsTask) -> AgentResult:
    """Despacha la tarea al agente hoja correcto."""
    stubs = {
        "data_analyst": data_analyst_stub,
        "ml_pipeline": ml_pipeline_stub,
        "dev_pipeline": dev_pipeline_stub,
        "financial_analyst": financial_analyst_stub,
    }
    return stubs[agent](task)


def print_task_header(task: AnalyticsTask) -> None:
    AGENT_COLORS[task.domain]
    print(f"\n{'░' * 64}")
    print(f"  {BOLD}{task.id}{RESET}  —  Complejidad: {task.complexity}")
    print(f"  {DIM}{textwrap.shorten(task.request, 70)}{RESET}")
    print(f"{'░' * 64}")


def print_routing_step(from_node: str, to_node: str, reason: str = "") -> None:
    arrow = f"  {DIM}{'─' * 3}►{RESET}"
    print(f"\n  {BOLD}[analysis_supervisor]{RESET}")
    print(f"  enruta: {BOLD}{from_node}{RESET} {arrow} {BOLD}{to_node}{RESET}")
    if reason:
        print(f"  razón:  {DIM}{reason}{RESET}")


def print_agent_result(result: AgentResult) -> None:
    color = AGENT_COLORS[result.agent]
    icon = AGENT_ICONS[result.agent]
    print(f"\n  {icon} {color}{BOLD}[{result.agent}]{RESET}")
    # Wrap summary at 60 chars
    summary_lines = textwrap.wrap(result.summary, 58)
    for line in summary_lines:
        print(f"     {line}")
    if result.artifacts:
        print(
            f"  {DIM}  Artefactos: {', '.join(result.artifacts[:3])}"
            f"{'…' if len(result.artifacts) > 3 else ''}{RESET}"
        )
    if result.metrics:
        m_str = "  ".join(f"{k}={v}" for k, v in list(result.metrics.items())[:3])
        print(f"  {DIM}  Métricas:   {m_str}{RESET}")
    print(f"\n  {DIM}[analysis_supervisor]{RESET} {DIM}← retorna del agente hoja → END{RESET}")


# ──────────────────────────────────────────────────────────────────────────────
# DEMO 1 — Simulación completa del orquestador
# ──────────────────────────────────────────────────────────────────────────────


def demo_simulation() -> None:
    """Ejecuta el pipeline completo de orquestación para las 6 tareas."""
    print(f"\n{'═' * 64}")
    print(f"  {BOLD}DEMO 1 — Simulación del analysis_orchestrator{RESET}")
    print("  6 tareas analíticas → enrutamiento jerárquico determinístico")
    print(f"{'═' * 64}")

    routing_log: list[tuple[str, str, str]] = []

    for task in TASKS:
        print_task_header(task)

        # Paso 1: analysis_supervisor decide el agente hoja
        routed_to = simulate_domain_supervisor(task.request)
        correct = routed_to == task.domain
        verdict = (
            f"{GREEN}✓ correcto{RESET}" if correct else f"{RED}✗ esperado: {task.domain}{RESET}"
        )

        print_routing_step(
            "analysis_orchestrator",
            routed_to,
            reason=f"Clasificado como dominio '{routed_to}' por keywords",
        )
        print(f"  {DIM}Routing: {verdict}{RESET}")

        # Paso 2: el agente hoja ejecuta la tarea
        result = run_leaf_agent(routed_to, task)
        print_agent_result(result)
        routing_log.append((task.id, task.domain, routed_to))

    # Resumen de routing
    print(f"\n\n{'═' * 64}")
    print(f"  {BOLD}Resumen de enrutamiento{RESET}")
    print(f"{'═' * 64}")
    print(f"  {'ID':<10} {'Esperado':<22} {'Enrutado':<22} {'OK'}")
    print(f"  {'─' * 9} {'─' * 21} {'─' * 21} {'─' * 4}")
    correct_count = 0
    for tid, expected, routed in routing_log:
        ok = expected == routed
        if ok:
            correct_count += 1
        mark = f"{GREEN}✓{RESET}" if ok else f"{RED}✗{RESET}"
        color_e = AGENT_COLORS[expected]
        color_r = AGENT_COLORS[routed]
        print(f"  {tid:<10} {color_e}{expected:<22}{RESET} {color_r}{routed:<22}{RESET} {mark}")

    accuracy = correct_count / len(routing_log) * 100
    print(
        f"\n  Precisión de routing: {BOLD}{accuracy:.0f}%{RESET} ({correct_count}/{len(routing_log)})"
    )
    print(f"{'═' * 64}")


# ──────────────────────────────────────────────────────────────────────────────
# DEMO 2 — Comparativa jerárquico vs plano
# ──────────────────────────────────────────────────────────────────────────────


def demo_comparison() -> None:
    """
    Muestra la diferencia entre el modo jerárquico y el modo plano.

    Modo plano:  root_supervisor → leaf_agent (1 nivel, root conoce ~12+ agentes)
    Modo jerárquico: root_supervisor → analysis_orchestrator → leaf_agent
                     (2 niveles, cada supervisor conoce ~3-4 agentes)
    """
    print(f"\n{'═' * 64}")
    print(f"  {BOLD}DEMO 2 — Jerárquico vs Plano{RESET}")
    print(f"{'═' * 64}")

    print(f"""
  {BOLD}Modo PLANO{RESET} (PRISMAL_HIERARCHICAL_MODE=false):

    root_supervisor conoce 12+ agentes:
    ┌────────────────────────────────────────────────┐
    │ researcher, rag_agent, cua_agent,               │
    │ coder, codeact_agent, planner, file_manager,    │
    │ skill_manager, data_analyst, dev_pipeline,      │
    │ ml_pipeline, financial_analyst, …               │
    └────────────────────────────────────────────────┘
    Problema: contexto de routing muy largo → menor precisión

  {BOLD}Modo JERÁRQUICO{RESET} (PRISMAL_HIERARCHICAL_MODE=true):

    root_supervisor conoce sólo 3 orquestadores:
    ┌─────────────────────────────────────────────┐
    │ research_orchestrator                        │
    │ engineering_orchestrator                     │
    │ analysis_orchestrator  ◄── esta demo        │
    └─────────────────────────────────────────────┘
         │
         └─► analysis_supervisor conoce sólo 4 hojas:
             ┌─────────────────────┐
             │ data_analyst        │ ← SQL/DataFrame/charts
             │ ml_pipeline         │ ← AutoML/entrenamiento
             │ dev_pipeline        │ ← PO→Architect→Dev→QA
             │ financial_analyst   │ ← mercados/técnico/riesgo
             └─────────────────────┘

  Ventajas del modo jerárquico:
    • Prompts de routing más cortos (~4 opciones vs ~12)
    • Mayor precisión de routing (dominios bien definidos)
    • Escalabilidad: añadir hojas no impacta al root
    • Iteration caps por dominio (8) evitan bucles locales
    • Contadores de iteración aislados por dominio
""")

    # Ejemplo concreto de routing para TASK-04
    task = TASKS[3]  # financial_analyst
    print(f"  {BOLD}Ejemplo concreto — {task.id}:{RESET}")
    print(f"  Request: «{textwrap.shorten(task.request, 60)}»")
    print("""
  Modo plano:
    root_supervisor → financial_analyst  (1 salto, prompt largo)

  Modo jerárquico:
    root_supervisor → analysis_orchestrator  [routing nivel 1]
         analysis_supervisor → financial_analyst  [routing nivel 2]
                  financial_analyst ejecuta la tarea
         analysis_supervisor ← retorna agente
    root_supervisor ← retorna orquestador

  Overhead: +1 llamada LLM para el routing intermedio
  Beneficio: root_supervisor con contexto mínimo → mejor precisión
  Configuración: PRISMAL_HIERARCHICAL_MODE=true en .env
""")
    print(f"{'═' * 64}")


# ──────────────────────────────────────────────────────────────────────────────
# DEMO 3 — LangGraph real con stubs y MemorySaver
# ──────────────────────────────────────────────────────────────────────────────


async def demo_real_langgraph() -> None:
    """
    Construye el analysis_orchestrator con nodos stub en lugar de
    los pipelines reales, y lo ejecuta con LangGraph + MemorySaver.

    Usa _make_definition() directamente para inyectar stubs de los
    tres pipeline subgraphs (dev_pipeline, ml_pipeline, financial_analyst).
    El data_analyst_node se reemplaza también para evitar llamadas LLM.
    """
    try:
        from typing import Annotated, TypedDict

        from langchain_core.messages import AIMessage, HumanMessage
        from langgraph.checkpoint.memory import MemorySaver

        from prismal.agents.domain_supervisor import make_domain_supervisor
        from prismal.agents.subgraphs.analysis_orchestrator.builder import (
            ANALYSIS_AGENTS,
            _analysis_router,
        )
        from prismal.agents.subgraphs.factory import SubgraphFactory
        from prismal.agents.subgraphs.registry import SubgraphDefinition
        from prismal.langgraph import END, StateGraph, add_messages
    except ImportError as e:
        print(f"\n  ⚠  Dependencia no disponible: {e}")
        print("     Instala con: uv pip install -e '.[dev,all]'")
        return

    print(f"\n{'═' * 64}")
    print(f"  {BOLD}DEMO 3 — LangGraph real con stubs{RESET}")
    print("  Usa _make_definition() + MemorySaver, sin LLM real")
    print(f"{'═' * 64}")

    # ── Definir estado reducido para el demo ──────────────────────────────────
    class DemoState(TypedDict, total=False):
        messages: Annotated[list, add_messages]
        current_agent: str
        next_agent: str | None
        metadata: dict
        session_id: str

    # ── Stubs para los agentes hoja ───────────────────────────────────────────
    def _make_stub(agent_name: str, canned_response: str):
        """Crea un nodo stub que devuelve una respuesta predefinida."""

        async def stub_node(state: DemoState) -> dict:
            print(f"  [{agent_name}] ejecutando tarea…")
            return {
                "messages": [AIMessage(content=canned_response, name=agent_name)],
                "current_agent": agent_name,
            }

        stub_node.__name__ = agent_name
        return stub_node

    data_analyst_stub_node = _make_stub(
        "data_analyst",
        "Análisis completado: Top 5 regiones por crecimiento YoY identificadas. "
        "Norte +34%, Sur +28%. DataFrame exportado como sales_q3_2025_regional.parquet.",
    )
    ml_pipeline_stub_node = _make_stub(
        "ml_pipeline",
        "Pipeline ML completado: AUC-ROC=0.891 (LightGBM). "
        "Modelo exportado a MLflow run_id=a3f9c12e.",
    )
    dev_pipeline_stub_node = _make_stub(
        "dev_pipeline",
        "Endpoint /api/v2/reports implementado. "
        "28 tests en verde. Review score=0.91. PR #247 creado.",
    )
    financial_analyst_stub_node = _make_stub(
        "financial_analyst",
        "NVDA: BUY (score=0.78). TSLA: HOLD (score=0.51). "
        "Portfolio VaR 95%=3.2%. Reporte generado.",
    )

    # ── Supervisor de dominio con routing determinístico (sin LLM) ────────────
    # Para el demo, overrideamos el supervisor con uno que usa keywords.
    async def demo_analysis_supervisor(state: DemoState) -> dict:
        """Versión demo del analysis_supervisor: usa keywords, no LLM."""
        metadata = dict(state.get("metadata", {}))
        slot = dict(metadata.get("domain_analysis", {}))
        iteration = int(slot.get("iteration_count", 0))

        # Loop breaker: si el último mensaje es de un agente hoja, END
        msgs = list(state.get("messages", []))
        if msgs:
            last = msgs[-1]
            last_agent = state.get("current_agent", "")
            if getattr(last, "type", "") == "ai" and last_agent != "analysis_supervisor":
                slot["iteration_count"] = iteration + 1
                metadata["domain_analysis"] = slot
                print(
                    f"  [analysis_supervisor] loop-breaker → END (agente {last_agent} ya respondió)"
                )
                return {
                    "current_agent": "analysis_supervisor",
                    "next_agent": None,
                    "metadata": metadata,
                }

        # Routing por keywords
        human_msgs = [m for m in msgs if getattr(m, "type", "") == "human"]
        request = human_msgs[-1].content if human_msgs else ""
        routed = simulate_domain_supervisor(request)
        print(f"  [analysis_supervisor] routing → {AGENT_COLORS[routed]}{BOLD}{routed}{RESET}")

        slot["iteration_count"] = iteration + 1
        metadata["domain_analysis"] = slot
        return {"current_agent": "analysis_supervisor", "next_agent": routed, "metadata": metadata}

    # ── Construir el grafo ────────────────────────────────────────────────────
    builder = StateGraph(DemoState)
    builder.add_node("analysis_supervisor", demo_analysis_supervisor)
    builder.add_node("data_analyst", data_analyst_stub_node)
    builder.add_node("ml_pipeline", ml_pipeline_stub_node)
    builder.add_node("dev_pipeline", dev_pipeline_stub_node)
    builder.add_node("financial_analyst", financial_analyst_stub_node)

    builder.set_entry_point("analysis_supervisor")
    builder.add_conditional_edges("analysis_supervisor", _analysis_router)
    for leaf in ANALYSIS_AGENTS:
        builder.add_edge(leaf, "analysis_supervisor")

    checkpointer = MemorySaver()
    graph = builder.compile(checkpointer=checkpointer)

    # ── Ejecutar 3 tareas ─────────────────────────────────────────────────────
    demo_tasks = [TASKS[0], TASKS[1], TASKS[2]]  # data_analyst, ml, dev
    for i, task in enumerate(demo_tasks, 1):
        config = {"configurable": {"thread_id": f"analysis-demo-{task.id}"}}
        initial: DemoState = {
            "messages": [HumanMessage(content=task.request)],
            "current_agent": "",
            "next_agent": None,
            "metadata": {},
            "session_id": f"demo-{i}",
        }
        print(f"\n  {BOLD}Tarea {i}/{len(demo_tasks)}: {task.id}{RESET}")
        print(f"  {DIM}{textwrap.shorten(task.request, 60)}{RESET}")

        result = await graph.ainvoke(initial, config)

        final_msgs = result.get("messages", [])
        ai_msgs = [m for m in final_msgs if getattr(m, "type", "") == "ai"]
        if ai_msgs:
            print(f"  {GREEN}✓{RESET} Respuesta del agente:")
            print(f"  {DIM}{textwrap.shorten(ai_msgs[-1].content, 70)}{RESET}")
        meta = result.get("metadata", {})
        iters = meta.get("domain_analysis", {}).get("iteration_count", "?")
        print(f"  Iteraciones del supervisor de dominio: {iters}")

    print(f"\n{'═' * 64}")
    print("  Demo LangGraph completado.")
    print("  Patrones clave demostrados:")
    print("    • Loop-breaker: supervisor detecta respuesta de hoja → END")
    print("    • Contador aislado: domain_analysis.iteration_count")
    print("    • Router condicional: _analysis_router(state) → str")
    print(f"{'═' * 64}")


# ──────────────────────────────────────────────────────────────────────────────
# DEMO 4 — Diagrama de la jerarquía completa de 3 niveles
# ──────────────────────────────────────────────────────────────────────────────


def demo_hierarchy() -> None:
    """Visualiza la jerarquía completa de 3 niveles del framework."""
    print(f"\n{'═' * 64}")
    print(f"  {BOLD}DEMO 4 — Jerarquía completa de 3 niveles{RESET}")
    print("  PRISMAL_HIERARCHICAL_MODE=true")
    print(f"{'═' * 64}")

    print(f"""
  {BOLD}Nivel 0 — Root Supervisor{RESET}
  ┌──────────────────────────────────────────────────────┐
  │  root_supervisor                                      │
  │  Conoce: 3 orquestadores de dominio                  │
  │  System prompt: ~300 tokens (vs ~900 en modo plano)  │
  └───────────┬──────────────┬──────────────┬────────────┘
              │              │              │
  {BOLD}Nivel 1 — Domain Orchestrators{RESET}
  ┌───────────┴───┐  ┌───────┴──────┐  ┌───┴────────────────┐
  │ research_     │  │ engineering_ │  │ {CYAN}analysis_{RESET}        │
  │ orchestrator  │  │ orchestrator │  │ {CYAN}orchestrator{RESET}     │
  │               │  │              │  │ ← esta demo         │
  │ Cap: 8 iters  │  │ Cap: 8 iters │  │ {CYAN}Cap: 8 iters{RESET}    │
  └───────┬───────┘  └──────┬───────┘  └────────┬───────────┘
          │                 │                   │
  {BOLD}Nivel 2 — Leaf Agents{RESET}
  ┌───────┴───────┐  ┌──────┴───────┐  ┌────────┴──────────┐
  │ researcher    │  │ coder        │  │ {CYAN}data_analyst{RESET}    │
  │ rag_agent     │  │ codeact_agent│  │ {PURPLE}ml_pipeline{RESET}     │
  │ cua_agent     │  │ planner      │  │ {GREEN}dev_pipeline{RESET}    │
  │               │  │ file_manager │  │ {ORANGE}financial_analyst{RESET}│
  │               │  │ skill_manager│  │                    │
  └───────────────┘  └──────────────┘  └───────────────────┘

  {BOLD}Propiedades del analysis_orchestrator:{RESET}
    • Entry point:     analysis_supervisor (node LangGraph)
    • Condicional:     _analysis_router(state) → str
    • Agentes hoja:    {ANALYSIS_AGENTS_STR}
    • Edges de retorno: cada hoja → analysis_supervisor
    • Bypass plano:    PRISMAL_HIERARCHICAL_MODE=false
    • Checkpointer:    SQLite aislado por orquestador
    • Registro:        SubgraphRegistry (idempotente)

  {BOLD}API pública:{RESET}
    from prismal.agents.subgraphs.analysis_orchestrator import (
        get_compiled_analysis_orchestrator,   # retorna CompiledStateGraph
        register_analysis_orchestrator,       # registra en SubgraphRegistry
    )

    # Con stubs para testing:
    graph = await get_compiled_analysis_orchestrator(
        dev_pipeline_graph=my_dev_stub,
        ml_pipeline_graph=my_ml_stub,
        financial_analyst_graph=my_fin_stub,
    )

    # Producción (lazy-builds los 3 pipelines):
    graph = await get_compiled_analysis_orchestrator()
""")

    # Mostrar routing real para cada tarea
    print(f"  {BOLD}Routing del dataset BI Center:{RESET}")
    print(f"  {'ID':<10} {'Agente hoja ruteado':<22} {'Complejidad'}")
    print(f"  {'─' * 9} {'─' * 21} {'─' * 10}")
    for task in TASKS:
        routed = simulate_domain_supervisor(task.request)
        color = AGENT_COLORS[routed]
        print(f"  {task.id:<10} {color}{routed:<22}{RESET} {task.complexity}")
    print(f"{'═' * 64}")


ANALYSIS_AGENTS_STR = "data_analyst, ml_pipeline, dev_pipeline, financial_analyst"


# ──────────────────────────────────────────────────────────────────────────────
# main
# ──────────────────────────────────────────────────────────────────────────────


async def main() -> None:
    print(f"""
{"═" * 64}
  {BOLD}Analysis Orchestrator — Orquestador Jerárquico de Dominio{RESET}
  Dataset: Business Intelligence Center (6 tareas analíticas)
  Agentes: data_analyst · ml_pipeline · dev_pipeline · financial_analyst
{"═" * 64}
  Modos disponibles:
    1. Simulación       — orquestación completa (sin LLM)
    2. Comparativa      — jerárquico vs plano + diagrama
    3. LangGraph real   — stubs + MemorySaver + grafo compilado
    4. Jerarquía        — mapa completo de 3 niveles + API
    5. Todos            — ejecuta 1 + 2 + 3 + 4
{"─" * 64}""")

    choice = input("  Selecciona modo [1-5] (Enter = 1): ").strip() or "1"

    if choice == "1":
        demo_simulation()
    elif choice == "2":
        demo_comparison()
    elif choice == "3":
        await demo_real_langgraph()
    elif choice == "4":
        demo_hierarchy()
    elif choice == "5":
        demo_simulation()
        demo_comparison()
        await demo_real_langgraph()
        demo_hierarchy()
    else:
        print("  Opción no válida — ejecutando simulación por defecto.")
        demo_simulation()

    print(f"""
  {DIM}Configuración en producción:
    PRISMAL_HIERARCHICAL_MODE=true   → activa los 3 orquestadores
    PRISMAL_HIERARCHICAL_MODE=false  → modo plano (default)
    PRISMAL_HITL_ENABLED=true        → aprobación humana en dev_pipeline{RESET}
""")


if __name__ == "__main__":
    asyncio.run(main())

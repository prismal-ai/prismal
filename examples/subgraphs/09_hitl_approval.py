"""
Human-in-the-Loop (HITL) Approval — Pipeline de Revisión de Decisiones Críticas
=================================================================================

Dataset:  AI Governance Decisions (custom)
          5 propuestas de despliegue de IA con diferentes niveles de riesgo
          (análogo a un comité de ética/seguridad que aprueba cambios en producción)

Patrón:   HITL con LangGraph interrupt() + Command(resume=...)
          • proposal_writer  → redacta la propuesta de cambio
          • risk_assessor    → evalúa riesgo (LOW / MEDIUM / HIGH)
          • approval_seed    → siembra metadatos HITL en estado
          • human_approval   → interrupt() — pausa y espera decisión humana
          • hitl_gate        → enruta según acción (approve / reject / request_changes)
          • finalizer        → ejecuta la propuesta aprobada
          • revision_handler → incorpora cambios solicitados y vuelve a someter

Modos:
  1. demo_interactive()   — loop interactivo en consola (pide decisión al usuario)
  2. demo_batch()         — simula una secuencia de decisiones automáticas
  3. demo_bypass()        — CI/CD mode: hitl_enabled=False → aprobación automática
  4. demo_real_langgraph()— grafo LangGraph real con MemorySaver e interrupt()

Uso:
  uv run python examples/subgraphs/09_hitl_approval.py
"""

from __future__ import annotations

import asyncio
import textwrap
from dataclasses import dataclass, field
from typing import Any

# ──────────────────────────────────────────────────────────────────────────────
# Dataset — propuestas de cambio en sistemas de IA
# ──────────────────────────────────────────────────────────────────────────────


@dataclass
class Proposal:
    id: str
    title: str
    description: str
    risk_level: str  # LOW | MEDIUM | HIGH
    risk_reasons: list[str]
    expected_impact: str
    rollback_plan: str


PROPOSALS: list[Proposal] = [
    Proposal(
        id="PROP-001",
        title="Activar modelo GPT-4o en producción para soporte al cliente",
        description="Reemplazar el modelo actual (GPT-3.5) por GPT-4o en el chatbot "
        "de soporte. Afecta a ~50 000 usuarios/día.",
        risk_level="HIGH",
        risk_reasons=[
            "Cambio en comportamiento de respuestas no completamente evaluado",
            "Mayor costo por token (×3) puede afectar el presupuesto",
            "Sin canary release planificado — cambio directo al 100% del tráfico",
        ],
        expected_impact="Mejora del 23% en CSAT; reducción del 18% en escalaciones",
        rollback_plan="Revertir variable de entorno MODEL_ID en < 5 min via feature flag",
    ),
    Proposal(
        id="PROP-002",
        title="Habilitar memoria a largo plazo para usuarios premium",
        description="Almacenar resúmenes de conversación en ChromaDB persistente "
        "(PII incluida). Solo usuarios opt-in con consentimiento explícito.",
        risk_level="HIGH",
        risk_reasons=[
            "Almacenamiento de PII requiere revisión legal (GDPR, LOPD)",
            "Vectores de ataque nuevos: extracción de memoria vía prompt injection",
            "Sin política de retención definida todavía",
        ],
        expected_impact="Continuidad de contexto entre sesiones; NPS +12 pts estimados",
        rollback_plan="Desactivar flag LONG_TERM_MEMORY_ENABLED; datos se borran en 30 días",
    ),
    Proposal(
        id="PROP-003",
        title="Desplegar agente autónomo de revisión de PRs",
        description="Integrar prismal code_review subgraph con GitHub Actions para "
        "revisar PRs automáticamente y hacer merge si score >= 0.95.",
        risk_level="MEDIUM",
        risk_reasons=[
            "Auto-merge sin revisión humana en casos edge",
            "Posible falso positivo en código de seguridad crítica",
        ],
        expected_impact="Reducción del 40% en tiempo de revisión; detección de bugs +30%",
        rollback_plan="Deshabilitar GitHub App; revertir a revisión manual en < 2 min",
    ),
    Proposal(
        id="PROP-004",
        title="Activar RAG sobre base de conocimiento interna",
        description="Indexar 12 000 documentos internos en ChromaDB y conectarlos "
        "al asistente. Documentos marcados como CONFIDENTIAL incluidos.",
        risk_level="MEDIUM",
        risk_reasons=[
            "Documentos CONFIDENTIAL podrían filtrarse en respuestas",
            "Sin control de acceso por rol implementado aún",
        ],
        expected_impact="Reducción del 60% en consultas manuales al equipo de soporte",
        rollback_plan="Remover colección de ChromaDB; rollback en < 10 min",
    ),
    Proposal(
        id="PROP-005",
        title="Actualizar sistema de prompts de safety a v2.1",
        description="Actualizar SecurePromptBuilder y guardrails con nuevas reglas "
        "de seguridad. Mejora detección de jailbreak del 71% al 89%.",
        risk_level="LOW",
        risk_reasons=[
            "Posible aumento de falsos positivos en queries legítimas (~2%)",
        ],
        expected_impact="Reducción de jailbreaks exitosos del 29% al 11%",
        rollback_plan="Git revert del PR de prompts en < 1 min",
    ),
]

# ──────────────────────────────────────────────────────────────────────────────
# Simulación de los nodos del pipeline (sin LLM real)
# ──────────────────────────────────────────────────────────────────────────────

RISK_COLORS = {"HIGH": "\033[91m", "MEDIUM": "\033[93m", "LOW": "\033[92m"}
RESET = "\033[0m"
BOLD = "\033[1m"
CYAN = "\033[96m"
DIM = "\033[2m"


def _risk_badge(level: str) -> str:
    color = RISK_COLORS.get(level, "")
    return f"{color}{BOLD}[{level}]{RESET}"


def proposal_writer(proposal: Proposal) -> dict[str, Any]:
    """Nodo 1 — Redacta el artefacto de propuesta para revisión."""
    artifact = {
        "id": proposal.id,
        "title": proposal.title,
        "description": proposal.description,
        "expected_impact": proposal.expected_impact,
        "rollback_plan": proposal.rollback_plan,
        "status": "DRAFT",
    }
    print(f"\n{CYAN}{'─' * 64}{RESET}")
    print(f"{BOLD}[1/5] proposal_writer{RESET}  →  {proposal.id}: {proposal.title}")
    print(f"      {DIM}{textwrap.shorten(proposal.description, 72)}{RESET}")
    return artifact


def risk_assessor(proposal: Proposal, artifact: dict[str, Any]) -> dict[str, Any]:
    """Nodo 2 — Evalúa riesgo y decide si requiere HITL."""
    risk = {
        "level": proposal.risk_level,
        "reasons": proposal.risk_reasons,
        "requires_hitl": proposal.risk_level in ("HIGH", "MEDIUM"),
    }
    badge = _risk_badge(proposal.risk_level)
    print(f"{BOLD}[2/5] risk_assessor{RESET}    →  Riesgo {badge}")
    for r in proposal.risk_reasons:
        print(f"        {DIM}• {r}{RESET}")
    return risk


def approval_seed(artifact_field: str, risk_level: str) -> dict[str, Any]:
    """Nodo 3 — Siembra metadatos HITL en el estado."""
    print(
        f"{BOLD}[3/5] approval_seed{RESET}    →  artifact_field={artifact_field!r}  risk={_risk_badge(risk_level)}"
    )
    return {
        "_hitl_artifact_field": artifact_field,
        "_hitl_risk_level": risk_level,
    }


def finalizer(proposal: Proposal, decision: str, modifications: dict[str, Any]) -> None:
    """Nodo final (approve path) — Ejecuta la propuesta aprobada."""
    title = modifications.get("title", proposal.title) or proposal.title
    print(f"\n{BOLD}[5/5] finalizer{RESET}        →  ✅  {BOLD}APROBADO y EJECUTADO{RESET}")
    print(f"      Propuesta: {title}")
    if modifications:
        print(f"      Modificaciones incorporadas: {list(modifications.keys())}")
    print("      Estado final: DEPLOYED")


def revision_handler(proposal: Proposal, modifications: dict[str, Any]) -> Proposal:
    """Nodo rechazo/cambios — Incorpora feedback y actualiza propuesta."""
    print(f"\n{BOLD}[rev] revision_handler{RESET} →  🔄  Incorporando feedback humano…")
    updated = Proposal(
        id=proposal.id,
        title=modifications.get("title", proposal.title),
        description=modifications.get("description", proposal.description),
        risk_level=proposal.risk_level,
        risk_reasons=proposal.risk_reasons,
        expected_impact=modifications.get("expected_impact", proposal.expected_impact),
        rollback_plan=modifications.get("rollback_plan", proposal.rollback_plan),
    )
    for k, v in modifications.items():
        print(f"      ← {k}: {v}")
    return updated


# ──────────────────────────────────────────────────────────────────────────────
# Simulador HITL — interrupt() + Command(resume=...)
# ──────────────────────────────────────────────────────────────────────────────


@dataclass
class HitlInterrupt:
    """Representa el estado suspendido en un interrupt()."""

    artifact_field: str
    artifact: dict[str, Any]
    risk_level: str
    proposal: Proposal
    iteration: int = 0


@dataclass
class HitlDecision:
    action: str  # approve | reject | request_changes
    modifications: dict[str, Any] = field(default_factory=dict)
    feedback: str = ""


def _print_interrupt_payload(it: HitlInterrupt) -> None:
    """Muestra el payload del interrupt al revisor humano."""
    badge = _risk_badge(it.risk_level)
    print(f"\n{'═' * 64}")
    print(f"  ⏸  INTERRUPT — Aprobación requerida  {badge}  (iter {it.iteration + 1})")
    print(f"{'═' * 64}")
    print(f"  {BOLD}Propuesta:{RESET} {it.artifact.get('id')} — {it.artifact.get('title')}")
    print(f"  {BOLD}Impacto:{RESET}   {it.artifact.get('expected_impact')}")
    print(f"  {BOLD}Rollback:{RESET}  {it.artifact.get('rollback_plan')}")
    print(f"  {BOLD}Razones de riesgo:{RESET}")
    for r in it.proposal.risk_reasons:
        print(f"    • {r}")
    print(f"{'─' * 64}")
    print(
        f"  Opciones válidas:  {BOLD}approve{RESET} | {BOLD}reject{RESET} | {BOLD}request_changes{RESET}"
    )
    print(f"{'─' * 64}")


def _human_input_interactive(it: HitlInterrupt) -> HitlDecision:
    """Lee la decisión del revisor humano desde consola."""
    _print_interrupt_payload(it)
    while True:
        raw = input("  Decisión [approve/reject/request_changes]: ").strip().lower()
        if raw in ("approve", "reject", "request_changes"):
            break
        print("  ⚠  Opción inválida. Escribe: approve, reject, o request_changes")

    modifications = {}
    feedback = ""
    if raw == "request_changes":
        feedback = input("  Feedback (describe los cambios requeridos): ").strip()
        rollback = input("  Nuevo rollback_plan (Enter para mantener actual): ").strip()
        if rollback:
            modifications["rollback_plan"] = rollback
    elif raw == "reject":
        feedback = input("  Motivo del rechazo: ").strip()

    return HitlDecision(action=raw, modifications=modifications, feedback=feedback)


def _human_input_scripted(it: HitlInterrupt, script: list[HitlDecision]) -> HitlDecision:
    """Lee la decisión de un script predefinido (para demo batch)."""
    idx = min(it.iteration, len(script) - 1)
    decision = script[idx]
    _print_interrupt_payload(it)
    badge_action = {
        "approve": f"\033[92m{BOLD}APPROVE{RESET}",
        "reject": f"\033[91m{BOLD}REJECT{RESET}",
        "request_changes": f"\033[93m{BOLD}REQUEST CHANGES{RESET}",
    }.get(decision.action, decision.action)
    print(f"\n  [simulado] Decisión: {badge_action}")
    if decision.feedback:
        print(f"  [simulado] Feedback: {decision.feedback}")
    if decision.modifications:
        print(f"  [simulado] Modificaciones: {decision.modifications}")
    return decision


# ──────────────────────────────────────────────────────────────────────────────
# Pipeline orquestado (simulación completa sin LangGraph)
# ──────────────────────────────────────────────────────────────────────────────

MAX_ITERATIONS = 3


def run_hitl_pipeline(
    proposal: Proposal,
    decision_fn,  # callable(HitlInterrupt) -> HitlDecision
    bypass: bool = False,
) -> str:
    """
    Ejecuta el pipeline HITL completo para una propuesta.

    Flujo:
      proposal_writer → risk_assessor
        ├── LOW  → finalizer (sin HITL)
        └── MEDIUM/HIGH → approval_seed → [interrupt] → decision_fn()
              ├── approve           → finalizer
              ├── reject            → END (rechazado)
              └── request_changes   → revision_handler → vuelve a someter (max 3 iter)

    Args:
        proposal:    Propuesta a evaluar.
        decision_fn: Callable que recibe HitlInterrupt y devuelve HitlDecision.
        bypass:      Si True, salta el HITL (CI/CD mode).

    Returns:
        Estado final: "approved" | "rejected" | "max_iterations"
    """
    iteration = 0
    current_proposal = proposal

    while iteration < MAX_ITERATIONS:
        # ── Nodo 1: proposal_writer ──────────────────────────────────────────
        artifact = proposal_writer(current_proposal)

        # ── Nodo 2: risk_assessor ────────────────────────────────────────────
        risk = risk_assessor(current_proposal, artifact)

        # ── Bypass por configuración (CI/CD mode) ────────────────────────────
        if bypass or not risk["requires_hitl"]:
            if bypass:
                print(
                    f"{BOLD}[hitl]{RESET}           →  ⚡ BYPASS (CI/CD mode) — aprobación automática"
                )
            else:
                print(
                    f"{BOLD}[hitl]{RESET}           →  ✅ Riesgo LOW — no requiere aprobación humana"
                )
            finalizer(current_proposal, "approve", {})
            return "approved"

        # ── Nodo 3: approval_seed ────────────────────────────────────────────
        seed_meta = approval_seed(
            artifact_field="hitl_demo.proposal",
            risk_level=risk["level"],
        )

        # ── Nodo 4: human_approval (interrupt) ──────────────────────────────
        interrupt_state = HitlInterrupt(
            artifact_field=seed_meta["_hitl_artifact_field"],
            artifact={**artifact, "risk_reasons": risk["reasons"]},
            risk_level=seed_meta["_hitl_risk_level"],
            proposal=current_proposal,
            iteration=iteration,
        )

        print(f"{BOLD}[4/5] human_approval{RESET}   →  ⏸  interrupt() — esperando decisión…")

        # ── Command(resume=...) ──────────────────────────────────────────────
        decision = decision_fn(interrupt_state)

        # ── Nodo 5: hitl_gate ────────────────────────────────────────────────
        print(
            f"\n{BOLD}[hitl_gate]{RESET}          →  acción recibida: {BOLD}{decision.action}{RESET}"
        )

        if decision.action == "approve":
            finalizer(current_proposal, decision.action, decision.modifications)
            return "approved"

        if decision.action == "reject":
            print(f"\n{BOLD}[END]{RESET}              →  ❌  {BOLD}RECHAZADO{RESET}")
            if decision.feedback:
                print(f"      Motivo: {decision.feedback}")
            return "rejected"

        if decision.action == "request_changes":
            current_proposal = revision_handler(current_proposal, decision.modifications)
            iteration += 1
            if iteration >= MAX_ITERATIONS:
                print(
                    f"\n{BOLD}[hitl_gate]{RESET}  →  ⚠  Máximo de iteraciones ({MAX_ITERATIONS}) alcanzado → rechazado automáticamente"
                )
                return "max_iterations"
            print(
                f"\n  → Re-sometiendo propuesta revisada (iteración {iteration + 1}/{MAX_ITERATIONS})…"
            )
            continue

    return "max_iterations"


# ──────────────────────────────────────────────────────────────────────────────
# DEMO 1 — Interactivo (el usuario decide en consola)
# ──────────────────────────────────────────────────────────────────────────────


def demo_interactive() -> None:
    """Pipeline interactivo: el usuario actúa como revisor humano."""
    print(f"\n{'═' * 64}")
    print(f"  {BOLD}DEMO 1 — HITL Interactivo{RESET}")
    print("  Actúas como revisor humano del Comité de IA")
    print(f"{'═' * 64}")

    for i, proposal in enumerate(PROPOSALS[:3], 1):
        print(f"\n\n{'░' * 64}")
        print(f"  Propuesta {i}/3  —  Riesgo {_risk_badge(proposal.risk_level)}")
        print(f"{'░' * 64}")
        result = run_hitl_pipeline(proposal, _human_input_interactive)
        print(f"\n  → Resultado final: {BOLD}{result.upper()}{RESET}")

    print(f"\n{'═' * 64}")
    print("  Demo interactivo completado")
    print(f"{'═' * 64}")


# ──────────────────────────────────────────────────────────────────────────────
# DEMO 2 — Batch (decisiones automáticas predefinidas)
# ──────────────────────────────────────────────────────────────────────────────

# Escenarios de decisión para cada propuesta
BATCH_SCRIPTS: dict[str, list[HitlDecision]] = {
    "PROP-001": [  # HIGH risk — pide cambios primero, luego aprueba
        HitlDecision(
            action="request_changes",
            modifications={
                "rollback_plan": "Canary 5% → 25% → 100% con rollback automático si error_rate > 1%"
            },
            feedback="Implementar canary release antes de aprobación final",
        ),
        HitlDecision(action="approve"),
    ],
    "PROP-002": [  # HIGH risk — rechazado directamente
        HitlDecision(
            action="reject",
            feedback="No procede hasta completar auditoría legal de PII (GDPR). Escalar a DPO.",
        ),
    ],
    "PROP-003": [  # MEDIUM risk — aprobado con modificación
        HitlDecision(
            action="request_changes",
            modifications={
                "rollback_plan": "Deshabilitar auto-merge para archivos en security/; mantener revisión manual allí"
            },
            feedback="Excluir directorio security/ del auto-merge",
        ),
        HitlDecision(action="approve"),
    ],
    "PROP-004": [  # MEDIUM risk — aprobado directamente
        HitlDecision(action="approve"),
    ],
    "PROP-005": [  # LOW risk — no requiere HITL
        HitlDecision(action="approve"),  # nunca se invoca
    ],
}


def demo_batch() -> None:
    """Pipeline con decisiones predefinidas — muestra los 5 escenarios."""
    print(f"\n{'═' * 64}")
    print(f"  {BOLD}DEMO 2 — Batch (decisiones simuladas){RESET}")
    print("  Simula un comité de revisión con respuestas pre-programadas")
    print(f"{'═' * 64}")

    results: list[tuple[str, str, str]] = []

    for proposal in PROPOSALS:
        script = BATCH_SCRIPTS[proposal.id]

        def decision_fn(it, s=script):
            return _human_input_scripted(it, s)

        print(f"\n\n{'░' * 64}")
        print(f"  {proposal.id} — {proposal.title}")
        print(f"  Riesgo: {_risk_badge(proposal.risk_level)}")
        print(f"{'░' * 64}")

        result = run_hitl_pipeline(proposal, decision_fn)
        results.append((proposal.id, proposal.risk_level, result))
        print(f"\n  → Resultado: {BOLD}{result.upper()}{RESET}")

    # Resumen
    print(f"\n\n{'═' * 64}")
    print(f"  {BOLD}Resumen del Comité de Revisión{RESET}")
    print(f"{'═' * 64}")
    print(f"  {'ID':<12} {'Riesgo':<10} {'Resultado'}")
    print(f"  {'─' * 11} {'─' * 9} {'─' * 20}")
    for pid, risk, res in results:
        badge = _risk_badge(risk)
        result_fmt = (
            f"\033[92m{BOLD}{res.upper()}{RESET}"
            if res == "approved"
            else f"\033[91m{BOLD}{res.upper()}{RESET}"
        )
        print(f"  {pid:<12} {badge:<22} {result_fmt}")

    approved = sum(1 for _, _, r in results if r == "approved")
    print(f"\n  Aprobadas: {approved}/{len(results)}")
    print(f"{'═' * 64}")


# ──────────────────────────────────────────────────────────────────────────────
# DEMO 3 — CI/CD Bypass
# ──────────────────────────────────────────────────────────────────────────────


def demo_bypass() -> None:
    """Muestra el comportamiento de CI/CD: hitl_enabled=False → aprobación automática."""
    print(f"\n{'═' * 64}")
    print(f"  {BOLD}DEMO 3 — CI/CD Bypass (hitl_enabled=False){RESET}")
    print("  Todas las propuestas se aprueban automáticamente sin interrupt")
    print(f"{'═' * 64}")

    for proposal in PROPOSALS:
        print(f"\n  {proposal.id} — Riesgo {_risk_badge(proposal.risk_level)}")
        result = run_hitl_pipeline(
            proposal,
            decision_fn=lambda _: HitlDecision(action="approve"),  # nunca se llama
            bypass=True,
        )
        print(f"  Resultado: {BOLD}{result}{RESET}")

    print("\n  → Todas las propuestas procesadas sin intervención humana.")
    print(f"{'═' * 64}")


# ──────────────────────────────────────────────────────────────────────────────
# DEMO 4 — LangGraph real con MemorySaver e interrupt()
# ──────────────────────────────────────────────────────────────────────────────


async def demo_real_langgraph() -> None:
    """
    Grafo LangGraph real usando MemorySaver y el interrupt() nativo.

    Muestra el patrón exacto que usa prismal en producción:
      graph.invoke(state, config)           → suspende en interrupt()
      graph.invoke(Command(resume=...), config) → reanuda con decisión humana
    """
    try:
        from langgraph.checkpoint.memory import MemorySaver
        from langgraph.graph.message import MessagesState
        from langgraph.types import Command

        from prismal.agents.subgraphs.gates import (
            hitl_gate,
            human_approval_node,
            seed_hitl_metadata,
        )
        from prismal.langgraph import END, StateGraph, interrupt
    except ImportError as e:
        print(f"\n  ⚠  Dependencia no disponible: {e}")
        print("     Instala con: uv pip install -e '.[dev,all]'")
        return

    print(f"\n{'═' * 64}")
    print(f"  {BOLD}DEMO 4 — LangGraph real con MemorySaver + interrupt(){RESET}")
    print(f"{'═' * 64}")

    proposal = PROPOSALS[0]  # PROP-001 (HIGH risk)

    # ── Definir estado del grafo ──────────────────────────────────────────────
    from typing import TypedDict

    class ReviewState(TypedDict, total=False):
        proposal_id: str
        proposal_title: str
        risk_level: str
        artifact: dict
        metadata: dict
        result: str

    # ── Nodos del grafo ───────────────────────────────────────────────────────
    def _writer_node(state: ReviewState) -> ReviewState:
        print(f"\n  [writer]   Redactando propuesta {state['proposal_id']}…")
        artifact = {
            "id": state["proposal_id"],
            "title": state["proposal_title"],
            "description": "Propuesta de despliegue de GPT-4o en producción",
            "rollback_plan": "Revertir feature flag en < 5 min",
        }
        return {"artifact": artifact}

    def _risk_node(state: ReviewState) -> ReviewState:
        print(f"  [risk]     Nivel de riesgo: {state['risk_level']}")
        meta = state.get("metadata", {})
        meta["proposal_risk"] = state["risk_level"]
        return {"metadata": meta}

    # seed_hitl_metadata devuelve una función nodo
    _seed_node = seed_hitl_metadata(
        artifact_field="artifact",
        risk_level="HIGH",
    )

    def _finalizer_node(state: ReviewState) -> ReviewState:
        meta = state.get("metadata", {})
        action = meta.get("_hitl_last_action", "approved (bypass)")
        print(f"  [finalizer] ✅ Propuesta ejecutada. Acción HITL: {action}")
        return {"result": "DEPLOYED"}

    def _rejected_node(state: ReviewState) -> ReviewState:
        print("  [rejected]  ❌ Propuesta rechazada.")
        return {"result": "REJECTED"}

    # ── Construir grafo ───────────────────────────────────────────────────────
    _gate = hitl_gate(
        artifact_field="artifact",
        on_approve="finalizer",
        on_reject="rejected",
        risk_level="HIGH",
    )

    builder = StateGraph(ReviewState)
    builder.add_node("writer", _writer_node)
    builder.add_node("risk", _risk_node)
    builder.add_node("approval_seed", _seed_node)
    builder.add_node("human_approval", human_approval_node)
    builder.add_node("finalizer", _finalizer_node)
    builder.add_node("rejected", _rejected_node)

    builder.set_entry_point("writer")
    builder.add_edge("writer", "risk")
    builder.add_edge("risk", "approval_seed")
    builder.add_edge("approval_seed", "human_approval")
    builder.add_conditional_edges("human_approval", _gate)
    builder.add_edge("finalizer", END)
    builder.add_edge("rejected", END)

    checkpointer = MemorySaver()
    graph = builder.compile(checkpointer=checkpointer, interrupt_before=["human_approval"])

    # ── Primera invocación — se suspende en human_approval ────────────────────
    config = {"configurable": {"thread_id": "demo-hitl-001"}}
    initial_state: ReviewState = {
        "proposal_id": proposal.id,
        "proposal_title": proposal.title,
        "risk_level": proposal.risk_level,
        "artifact": {},
        "metadata": {},
        "result": "",
    }

    print(f"\n  Invocando grafo para {proposal.id}…")
    graph.invoke(initial_state, config)

    # Detectar que estamos en interrupt
    state_snapshot = graph.get_state(config)
    pending = state_snapshot.next
    print(f"\n  ⏸  Grafo suspendido. Próximo nodo: {pending}")
    print(f"  Artefacto en estado: {state_snapshot.values.get('artifact', {}).get('title', '?')}")

    # ── Simular decisión humana 1: request_changes ────────────────────────────
    print("\n  [humano] Decisión: request_changes — añadir canary release")
    decision_1 = {
        "action": "request_changes",
        "modifications": {"rollback_plan": "Canary 5%→25%→100% con auto-rollback"},
    }
    graph.invoke(Command(resume=decision_1), config)

    # El grafo llega a rejected (on_reject) porque request_changes → on_reject
    # En este grafo simplificado, on_reject = "rejected". En el dev_pipeline real
    # on_reject = "developer" para volver a generar.
    state2 = graph.get_state(config)
    print(f"\n  Estado tras request_changes: {state2.values.get('result', 'pendiente')}")
    print(f"  Metadata HITL: action={state2.values.get('metadata', {}).get('_hitl_last_action')}")

    # ── Segunda ejecución: approve ────────────────────────────────────────────
    config2 = {"configurable": {"thread_id": "demo-hitl-002"}}
    print("\n  ─── Segunda ejecución (thread diferente) → approve ───")
    graph.invoke(initial_state, config2)
    print("\n  [humano] Decisión: approve")
    graph.invoke(Command(resume={"action": "approve"}), config2)
    state_final = graph.get_state(config2)
    print(f"\n  Estado final: {state_final.values.get('result')}")

    print(f"\n{'═' * 64}")
    print("  Demo LangGraph real completado.")
    print("  Patrón clave:")
    print("    graph.invoke(state, config)              → suspende")
    print("    graph.invoke(Command(resume=...), config) → reanuda")
    print(f"{'═' * 64}")


# ──────────────────────────────────────────────────────────────────────────────
# Explicación del patrón HITL
# ──────────────────────────────────────────────────────────────────────────────


def print_pattern_explanation() -> None:
    """Imprime el diagrama del patrón y la guía de uso."""
    print(f"""
{"═" * 64}
  {BOLD}Patrón HITL — Human-in-the-Loop{RESET}
{"═" * 64}

  {BOLD}Flujo completo:{RESET}

    proposal_writer
         │
    risk_assessor
         │
    ┌────┴────────────────────────────────┐
    │ risk=LOW                 risk=MEDIUM/HIGH
    │                               │
    │                        approval_seed
    │                        (siembra metadatos)
    │                               │
    │                        human_approval
    │                        interrupt() ⏸
    │                               │
    │                          decision
    │                      ┌─────┬──┴────────────┐
    │                   approve  reject  request_changes
    │                      │       │           │
    finalizer          finalizer END    revision_handler
    (bypass)           (deploy)          (loop ≤ 3 iter)

  {BOLD}API del framework:{RESET}

    from prismal.agents.subgraphs.gates import (
        seed_hitl_metadata,    # nodo que siembra _hitl_artifact_field
        human_approval_node,   # nodo async con interrupt()
        hitl_gate,             # conditional edge (approve/reject/changes)
    )

    # Wiring en StateGraph:
    builder.add_node("approval_seed",  seed_hitl_metadata("my_artifact", "HIGH"))
    builder.add_node("human_approval", human_approval_node)
    builder.add_conditional_edges("human_approval",
        hitl_gate("my_artifact", on_approve="finalizer", on_reject="generator"))

  {BOLD}Ciclo de vida LangGraph:{RESET}

    # Invocación 1 — suspende en interrupt_before=["human_approval"]
    graph.invoke(state, config)

    # El revisor humano inspecciona el estado y decide:
    decision = {{"action": "approve"}}                    # o:
    decision = {{"action": "request_changes",
                "modifications": {{"rollback_plan": "..."}}}}

    # Invocación 2 — reanuda desde el checkpoint
    graph.invoke(Command(resume=decision), config)

  {BOLD}Bypass CI/CD:{RESET}

    # En .env o settings:
    PRISMAL_HITL_ENABLED=false   # → aprobación automática

    # O por código (tests):
    hitl_gate(..., bypass_condition=lambda _: True)

{"═" * 64}""")


# ──────────────────────────────────────────────────────────────────────────────
# main
# ──────────────────────────────────────────────────────────────────────────────


async def main() -> None:
    print(f"""
{"═" * 64}
  {BOLD}HITL Approval — Patrones de Revisión Humana en Agentes IA{RESET}
  Dataset: AI Governance Decisions (5 propuestas de despliegue)
  Framework: prismal / LangGraph interrupt()
{"═" * 64}
  Modos disponibles:
    1. Interactivo  — tú eres el revisor (consola)
    2. Batch        — decisiones simuladas (demo automático)
    3. Bypass       — CI/CD sin intervención humana
    4. LangGraph    — grafo real con MemorySaver + interrupt()
    5. Todos        — ejecuta demos 2, 3 y 4 en secuencia
    6. Patrón       — muestra explicación del patrón HITL
{"─" * 64}""")

    choice = input("  Selecciona modo [1-6] (Enter = 2): ").strip() or "2"

    if choice == "1":
        demo_interactive()
    elif choice == "2":
        demo_batch()
    elif choice == "3":
        demo_bypass()
    elif choice == "4":
        await demo_real_langgraph()
    elif choice == "5":
        demo_batch()
        demo_bypass()
        await demo_real_langgraph()
    elif choice == "6":
        print_pattern_explanation()
    else:
        print("  Opción no válida. Ejecutando demo batch por defecto.")
        demo_batch()

    print(f"\n  Tip: {DIM}hitl_enabled=True  → revisión humana obligatoria")
    print(f"       hitl_enabled=False → bypass automático (CI/CD mode){RESET}\n")


if __name__ == "__main__":
    asyncio.run(main())

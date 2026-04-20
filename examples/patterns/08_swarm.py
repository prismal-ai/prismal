"""
Swarm / Handoff Descentralizado — Enrutamiento de soporte al cliente
=====================================================================
Patrón: SPEC-PAT-007 / lightagent.agents.patterns.swarm

Dataset: ATIS (Airline Travel Information System) + Ticket de Soporte
  • ATIS: ~5 871 utterances clasificadas en 26 intenciones de viaje/soporte.
  • Referencia: https://huggingface.co/datasets/tuetschek/atis
  • Por qué: Swarm es ideal para sistemas de soporte donde distintos
    agentes especializados manejan diferentes tipos de consultas. ATIS
    proporciona intenciones reales de usuarios que mapean naturalmente
    a handoffs entre agentes especializados.

Descripción del patrón:
  swarm_handoff transfiere control entre agentes sin supervisor central:
  1. El agente actual decide a qué agente especializado derivar.
  2. Se registra un HandoffRecord en state["metadata"]["handoff_history"].
  3. state["next_agent"] se actualiza al agente destino.
  4. El nuevo agente puede hacer otro handoff o resolver la consulta.

Garantías:
  - Inmutabilidad: el estado de entrada no se muta.
  - Anti-bucle: self-handoff rechazado con ValueError.
  - Allow-listing: solo agentes en valid_targets son destinos válidos.
  - Audit trail: cada handoff registra timestamp, motivo y snapshot.

Uso:
    uv run python examples/patterns/08_swarm.py
"""

from __future__ import annotations

import asyncio
from typing import Any

from lightagent.agents.patterns.swarm import (
    VALID_HANDOFF_TARGETS,
    HandoffRecord,
    swarm_handoff,
)

# ── Dataset: tickets de soporte al cliente ────────────────────────────────────
# Inspirado en ATIS + Zendesk/Freshdesk support ticket categories.
SUPPORT_TICKETS = [
    {
        "id": "ST001",
        "channel": "email",
        "text": (
            "Hola, llevo 3 días esperando mi pedido #12345 y no ha llegado. "
            "La fecha estimada de entrega era ayer. ¿Pueden rastrear mi paquete?"
        ),
        "initial_agent": "researcher",  # comienza en investigador general
        "expected_path": ["researcher", "file_manager"],  # investigar → gestionar archivo
        "intent": "order_tracking",
    },
    {
        "id": "ST002",
        "channel": "chat",
        "text": (
            "Necesito ayuda para programar una consulta SQL compleja que calcule "
            "la tasa de retención de clientes por cohorte mensual en los últimos "
            "24 meses. Tengo un DataFrame de pandas con columnas: user_id, signup_date, last_order."
        ),
        "initial_agent": "researcher",
        "expected_path": ["researcher", "coder", "data_analyst"],
        "intent": "technical_data_analysis",
    },
    {
        "id": "ST003",
        "channel": "phone_transcript",
        "text": (
            "El informe de ventas del Q3 tiene un error en las columnas de Europa. "
            "Los números no cuadran con lo que tenemos en el CRM. Necesito que "
            "alguien analice los datos y genere un informe corregido."
        ),
        "initial_agent": "researcher",
        "expected_path": ["researcher", "data_analyst", "file_manager"],
        "intent": "data_analysis_report",
    },
    {
        "id": "ST004",
        "channel": "ticket",
        "text": (
            "Tenemos un bug crítico en producción. El endpoint /api/payments "
            "está devolviendo 500 errors con rate del 23% en los últimos 30 minutos. "
            "Los logs muestran: KeyError 'transaction_id' en payment_processor.py:L145"
        ),
        "initial_agent": "researcher",
        "expected_path": ["researcher", "coder", "critic"],
        "intent": "critical_bug_production",
    },
    {
        "id": "ST005",
        "channel": "slack",
        "text": (
            "¿Pueden ayudarme a planificar el sprint de Q4? Tenemos 8 features "
            "por implementar, 3 desarrolladores y 6 semanas. Necesito estimar "
            "esfuerzos y priorizar según impacto en negocio."
        ),
        "initial_agent": "researcher",
        "expected_path": ["researcher", "planner"],
        "intent": "sprint_planning",
    },
]


# ── Lógica de enrutamiento de agentes ────────────────────────────────────────

def classify_intent(text: str) -> str:
    """Clasificador simple de intención basado en keywords.

    En producción, esto sería un clasificador de intenciones con LLM
    o un modelo de clasificación de texto entrenado.

    Args:
        text: Texto del ticket de soporte.

    Returns:
        Intención clasificada.
    """
    text_lower = text.lower()

    if any(kw in text_lower for kw in ["bug", "error", "exception", "500", "crash", "traceback"]):
        return "technical_bug"
    elif any(kw in text_lower for kw in ["sql", "pandas", "dataframe", "análisis", "analizar", "informe"]):
        return "data_analysis"
    elif any(kw in text_lower for kw in ["pedido", "entrega", "paquete", "envío", "tracking"]):
        return "order_support"
    elif any(kw in text_lower for kw in ["planificar", "sprint", "priorizar", "roadmap", "features"]):
        return "planning"
    elif any(kw in text_lower for kw in ["código", "implementar", "función", "api", "endpoint"]):
        return "coding"
    else:
        return "general"


def decide_handoff(current_agent: str, intent: str, state: dict) -> tuple[str, str] | None:
    """Decide si hacer un handoff y a qué agente.

    Implementa la lógica de enrutamiento descentralizado del enjambre.

    Args:
        current_agent: Agente actualmente activo.
        intent: Intención clasificada del ticket.
        state: Estado actual del agente.

    Returns:
        Tupla (target_agent, reason) o None si no hay handoff.
    """
    handoff_history = state.get("metadata", {}).get("handoff_history", [])
    agents_visited = {h["to_agent"] for h in handoff_history}
    agents_visited.add(current_agent)

    # Tabla de enrutamiento: intent → secuencia de agentes especializados
    routing_table: dict[str, list[str]] = {
        "technical_bug": ["coder", "critic"],
        "data_analysis": ["data_analyst", "file_manager"],
        "order_support": ["file_manager"],
        "planning": ["planner"],
        "coding": ["coder"],
        "general": [],
    }

    target_sequence = routing_table.get(intent, [])

    # Buscar el primer agente no visitado en la secuencia
    for target in target_sequence:
        if target not in agents_visited and target in VALID_HANDOFF_TARGETS:
            reasons = {
                "coder": "Consulta requiere generación o análisis de código",
                "critic": "Código generado necesita revisión de calidad",
                "data_analyst": "Consulta requiere análisis de datos y estadísticas",
                "file_manager": "Necesita gestión de archivos o informes",
                "planner": "Consulta requiere planificación de tareas y sprints",
                "rag_agent": "Necesita recuperación de información de base de conocimiento",
                "researcher": "Requiere investigación y recopilación de información",
            }
            return target, reasons.get(target, f"Especialista en {target}")

    return None  # No hay más handoffs necesarios


async def process_ticket(ticket: dict) -> dict[str, Any]:
    """Procesa un ticket de soporte mediante handoffs de enjambre.

    Args:
        ticket: Ticket con texto, canal e intención esperada.

    Returns:
        Estado final con el historial completo de handoffs.
    """
    # Estado inicial del ticket
    state: dict[str, Any] = {
        "messages": [],
        "current_agent": ticket["initial_agent"],
        "metadata": {
            "ticket_id": ticket["id"],
            "channel": ticket["channel"],
            "handoff_history": [],
        },
    }

    intent = classify_intent(ticket["text"])
    current_agent = ticket["initial_agent"]
    max_hops = 5  # límite de seguridad anti-bucle

    print(f"\n[{ticket['id']}] Canal: {ticket['channel']}")
    print(f"  Intención detectada: {intent}")
    print(f"  Texto: {ticket['text'][:80]}...")
    print(f"\n  Enrutamiento:")

    hop = 0
    while hop < max_hops:
        handoff_decision = decide_handoff(current_agent, intent, state)

        if handoff_decision is None:
            print(f"    ✓ {current_agent} resuelve el ticket (no hay más handoffs)")
            break

        target_agent, reason = handoff_decision

        try:
            # Ejecutar el handoff
            state = await swarm_handoff(
                current_agent=current_agent,
                target_agent=target_agent,
                state=state,
                reason=reason,
                valid_targets=VALID_HANDOFF_TARGETS,
            )

            print(f"    {current_agent} → {target_agent}  [{reason}]")
            current_agent = state.get("next_agent", target_agent)
            hop += 1

        except Exception as exc:
            print(f"    ✗ Handoff falló: {exc}")
            break

    return state


async def main() -> None:
    print("=" * 70)
    print("  Swarm / Handoff — Dataset: ATIS + tickets de soporte")
    print("=" * 70)
    print(f"\n  Agentes válidos en el enjambre: {sorted(VALID_HANDOFF_TARGETS)}")

    all_states = []

    for ticket in SUPPORT_TICKETS:
        state = await process_ticket(ticket)
        all_states.append((ticket, state))

    # Resumen de handoffs
    print("\n" + "═" * 70)
    print("  RESUMEN DE ENRUTAMIENTO")
    print("═" * 70)
    print(f"  {'Ticket':<8} {'Canal':<20} {'Intención':<25} {'Saltos':>6}")
    print("  " + "─" * 60)

    for ticket, state in all_states:
        history = state.get("metadata", {}).get("handoff_history", [])
        intent = classify_intent(ticket["text"])
        print(
            f"  {ticket['id']:<8} {ticket['channel']:<20} "
            f"{intent:<25} {len(history):>6}"
        )

    # Análisis del audit trail
    print("\n[Audit Trail — Handoff History del último ticket]")
    last_ticket, last_state = all_states[-1]
    history = last_state.get("metadata", {}).get("handoff_history", [])
    for i, record in enumerate(history, 1):
        print(f"  {i}. {record['from_agent']} → {record['to_agent']}")
        print(f"     Motivo   : {record['reason']}")
        print(f"     Timestamp: {record['timestamp']}")

    # Demostrar self-handoff rechazado
    print("\n[Validación de seguridad — self-handoff]")
    try:
        await swarm_handoff(
            current_agent="coder",
            target_agent="coder",
            state={"metadata": {}},
            reason="test de self-handoff",
        )
    except (ValueError, Exception) as e:
        print(f"  ✓ Self-handoff correctamente rechazado: {type(e).__name__}")

    # Demostrar allow-listing
    print("\n[Validación de seguridad — target no permitido]")
    try:
        await swarm_handoff(
            current_agent="coder",
            target_agent="malicious_agent",
            state={"metadata": {}},
            reason="test de target inválido",
            valid_targets=VALID_HANDOFF_TARGETS,
        )
    except (ValueError, Exception) as e:
        print(f"  ✓ Target inválido correctamente rechazado: {type(e).__name__}")


if __name__ == "__main__":
    asyncio.run(main())

"""
Customer Service Subgraph — Atención al cliente con RAG y escalación
=====================================================================
Subgraph: lightagent.agents.subgraphs.customer_service

Dataset: ATIS + Amazon Customer Reviews (soporte simulado)
  • ATIS (Airline Travel Information System): 5 000+ queries de usuarios
    en lenguaje natural clasificadas por intención (flight, fare, ground,
    abbreviation, capacity, city, …).
  • Amazon Customer Reviews: consultas reales de soporte técnico/devoluciones.
  • Referencia: https://huggingface.co/datasets/atis y
    https://huggingface.co/datasets/amazon_reviews_multi
  • Por qué: El subgraph customer_service clasifica intenciones (faq /
    complaint / technical / other) y enruta según confianza. ATIS provee
    intenciones limpias; Amazon Reviews añade quejas y casos ambiguos que
    prueban el escalation_gate.

Descripción del subgraph Customer Service:
  classifier → faq_retrieval → escalation_gate ─┬→ response_generator
                                                 └→ ticket_creator

  Nodos:
  1. classifier        — LLM clasifica la query en faq/complaint/technical/other
  2. faq_retrieval     — RAG sobre base de conocimiento interna
  3. escalation_gate   — si confidence < threshold → ticket_creator
                         si confidence ≥ threshold → response_generator
  4. response_generator — redacta la respuesta final al cliente
  5. ticket_creator    — abre ticket de soporte y devuelve confirmación

  escalation_threshold: 0.6 (default) — por debajo → escala a ticket

Uso:
    uv run python examples/subgraphs/06_customer_service.py
"""

from __future__ import annotations

import asyncio
import random

# Importar con manejo de error
try:
    from lightagent.agents.subgraphs.customer_service.builder import (
        build_customer_service_subgraph,
        register_customer_service,
    )

    CUSTOMER_SERVICE_AVAILABLE = True
except ImportError:
    CUSTOMER_SERVICE_AVAILABLE = False

# ── Dataset: queries de soporte ───────────────────────────────────────────────
SUPPORT_QUERIES = [
    # Categoría: FAQ (deberían ir a response_generator)
    {
        "id": "CS-001",
        "query": "¿Cuál es la política de devoluciones?",
        "category": "faq",
        "expected_route": "response_generator",
        "context": "El cliente pregunta sobre plazos y condiciones de devolución.",
        "user_id": "U1001",
    },
    {
        "id": "CS-002",
        "query": "¿Cómo puedo rastrear mi pedido número 847392?",
        "category": "faq",
        "expected_route": "response_generator",
        "context": "Consulta de seguimiento estándar de envío.",
        "user_id": "U1002",
    },
    {
        "id": "CS-003",
        "query": "What are your business hours and support channels?",
        "category": "faq",
        "expected_route": "response_generator",
        "context": "Pregunta sobre horarios de atención.",
        "user_id": "U1003",
    },
    # Categoría: Technical (alta confianza → response_generator)
    {
        "id": "CS-004",
        "query": "El producto que recibí no enciende, ¿qué hago?",
        "category": "technical",
        "expected_route": "response_generator",
        "context": "Problema técnico con dispositivo electrónico recién recibido.",
        "user_id": "U1004",
    },
    {
        "id": "CS-005",
        "query": "How do I reset my password? I've been locked out.",
        "category": "technical",
        "expected_route": "response_generator",
        "context": "Acceso bloqueado a la cuenta de usuario.",
        "user_id": "U1005",
    },
    # Categoría: Complaint (baja confianza → ticket_creator)
    {
        "id": "CS-006",
        "query": "Esto es inaceptable. Llevo 3 semanas esperando mi reembolso y nadie "
        "me responde. Voy a dejar una reseña negativa en todos lados.",
        "category": "complaint",
        "expected_route": "ticket_creator",
        "context": "Cliente muy frustrado, caso urgente de escalación.",
        "user_id": "U1006",
    },
    {
        "id": "CS-007",
        "query": "Me cobraron dos veces el mismo pedido. Necesito que me devuelvan "
        "el dinero urgentemente. Esto es un fraude.",
        "category": "complaint",
        "expected_route": "ticket_creator",
        "context": "Doble cargo en cuenta bancaria, requiere intervención humana.",
        "user_id": "U1007",
    },
    # Categoría: Other/Ambiguous (confianza variable)
    {
        "id": "CS-008",
        "query": "Quiero saber si pueden hacer una excepción a la política de "
        "devoluciones para mi caso particular, ya que el regalo era para "
        "mi madre enferma y no pudo usarlo.",
        "category": "other",
        "expected_route": "ticket_creator",
        "context": "Solicitud de excepción — requiere decisión humana.",
        "user_id": "U1008",
    },
]

# ── FAQ Knowledge Base ────────────────────────────────────────────────────────
# En producción, esto estaría indexado en ChromaVectorStore.
FAQ_KB = [
    {
        "id": "faq_001",
        "question": "¿Cuál es la política de devoluciones?",
        "answer": (
            "Aceptamos devoluciones en un plazo de 30 días desde la fecha de compra. "
            "El producto debe estar en su estado original con todos los accesorios. "
            "El reembolso se procesa en 3-5 días hábiles tras recibir el artículo."
        ),
        "category": "returns",
    },
    {
        "id": "faq_002",
        "question": "¿Cómo hago seguimiento de mi pedido?",
        "answer": (
            "Puedes rastrear tu pedido en 'Mi Cuenta → Pedidos'. También recibirás "
            "un email con el número de seguimiento de la transportista una vez "
            "que el paquete sea enviado."
        ),
        "category": "shipping",
    },
    {
        "id": "faq_003",
        "question": "¿Cuáles son los horarios de atención al cliente?",
        "answer": (
            "Nuestro equipo de soporte está disponible de lunes a viernes de 9:00 a 18:00 "
            "(CET). También puedes contactarnos por email a soporte@empresa.com con "
            "respuesta en menos de 24 horas."
        ),
        "category": "support",
    },
    {
        "id": "faq_004",
        "question": "¿El dispositivo no enciende?",
        "answer": (
            "1. Asegúrate de que la batería esté completamente cargada (mínimo 2h). "
            "2. Mantén presionado el botón de encendido por 10 segundos. "
            "3. Si sigue sin funcionar, puede ser un fallo de hardware. "
            "Contacta con soporte técnico adjuntando foto del defecto."
        ),
        "category": "technical",
    },
    {
        "id": "faq_005",
        "question": "¿Cómo recuperar contraseña?",
        "answer": (
            "Ve a 'Iniciar sesión → ¿Olvidaste tu contraseña?' e introduce tu email. "
            "Recibirás un enlace de recuperación válido 24 horas. Si no lo recibes, "
            "revisa la carpeta de spam o contacta soporte@empresa.com."
        ),
        "category": "account",
    },
]


# ── Simulador del pipeline ────────────────────────────────────────────────────


def classify_intent(query: str) -> tuple[str, float]:
    """Simula el nodo classifier (LLM en modo real).

    Returns:
        (category, confidence) — category: faq/complaint/technical/other
    """
    query_lower = query.lower()

    # Señales de queja
    complaint_signals = [
        "inaceptable",
        "fraude",
        "urgente",
        "nunca",
        "reseña negativa",
        "tres semanas",
        "cobrado dos veces",
        "devuelvan",
        "escándalo",
    ]
    faq_signals = [
        "política",
        "horario",
        "rastrear",
        "seguimiento",
        "cómo puedo",
        "how do i",
        "what are",
        "business hours",
        "password",
        "reset",
    ]
    technical_signals = [
        "no enciende",
        "no funciona",
        "error",
        "fallo",
        "bloqueado",
        "locked out",
        "doesn't work",
    ]

    complaint_score = sum(1 for s in complaint_signals if s in query_lower)
    faq_score = sum(1 for s in faq_signals if s in query_lower)
    technical_score = sum(1 for s in technical_signals if s in query_lower)

    if complaint_score >= 2:
        return "complaint", 0.30  # baja confianza → escalará
    if complaint_score == 1:
        return "complaint", 0.45  # aún baja → escalará
    if technical_score >= 1:
        return "technical", 0.75
    if faq_score >= 1:
        return "faq", 0.85
    return "other", 0.40  # ambiguo → escalará


def faq_retrieval(query: str, top_k: int = 1) -> tuple[str | None, float]:
    """Simula el nodo faq_retrieval (RAG en modo real).

    Returns:
        (answer, confidence) — None si no encuentra coincidencia
    """
    query_lower = query.lower()
    best_score = 0.0
    best_answer = None

    for faq in FAQ_KB:
        # Overlap de palabras clave (proxy de similitud semántica)
        faq_text = (faq["question"] + " " + faq["answer"]).lower()
        query_words = set(query_lower.split())
        faq_words = set(faq_text.split())
        overlap = len(query_words & faq_words) / max(len(query_words), 1)

        if overlap > best_score:
            best_score = overlap
            best_answer = faq["answer"]

    # Escalar score a rango [0.3, 0.9]
    confidence = min(0.3 + best_score * 3.0, 0.9)
    return (best_answer, confidence) if best_score > 0.05 else (None, 0.0)


def generate_ticket_id(user_id: str) -> str:
    """Genera un ID de ticket de soporte."""
    ticket_num = random.randint(100000, 999999)
    return f"TKT-{ticket_num}"


def generate_response(query: str, faq_answer: str | None, category: str) -> str:
    """Simula el nodo response_generator."""
    if faq_answer:
        return (
            f"Hola, gracias por contactarnos. {faq_answer} ¿Hay algo más en lo que pueda ayudarte?"
        )
    templates = {
        "faq": "Hola, gracias por tu consulta. He revisado tu caso y te puedo confirmar que nuestro equipo te atenderá en las próximas horas.",
        "technical": "Hola, lamentamos los inconvenientes. Para resolver tu problema técnico, por favor sigue estos pasos: 1) Reinicia el dispositivo. 2) Si persiste, escríbenos a soporte@empresa.com con una foto.",
        "other": "Hola, hemos recibido tu consulta. Un agente especializado revisará tu caso y te contactará en un plazo de 24 horas.",
    }
    return templates.get(category, "Gracias por contactarnos. Un agente te atenderá pronto.")


async def run_customer_service(query_data: dict, escalation_threshold: float = 0.6) -> dict:
    """Ejecuta el pipeline de customer service para una query."""
    query = query_data["query"]
    print(f"\n[{query_data['id']}] {query_data['category'].upper()}")
    print(f"  Query: {query[:80]}{'...' if len(query) > 80 else ''}")
    print(f"  User : {query_data['user_id']}")

    if not CUSTOMER_SERVICE_AVAILABLE:
        print("  [Modo demo — subgraph simulado]")

        # Nodo 1: classifier
        category, cls_confidence = classify_intent(query)
        print("\n  ── Nodo 1: classifier ──")
        print(f"    Categoría  : {category}")
        print(f"    Confianza  : {cls_confidence:.2f}")

        # Nodo 2: faq_retrieval
        print("\n  ── Nodo 2: faq_retrieval ──")
        faq_answer, faq_confidence = faq_retrieval(query)
        overall_confidence = (cls_confidence + faq_confidence) / 2
        print(f"    FAQ encontrado  : {'Sí' if faq_answer else 'No'}")
        print(f"    Confianza RAG   : {faq_confidence:.2f}")
        print(f"    Confianza total : {overall_confidence:.2f}")
        print(f"    Umbral escalación: {escalation_threshold}")

        # Gate: escalation
        print("\n  ── Gate: escalation_gate ──")
        should_escalate = overall_confidence < escalation_threshold or category == "complaint"
        route = "ticket_creator" if should_escalate else "response_generator"
        print(f"    ¿Escalar? {'Sí' if should_escalate else 'No'}  →  {route}")

        result: dict = {
            "id": query_data["id"],
            "category": category,
            "confidence": overall_confidence,
            "route": route,
        }

        if should_escalate:
            # Nodo 5: ticket_creator
            print("\n  ── Nodo 5: ticket_creator ──")
            ticket_id = generate_ticket_id(query_data["user_id"])
            print(f"    Ticket creado : {ticket_id}")
            print(f"    Prioridad     : {'Alta' if category == 'complaint' else 'Normal'}")
            print("    Asignado a    : soporte_humano")
            result["ticket_id"] = ticket_id
            result["response"] = (
                f"Hemos registrado tu caso con número de ticket {ticket_id}. "
                f"Un agente especializado te contactará en un plazo de 2-4 horas. "
                f"Disculpa los inconvenientes."
            )
        else:
            # Nodo 4: response_generator
            print("\n  ── Nodo 4: response_generator ──")
            response = generate_response(query, faq_answer, category)
            print(f"    Respuesta generada ({len(response)} chars)")
            result["response"] = response

        print(f"\n  Respuesta: {result['response'][:100]}...")

        # Verificar si la ruta coincide con la esperada
        expected = query_data["expected_route"]
        match = "✓" if route == expected else "✗"
        print(f"  Ruta: {route} {match} (esperado: {expected})")

        return result

    # Modo real con subgraph LangGraph
    from langchain_core.messages import HumanMessage

    from lightagent.agents.state import initial_state

    await register_customer_service(escalation_threshold=escalation_threshold)
    subgraph = build_customer_service_subgraph(
        escalation_threshold=escalation_threshold,
    )

    state = initial_state()
    state["messages"] = [HumanMessage(content=query)]
    state["metadata"] = {
        "customer_service": {
            "user_id": query_data["user_id"],
            "query_id": query_data["id"],
        }
    }

    config = {"configurable": {"thread_id": f"cs_{query_data['id']}_001"}}
    final_state = await subgraph.graph.ainvoke(state, config=config)

    cs_meta = final_state.get("metadata", {}).get("customer_service", {})
    messages = final_state.get("messages", [])
    return {
        "id": query_data["id"],
        "route": cs_meta.get("route", "unknown"),
        "response": str(messages[-1].content) if messages else "",
        "ticket_id": cs_meta.get("ticket_id"),
    }


async def main() -> None:
    ESCALATION_THRESHOLD = 0.6

    print("=" * 70)
    print("  Customer Service Subgraph — Dataset: ATIS + Amazon Reviews")
    print("=" * 70)

    print("\n[Arquitectura del subgraph Customer Service]")
    print("  classifier")
    print("       ↓")
    print("  faq_retrieval  ←  RAG sobre Knowledge Base")
    print("       ↓")
    print("  escalation_gate")
    print(f"       ├── confidence ≥ {ESCALATION_THRESHOLD} → response_generator → cliente")
    print(f"       └── confidence < {ESCALATION_THRESHOLD} → ticket_creator → agente humano")

    print(f"\n[Knowledge Base: {len(FAQ_KB)} FAQs disponibles]")
    for faq in FAQ_KB:
        print(f"  [{faq['category']:10s}] {faq['question']}")

    print(f"\n[Procesando {len(SUPPORT_QUERIES)} queries de soporte]")
    results = []
    for query_data in SUPPORT_QUERIES:
        result = await run_customer_service(query_data, ESCALATION_THRESHOLD)
        results.append(result)
        print("─" * 70)

    # ── Estadísticas ──────────────────────────────────────────────────────────
    print("\n[Resumen estadístico]")

    routed_to_response = sum(1 for r in results if r["route"] == "response_generator")
    routed_to_ticket = sum(1 for r in results if r["route"] == "ticket_creator")
    correct_routes = sum(
        1
        for r, q in zip(results, SUPPORT_QUERIES, strict=False)
        if r["route"] == q["expected_route"]
    )

    print(f"  Queries procesadas    : {len(results)}")
    print(
        f"  → response_generator  : {routed_to_response} ({routed_to_response / len(results):.0%})"
    )
    print(f"  → ticket_creator      : {routed_to_ticket} ({routed_to_ticket / len(results):.0%})")
    print(
        f"  Rutas correctas       : {correct_routes}/{len(results)} "
        f"({correct_routes / len(results):.0%} routing accuracy)"
    )
    tickets = [r.get("ticket_id") for r in results if r.get("ticket_id")]
    if tickets:
        print(f"  Tickets creados       : {tickets}")

    # ── Comparativa de thresholds ─────────────────────────────────────────────
    print("\n[Impacto del escalation_threshold en el routing]")
    print(f"  {'Threshold':<12} {'→ Response':<14} {'→ Ticket':<12} {'Descripción'}")
    print("  " + "─" * 60)
    thresholds = [
        (0.3, "Casi todo auto-resuelto, poca escalación"),
        (0.6, "Balance óptimo (default)"),
        (0.8, "Escalación agresiva, más tickets humanos"),
        (1.0, "Todo escala — sin auto-respuesta"),
    ]
    for thresh, desc in thresholds:
        # Simular conteos con el threshold dado
        sim_auto = sum(
            1
            for q in SUPPORT_QUERIES
            if classify_intent(q["query"])[1] >= thresh and q["category"] != "complaint"
        )
        sim_ticket = len(SUPPORT_QUERIES) - sim_auto
        marker = "← recomendado" if thresh == 0.6 else ""
        print(f"  {thresh:<12.1f} {sim_auto:<14d} {sim_ticket:<12d} {desc} {marker}")

    print("\n[Cuándo usar Customer Service Subgraph]")
    print("  ✓ Empresas con alto volumen de consultas repetitivas (FAQ)")
    print("  ✓ Cuando hay una Knowledge Base bien curada")
    print("  ✓ Para reducir tiempo de primera respuesta (TTFR)")
    print("  ✓ Escalación inteligente a agentes humanos para casos complejos")
    print("  ✗ Sin Knowledge Base → siempre escalará (confianza RAG = 0)")


if __name__ == "__main__":
    asyncio.run(main())

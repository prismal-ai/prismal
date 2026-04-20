"""
Debate Consensus Subgraph — Síntesis de posiciones mediante debate estructurado
===============================================================================
Subgraph: lightagent.agents.subgraphs.debate_consensus

Dataset: AI Policy & Tech Ethics (debates de política tecnológica)
  • Temas: Regulación de IA, modelos de lenguaje open-source, privacidad vs
    personalización, contratación algorítmica, derechos de autor en IA generativa.
  • Referencia: Stanford Encyclopedia of Philosophy (AI Ethics), EU AI Act,
    debates del ACM FAccT conference, UNESCO Recommendation on AI (2021).
  • Por qué: Los debates de política tecnológica son ideales porque tienen
    argumentos legítimos en ambos lados (proponent/opponent), requieren síntesis
    matizada (moderator), y generan posiciones verificables por Jaccard
    agreement (consensus). Los temas de ética en IA son bien conocidos por los
    LLMs y permiten evaluar la calidad del razonamiento.

Descripción del subgraph Debate Consensus:
  proponent → opponent → moderator → consensus

  Nodos:
  1. proponent — construye el argumento más sólido A FAVOR de la tesis
  2. opponent  — construye el argumento más sólido EN CONTRA de la tesis
  3. moderator — evalúa ambas posiciones, identifica puntos de acuerdo y tensión
  4. consensus — sintetiza una posición equilibrada con score de acuerdo Jaccard

  Reutiliza primitivas de lightagent.agents.patterns.debate:
    DebatePosition, pairwise_jaccard()

Uso:
    uv run python examples/subgraphs/08_debate_consensus.py
"""

from __future__ import annotations

import asyncio

# Importar primitivas del patrón debate
try:
    from lightagent.agents.patterns.debate import DebatePosition, pairwise_jaccard
    DEBATE_PRIMITIVES_AVAILABLE = True
except ImportError:
    DEBATE_PRIMITIVES_AVAILABLE = False

# Importar el subgraph
try:
    from lightagent.agents.subgraphs.debate_consensus.builder import (
        build_debate_consensus_subgraph,
        register_debate_consensus,
    )
    DEBATE_CONSENSUS_AVAILABLE = True
except ImportError:
    DEBATE_CONSENSUS_AVAILABLE = False

# ── Dataset: temas de debate sobre política tecnológica ──────────────────────
DEBATE_TOPICS = [
    {
        "id": "DEB-001",
        "thesis": "Los gobiernos deberían regular estrictamente el desarrollo de IA mediante "
                  "licencias obligatorias y auditorías independientes antes del despliegue.",
        "domain": "AI Regulation",
        "context": (
            "Referencia: EU AI Act (2024), propuestas de GPAI governance, debates en el "
            "Congreso de EE.UU. sobre el AI Act 2024."
        ),
        "expected_agreement": "medium",  # puntos de acuerdo + desacuerdo claros
    },
    {
        "id": "DEB-002",
        "thesis": "Los modelos de lenguaje de gran escala deberían ser completamente "
                  "open-source, con pesos y datos de entrenamiento públicos.",
        "domain": "Open Source AI",
        "context": (
            "Meta Llama vs OpenAI GPT-4. Debate entre Yann LeCun y Sam Altman sobre "
            "apertura vs seguridad en LLMs (2023-2024)."
        ),
        "expected_agreement": "low",  # posiciones muy enfrentadas
    },
    {
        "id": "DEB-003",
        "thesis": "El uso de IA en decisiones de contratación laboral debería estar "
                  "prohibido hasta que se demuestren sesgos algorítmicos < 5%.",
        "domain": "Algorithmic Hiring",
        "context": (
            "Amazon desactivó su sistema de IA para contratación en 2018 por sesgos de género. "
            "NYC Local Law 144 (2023) exige auditorías de sesgo en IA de RR.HH."
        ),
        "expected_agreement": "high",  # consenso posible con condiciones
    },
    {
        "id": "DEB-004",
        "thesis": "El entrenamiento de modelos de IA generativa con obras protegidas por "
                  "copyright debería requerir licencia y compensación a los autores originales.",
        "domain": "AI Copyright",
        "context": (
            "Casos: Getty Images vs Stability AI, New York Times vs OpenAI (2023). "
            "Debate sobre fair use, transformative use, y derechos de los creadores."
        ),
        "expected_agreement": "medium",
    },
]

# ── Argumentos pre-elaborados (simula lo que generaría el LLM) ───────────────
DEBATE_ARGUMENTS = {
    "DEB-001": {
        "proponent": [
            "La IA de alta capacidad presenta riesgos sistémicos comparables a la energía nuclear — necesita oversight gubernamental.",
            "Sin regulación, la carrera armamentística en IA entre empresas sacrificará la seguridad por velocidad.",
            "Los ciudadanos tienen derecho a saber qué sistemas de IA toman decisiones que les afectan.",
            "Las auditorías independientes crean incentivos para safety-by-design en lugar de safety-as-afterthought.",
            "El EU AI Act demuestra que regulación y innovación son compatibles.",
        ],
        "opponent": [
            "Las licencias obligatorias crearían barreras de entrada que concentrarían el poder en pocas empresas ya establecidas.",
            "La innovación en IA es demasiado rápida para marcos regulatorios estáticos — quedarían obsoletos antes de implementarse.",
            "La regulación excesiva desplazará el desarrollo a jurisdicciones menos reguladas, empeorando el problema.",
            "Los gobiernos carecen de expertise técnico para auditar sistemas de IA complejos de forma significativa.",
            "La auto-regulación de la industria ha funcionado para internet — el mismo modelo puede aplicarse a IA.",
        ],
        "agreement_points": [
            "La transparencia básica sobre sistemas de IA de alto riesgo es necesaria.",
            "Algún nivel de accountability para daños causados por IA es razonable.",
        ],
        "tension_points": [
            "¿Quién define qué es 'alto riesgo'?",
            "¿Cómo evitar que la regulación favorezca a incumbentes?",
        ],
    },
    "DEB-002": {
        "proponent": [
            "El código abierto democratiza el acceso a IA, evitando la concentración de poder en pocas corporaciones.",
            "La transparencia total permite auditorías de seguridad independientes que mejoran la confianza.",
            "Los modelos open-source como Llama 2/3 han acelerado la investigación sin incidentes de seguridad mayores.",
            "El control de los datos de entrenamiento permite a organizaciones cumplir con GDPR y otras regulaciones de privacidad.",
            "La competencia en IA open-source es la mejor defensa contra el monopolio tecnológico.",
        ],
        "opponent": [
            "Publicar pesos de modelos capaces de síntesis de bioweapons o cyberataques no es reversible — el riesgo es permanente.",
            "Los actores maliciosos (estados, grupos terroristas) se benefician desproporcionadamente del acceso sin restricciones.",
            "Los datos de entrenamiento a menudo contienen PII, información propietaria o contenido ilegal — publicarlos crea nuevos problemas legales.",
            "La apertura total elimina los mecanismos de safety alignment que requieren acceso controlado.",
            "Meta y otros 'open-source' de IA retienen control sobre fine-tuning comercial — no es verdaderamente abierto.",
        ],
        "agreement_points": [
            "La transparencia sobre arquitectura y metodología de entrenamiento es beneficiosa.",
            "Los modelos de menor capacidad pueden ser open-source con menor riesgo.",
        ],
        "tension_points": [
            "¿Dónde trazar la línea de capacidad para el acceso abierto?",
            "¿Quién decide qué es 'suficientemente seguro' para publicar?",
        ],
    },
    "DEB-003": {
        "proponent": [
            "Los sistemas de IA para contratación han demostrado reproducir y amplificar sesgos de género, raza y edad.",
            "Una prohibición temporal hasta validar fairness protege a grupos históricamente discriminados.",
            "La NYC Local Law 144 ya establece un precedente viable de regulación con umbral medible.",
            "Los candidatos tienen derecho a saber si la IA los rechazó y por qué.",
            "El costo de auditoría de sesgo es negligible vs el daño a candidatos discriminados.",
        ],
        "opponent": [
            "Los procesos de contratación humanos son más sesgados que los algoritmos correctamente auditados.",
            "Una prohibición total impide mejoras iterativas — se debería exigir auditoría, no prohibición.",
            "El umbral de 5% es arbitrario y no tiene base científica consensuada.",
            "Muchas startups no pueden permitirse auditorías independientes, lo que favorece a grandes corporaciones.",
            "La IA puede detectar patrones de éxito que el criterio humano ignoraría injustamente.",
        ],
        "agreement_points": [
            "Los sistemas de contratación IA deben ser auditados para detectar sesgos antes del despliegue.",
            "Los candidatos deben tener derecho a recurrir decisiones automatizadas.",
            "La transparencia sobre el rol de la IA en el proceso es necesaria.",
        ],
        "tension_points": [
            "¿Prohibición o regulación con requisitos estrictos?",
            "¿Qué métrica de 'sesgo' usar y quién la valida?",
        ],
    },
    "DEB-004": {
        "proponent": [
            "El entrenamiento en obras protegidas sin compensación es robo intelectual en escala masiva.",
            "Los modelos generativos generan valor económico directamente derivado del trabajo creativo ajeno.",
            "Un sistema de licencias como el de la música (ASCAP/BMI) es técnicamente viable para IA.",
            "Sin compensación, la creación de contenido original se vuelve económicamente inviable.",
            "El caso NYT vs OpenAI sugiere que los tribunales respaldarán los derechos de los creadores.",
        ],
        "opponent": [
            "El aprendizaje humano también se basa en consumir obras protegidas — los modelos de IA hacen lo mismo.",
            "El 'fair use' para transformative use está bien establecido en la jurisprudencia americana.",
            "Es técnicamente inviable rastrear qué obras específicas contribuyeron a qué outputs de IA.",
            "Un sistema de licencias masivo paralizaría la investigación académica en NLP.",
            "Los modelos no 'copian' — extraen patrones estadísticos, no reproducen obras.",
        ],
        "agreement_points": [
            "Reproducir obras protegidas directamente (memorización) viola copyright.",
            "Algún mecanismo de opt-out para creadores es razonable.",
        ],
        "tension_points": [
            "¿Dónde termina el aprendizaje legítimo y empieza la infracción?",
            "¿Cómo implementar compensación a escala de billones de parámetros?",
        ],
    },
}


def simple_jaccard(text_a: str, text_b: str) -> float:
    """Calcula Jaccard similarity entre dos textos (proxy de acuerdo)."""
    words_a = set(text_a.lower().split())
    words_b = set(text_b.lower().split())
    if not words_a and not words_b:
        return 1.0
    intersection = len(words_a & words_b)
    union = len(words_a | words_b)
    return intersection / union if union > 0 else 0.0


def simulate_moderator(args: dict) -> dict:
    """Simula el nodo moderator: evalúa ambas posiciones."""
    proponent_args = args["proponent"]
    opponent_args = args["opponent"]

    # Calcular Jaccard entre posiciones (cuanto menor, más divergentes)
    pro_text = " ".join(proponent_args)
    con_text = " ".join(opponent_args)

    # Usar pairwise_jaccard si está disponible
    if DEBATE_PRIMITIVES_AVAILABLE:
        jaccard = pairwise_jaccard(proponent_args, opponent_args)
    else:
        jaccard = simple_jaccard(pro_text, con_text)

    return {
        "jaccard_agreement": jaccard,
        "agreement_points": args.get("agreement_points", []),
        "tension_points": args.get("tension_points", []),
        "proponent_strength": min(0.5 + len(proponent_args) * 0.08, 0.95),
        "opponent_strength": min(0.5 + len(opponent_args) * 0.08, 0.95),
    }


def simulate_consensus(topic: dict, moderation: dict) -> str:
    """Simula el nodo consensus: genera posición balanceada."""
    agreement_pts = moderation.get("agreement_points", [])
    tension_pts = moderation.get("tension_points", [])
    jaccard = moderation["jaccard_agreement"]

    if jaccard > 0.15:
        stance = "parcialmente de acuerdo con regulación progresiva"
    elif jaccard > 0.08:
        stance = "requiere análisis contextual caso por caso"
    else:
        stance = "polarizado, sin consenso claro — debate continúa abierto"

    consensus_text = (
        f"Tras analizar ambas posiciones, la síntesis indica que el debate está {stance}. "
        f"Existen {len(agreement_pts)} puntos de convergencia y {len(tension_pts)} tensiones no resueltas. "
        f"El score de acuerdo Jaccard ({jaccard:.3f}) refleja "
        f"{'considerable solapamiento conceptual' if jaccard > 0.1 else 'posiciones fundamentalmente divergentes'}."
    )
    return consensus_text


async def run_debate(topic: dict) -> dict:
    """Ejecuta el pipeline debate_consensus para un tema."""
    print(f"\n[{topic['id']}] {topic['domain'].upper()}")
    print(f"  Tesis: {topic['thesis'][:80]}...")
    print(f"  Contexto: {topic['context'][:80]}...")

    args = DEBATE_ARGUMENTS[topic["id"]]

    if not DEBATE_CONSENSUS_AVAILABLE:
        print("  [Modo demo — subgraph simulado]")

        # Nodo 1: proponent
        print(f"\n  ── Nodo 1: proponent ──")
        print(f"    Argumentos A FAVOR ({len(args['proponent'])}):")
        for arg in args["proponent"][:3]:
            print(f"      + {arg[:70]}")

        # Nodo 2: opponent
        print(f"\n  ── Nodo 2: opponent ──")
        print(f"    Argumentos EN CONTRA ({len(args['opponent'])}):")
        for arg in args["opponent"][:3]:
            print(f"      - {arg[:70]}")

        # Nodo 3: moderator
        print(f"\n  ── Nodo 3: moderator ──")
        moderation = simulate_moderator(args)
        jaccard = moderation["jaccard_agreement"]
        print(f"    Jaccard agreement score: {jaccard:.4f}")
        agreement_level = (
            "ALTO" if jaccard > 0.15 else
            "MEDIO" if jaccard > 0.08 else
            "BAJO"
        )
        bar = "█" * int(jaccard * 100)
        print(f"    Nivel de acuerdo      : {agreement_level}  {bar}")
        print(f"    Puntos en común ({len(moderation['agreement_points'])}):")
        for pt in moderation["agreement_points"]:
            print(f"      ≈ {pt[:70]}")
        print(f"    Tensiones no resueltas ({len(moderation['tension_points'])}):")
        for pt in moderation["tension_points"]:
            print(f"      ⚡ {pt[:70]}")

        # Nodo 4: consensus
        print(f"\n  ── Nodo 4: consensus ──")
        consensus_text = simulate_consensus(topic, moderation)
        print(f"    {consensus_text}")

        # Verificar expectativa de acuerdo
        expected = topic["expected_agreement"]
        actual_level = agreement_level.lower()
        match = "✓" if actual_level == expected or (
            expected == "medium" and actual_level in ("medio", "medium")
        ) else "~"
        print(f"\n  Acuerdo esperado: {expected} | Obtenido: {agreement_level.lower()} {match}")

        return {
            "id": topic["id"],
            "domain": topic["domain"],
            "jaccard": jaccard,
            "agreement_level": agreement_level,
            "consensus": consensus_text,
            "agreement_points": len(moderation["agreement_points"]),
            "tension_points": len(moderation["tension_points"]),
        }

    # Modo real con subgraph LangGraph
    from lightagent.agents.state import initial_state
    from langchain_core.messages import HumanMessage

    await register_debate_consensus()
    subgraph = build_debate_consensus_subgraph()

    state = initial_state()
    state["messages"] = [HumanMessage(content=(
        f"Debate sobre la siguiente tesis:\n{topic['thesis']}\n\n"
        f"Contexto: {topic['context']}"
    ))]
    state["metadata"] = {
        "debate_consensus": {
            "thesis": topic["thesis"],
            "domain": topic["domain"],
        }
    }

    config = {"configurable": {"thread_id": f"debate_{topic['id']}_001"}}
    final_state = await subgraph.graph.ainvoke(state, config=config)

    debate_meta = final_state.get("metadata", {}).get("debate_consensus", {})
    messages = final_state.get("messages", [])
    return {
        "id": topic["id"],
        "domain": topic["domain"],
        "jaccard": debate_meta.get("jaccard_score", 0.0),
        "consensus": str(messages[-1].content) if messages else "",
        "agreement_points": len(debate_meta.get("agreement_points", [])),
        "tension_points": len(debate_meta.get("tension_points", [])),
    }


async def main() -> None:
    print("=" * 70)
    print("  Debate Consensus Subgraph — Dataset: AI Policy & Tech Ethics")
    print("=" * 70)

    print("\n[Arquitectura del subgraph Debate Consensus]")
    print("  proponent  → construye el argumento MÁS SÓLIDO a favor")
    print("       ↓")
    print("  opponent   → construye el argumento MÁS SÓLIDO en contra")
    print("       ↓")
    print("  moderator  → evalúa ambas posiciones; calcula Jaccard agreement")
    print("       ↓")
    print("  consensus  → sintetiza posición balanceada con score de acuerdo")
    print()
    print("  Basado en: lightagent.agents.patterns.debate")
    print("  Score: pairwise_jaccard(pro_args, con_args) ∈ [0, 1]")
    print("    → 0.0 = posiciones totalmente opuestas")
    print("    → 1.0 = posiciones idénticas")

    print(f"\n[Debatiendo {len(DEBATE_TOPICS)} temas de política tecnológica]")
    results = []
    for topic in DEBATE_TOPICS:
        result = await run_debate(topic)
        results.append(result)
        print("─" * 70)

    # ── Estadísticas globales ─────────────────────────────────────────────────
    print("\n[Resumen estadístico — todos los debates]")
    print(f"\n  {'ID':<10} {'Dominio':<25} {'Jaccard':>8} {'Acuerdo':>8} {'Común':>6} {'Tensión':>8}")
    print("  " + "─" * 68)
    for r in results:
        print(f"  {r['id']:<10} {r['domain']:<25} {r['jaccard']:>8.4f} "
              f"{r['agreement_level']:>8} {r['agreement_points']:>6} {r['tension_points']:>8}")

    avg_jaccard = sum(r["jaccard"] for r in results) / len(results)
    print(f"\n  Jaccard promedio: {avg_jaccard:.4f}")
    print(f"  Tema más polarizado: {min(results, key=lambda r: r['jaccard'])['domain']}")
    print(f"  Tema más consensuado: {max(results, key=lambda r: r['jaccard'])['domain']}")

    # ── Comparativa: Debate Pattern vs Debate Consensus Subgraph ─────────────
    print("\n[Debate Pattern vs Debate Consensus Subgraph]")
    comparison = [
        ("debate.py (pattern)",       "N agentes", "M rondas", "Jaccard + síntesis", "Flexible"),
        ("debate_consensus (subgraph)", "2 roles fijos", "1 ronda", "Jaccard + consenso", "Structured"),
    ]
    header = f"  {'Componente':<28} {'Agentes':<12} {'Rondas':<8} {'Score':<22} {'Uso'}"
    print(header)
    print("  " + "─" * 75)
    for name, agents, rounds, score, use in comparison:
        print(f"  {name:<28} {agents:<12} {rounds:<8} {score:<22} {use}")

    print("\n[Cuándo usar Debate Consensus Subgraph]")
    use_cases = [
        ("✓", "Decisiones binarias complejas (adoptar tecnología X vs Y)"),
        ("✓", "Análisis de riesgos con múltiples perspectivas"),
        ("✓", "Generación de contenido balanceado sobre temas controvertidos"),
        ("✓", "Validación de propuestas de arquitectura o diseño"),
        ("✓", "Due diligence: pros/cons de una adquisición o partnership"),
        ("✗", "Temas con respuesta objetivamente correcta (matemáticas, hechos)"),
        ("✗", "Cuando se requieren > 2 posiciones — usar debate.py con N agentes"),
    ]
    for mark, case in use_cases:
        print(f"  {mark} {case}")

    print("\n[Integración con el patrón Debate completo]")
    print("  # Para debates más complejos con N agentes y M rondas:")
    print("  from lightagent.agents.patterns.debate import debate_round")
    print("  result = await debate_round(")
    print("      query=thesis,")
    print("      state=state,")
    print("      n_agents=5,    # proponent, opponent, + 3 especialistas")
    print("      n_rounds=3,    # rebate y contra-rebate")
    print("      roles=['economist', 'ethicist', 'engineer', 'regulator', 'citizen'],")
    print("      synthesis_strategy='moderator',")
    print("  )")
    print("  # result.agreement_score: Jaccard promedio entre todas las posiciones")


if __name__ == "__main__":
    asyncio.run(main())

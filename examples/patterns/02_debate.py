"""
Debate / Society of Mind — Ética en IA y decisiones empresariales
==================================================================
Patrón: SPEC-PAT-002 / lightagent.agents.patterns.debate

Dataset: BoolQ + preguntas éticas personalizadas
  • BoolQ: 15 942 preguntas de sí/no extraídas de Wikipedia.
  • Referencia: https://huggingface.co/datasets/google/boolq
  • Por qué: El patrón Debate genera perspectivas múltiples sobre afirmaciones
    ambiguas o controvertidas, exactamente el tipo de preguntas donde BoolQ
    y las preguntas éticas aportan valor.

Descripción del patrón:
  N agentes con roles distintos (proponente, oponente, neutral, analista)
  debaten una pregunta en M rondas. Cada ronda, los agentes ven las posiciones
  anteriores y refinan las suyas. El moderador sintetiza el consenso final.
  El acuerdo se mide con similitud Jaccard sobre los conjuntos de tokens.

Uso:
    uv run python examples/patterns/02_debate.py
"""

from __future__ import annotations

import asyncio

from lightagent.agents.patterns.debate import DebateResult, debate_round

# ── Dataset: temas de debate seleccionados ────────────────────────────────────
# Combinamos preguntas de BoolQ (controvertidas) con dilemas éticos de IA.
DEBATE_TOPICS = [
    {
        "query": (
            "¿Debería usarse inteligencia artificial para tomar decisiones "
            "de contratación de personal sin supervisión humana?"
        ),
        "category": "ética_IA",
        "n_agents": 3,
        "roles": ["proponente", "oponente", "neutral"],
        "n_rounds": 2,
    },
    {
        "query": (
            "¿Los modelos de lenguaje grande (LLMs) representan un riesgo "
            "existencial para la humanidad en los próximos 20 años?"
        ),
        "category": "riesgo_existencial",
        "n_agents": 4,
        "roles": ["optimista_tecnológico", "pesimista_existencial", "pragmático", "ético"],
        "n_rounds": 3,
    },
    {
        "query": (
            "¿Es ético que las empresas tecnológicas moneticen los datos "
            "personales de sus usuarios para entrenar modelos de IA?"
        ),
        "category": "privacidad_datos",
        "n_agents": 3,
        "roles": ["defensor_empresa", "defensor_usuario", "regulador"],
        "n_rounds": 2,
    },
    {
        "query": (
            "¿Debería el código fuente de los modelos de IA de frontera "
            "ser de código abierto para garantizar la seguridad pública?"
        ),
        "category": "open_source_IA",
        "n_agents": 3,
        "roles": ["investigador_seguridad", "directivo_empresa", "académico"],
        "n_rounds": 2,
    },
]


def print_separator(char: str = "─", width: int = 70) -> None:
    print(char * width)


def print_result(topic: dict, result: DebateResult) -> None:
    """Imprime el resultado de un debate de forma estructurada."""
    print_separator("═")
    print(f"  Categoría : {topic['category']}")
    print(f"  Pregunta  : {topic['query'][:80]}...")
    print(f"  Agentes   : {topic['n_agents']} | Rondas: {topic['n_rounds']}")
    print(f"  Estrategia: moderador")
    print_separator()

    # Mostrar posiciones por ronda
    for ronda in range(1, topic["n_rounds"] + 1):
        ronda_positions = [p for p in result.positions if p.round == ronda]
        if ronda_positions:
            print(f"\n  [Ronda {ronda}]")
            for pos in ronda_positions:
                print(f"    [{pos.role}] {pos.content[:120]}...")

    # Consenso final
    print(f"\n  [CONSENSO FINAL]")
    print(f"  {result.consensus}")

    # Métricas
    print(f"\n  Acuerdo (Jaccard): {result.agreement_score:.3f}", end="")
    if result.agreement_score > 0.6:
        print("  ✓ Alto consenso")
    elif result.agreement_score > 0.3:
        print("  ~ Consenso moderado")
    else:
        print("  ✗ Bajo consenso / alta divergencia")

    if result.dissenting_views:
        print(f"\n  Vistas disidentes ({len(result.dissenting_views)}):")
        for dv in result.dissenting_views[:2]:
            print(f"    • {dv[:100]}...")

    print(f"\n  Rondas completadas: {result.rounds_completed}")
    print_separator("─")


async def run_debate(topic: dict) -> DebateResult:
    """Ejecuta un debate para un tema dado."""
    result = await debate_round(
        query=topic["query"],
        state={},  # estado opaco — reservado para extensiones futuras
        n_agents=topic["n_agents"],
        n_rounds=topic["n_rounds"],
        roles=topic["roles"],
        synthesis_strategy="moderator",
    )
    return result


async def main() -> None:
    print("\n" + "═" * 70)
    print("  Debate / Society of Mind — Dataset: Ética en IA + BoolQ-style")
    print("═" * 70)

    results = []

    for i, topic in enumerate(DEBATE_TOPICS, 1):
        print(f"\n>>> Debate {i}/{len(DEBATE_TOPICS)}: {topic['category']}")
        result = await run_debate(topic)
        results.append((topic, result))
        print_result(topic, result)

    # Resumen comparativo
    print("\n" + "═" * 70)
    print("  RESUMEN COMPARATIVO")
    print("═" * 70)
    print(f"  {'Categoría':<30} {'Agentes':>7} {'Rondas':>6} {'Acuerdo':>8}")
    print("  " + "─" * 60)
    for topic, res in results:
        print(
            f"  {topic['category']:<30} "
            f"{topic['n_agents']:>7} "
            f"{res.rounds_completed:>6} "
            f"{res.agreement_score:>8.3f}"
        )

    # Tema con mayor acuerdo
    best = max(results, key=lambda x: x[1].agreement_score)
    worst = min(results, key=lambda x: x[1].agreement_score)
    print(f"\n  Mayor acuerdo : {best[0]['category']} ({best[1].agreement_score:.3f})")
    print(f"  Mayor disputa : {worst[0]['category']} ({worst[1].agreement_score:.3f})")


if __name__ == "__main__":
    asyncio.run(main())

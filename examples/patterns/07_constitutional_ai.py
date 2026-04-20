"""
Constitutional AI — Filtrado y revisión por principios de seguridad
====================================================================
Patrón: SPEC-PAT-003 / lightagent.agents.patterns.constitutional

Dataset: SafeNLP / AdvBench (prompts adversariales y texto a revisar)
  • AdvBench: 520 instrucciones adversariales.
  • Referencia: https://huggingface.co/datasets/walledai/AdvBench
  • Por qué: Constitutional AI fue diseñado específicamente para detectar
    y corregir violaciones de principios de seguridad, PII, y contenido
    dañino — exactamente el dominio de AdvBench y SafeNLP.

Descripción del patrón:
  Para cada principio configurado:
  1. El LLM comprueba si el output viola el principio (critique_prompt).
  2. Si hay violación → el LLM reescribe el output (revision_prompt).
  3. El ciclo revisa/comprueba se repite hasta max_revisions por principio.
  4. Si el LLM no puede cumplir el principio → max_revisions_reached=True.
  Todos los cambios se registran en el audit log.

Usa los principios por defecto:
  P001 — no_harmful_content (crítico)
  P002 — factual_accuracy (alto)
  P003 — no_pii_exposure (alto)

También muestra cómo definir principios personalizados.

Uso:
    uv run python examples/patterns/07_constitutional_ai.py
"""

from __future__ import annotations

import asyncio

from lightagent.agents.patterns.constitutional import (
    ConstitutionalFilter,
    ConstitutionalPrinciple,
    ConstitutionalResult,
)

# Importar principios por defecto si están disponibles
try:
    from lightagent.agents.patterns.constitutional import DEFAULT_PRINCIPLES
except ImportError:
    DEFAULT_PRINCIPLES = None  # se definen abajo

# ── Dataset: textos de ejemplo a evaluar ─────────────────────────────────────
# Textos representativos que combinan contenido seguro e inseguro.
TEST_OUTPUTS = [
    {
        "id": "T1",
        "category": "contenido_seguro",
        "context": "Explicación de seguridad informática",
        "text": (
            "Para proteger tu cuenta bancaria en línea, te recomiendo usar "
            "autenticación de dos factores, contraseñas fuertes únicas para "
            "cada servicio, y verificar siempre la URL del banco antes de "
            "introducir tus credenciales. Nunca compartas tu contraseña."
        ),
    },
    {
        "id": "T2",
        "category": "posible_pii",
        "context": "Respuesta de soporte al cliente",
        "text": (
            "El cliente Juan García con DNI 12345678A y tarjeta 4532-XXXX-XXXX-1234 "
            "ha realizado una compra de €150. Su dirección de entrega es Calle Mayor "
            "15, Madrid y su email es juan.garcia@example.com."
        ),
    },
    {
        "id": "T3",
        "category": "imprecision_factual",
        "context": "Artículo sobre historia de la computación",
        "text": (
            "Python fue creado por Linus Torvalds en 1995 en los laboratorios de "
            "Microsoft. Es el lenguaje de programación más antiguo del mundo, "
            "inventado antes que FORTRAN y COBOL."
        ),
    },
    {
        "id": "T4",
        "category": "contenido_sesgado",
        "context": "Consejo financiero automatizado",
        "text": (
            "Todas las personas mayores de 60 años deberían invertir solo en "
            "depósitos bancarios. Los jóvenes menores de 25 no saben manejar "
            "dinero y no deberían recibir asesoramiento financiero complejo."
        ),
    },
    {
        "id": "T5",
        "category": "mixto_complejo",
        "context": "Blog post sobre IA",
        "text": (
            "La inteligencia artificial definitivamente va a destruir todos los "
            "empleos humanos en los próximos 5 años. Los datos del experto "
            "Dr. Smith (john.smith@university.edu, tel: +1-555-0123) confirman "
            "que el 100% de los trabajos desaparecerán. Esta es una certeza "
            "científica absoluta e irrefutable."
        ),
    },
]

# ── Principios constitucionales personalizados ────────────────────────────────
CUSTOM_PRINCIPLES = [
    ConstitutionalPrinciple(
        id="P004",
        name="no_discriminatory_content",
        description="El output no debe contener generalizaciones discriminatorias por edad, género, raza u otras características protegidas.",
        critique_prompt=(
            "¿El texto contiene generalizaciones discriminatorias o estereotipos "
            "negativos basados en edad, género, raza, religión u otras características "
            "protegidas? Responde 'SÍ: [descripción]' o 'NO'."
        ),
        revision_prompt=(
            "Reescribe el texto eliminando las generalizaciones discriminatorias. "
            "Usa lenguaje inclusivo y basado en hechos, evitando estereotipos."
        ),
        severity="high",
    ),
    ConstitutionalPrinciple(
        id="P005",
        name="epistemic_humility",
        description="El output no debe presentar afirmaciones inciertas como certezas absolutas.",
        critique_prompt=(
            "¿El texto presenta afirmaciones inciertas o especulativas como hechos "
            "absolutos e irrefutables? Responde 'SÍ: [afirmaciones problemáticas]' o 'NO'."
        ),
        revision_prompt=(
            "Reescribe el texto añadiendo los calificadores epistémicos apropiados "
            "('puede', 'es probable que', 'según algunos expertos', 'existe debate sobre'). "
            "Mantén la información pero con el nivel de certeza correcto."
        ),
        severity="medium",
    ),
]


def print_result(text_sample: dict, result: ConstitutionalResult) -> None:
    """Imprime el resultado del filtro constitucional."""
    print(f"\n[{text_sample['id']}] Categoría: {text_sample['category']}")
    print(f"  Contexto: {text_sample['context']}")
    print(f"  Texto original ({len(text_sample['text'])} chars):")
    print(f"    {text_sample['text'][:150]}...")

    print(f"\n  Resultado:")
    print(f"    Todos los principios satisfechos: {result.all_principles_satisfied}")
    print(f"    Max. revisiones alcanzadas     : {result.max_revisions_reached}")
    print(f"    Revisiones aplicadas           : {len(result.revisions)}")

    if result.revisions:
        print(f"\n  Revisiones aplicadas:")
        for rev in result.revisions:
            print(f"    [{rev.principle_id}] Violación: {rev.violation_detected[:80]}...")
            print(f"    → Texto revisado: {rev.revised[:120]}...")
    else:
        print(f"  ✓ Sin revisiones necesarias")

    print(f"\n  Output final ({len(result.final_output)} chars):")
    print(f"    {result.final_output[:200]}...")
    print("─" * 70)


async def run_constitutional_filter(
    text_sample: dict,
    principles: list[ConstitutionalPrinciple] | None = None,
) -> ConstitutionalResult:
    """Aplica el filtro constitucional a un texto.

    Args:
        text_sample: Texto y metadata.
        principles: Principios a aplicar (None = DEFAULT_PRINCIPLES).

    Returns:
        ConstitutionalResult con el texto revisado y el historial de cambios.
    """
    filt = ConstitutionalFilter(
        principles=principles,
        max_revisions=3,  # máximo 3 revisiones por principio
    )

    result = await filt.apply(
        output=text_sample["text"],
        context=text_sample["context"],
    )
    return result


async def main() -> None:
    print("=" * 70)
    print("  Constitutional AI — Dataset: AdvBench + textos de evaluación")
    print("=" * 70)

    # ── Parte 1: Principios por defecto (P001, P002, P003) ────────────────
    print("\n[Parte 1: Principios por defecto]")
    print("  P001 — no_harmful_content (severity: critical)")
    print("  P002 — factual_accuracy   (severity: high)")
    print("  P003 — no_pii_exposure    (severity: high)")
    print()

    for sample in TEST_OUTPUTS[:3]:
        result = await run_constitutional_filter(sample, principles=DEFAULT_PRINCIPLES)
        print_result(sample, result)

    # ── Parte 2: Principios personalizados ────────────────────────────────
    print("\n[Parte 2: Principios personalizados (P004 + P005)]")
    print("  P004 — no_discriminatory_content (severity: high)")
    print("  P005 — epistemic_humility         (severity: medium)")
    print()

    all_principles = (DEFAULT_PRINCIPLES or []) + CUSTOM_PRINCIPLES
    for sample in TEST_OUTPUTS[3:]:
        result = await run_constitutional_filter(sample, principles=all_principles)
        print_result(sample, result)

    # ── Parte 3: Análisis estadístico ────────────────────────────────────
    print("\n[Resumen estadístico]")
    print("  Evaluando todos los textos con el conjunto completo de principios...")

    all_results = []
    for sample in TEST_OUTPUTS:
        r = await run_constitutional_filter(sample, principles=all_principles)
        all_results.append((sample, r))

    total_violations = sum(len(r.revisions) for _, r in all_results)
    fully_safe = sum(1 for _, r in all_results if r.all_principles_satisfied)

    print(f"\n  Textos evaluados        : {len(TEST_OUTPUTS)}")
    print(f"  Textos completamente seguros: {fully_safe}/{len(TEST_OUTPUTS)}")
    print(f"  Total violaciones detectadas: {total_violations}")
    print(f"  Total revisiones aplicadas  : {total_violations}")

    # Agrupar por principio
    from collections import Counter
    violations_by_principle: Counter = Counter()
    for _, r in all_results:
        for rev in r.revisions:
            violations_by_principle[rev.principle_id] += 1

    if violations_by_principle:
        print(f"\n  Violaciones por principio:")
        for pid, count in violations_by_principle.most_common():
            print(f"    {pid}: {count} violaciones")


if __name__ == "__main__":
    asyncio.run(main())

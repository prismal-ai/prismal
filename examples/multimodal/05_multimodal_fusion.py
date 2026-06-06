"""
MultimodalFusion — Combinar observaciones por modalidad (concat | moderator | moa)
====================================================================================
Componente: SPEC-MM-AGT-005 / prismal.agents.multimodal.multimodal_fusion

Dataset: VQA-style — observaciones IMG + AUDIO + TEXT sobre la misma escena
  • Inspirado en Visual Question Answering 2.0 (Goyal et al. 2017),
    donde la respuesta correcta requiere razonar sobre imagen + texto.
  • Aquí cada escena viene con 3 contribuciones (vision, audio, text);
    la fusión sintetiza una respuesta única.
  • Por qué: VQA es el benchmark canónico para fusión multimodal y
    cubre los 3 modos del componente.

Descripción del componente:
  `MultimodalFusion(strategy, moa, moderator_fn, settings).combine(
    contributions, context=…)` produce un `FusionResult(answer,
    contributions, strategy_used)` usando una de tres estrategias:

    - "concat"     : determinístico, etiqueta cada contribución por
                     `[modality · agent_id · conf=…]` y concatena.
                     Cero llamadas LLM — ideal para tests / fallback.
    - "moderator"  : una sola llamada a un LLM multimodal (vía
                     `moderator_fn` inyectable). Sintetiza un único
                     párrafo coherente.
    - "moa"        : delega en `MixtureOfAgents` — N propositores +
                     agregador. Mejor calidad, más coste.

Uso:
    uv run python examples/multimodal/05_multimodal_fusion.py
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from prismal.agents.multimodal import (
    ModalContribution,
    Modality,
    MultimodalFusion,
)

# ── Dataset: 3 escenas VQA-style ─────────────────────────────────────────────
SCENES = [
    {
        "id": "vqa_001",
        "question": "What is the dog doing on the beach?",
        "contributions": [
            ModalContribution(
                modality=Modality.IMAGE,
                content="A golden retriever running across wet sand near the shoreline.",
                agent_id="vision",
                confidence=0.92,
            ),
            ModalContribution(
                modality=Modality.AUDIO,
                content="Sound of waves crashing and faint barking.",
                agent_id="audio",
                confidence=0.78,
            ),
            ModalContribution(
                modality=Modality.TEXT,
                content="The user uploaded this photo with caption 'Max's morning run'.",
                agent_id="text",
                confidence=0.85,
            ),
        ],
    },
    {
        "id": "vqa_002",
        "question": "Why is the crowd cheering?",
        "contributions": [
            ModalContribution(
                modality=Modality.IMAGE,
                content="A soccer player celebrating with arms raised, ball in the net.",
                agent_id="vision",
                confidence=0.88,
            ),
            ModalContribution(
                modality=Modality.AUDIO,
                content="Stadium chants and a sharp referee whistle.",
                agent_id="audio",
                confidence=0.81,
            ),
            ModalContribution(
                modality=Modality.VIDEO,
                content="Player kicks ball past the goalkeeper at second 14.",
                agent_id="video",
                confidence=0.90,
            ),
        ],
    },
    {
        "id": "vqa_003",
        "question": "Is the meeting room available?",
        "contributions": [
            ModalContribution(
                modality=Modality.IMAGE,
                content="Conference room with empty chairs and no people visible.",
                agent_id="vision",
                confidence=0.94,
            ),
            ModalContribution(
                modality=Modality.TEXT,
                content="Calendar API reports no events between 10:00 and 12:00.",
                agent_id="text",
                confidence=0.99,
            ),
        ],
    },
]


# ── Fake moderator — sintetiza desde el prompt sin llamar a un LLM ──────────
async def fake_moderator(prompt: str) -> str:
    """Toma las contribuciones del prompt y produce un resumen estable."""
    # En producción esto sería: `llm.ainvoke(messages).content`
    contributions = prompt.split("\n\n")[1]  # bloque "[modality · agent · …]"
    points = [
        line.split("\n", 1)[1]
        for line in contributions.split("\n\n")
        if "\n" in line and line.startswith("[")
    ]
    return "Synthesised: " + " ".join(points)


# ── Fake MoA (duck-typed) sin LLMs ──────────────────────────────────────────
# MultimodalFusion._combine_moa sólo necesita `await moa.generate(prompt, state)`
# y `result.final_answer`. Construimos un sustituto liviano para la demo
# (el `MixtureOfAgents` real requiere model ids configurados en el provider
# registry, lo cual no procede sin claves de API).
@dataclass
class _FakeMoAResult:
    final_answer: str


class FakeMoA:
    """Sustituto duck-typed de MixtureOfAgents para la fusión `moa`."""

    def __init__(self, n_proposers: int = 3) -> None:
        self._n = n_proposers

    async def generate(self, query: str, state) -> _FakeMoAResult:
        proposals = [f"[Expert {i + 1}] view of: {query[:60]}" for i in range(self._n)]
        synth = f"MoA-synth ({self._n} proposers): integrated answer drawing from " + "; ".join(
            p.split(": ", 1)[1] for p in proposals
        )
        return _FakeMoAResult(final_answer=synth)


def make_fake_moa() -> FakeMoA:
    """3 propositores sintéticos + agregador mock."""
    return FakeMoA(n_proposers=3)


async def main() -> None:
    print("=" * 70)
    print("MultimodalFusion · 3 estrategias sobre escenas VQA-style")
    print("=" * 70)

    for scene in SCENES:
        print("\n" + "─" * 70)
        print(f"Escena {scene['id']} · {scene['question']}")
        print("─" * 70)

        for strategy in ("concat", "moderator", "moa"):
            kwargs = {}
            if strategy == "moderator":
                kwargs["moderator_fn"] = fake_moderator
            if strategy == "moa":
                kwargs["moa"] = make_fake_moa()
            fusion = MultimodalFusion(strategy=strategy, **kwargs)
            result = await fusion.combine(scene["contributions"], context=scene["question"])
            print(f"\n  [{strategy}] · strategy_used={result.strategy_used}")
            for line in result.answer.splitlines()[:6]:
                print(f"    {line}")
            if len(result.answer.splitlines()) > 6:
                print(f"    … ({len(result.answer.splitlines()) - 6} líneas más)")

    # Demo extra: error si la estrategia es desconocida
    print("\n" + "─" * 70)
    print("Validación: una estrategia desconocida levanta ValueError al construir")
    print("─" * 70)
    try:
        MultimodalFusion(strategy="ensemble")  # type: ignore[arg-type]
    except ValueError as exc:
        print(f"  ✓ ValueError: {exc}")

    print("\n" + "=" * 70)
    print("OK — fusión cubre concat (sin LLM) / moderator (1 LLM) / MoA (N+1)")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())

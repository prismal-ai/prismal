"""
ModalityRouter — Clasificación determinística de modalidad y nodo LangGraph
============================================================================
Componente: SPEC-MM-AGT-004 / prismal.agents.multimodal.modality_router

Dataset: ATIS + arXiv + ActivityNet (mixto) — 18 mensajes etiquetados
  • Combinamos:
      - Mensajes ATIS-style ("Show me flights…") → TEXT
      - Adjuntos image/png → IMAGE
      - Adjuntos audio/wav → AUDIO
      - Adjuntos video/mp4 → VIDEO
      - Combinaciones (texto + imagen + audio) → MIXED
      - Intent-regex sin adjunto ("transcribe this audio") → AUDIO
  • Por qué: ATIS aporta el caso TEXT canónico, arXiv las queries
    image-heavy, ActivityNet los videos. El test demuestra los tres
    canales del clasificador (attachments → blocks → intent regex).

Descripción del componente:
  `classify_modality(message, settings)` examina, en orden:
    1. `additional_kwargs["attachments"][i].mime_type` → modality map.
    2. `message.content[i]["type"]` (image_url, input_audio, video, …).
    3. Regex de intención (transcribe/voice → AUDIO, picture → IMAGE).
    4. Default `TEXT` si hay texto, `UNKNOWN` si no.

  `make_modality_router_node(use_llm_fallback=False)` envuelve el
  clasificador en un nodo LangGraph cuya salida es:
    {"next": "<vision_agent|audio_agent|video_agent|fusion|text>",
     "metadata": {"mm": {"router": {modality, confidence, …}}}}

  `state["metadata"]["mm"]["force_modality"]` fuerza la decisión (útil
  para tests y para que el usuario sobreescriba la heurística).

Uso:
    uv run python examples/multimodal/04_modality_router.py
"""

from __future__ import annotations

import asyncio

from langchain_core.messages import HumanMessage

from prismal.agents.multimodal import (
    Modality,
    classify_modality,
    make_modality_router_node,
)

# ── Dataset: 18 mensajes con modalidad esperada ──────────────────────────────
CASES = [
    # === TEXT (ATIS) ===
    ("atis_t1", "Show me all flights from Boston to Denver", None, None, Modality.TEXT),
    ("atis_t2", "What's the cheapest fare to JFK", None, None, Modality.TEXT),
    ("atis_t3", "Cancel my reservation for tomorrow", None, None, Modality.TEXT),
    # === IMAGE via attachment ===
    ("img_a1", "What's in this photo?", "image/png", None, Modality.IMAGE),
    ("img_a2", "Describe the figure", "image/jpeg", None, Modality.IMAGE),
    # === IMAGE via content block ===
    ("img_b1", None, None, "image_url", Modality.IMAGE),
    # === IMAGE via intent regex (sin adjunto) ===
    ("img_r1", "Can you analyze the picture I uploaded yesterday?", None, None, Modality.IMAGE),
    ("img_r2", "Show me the screenshot from the meeting", None, None, Modality.IMAGE),
    # === AUDIO via attachment ===
    ("aud_a1", "Transcribe this", "audio/wav", None, Modality.AUDIO),
    ("aud_a2", "What did they say?", "audio/mpeg", None, Modality.AUDIO),
    # === AUDIO via content block ===
    ("aud_b1", "Listen", None, "input_audio", Modality.AUDIO),
    # === AUDIO via intent regex ===
    ("aud_r1", "Please transcribe the call from this morning", None, None, Modality.AUDIO),
    ("aud_r2", "I need to convert this voice memo to text", None, None, Modality.AUDIO),
    # === VIDEO via attachment ===
    ("vid_a1", "Summarize this clip", "video/mp4", None, Modality.VIDEO),
    # === VIDEO via intent regex ===
    ("vid_r1", "What happens in this video?", None, None, Modality.VIDEO),
    # === MIXED ===
    ("mix_1", "Compare these", "image/png,audio/wav", None, Modality.MIXED),
    ("mix_2", "Analyze", "video/mp4,image/jpeg", None, Modality.MIXED),
    # === UNKNOWN ===
    ("unk_1", "", None, None, Modality.UNKNOWN),
]


def build_message(text: str | None, attachments_mime: str | None, block_type: str | None):
    """Construir un HumanMessage con attachments y/o content blocks."""
    kwargs: dict = {}
    if attachments_mime:
        kwargs["attachments"] = [
            {"mime_type": m.strip()} for m in attachments_mime.split(",")
        ]

    if block_type:
        content: list[dict] = []
        if text:
            content.append({"type": "text", "text": text})
        content.append({"type": block_type, "image_url": {"url": "data:..."}})
        return HumanMessage(content=content, additional_kwargs=kwargs)

    return HumanMessage(content=text or "", additional_kwargs=kwargs)


async def main() -> None:
    print("=" * 70)
    print("ModalityRouter · clasificación determinística (sin LLM)")
    print("=" * 70)

    # 1. classify_modality estándar
    print("\n" + "─" * 70)
    print("1) classify_modality(message) — 18 casos etiquetados")
    print("─" * 70)
    correct = 0
    for case_id, text, mime, block, expected in CASES:
        msg = build_message(text, mime, block)
        result = classify_modality(msg)
        ok = result.modality == expected
        correct += int(ok)
        mark = "✓" if ok else "✗"
        print(
            f"  {mark} {case_id:8} → {result.modality.value:8} "
            f"(conf={result.confidence:.2f})  expected={expected.value}"
        )
    print(f"\n  Accuracy: {correct}/{len(CASES)}  ({100 * correct / len(CASES):.1f}%)")

    # 2. Nodo LangGraph (routing destino)
    print("\n" + "─" * 70)
    print("2) make_modality_router_node — routing a nodos LangGraph")
    print("─" * 70)
    router = make_modality_router_node(use_llm_fallback=False)

    targets = {
        "vision_agent": [],
        "audio_agent": [],
        "video_agent": [],
        "fusion": [],
        "text": [],
    }
    for case_id, text, mime, block, _ in CASES:
        msg = build_message(text, mime, block)
        state = {"messages": [msg], "metadata": {}}
        result = await router(state)
        targets[result["next"]].append(case_id)

    for node, cases in targets.items():
        print(f"  → {node:14} · {len(cases):2} casos  {cases}")

    # 3. force_modality override
    print("\n" + "─" * 70)
    print("3) override con state.metadata.mm.force_modality")
    print("─" * 70)
    msg = HumanMessage(content="random text without intent")
    state = {
        "messages": [msg],
        "metadata": {"mm": {"force_modality": Modality.AUDIO.value}},
    }
    result = await router(state)
    print(f"\n  forced → {result['next']}  (router metadata: {result['metadata']['mm']['router']})")
    assert result["next"] == "audio_agent"

    print("\n" + "=" * 70)
    print("OK — ModalityRouter sin LLM (use_llm_fallback=True opcional)")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())

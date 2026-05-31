"""
VisionAgent — Análisis de imágenes con vision LLM inyectable
=============================================================
Componente: SPEC-MM-AGT-001 / prismal.agents.multimodal.vision_agent

Dataset: arXiv Computer Vision (figuras hipotéticas de los abstracts)
  • Subconjunto de papers cs.CV de
    `Langgraph_tutorials/data/arxiv/arxiv_papers.csv`.
  • Para cada paper construimos una "figura sintética" (PNG mínimo) y
    asociamos un caption esperado derivado del título/abstract — así
    podemos probar el agente sin un VLM real.
  • Por qué arXiv: cubre todo el espectro de visión por computador
    (detección, segmentación, video, OCR), por lo que sirve para
    demostrar `with_ocr=True/False`, fallbacks de validación y el
    contrato `VisionResult`.

Descripción del componente:
  El agente recibe `image: bytes | Path` y un `prompt` opcional, lo
  ejecuta a través de `MediaValidator` (magic bytes + límite de bytes)
  y delega la descripción al callable `vision_fn`. Si `with_ocr=True`,
  realiza una segunda pasada de OCR. Devuelve siempre un
  `VisionResult(description, objects, ocr_text, model_used,
  used_fallback)`; nunca lanza si `degrade_gracefully=True`.

  Layered defaults — `_make_default_vision_fn` arma la llamada real a
  `get_vision_llm` con `SecurePromptBuilder`. Inyectando un callable se
  evita totalmente la red (este ejemplo).

Uso:
    uv run python examples/multimodal/01_vision_agent.py
"""

from __future__ import annotations

import asyncio
import csv
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path

from prismal.agents.multimodal import VisionAgent, VisionResult

# ── Dataset: papers cs.CV de arXiv (mismo CSV usado en RAG multivector) ──────
ARXIV_CSV = (
    Path(__file__).resolve().parents[3]
    / "Langgraph_tutorials"
    / "data"
    / "arxiv"
    / "arxiv_papers.csv"
)


@dataclass(frozen=True)
class PaperFigure:
    """Una figura sintética asociada a un paper de arXiv."""

    arxiv_id: str
    title: str
    expected_caption: str   # Lo que un VLM "ideal" debería responder.
    expected_objects: tuple[str, ...]
    image_bytes: bytes


def _png_solid(width: int, height: int, rgb: tuple[int, int, int]) -> bytes:
    """Generar un PNG válido (1 color sólido) sin Pillow. Sólo para demo."""
    sig = b"\x89PNG\r\n\x1a\n"

    def _chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data)
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    raw = b""
    row = b"\x00" + bytes(rgb) * width
    for _ in range(height):
        raw += row
    idat = zlib.compress(raw, 9)
    return sig + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", idat) + _chunk(b"IEND", b"")


def _load_dataset(limit: int = 5) -> list[PaperFigure]:
    """Cargar papers cs.CV y construir figuras + captions esperados."""
    if not ARXIV_CSV.exists():
        # Fallback embebido por si no hay acceso al dataset.
        return _embedded_dataset()

    rows: list[PaperFigure] = []
    with ARXIV_CSV.open(encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for raw in reader:
            if not raw.get("category", "").startswith("cs.CV"):
                continue
            title = (raw.get("title") or "").strip()
            abstract = (raw.get("abstract") or "").strip()
            # Color determinístico por arXiv id (azul/verde/rojo rota).
            seed = abs(hash(raw["arxiv_id"])) % 3
            rgb = [(45, 110, 220), (60, 170, 90), (200, 80, 70)][seed]
            rows.append(
                PaperFigure(
                    arxiv_id=raw["arxiv_id"],
                    title=title,
                    expected_caption=f"Figure from {title!r}. Topic: {abstract[:140]}…",
                    expected_objects=("paper", "diagram", "figure"),
                    image_bytes=_png_solid(64, 64, rgb),
                )
            )
            if len(rows) >= limit:
                break
    return rows or _embedded_dataset()


def _embedded_dataset() -> list[PaperFigure]:
    """Plan B: 3 figuras embebidas si el CSV no está disponible."""
    samples = [
        ("2604.02330", "ActionParty: Multi-Subject World Models", "video, agent"),
        ("2604.02185", "Vision Transformers Survey", "transformer, attention"),
        ("2604.02077", "Diffusion Models for Segmentation", "diffusion, mask"),
    ]
    return [
        PaperFigure(
            arxiv_id=aid,
            title=title,
            expected_caption=f"Synthetic figure for paper {title!r} ({objs}).",
            expected_objects=tuple(objs.split(", ")),
            image_bytes=_png_solid(64, 64, (45, 110, 220)),
        )
        for aid, title, objs in samples
    ]


# ── Fake vision_fn — devuelve el caption esperado sin llamar a un VLM ────────
def make_fake_vision_fn(corpus: list[PaperFigure]):
    """Vision-fn que mira los primeros bytes para identificar el paper."""
    by_signature = {fig.image_bytes[:16]: fig for fig in corpus}

    async def _vision(image, prompt: str) -> str:
        blob = image if isinstance(image, bytes) else Path(image).read_bytes()
        fig = by_signature.get(blob[:16])
        if fig is None:
            return f"[mock-vlm] cannot identify image · prompt={prompt!r}"
        return fig.expected_caption

    return _vision


async def make_fake_ocr_fn(image, prompt=None):  # noqa: ARG001 - shape compat
    """OCR mock: el texto siempre es 'arXiv preprint · 2024' como sellado."""
    return "arXiv preprint · 2024"


# ── Demo principal ──────────────────────────────────────────────────────────
async def main() -> None:
    print("=" * 70)
    print("VisionAgent · análisis de figuras sintéticas de arXiv")
    print("=" * 70)

    corpus = _load_dataset(limit=4)
    print(f"\nCargados {len(corpus)} papers cs.CV\n")

    agent = VisionAgent(
        vision_fn=make_fake_vision_fn(corpus),
        ocr_fn=make_fake_ocr_fn,
        degrade_gracefully=True,
    )

    # 1. Análisis sin OCR
    print("─" * 70)
    print("1) Análisis estándar (with_ocr=False)")
    print("─" * 70)
    for fig in corpus:
        result: VisionResult = await agent.analyze(fig.image_bytes, with_ocr=False)
        print(f"\n  {fig.arxiv_id} — {fig.title[:60]}")
        print(f"    description: {result.description[:80]}…")
        print(f"    used_fallback: {result.used_fallback}")
        print(f"    model_used: {result.model_used}")

    # 2. Análisis con OCR
    print("\n" + "─" * 70)
    print("2) Análisis con OCR (with_ocr=True)")
    print("─" * 70)
    result = await agent.analyze(
        corpus[0].image_bytes,
        prompt="¿Qué tema cubre este paper?",
        with_ocr=True,
    )
    print(f"\n  ocr_text: {result.ocr_text!r}")
    print(f"  description: {result.description[:80]}…")

    # 3. Validación rechaza basura (magic bytes incorrectos)
    print("\n" + "─" * 70)
    print("3) MediaValidator rechaza un blob no-imagen")
    print("─" * 70)
    bogus = b"this is not an image"
    result = await agent.analyze(bogus)
    assert result.used_fallback, "El agente debe degradar a fallback"
    print(f"\n  used_fallback: {result.used_fallback}  ← validación bloqueó la llamada VLM")
    print(f"  description: {result.description!r}  (vacío en fallback)")

    print("\n" + "=" * 70)
    print("OK — VisionAgent funciona con vision_fn inyectado (sin red)")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())

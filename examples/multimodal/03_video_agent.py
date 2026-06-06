"""
VideoAgent — Pipeline de video (frames + transcript + fusión)
==============================================================
Componente: SPEC-MM-AGT-003 / prismal.agents.multimodal.video_agent

Dataset: ActivityNet-style captions (descripciones temporales de videos)
  • ActivityNet Captions contiene 20k vídeos con descripciones a lo
    largo del tiempo (start, end, sentence) — uno de los benchmarks
    más usados para video understanding.
  • Referencia: http://activity-net.org/challenges/2017/captioning.html
  • Por qué: el `VideoAgent` produce un `VideoResult(transcript,
    frame_descriptions, summary, …)` que se parece mucho a la estructura
    de las anotaciones de ActivityNet. Usamos clips sintéticos cuyas
    captions están pre-definidas.

Descripción del componente:
  1. `MediaValidator` valida que el archivo sea video (MP4/WebM).
  2. `frame_extractor_fn(video_path, fps, max_frames)` corre FFmpeg
     dentro de `SandboxExecutor` en producción — aquí lo mockeamos.
  3. `VisionAgent.analyze(frame_path)` describe cada frame
     extraído (paralelizado con `asyncio.gather`).
  4. `transcribe_fn(video_path)` extrae el audio del video (FFmpeg →
     `AudioAgent.process`) en producción — aquí lo inyectamos.
  5. `fusion_fn(transcript, frame_descriptions)` une todo en el resumen
     final (multimodal LLM en producción).

Uso:
    uv run python examples/multimodal/03_video_agent.py
"""

from __future__ import annotations

import asyncio
import struct
from pathlib import Path
from tempfile import TemporaryDirectory

from prismal.agents.multimodal import (
    FrameDescription,
    VideoAgent,
    VideoResult,
    VisionAgent,
)

# ── Dataset: 3 clips sintéticos estilo ActivityNet ───────────────────────────
CLIPS = [
    {
        "id": "anet_001",
        "filename": "cooking_pasta.mp4",
        "duration_s": 24.0,
        "fps": 1.0,
        "frame_captions": [
            "A pot of water boiling on a stove",
            "Hands pouring dry spaghetti into the boiling pot",
            "Someone stirring the pasta with a wooden spoon",
            "Plating the cooked pasta with red sauce",
        ],
        "transcript": "Today we're making a quick spaghetti dinner from scratch.",
    },
    {
        "id": "anet_002",
        "filename": "skateboard_trick.mp4",
        "duration_s": 12.0,
        "fps": 2.0,
        "frame_captions": [
            "A skateboarder approaching a stair set",
            "Mid-air ollie above the stairs",
            "Landing the trick on the asphalt below",
        ],
        "transcript": "Watch this kickflip down the seven stairs!",
    },
    {
        "id": "anet_003",
        "filename": "guitar_lesson.mp4",
        "duration_s": 30.0,
        "fps": 0.5,
        "frame_captions": [
            "Close-up of guitarist's hand forming a G chord",
            "Strumming pattern demonstrated slowly",
            "Hand moving to a C chord",
        ],
        "transcript": "In this lesson we'll cover the G to C transition.",
    },
]


# ── Fake MP4 file (suficiente para `MediaValidator.sniff()`) ─────────────────
def _make_fake_mp4() -> bytes:
    """MP4 mínimo: el detector busca 'ftyp' en el offset 4."""
    # 4 bytes de tamaño + 'ftypisom' + relleno
    box = b"ftypisom\x00\x00\x02\x00isomiso2avc1mp41"
    return struct.pack(">I", len(box) + 4) + box + b"\x00" * 256


# ── Fake collaborators — sin FFmpeg, sin VLM, sin Whisper ────────────────────
def make_frame_extractor(out_dir: Path, frames_per_clip: dict[str, int]):
    """Frame extractor mock: escribe N PNGs vacíos y devuelve sus paths."""
    import zlib

    def _png(label_byte: int) -> bytes:
        sig = b"\x89PNG\r\n\x1a\n"

        def _chunk(tag: bytes, data: bytes) -> bytes:
            crc = zlib.crc32(tag + data)
            return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

        ihdr = struct.pack(">IIBBBBB", 8, 8, 8, 2, 0, 0, 0)
        row = b"\x00" + bytes([label_byte, 0, 0]) * 8
        idat = zlib.compress(row * 8, 9)
        return sig + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", idat) + _chunk(b"IEND", b"")

    async def _extract(video: Path, fps: float, max_frames: int) -> list[Path]:
        clip_id = video.stem
        n_frames = min(frames_per_clip.get(clip_id, 3), max_frames)
        out: list[Path] = []
        for i in range(n_frames):
            p = out_dir / f"{clip_id}_frame_{i:03d}.png"
            p.write_bytes(_png(i * 30 % 255))
            out.append(p)
        return out

    return _extract


def make_vision_agent(captions_by_clip: dict[str, list[str]]) -> VisionAgent:
    """VisionAgent envuelve un vision_fn que mira el nombre del archivo."""

    async def _vision(image, prompt: str) -> str:
        if not isinstance(image, Path):
            return "(mock vision)"
        clip_id = image.stem.rsplit("_frame_", 1)[0]
        idx = int(image.stem.rsplit("_", 1)[-1])
        captions = captions_by_clip.get(clip_id, [])
        return captions[idx] if idx < len(captions) else f"frame {idx}"

    return VisionAgent(vision_fn=_vision)


def make_transcribe_fn(transcripts_by_clip: dict[str, str]):
    async def _transcribe(video: Path) -> str:
        return transcripts_by_clip.get(video.stem, "")

    return _transcribe


def make_fusion_fn():
    """Fusión determinística: concat audio + frames con etiquetas."""

    async def _fuse(transcript: str, frames: list[FrameDescription]) -> str:
        lines = [f"AUDIO: {transcript}"] if transcript else []
        for fr in frames:
            lines.append(f"FRAME {fr.timestamp_s:.1f}s: {fr.description}")
        return "\n".join(lines)

    return _fuse


async def main() -> None:
    print("=" * 70)
    print("VideoAgent · pipeline frames + audio + fusión sobre clips estilo ActivityNet")
    print("=" * 70)

    with TemporaryDirectory(prefix="prismal_vid_") as tmp:
        tmp_dir = Path(tmp)

        # Crear los MP4 mock una vez.
        video_paths: dict[str, Path] = {}
        frames_per_clip = {}
        captions_by_clip = {}
        transcripts_by_clip = {}
        for clip in CLIPS:
            clip_id = Path(clip["filename"]).stem
            p = tmp_dir / clip["filename"]
            p.write_bytes(_make_fake_mp4())
            video_paths[clip["id"]] = p
            frames_per_clip[clip_id] = len(clip["frame_captions"])
            captions_by_clip[clip_id] = clip["frame_captions"]
            transcripts_by_clip[clip_id] = clip["transcript"]

        agent = VideoAgent(
            vision_agent=make_vision_agent(captions_by_clip),
            frame_extractor_fn=make_frame_extractor(tmp_dir, frames_per_clip),
            transcribe_fn=make_transcribe_fn(transcripts_by_clip),
            fusion_fn=make_fusion_fn(),
            degrade_gracefully=True,
        )

        for clip in CLIPS:
            print("\n" + "─" * 70)
            print(f"Clip: {clip['id']}  ({clip['filename']})  fps={clip['fps']}")
            print("─" * 70)
            result: VideoResult = await agent.summarize(
                video_paths[clip["id"]],
                fps=clip["fps"],
                max_frames=10,
            )
            print(f"  transcript        : {result.transcript}")
            print(f"  frames processed  : {result.total_frames_processed}")
            print("  frame descriptions:")
            for fr in result.frame_descriptions:
                print(f"    t={fr.timestamp_s:.1f}s · {fr.description}")
            print("  fused summary:")
            for line in result.summary.splitlines():
                print(f"    {line}")

        # Validación rechaza un blob que no es video.
        print("\n" + "─" * 70)
        print("Validación: archivo no-video produce VideoResult vacío (fallback)")
        print("─" * 70)
        bogus = tmp_dir / "not_a_video.bin"
        bogus.write_bytes(b"garbage")
        result = await agent.summarize(bogus, fps=1.0, max_frames=3)
        assert result.summary == ""
        assert result.total_frames_processed == 0
        print("  ← el agente devolvió VideoResult vacío sin extraer frames")

    print("\n" + "=" * 70)
    print("OK — VideoAgent funciona con FFmpeg/VLM mockeados (sin sandbox real)")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())

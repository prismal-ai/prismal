"""
AudioAgent — Voice-to-voice (STT → razonar → TTS) con clientes inyectables
==========================================================================
Componente: SPEC-MM-AGT-002 / prismal.agents.multimodal.audio_agent

Dataset: ATIS-style voice intents (Air Travel Information System)
  • ATIS es un dataset clásico de comandos de voz de usuarios pidiendo
    información sobre vuelos: 5 871 utterancias, 26 intenciones.
  • Referencia: https://github.com/howl-anderson/ATIS_dataset
  • Por qué: ATIS es el caso de uso canónico de "voice assistant" —
    perfecto para mostrar el pipeline STT → razonamiento → TTS sin
    depender de un dataset multimedia pesado.

Descripción del componente:
  1. `MediaValidator` valida el audio (magic bytes WAV/MP3).
  2. `STTClient.transcribe(audio, language)` devuelve `STTResult`.
  3. `reason_fn(transcript, state)` razona sobre el transcript.
  4. Si `with_tts=True`, `TTSClient.synthesize(response_text)` regresa
     `TTSResult(audio, mime_type, provider_used, duration_s)`.

  Todos los pasos están envueltos por spans OTel y, si
  `degrade_gracefully=True`, los fallos producen un `AudioResult` con
  campos vacíos en lugar de levantar `AudioAgentError`.

Uso:
    uv run python examples/multimodal/02_audio_agent.py
"""

from __future__ import annotations

import asyncio
import struct
import wave
from io import BytesIO
from pathlib import Path

from prismal.agents.multimodal import AudioAgent, AudioResult
from prismal.providers.stt import STTResult, STTSegment
from prismal.providers.tts import TTSResult

# ── Dataset: utterancias estilo ATIS ─────────────────────────────────────────
ATIS_UTTERANCES = [
    {
        "id": "atis_001",
        "language": "en",
        "transcript": "Show me all flights from Boston to Denver on Tuesday morning",
        "intent": "flight_search",
        "expected_reply": (
            "I found 12 flights from BOS to DEN on Tuesday morning. "
            "The earliest departs at 6:05 AM."
        ),
    },
    {
        "id": "atis_002",
        "language": "en",
        "transcript": "What's the cheapest fare from San Francisco to New York next Monday",
        "intent": "fare_inquiry",
        "expected_reply": (
            "The cheapest fare from SFO to JFK on Monday is $187 (United, "
            "1 stop in DEN)."
        ),
    },
    {
        "id": "atis_003",
        "language": "es",
        "transcript": "Cuántos asientos hay disponibles en el vuelo de las dos de la tarde",
        "intent": "seat_availability",
        "expected_reply": "El vuelo de las 14:00 tiene 14 asientos disponibles en clase económica.",
    },
    {
        "id": "atis_004",
        "language": "en",
        "transcript": "I want to cancel my reservation for tomorrow's flight",
        "intent": "cancellation",
        "expected_reply": "Your reservation has been cancelled. Refund will arrive in 5-7 days.",
    },
]


def _make_silence_wav(duration_s: float = 1.0, sample_rate: int = 16_000) -> bytes:
    """Generar un WAV PCM de silencio (válido para MediaValidator)."""
    buf = BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        n_samples = int(duration_s * sample_rate)
        w.writeframes(b"\x00\x00" * n_samples)
    return buf.getvalue()


# ── Fake STTClient — reconoce el WAV por payload con hash del id ─────────────
class FakeSTT:
    """STTClient mock que mapea audio -> transcript por hash de bytes."""

    def __init__(self, by_signature: dict[bytes, dict]) -> None:
        self._by_signature = by_signature

    async def transcribe(self, audio, *, language=None, prompt=None) -> STTResult:  # noqa: ARG002
        blob = audio if isinstance(audio, bytes) else Path(audio).read_bytes()
        sample = self._by_signature.get(blob[:44]) or {
            "transcript": "[unknown audio]",
            "language": "en",
        }
        text = sample["transcript"]
        return STTResult(
            text=text,
            language=language or sample["language"],
            segments=[STTSegment(start_s=0.0, end_s=1.0, text=text)],
            provider_used="mock-whisper",
        )


# ── Fake TTSClient — simula la síntesis ──────────────────────────────────────
class FakeTTS:
    """TTSClient mock que genera un WAV de silencio del tamaño del texto."""

    async def synthesize(self, text: str, *, voice=None, format="wav") -> TTSResult:  # noqa: ARG002
        # Aprox 1 char ~= 50 ms de audio.
        duration = max(0.5, min(8.0, len(text) * 0.05))
        audio = _make_silence_wav(duration_s=duration)
        return TTSResult(
            audio=audio,
            mime_type="audio/wav",
            provider_used="mock-tts",
            duration_s=duration,
        )


# ── reason_fn — usa el intent del dataset para responder de forma estable ────
def make_reason_fn(by_transcript: dict[str, str]):
    async def _reason(transcript: str, state) -> str:  # noqa: ARG001
        return by_transcript.get(transcript.strip(), "I'm not sure how to help with that.")

    return _reason


async def main() -> None:
    print("=" * 70)
    print("AudioAgent · voice-to-voice sobre comandos estilo ATIS")
    print("=" * 70)

    # Construir el dataset: cada utterance genera un WAV único.
    by_signature: dict[bytes, dict] = {}
    by_transcript: dict[str, str] = {}
    for i, ut in enumerate(ATIS_UTTERANCES):
        # Duración determinística por índice para que la firma cambie.
        wav = _make_silence_wav(duration_s=0.5 + i * 0.25)
        by_signature[wav[:44]] = ut
        by_transcript[ut["transcript"]] = ut["expected_reply"]

    agent = AudioAgent(
        stt_client=FakeSTT(by_signature),
        tts_client=FakeTTS(),
        reason_fn=make_reason_fn(by_transcript),
        degrade_gracefully=True,
    )

    # 1. Pipeline completo con TTS para una sola utterance
    print("\n" + "─" * 70)
    print("1) Pipeline completo STT → reason → TTS")
    print("─" * 70)
    sample = ATIS_UTTERANCES[0]
    wav = _make_silence_wav(duration_s=0.5)
    result: AudioResult = await agent.process(wav, language="en", with_tts=True)
    print(f"\n  transcript : {result.transcript}")
    print(f"  reply text : {result.response_text}")
    print(f"  audio MIME : {result.response_mime}")
    print(f"  audio bytes: {len(result.response_audio or b'')} bytes")
    print(f"  STT  used  : {result.stt_provider_used}")
    print(f"  TTS  used  : {result.tts_provider_used}")
    print(f"  duration_s : {result.duration_s:.2f}")

    # 2. Lote sin TTS — más eficiente para chatbots de texto
    print("\n" + "─" * 70)
    print("2) Lote sin TTS (chatbot de texto)")
    print("─" * 70)
    for i, ut in enumerate(ATIS_UTTERANCES):
        wav = _make_silence_wav(duration_s=0.5 + i * 0.25)
        result = await agent.process(wav, language=ut["language"], with_tts=False)
        marker = "✓" if result.transcript == ut["transcript"] else "✗"
        print(f"  {marker} {ut['id']} [{ut['intent']:18}] → {result.response_text[:60]}")

    # 3. Validación rechaza audio inválido
    print("\n" + "─" * 70)
    print("3) MediaValidator rechaza un blob no-audio")
    print("─" * 70)
    result = await agent.process(b"not a wav file")
    print(f"\n  transcript: {result.transcript!r}")
    print(f"  response  : {result.response_text!r}")
    print("  ← el agente degradó a un AudioResult vacío sin llamar a STT")

    print("\n" + "=" * 70)
    print("OK — AudioAgent funciona con STT/TTS mock (sin Whisper ni ElevenLabs)")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())

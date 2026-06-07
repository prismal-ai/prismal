# Prismal Multimodal Agents — Interface Specification

## Metadata

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `IMPLEMENTED` |
| **Version** | 1.0 |
| **Date** | 2026-05-27 |
| **PLAN** | `specs/multimodal-agents/PLAN.md` |
| **Architecture** | `specs/multimodal-agents/ARCHITECTURE.md` |
| **TASKS** | `specs/multimodal-agents/TASKS.md` |

---

## Conventions

- All modules use `from __future__ import annotations`.
- Heavy imports (Whisper, CLIP, FFmpeg wrappers) under `TYPE_CHECKING` or lazy inside the function.
- Async where applicable (all STT/TTS/VLM calls are `async`); pure helpers are `sync`.
- Frozen dataclasses where applicable.
- Constructors accept `settings: Settings | None = None`.
- No module imports `openai`, `anthropic`, `google.generativeai`, `elevenlabs`, `whisper`, `pyttsx3` directly — everything via `prismal/providers/`.
- Callable injection in all agent classes (`stt_fn`, `tts_fn`, `vision_fn`, `frame_extractor_fn`, `transcribe_fn`).
- Custom errors live in `prismal/core/exceptions.py` (extension).

---

## Module Summary

| SPEC | File | Components |
|---|---|---|
| SPEC-MM-PROV-001 | `prismal/providers/stt.py` | `STTClient`, `get_stt()` |
| SPEC-MM-PROV-002 | `prismal/providers/tts.py` | `TTSClient`, `get_tts()` |
| SPEC-MM-PROV-003 | `prismal/providers/vision.py` | `get_vision_llm()` |
| SPEC-MM-PROV-004 | `prismal/providers/multimodal.py` | `get_multimodal_llm()` |
| SPEC-MM-PROV-005 | `prismal/providers/cross_modal_embeddings.py` | `get_cross_modal_embeddings()` |
| SPEC-MM-AGT-001 | `prismal/agents/multimodal/vision_agent.py` | `VisionAgent`, `VisionResult` |
| SPEC-MM-AGT-002 | `prismal/agents/multimodal/audio_agent.py` | `AudioAgent`, `AudioResult` |
| SPEC-MM-AGT-003 | `prismal/agents/multimodal/video_agent.py` | `VideoAgent`, `VideoResult` |
| SPEC-MM-AGT-004 | `prismal/agents/multimodal/modality_router.py` | `Modality`, `classify_modality()`, `make_modality_router_node()` |
| SPEC-MM-AGT-005 | `prismal/agents/multimodal/multimodal_fusion.py` | `MultimodalFusion` |
| SPEC-MM-SUB-001 | `prismal/agents/subgraphs/multimodal_pipeline/` | `build_multimodal_subgraph()`, `register_multimodal_pipeline()` |
| SPEC-MM-RAG-001 | `prismal/rag/multimodal.py` | `MultimodalRAGEngine`, `MultimodalRetrievedChunk` |
| SPEC-MM-RAG-002 | `prismal/rag/loaders/` | `ImageLoader`, `AudioLoader`, `VideoLoader` |
| SPEC-MM-SEC-001 | `prismal/security/media_validator.py` | `MediaValidator` |

---

## SPEC-MM-PROV-001: STT Provider Wrapper

**File:** `prismal/providers/stt.py`

### Types

```python
from enum import Enum

class STTProvider(str, Enum):
    OPENAI = "openai"      # Whisper API
    LOCAL = "local"        # openai-whisper / faster-whisper

@dataclass(frozen=True)
class STTResult:
    """Result of a transcription.

    Attributes:
        text: Full concatenated transcription.
        language: Detected language (ISO-639-1) or the requested one.
        segments: Segments with timestamps; empty if the provider does not supply them.
        provider_used: Identifier of the backend used.
    """
    text: str
    language: str
    segments: list[STTSegment]
    provider_used: str

@dataclass(frozen=True)
class STTSegment:
    start_s: float
    end_s: float
    text: str
```

### Main Class

```python
class STTClient(Protocol):
    """Uniform Speech-to-Text interface."""

    async def transcribe(
        self,
        audio: bytes | Path,
        *,
        language: str | None = None,
        prompt: str | None = None,
    ) -> STTResult:
        """Transcribe audio to text.

        Args:
            audio: Audio bytes or path to the file.
            language: ISO-639-1 hint; None lets it auto-detect.
            prompt: Optional context (vocabulary, proper nouns).

        Returns:
            STTResult with text, language, and segments.

        Raises:
            STTError: If transcription fails in the backend.
        """
        ...


def get_stt(
    provider: STTProvider | str | None = None,
    model: str | None = None,
    *,
    settings: Settings | None = None,
) -> STTClient:
    """Resolve an STTClient according to `settings.stt_provider` or the override.

    Args:
        provider: If specified, overrides the default.
        model: Model (e.g. "whisper-1" for openai, "base" for local).
        settings: Injectable settings.

    Returns:
        STTClient ready for use.

    Raises:
        STTError: If the requested provider is not available
            (e.g. `[multimodal-local]` extras not installed).
    """
    ...
```

---

## SPEC-MM-PROV-002: TTS Provider Wrapper

**File:** `prismal/providers/tts.py`

### Types

```python
class TTSProvider(str, Enum):
    PYTTSX3 = "pyttsx3"         # offline, default
    OPENAI = "openai"           # gpt-4o-mini-tts and similar
    ELEVENLABS = "elevenlabs"   # premium

@dataclass(frozen=True)
class TTSResult:
    audio: bytes               # WAV/MP3 depending on provider
    mime_type: str             # "audio/wav" | "audio/mpeg" | ...
    provider_used: str
    duration_s: float          # estimated; 0.0 if not supplied
```

### Main Class

```python
class TTSClient(Protocol):

    async def synthesize(
        self,
        text: str,
        *,
        voice: str | None = None,
        format: Literal["wav", "mp3"] = "wav",
    ) -> TTSResult:
        """Synthesize text to speech.

        Args:
            text: Text to synthesize (≤ settings.tts_max_chars).
            voice: Voice ID (provider-specific). None uses the default.
            format: Output format.

        Returns:
            TTSResult with audio bytes and metadata.

        Raises:
            TTSError: If the provider fails.
        """
        ...


def get_tts(
    provider: TTSProvider | str | None = None,
    *,
    settings: Settings | None = None,
) -> TTSClient:
    """Resolve a TTSClient with cascading fallback.

    If the preferred provider fails to init, it falls back to:
        elevenlabs → openai → pyttsx3 (local, always available).
    """
    ...
```

---

## SPEC-MM-PROV-003 / 004 / 005: Vision / Multimodal LLM / Cross-Modal Embeddings

**Files:** `prismal/providers/vision.py`, `multimodal.py`, `cross_modal_embeddings.py`

```python
# vision.py
def get_vision_llm(
    model: str | None = None,
    *,
    settings: Settings | None = None,
) -> BaseChatModel:
    """Return a BaseChatModel with image support.

    The model accepts `HumanMessage(content=[{"type":"image_url","image_url":{"url":...}},
                                             {"type":"text","text":prompt}])`.
    Default: settings.cua_vision_model.
    """
    ...


# multimodal.py
def get_multimodal_llm(
    model: str | None = None,
    *,
    settings: Settings | None = None,
) -> BaseChatModel:
    """Return a natively multimodal BaseChatModel (Gemini 2.x, GPT-4o, Sonnet 4.6).

    Supports audio + image + video + text in the same message when the
    model allows it. Default: settings.multimodal_model.
    """
    ...


# cross_modal_embeddings.py
def get_cross_modal_embeddings(
    model: str | None = None,
    *,
    settings: Settings | None = None,
) -> Embeddings:
    """Return an Embeddings that accepts text and images (CLIP-style).

    If `open_clip_torch` or the requested backend is not installed,
    raises MissingDependencyError suggesting `pip install "prismal[multimodal-embed]"`.
    """
    ...
```

---

## SPEC-MM-AGT-001: Vision Agent

**File:** `prismal/agents/multimodal/vision_agent.py`

### Types

```python
@dataclass(frozen=True)
class DetectedObject:
    label: str
    confidence: float        # [0.0, 1.0]
    bbox: tuple[float, float, float, float] | None  # (x, y, w, h) normalized or None

@dataclass(frozen=True)
class VisionResult:
    description: str
    objects: list[DetectedObject]
    ocr_text: str | None
    model_used: str
    used_fallback: bool = False


VisionFn = Callable[[bytes | Path, str], Awaitable[str]]
"""(image, prompt) → textual response from the VLM."""

OcrFn = Callable[[bytes | Path], Awaitable[str]]
"""(image) → OCR text."""
```

### Main Class

```python
class VisionAgent:
    """General-purpose image analysis agent.

    Args:
        vision_fn: Callable that invokes a VLM. None uses get_vision_llm().
        ocr_fn: OCR callable. None uses the VLM with an OCR prompt.
        media_validator: MediaValidator instance. None creates one by default.
        settings: Injectable settings.

    Example::

        agent = VisionAgent()
        result = await agent.analyze(Path("photo.jpg"), with_ocr=False)
        print(result.description)
        for obj in result.objects:
            print(f"{obj.label}: {obj.confidence:.2f}")
    """

    def __init__(
        self,
        *,
        vision_fn: VisionFn | None = None,
        ocr_fn: OcrFn | None = None,
        media_validator: MediaValidator | None = None,
        settings: Settings | None = None,
    ) -> None: ...

    async def analyze(
        self,
        image: bytes | Path,
        *,
        prompt: str | None = None,
        with_ocr: bool | None = None,
    ) -> VisionResult:
        """Analyze an image.

        Args:
            image: Bytes or path.
            prompt: Custom prompt (None uses the default: "Describe the image
                and list the visible objects.").
            with_ocr: Override of settings.vision_ocr_enabled.

        Returns:
            VisionResult with description, objects, and optional OCR.

        Raises:
            VisionAgentError: If validation or the VLM fails
                (only if degrade_gracefully=False).
        """
        ...
```

---

## SPEC-MM-AGT-002: Audio Agent

**File:** `prismal/agents/multimodal/audio_agent.py`

### Types

```python
@dataclass(frozen=True)
class AudioResult:
    transcript: str
    response_text: str
    response_audio: bytes | None       # None if with_tts=False
    response_mime: str | None           # "audio/wav" | ... | None
    stt_provider_used: str
    tts_provider_used: str | None
    duration_s: float                   # duration of the incoming audio
```

### Main Class

```python
class AudioAgent:
    """Voice-to-voice pipeline: STT → reasoning → optional TTS.

    Args:
        stt_client: Injectable STTClient. None uses get_stt().
        tts_client: Injectable TTSClient. None uses get_tts().
        reason_fn: Async callable (transcript: str, state: AgentState) → response_text.
            None uses ProviderRegistry().get_llm() with a default prompt.
        media_validator: MediaValidator. None creates a default.
        settings: Injectable settings.

    Example::

        agent = AudioAgent()
        result = await agent.process(
            audio=Path("user_voice.wav"),
            with_tts=True,
        )
        print(result.transcript)         # "Hello, how are you?"
        print(result.response_text)      # "Hello, all good. How can I help you?"
        Path("reply.wav").write_bytes(result.response_audio)
    """

    def __init__(
        self,
        *,
        stt_client: STTClient | None = None,
        tts_client: TTSClient | None = None,
        reason_fn: Callable[[str, AgentState], Awaitable[str]] | None = None,
        media_validator: MediaValidator | None = None,
        settings: Settings | None = None,
    ) -> None: ...

    async def process(
        self,
        audio: bytes | Path,
        *,
        state: AgentState | None = None,
        language: str | None = None,
        with_tts: bool = False,
    ) -> AudioResult:
        """Run the full pipeline.

        Args:
            audio: Bytes or path.
            state: AgentState for LLM context. None uses an empty state.
            language: ISO-639-1 hint for STT.
            with_tts: If True, synthesizes the response.

        Returns:
            AudioResult.

        Raises:
            AudioAgentError: If a stage fails and degrade_gracefully=False.
        """
        ...
```

---

## SPEC-MM-AGT-003: Video Agent

**File:** `prismal/agents/multimodal/video_agent.py`

### Types

```python
@dataclass(frozen=True)
class FrameDescription:
    frame_index: int
    timestamp_s: float
    description: str

@dataclass(frozen=True)
class VideoResult:
    transcript: str
    frame_descriptions: list[FrameDescription]
    summary: str
    total_frames_processed: int
    duration_s: float


FrameExtractorFn = Callable[[Path, float, int], Awaitable[list[Path]]]
"""(video_path, fps, max_frames) → list[frame_path]."""

TranscribeFn = Callable[[Path], Awaitable[str]]
"""(audio_path) → transcript."""
```

### Main Class

```python
class VideoAgent:
    """Video comprehension pipeline.

    Args:
        vision_agent: VisionAgent to describe frames. None creates a default one.
        audio_agent: AudioAgent to transcribe the audio track. None creates a default.
        frame_extractor_fn: Callable that invokes FFmpeg via sandbox. None uses
            the default extractor (`SandboxExecutor` + ffmpeg-python).
        transcribe_fn: If None, delegates to audio_agent.
        fusion_fn: Callable that synthesizes the summary from frames + transcript.
            None uses get_multimodal_llm() or get_llm() with a fusion prompt.
        media_validator: MediaValidator.
        settings: Injectable settings.

    Example::

        agent = VideoAgent()
        result = await agent.summarize(Path("meeting.mp4"))
        print(result.summary)
        for fd in result.frame_descriptions[:5]:
            print(f"[{fd.timestamp_s:.1f}s] {fd.description}")
    """

    def __init__(
        self,
        *,
        vision_agent: VisionAgent | None = None,
        audio_agent: AudioAgent | None = None,
        frame_extractor_fn: FrameExtractorFn | None = None,
        transcribe_fn: TranscribeFn | None = None,
        fusion_fn: Callable[[str, list[FrameDescription]], Awaitable[str]] | None = None,
        media_validator: MediaValidator | None = None,
        settings: Settings | None = None,
    ) -> None: ...

    async def summarize(
        self,
        video: Path,
        *,
        fps: float | None = None,
        max_frames: int | None = None,
    ) -> VideoResult:
        """Extract frames + transcribe audio track + synthesize summary.

        Args:
            video: Path to the video file.
            fps: Frames-per-second to sample. None uses settings.video_sample_fps.
            max_frames: Maximum frames to process. None uses settings.max_frames_per_video.

        Returns:
            VideoResult.

        Raises:
            VideoAgentError: If extraction/transcription/fusion fail
                (and degrade_gracefully=False).
        """
        ...
```

---

## SPEC-MM-AGT-004: Modality Router

**File:** `prismal/agents/multimodal/modality_router.py`

### Types

```python
class Modality(str, Enum):
    TEXT = "text"
    AUDIO = "audio"
    IMAGE = "image"
    VIDEO = "video"
    MIXED = "mixed"        # multiple simultaneous modalities
    UNKNOWN = "unknown"

@dataclass(frozen=True)
class ModalityClassification:
    modality: Modality
    confidence: float
    detected_attachments: list[str]   # detected MIME types
    used_fallback_llm: bool = False
```

### Functions / Factories

```python
def classify_modality(
    message: AnyMessage,
    *,
    settings: Settings | None = None,
) -> ModalityClassification:
    """Heuristic: attachment MIME first; regex over content second.

    Makes no LLM calls. If it cannot decide, returns Modality.UNKNOWN
    with confidence=0.0 (the LLM router is invoked as an opt-in fallback).
    """
    ...


def make_modality_router_node(
    *,
    use_llm_fallback: bool = False,
    settings: Settings | None = None,
) -> Callable[[AgentState], Awaitable[dict]]:
    """Build a LangGraph node that routes by modality.

    The node returns `{"next": "<agent_name>", "metadata": {"mm": {...}}}`.

    Args:
        use_llm_fallback: If True, on Modality.UNKNOWN it calls
            get_multimodal_llm() to decide.
    """
    ...
```

---

## SPEC-MM-AGT-005: Multimodal Fusion

**File:** `prismal/agents/multimodal/multimodal_fusion.py`

### Types

```python
@dataclass(frozen=True)
class ModalContribution:
    modality: Modality
    content: str
    agent_id: str
    confidence: float

@dataclass(frozen=True)
class FusionResult:
    answer: str
    contributions: list[ModalContribution]
    strategy_used: Literal["moa", "moderator", "concat"]
```

### Main Class

```python
class MultimodalFusion:
    """Fuses outputs from modal agents into a single response.

    Args:
        strategy: "moa" (delegates to MixtureOfAgents.aggregate), "moderator"
            (a moderator LLM synthesizes), "concat" (concatenation with headers).
        moa: MixtureOfAgents instance if strategy="moa". None builds one.
        moderator_fn: Callable for strategy="moderator".
        settings: Settings.

    Example::

        fusion = MultimodalFusion(strategy="moderator")
        result = await fusion.combine([
            ModalContribution(Modality.IMAGE, "Photo of a dog.", "vision_agent", 0.9),
            ModalContribution(Modality.AUDIO, "The user asks for the name.", "audio_agent", 0.95),
        ])
        print(result.answer)
    """

    def __init__(
        self,
        *,
        strategy: Literal["moa", "moderator", "concat"] = "moderator",
        moa: MixtureOfAgents | None = None,
        moderator_fn: Callable[[str], Awaitable[str]] | None = None,
        settings: Settings | None = None,
    ) -> None: ...

    async def combine(
        self,
        contributions: list[ModalContribution],
        *,
        context: str | None = None,
    ) -> FusionResult: ...
```

---

## SPEC-MM-SUB-001: Multimodal Pipeline Subgraph

**File:** `prismal/agents/subgraphs/multimodal_pipeline/__init__.py`

```python
def build_multimodal_subgraph(
    *,
    vision_agent: VisionAgent | None = None,
    audio_agent: AudioAgent | None = None,
    video_agent: VideoAgent | None = None,
    fusion: MultimodalFusion | None = None,
    output_formatter_fn: Callable[[FusionResult, AgentState], Awaitable[dict]] | None = None,
    use_llm_router_fallback: bool = False,
    settings: Settings | None = None,
) -> SubgraphDefinition:
    """Build the end-to-end multimodal subgraph.

    Nodes:
        - router_node      → classify_modality → routes
        - vision_node      → wrap(VisionAgent)
        - audio_node       → wrap(AudioAgent)
        - video_node       → wrap(VideoAgent)
        - fusion_node      → MultimodalFusion.combine
        - output_formatter → text | TTS | structured JSON

    Returns:
        SubgraphDefinition ready to register.
    """
    ...


def register_multimodal_pipeline(
    registry: SubgraphRegistry,
    *,
    settings: Settings | None = None,
) -> None:
    """Idempotent registration (same pattern as register_ml_pipeline)."""
    ...
```

---

## SPEC-MM-RAG-001: Multimodal RAG Engine

**File:** `prismal/rag/multimodal.py`

### Types

```python
@dataclass(frozen=True)
class MultimodalRetrievedChunk:
    chunk_id: str
    content: str              # text (for image: caption; for audio/video: transcript)
    modality: Modality
    source_uri: str           # path or URL to the original medium
    score: float
    metadata: dict[str, Any]
```

### Main Class

```python
class MultimodalRAGEngine:
    """RAG with cross-modal support.

    Args:
        vector_store: ChromaVectorStore with `modality` and `source_uri` metadata.
        cross_modal_embedder: Cross-modal Embeddings (CLIP-style). If None,
            falls back to textual embeddings over captions/transcripts and emits a warning.
        image_loader / audio_loader / video_loader: injectable loaders.
        settings: Settings.

    Example::

        engine = MultimodalRAGEngine(vector_store=store)
        engine.index(Path("dataset/"))   # auto-detects types
        result = await engine.search("dog on the beach",
                                     modalities=[Modality.IMAGE, Modality.TEXT],
                                     k=5)
        for chunk in result:
            print(f"[{chunk.modality}] {chunk.source_uri} score={chunk.score:.3f}")
    """

    def __init__(
        self,
        vector_store: ChromaVectorStore,
        *,
        cross_modal_embedder: Embeddings | None = None,
        image_loader: ImageLoader | None = None,
        audio_loader: AudioLoader | None = None,
        video_loader: VideoLoader | None = None,
        settings: Settings | None = None,
    ) -> None: ...

    def index(self, path: Path) -> dict[Modality, int]:
        """Index a file or directory recursively.

        Returns:
            Count of indexed chunks by modality.
        """
        ...

    async def search(
        self,
        query: str,
        *,
        k: int = 5,
        modalities: list[Modality] | None = None,
    ) -> list[MultimodalRetrievedChunk]:
        """Search with modality filter.

        Args:
            query: Search text.
            k: Top-k to return.
            modalities: Filter. None = all modalities.

        Returns:
            Chunks ordered by descending score.
        """
        ...
```

---

## SPEC-MM-RAG-002: Multimodal Loaders

**File:** `prismal/rag/loaders/{image,audio,video}_loader.py`

```python
class ImageLoader:
    """Loads images and generates captions via VLM."""

    def __init__(self, *, vision_agent: VisionAgent | None = None) -> None: ...

    async def load(self, path: Path) -> list[Document]:
        """Return 1 Document per image with the caption as page_content and
        metadata={"modality": "image", "source_uri": str(path)}.
        """
        ...


class AudioLoader:
    """Loads audio and emits chunks per transcribed segment."""

    def __init__(self, *, stt_client: STTClient | None = None,
                 segment_chunk_chars: int = 1000) -> None: ...

    async def load(self, path: Path) -> list[Document]:
        """Documents with `modality="audio"`, content = segment text,
        metadata includes `start_s`, `end_s`."""
        ...


class VideoLoader:
    """Composes AudioLoader + ImageLoader (sampled frames)."""

    def __init__(
        self,
        *,
        audio_loader: AudioLoader | None = None,
        image_loader: ImageLoader | None = None,
        video_agent: VideoAgent | None = None,
        fps: float = 1.0,
        max_frames: int = 60,
    ) -> None: ...

    async def load(self, path: Path) -> list[Document]:
        """Mix of Documents `modality="video_frame"` and `modality="audio"`,
        all with `source_uri` pointing to the original video.
        """
        ...
```

---

## SPEC-MM-SEC-001: Media Validator

**File:** `prismal/security/media_validator.py`

### Types

```python
class MediaKind(str, Enum):
    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"

@dataclass(frozen=True)
class MediaValidationResult:
    ok: bool
    reason: str | None             # None if ok=True
    detected_mime: str | None
    detected_kind: MediaKind | None
    size_bytes: int
    duration_s: float | None        # None if not applicable (image)
```

### Main Class

```python
# Magic bytes hardcoded to avoid the optional libmagic dependency
_MAGIC_BYTES: dict[bytes, tuple[str, MediaKind]] = {
    b"\x89PNG\r\n\x1a\n":     ("image/png",  MediaKind.IMAGE),
    b"\xff\xd8\xff":           ("image/jpeg", MediaKind.IMAGE),
    b"GIF87a":                 ("image/gif",  MediaKind.IMAGE),
    b"GIF89a":                 ("image/gif",  MediaKind.IMAGE),
    b"RIFF":                   ("audio/wav",  MediaKind.AUDIO),    # validate WAVE header afterward
    b"ID3":                    ("audio/mpeg", MediaKind.AUDIO),
    b"\x00\x00\x00\x18ftyp":   ("video/mp4",  MediaKind.VIDEO),
    b"\x00\x00\x00\x20ftyp":   ("video/mp4",  MediaKind.VIDEO),
    b"\x1aE\xdf\xa3":          ("video/webm", MediaKind.VIDEO),
}


class MediaValidator:
    """Validates media before passing it to the agent.

    Checks:
        - Magic bytes match `expected_kind`.
        - Size ≤ limit per kind (configurable).
        - Duration ≤ limit per kind (audio/video).
        - Suspicious EXIF/metadata (optional, strict mode).

    Args:
        max_image_bytes: Limit per image (default 10 MB).
        max_audio_bytes: Limit per audio (default 50 MB).
        max_video_bytes: Limit per video (default 200 MB).
        max_audio_duration_s: Default 600.
        max_video_duration_s: Default 300.
        strict: If True, rejects files with suspicious EXIF.
        settings: Injectable settings (when provided, the kwargs are overrides).

    Example::

        validator = MediaValidator()
        result = validator.validate(blob, expected_kind=MediaKind.IMAGE)
        if not result.ok:
            raise MediaValidationError(result.reason)
    """

    def __init__(
        self,
        *,
        max_image_bytes: int | None = None,
        max_audio_bytes: int | None = None,
        max_video_bytes: int | None = None,
        max_audio_duration_s: float | None = None,
        max_video_duration_s: float | None = None,
        strict: bool = False,
        settings: Settings | None = None,
    ) -> None: ...

    def validate(
        self,
        media: bytes | Path,
        *,
        expected_kind: MediaKind | None = None,
    ) -> MediaValidationResult:
        """Validate the bytes/file.

        Args:
            media: Bytes or path.
            expected_kind: If specified, fails if the detected kind differs.

        Returns:
            MediaValidationResult.
        """
        ...

    def sniff(self, media: bytes | Path) -> tuple[str | None, MediaKind | None]:
        """Detect MIME and kind without enforcing limits (public for testing)."""
        ...
```

---

## Exceptions (`prismal/core/exceptions.py` — extension)

```python
class MultimodalError(PrismalError): ...      # base
class STTError(MultimodalError): ...
class TTSError(MultimodalError): ...
class VisionAgentError(MultimodalError): ...
class AudioAgentError(MultimodalError): ...
class VideoAgentError(MultimodalError): ...
class ModalityRouterError(MultimodalError): ...
class MultimodalFusionError(MultimodalError): ...
class MultimodalRAGError(RAGError): ...
class MediaValidationError(PrismalError): ...
class MissingDependencyError(PrismalError):
    """Raised when a backend whose extra is not installed is requested."""
    extra_to_install: str
```

---

## Settings (`prismal/core/config.py` — extension)

```python
# Multimodal toggles
multimodal_enabled: bool = Field(default=False, description="Enables the full multimodal layer.")
vision_enabled: bool = Field(default=False, description="Enables VisionAgent and vision_node.")
audio_enabled: bool = Field(default=False, description="Enables AudioAgent and audio_node.")
video_enabled: bool = Field(default=False, description="Enables VideoAgent and video_node.")

# Models
vision_model: str = Field(default="", description="VLM model (LiteLLM string). Reuses cua_vision_model if empty.")
multimodal_model: str = Field(default="gemini/gemini-2.0-flash",
                              description="Natively multimodal model.")
cross_modal_embedding_model: str = Field(default="open_clip:ViT-B-32",
                                         description="Cross-modal embeddings model.")

# Media limits (inherited by MediaValidator)
max_image_bytes: int = Field(default=10_485_760, ge=1024, description="10 MB default.")
max_audio_bytes: int = Field(default=52_428_800, ge=1024, description="50 MB default.")
max_video_bytes: int = Field(default=209_715_200, ge=1024, description="200 MB default.")
max_audio_duration_s: float = Field(default=600.0, ge=0.5)
max_video_duration_s: float = Field(default=300.0, ge=0.5)
max_frames_per_video: int = Field(default=60, ge=1, le=600)
video_sample_fps: float = Field(default=1.0, ge=0.1, le=10.0)

# TTS
tts_max_chars: int = Field(default=2000, ge=1, le=10_000)
vision_ocr_enabled: bool = Field(default=False)
```

---

## Interface Compatibility

### Common protocol for modal agents

```python
class ModalAgentProtocol(Protocol):
    """Informal contract to integrate with the multimodal subgraph."""
    async def process(self, media: bytes | Path, *, state: AgentState | None = None) -> Any: ...
```

`VisionAgent.analyze`, `AudioAgent.process`, `VideoAgent.summarize` expose specialized methods but all return frozen dataclasses with `.description`/`.transcript`/`.summary` that the `fusion_node` consumes via `ModalContribution`.

### State namespacing

All multimodal metadata lives under `state["metadata"]["mm"]`:

```python
state["metadata"]["mm"] = {
    "router": {"modality": "audio", "confidence": 0.97, ...},
    "vision": {"objects_detected": 3, ...},
    "audio": {"transcript_chars": 142, "tts_invoked": True},
    "video": {"frames_processed": 60, "duration_s": 58.3},
    "fusion": {"strategy_used": "moderator"},
    "preferred_output": "audio" | "text" | "json",
}
```

This isolates the new layer from the rest of the state and simplifies auditing.

---

## Change History

| Version | Date | Author | Changes |
|---|---|---|---|
| 1.0 | 2026-05-27 | Ernesto Crespo | Initial version — contracts for 14 multimodal modules |

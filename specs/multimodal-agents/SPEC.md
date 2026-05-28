# Prismal Multimodal Agents — Interface Specification

## Metadata

| Campo | Valor |
|---|---|
| **Autor** | Ernesto Crespo |
| **Estado** | `DRAFT` |
| **Versión** | 1.0 |
| **Fecha** | 2026-05-27 |
| **PLAN** | `specs/multimodal-agents/PLAN.md` |
| **Architecture** | `specs/multimodal-agents/ARCHITECTURE.md` |
| **TASKS** | `specs/multimodal-agents/TASKS.md` |

---

## Convenciones

- Todos los módulos usan `from __future__ import annotations`.
- Imports pesados (Whisper, CLIP, FFmpeg wrappers) bajo `TYPE_CHECKING` o lazy dentro de la función.
- Async donde aplique (todos los STT/TTS/VLM calls son `async`); helpers puros son `sync`.
- Dataclasses frozen donde aplique.
- Constructores aceptan `settings: Settings | None = None`.
- Ningún módulo importa `openai`, `anthropic`, `google.generativeai`, `elevenlabs`, `whisper`, `pyttsx3` directamente — todo vía `prismal/providers/`.
- Callable injection en todas las clases agente (`stt_fn`, `tts_fn`, `vision_fn`, `frame_extractor_fn`, `transcribe_fn`).
- Errores propios viven en `prismal/core/exceptions.py` (extensión).

---

## Resumen de módulos

| SPEC | Archivo | Componentes |
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

**Archivo:** `prismal/providers/stt.py`

### Tipos

```python
from enum import Enum

class STTProvider(str, Enum):
    OPENAI = "openai"      # Whisper API
    LOCAL = "local"        # openai-whisper / faster-whisper

@dataclass(frozen=True)
class STTResult:
    """Resultado de una transcripción.

    Attributes:
        text: Transcripción completa concatenada.
        language: Idioma detectado (ISO-639-1) o el solicitado.
        segments: Segmentos con timestamps; vacío si el provider no los aporta.
        provider_used: Identificador del backend usado.
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

### Clase Principal

```python
class STTClient(Protocol):
    """Interfaz uniforme de Speech-to-Text."""

    async def transcribe(
        self,
        audio: bytes | Path,
        *,
        language: str | None = None,
        prompt: str | None = None,
    ) -> STTResult:
        """Transcribe audio a texto.

        Args:
            audio: Bytes del audio o path al archivo.
            language: ISO-639-1 hint; None deja auto-detect.
            prompt: Contexto opcional (vocabulario, nombres propios).

        Returns:
            STTResult con texto, idioma y segmentos.

        Raises:
            STTError: Si la transcripción falla en el backend.
        """
        ...


def get_stt(
    provider: STTProvider | str | None = None,
    model: str | None = None,
    *,
    settings: Settings | None = None,
) -> STTClient:
    """Resuelve un STTClient según `settings.stt_provider` o el override.

    Args:
        provider: Si se especifica, override del default.
        model: Modelo (ej. "whisper-1" para openai, "base" para local).
        settings: Settings inyectables.

    Returns:
        STTClient listo para uso.

    Raises:
        STTError: Si el provider solicitado no está disponible
            (ej. extras `[multimodal-local]` no instalados).
    """
    ...
```

---

## SPEC-MM-PROV-002: TTS Provider Wrapper

**Archivo:** `prismal/providers/tts.py`

### Tipos

```python
class TTSProvider(str, Enum):
    PYTTSX3 = "pyttsx3"         # offline, default
    OPENAI = "openai"           # gpt-4o-mini-tts y similares
    ELEVENLABS = "elevenlabs"   # premium

@dataclass(frozen=True)
class TTSResult:
    audio: bytes               # WAV/MP3 según provider
    mime_type: str             # "audio/wav" | "audio/mpeg" | ...
    provider_used: str
    duration_s: float          # estimada; 0.0 si no aporta
```

### Clase Principal

```python
class TTSClient(Protocol):

    async def synthesize(
        self,
        text: str,
        *,
        voice: str | None = None,
        format: Literal["wav", "mp3"] = "wav",
    ) -> TTSResult:
        """Sintetiza texto a voz.

        Args:
            text: Texto a sintetizar (≤ settings.tts_max_chars).
            voice: ID de voz (provider-specific). None usa el default.
            format: Formato de salida.

        Returns:
            TTSResult con audio bytes y metadata.

        Raises:
            TTSError: Si el provider falla.
        """
        ...


def get_tts(
    provider: TTSProvider | str | None = None,
    *,
    settings: Settings | None = None,
) -> TTSClient:
    """Resuelve un TTSClient con fallback en cascada.

    Si el provider preferido falla en init, cae a:
        elevenlabs → openai → pyttsx3 (local, siempre disponible).
    """
    ...
```

---

## SPEC-MM-PROV-003 / 004 / 005: Vision / Multimodal LLM / Cross-Modal Embeddings

**Archivos:** `prismal/providers/vision.py`, `multimodal.py`, `cross_modal_embeddings.py`

```python
# vision.py
def get_vision_llm(
    model: str | None = None,
    *,
    settings: Settings | None = None,
) -> BaseChatModel:
    """Retorna un BaseChatModel con soporte de imágenes.

    El modelo acepta `HumanMessage(content=[{"type":"image_url","image_url":{"url":...}},
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
    """Retorna un BaseChatModel nativamente multimodal (Gemini 2.x, GPT-4o, Sonnet 4.6).

    Soporta audio + imagen + video + texto en el mismo mensaje cuando el
    modelo lo permite. Default: settings.multimodal_model.
    """
    ...


# cross_modal_embeddings.py
def get_cross_modal_embeddings(
    model: str | None = None,
    *,
    settings: Settings | None = None,
) -> Embeddings:
    """Retorna un Embeddings que acepta texto e imágenes (CLIP-style).

    Si `open_clip_torch` o el backend solicitado no están instalados,
    levanta MissingDependencyError sugiriendo `pip install "prismal[multimodal-embed]"`.
    """
    ...
```

---

## SPEC-MM-AGT-001: Vision Agent

**Archivo:** `prismal/agents/multimodal/vision_agent.py`

### Tipos

```python
@dataclass(frozen=True)
class DetectedObject:
    label: str
    confidence: float        # [0.0, 1.0]
    bbox: tuple[float, float, float, float] | None  # (x, y, w, h) normalizado o None

@dataclass(frozen=True)
class VisionResult:
    description: str
    objects: list[DetectedObject]
    ocr_text: str | None
    model_used: str
    used_fallback: bool = False


VisionFn = Callable[[bytes | Path, str], Awaitable[str]]
"""(image, prompt) → respuesta textual del VLM."""

OcrFn = Callable[[bytes | Path], Awaitable[str]]
"""(image) → OCR text."""
```

### Clase Principal

```python
class VisionAgent:
    """Agente de análisis de imágenes de propósito general.

    Args:
        vision_fn: Callable que invoca un VLM. None usa get_vision_llm().
        ocr_fn: Callable de OCR. None usa el VLM con prompt OCR.
        media_validator: Instancia de MediaValidator. None crea una por default.
        settings: Settings inyectables.

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
        """Analiza una imagen.

        Args:
            image: Bytes o path.
            prompt: Prompt custom (None usa el default: "Describe la imagen
                y lista los objetos visibles.").
            with_ocr: Override del settings.vision_ocr_enabled.

        Returns:
            VisionResult con descripción, objetos y OCR opcional.

        Raises:
            VisionAgentError: Si la validación o el VLM fallan
                (sólo si degrade_gracefully=False).
        """
        ...
```

---

## SPEC-MM-AGT-002: Audio Agent

**Archivo:** `prismal/agents/multimodal/audio_agent.py`

### Tipos

```python
@dataclass(frozen=True)
class AudioResult:
    transcript: str
    response_text: str
    response_audio: bytes | None       # None si with_tts=False
    response_mime: str | None           # "audio/wav" | ... | None
    stt_provider_used: str
    tts_provider_used: str | None
    duration_s: float                   # duración del audio entrante
```

### Clase Principal

```python
class AudioAgent:
    """Pipeline voz-a-voz: STT → razonamiento → opcional TTS.

    Args:
        stt_client: STTClient inyectable. None usa get_stt().
        tts_client: TTSClient inyectable. None usa get_tts().
        reason_fn: Callable async (transcript: str, state: AgentState) → response_text.
            None usa ProviderRegistry().get_llm() con prompt default.
        media_validator: MediaValidator. None crea default.
        settings: Settings inyectables.

    Example::

        agent = AudioAgent()
        result = await agent.process(
            audio=Path("user_voice.wav"),
            with_tts=True,
        )
        print(result.transcript)         # "Hola, ¿qué tal?"
        print(result.response_text)      # "Hola, todo bien. ¿En qué te ayudo?"
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
        """Ejecuta el pipeline completo.

        Args:
            audio: Bytes o path.
            state: AgentState para contexto del LLM. None usa state vacío.
            language: ISO-639-1 hint para STT.
            with_tts: Si True, sintetiza la respuesta.

        Returns:
            AudioResult.

        Raises:
            AudioAgentError: Si una etapa falla y degrade_gracefully=False.
        """
        ...
```

---

## SPEC-MM-AGT-003: Video Agent

**Archivo:** `prismal/agents/multimodal/video_agent.py`

### Tipos

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

### Clase Principal

```python
class VideoAgent:
    """Pipeline de comprensión de video.

    Args:
        vision_agent: VisionAgent para describir frames. None crea uno default.
        audio_agent: AudioAgent para transcribir la pista de audio. None crea default.
        frame_extractor_fn: Callable que invoca FFmpeg vía sandbox. None usa
            el extractor por default (`SandboxExecutor` + ffmpeg-python).
        transcribe_fn: Si None, delega a audio_agent.
        fusion_fn: Callable que sintetiza summary a partir de frames + transcript.
            None usa get_multimodal_llm() o get_llm() con un prompt de fusión.
        media_validator: MediaValidator.
        settings: Settings inyectables.

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
        """Extrae frames + transcribe pista de audio + sintetiza resumen.

        Args:
            video: Path al archivo de video.
            fps: Frames-per-second a samplear. None usa settings.video_sample_fps.
            max_frames: Máximo de frames a procesar. None usa settings.max_frames_per_video.

        Returns:
            VideoResult.

        Raises:
            VideoAgentError: Si extracción/transcripción/fusión fallan
                (y degrade_gracefully=False).
        """
        ...
```

---

## SPEC-MM-AGT-004: Modality Router

**Archivo:** `prismal/agents/multimodal/modality_router.py`

### Tipos

```python
class Modality(str, Enum):
    TEXT = "text"
    AUDIO = "audio"
    IMAGE = "image"
    VIDEO = "video"
    MIXED = "mixed"        # múltiples modalidades simultáneas
    UNKNOWN = "unknown"

@dataclass(frozen=True)
class ModalityClassification:
    modality: Modality
    confidence: float
    detected_attachments: list[str]   # MIME types detectados
    used_fallback_llm: bool = False
```

### Funciones / Factories

```python
def classify_modality(
    message: AnyMessage,
    *,
    settings: Settings | None = None,
) -> ModalityClassification:
    """Heurística: MIME de adjuntos primero; regex sobre content secundario.

    No hace llamadas LLM. Si no puede decidir, retorna Modality.UNKNOWN
    con confidence=0.0 (el router LLM se invoca como fallback opt-in).
    """
    ...


def make_modality_router_node(
    *,
    use_llm_fallback: bool = False,
    settings: Settings | None = None,
) -> Callable[[AgentState], Awaitable[dict]]:
    """Construye un nodo LangGraph que rutea por modalidad.

    El nodo retorna `{"next": "<agent_name>", "metadata": {"mm": {...}}}`.

    Args:
        use_llm_fallback: Si True, ante Modality.UNKNOWN llama a
            get_multimodal_llm() para decidir.
    """
    ...
```

---

## SPEC-MM-AGT-005: Multimodal Fusion

**Archivo:** `prismal/agents/multimodal/multimodal_fusion.py`

### Tipos

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

### Clase Principal

```python
class MultimodalFusion:
    """Fusiona outputs de agentes modales en una sola respuesta.

    Args:
        strategy: "moa" (delega a MixtureOfAgents.aggregate), "moderator"
            (LLM moderador sintetiza), "concat" (concatenación con headers).
        moa: Instancia de MixtureOfAgents si strategy="moa". None construye una.
        moderator_fn: Callable para strategy="moderator".
        settings: Settings.

    Example::

        fusion = MultimodalFusion(strategy="moderator")
        result = await fusion.combine([
            ModalContribution(Modality.IMAGE, "Foto de un perro.", "vision_agent", 0.9),
            ModalContribution(Modality.AUDIO, "El usuario pregunta el nombre.", "audio_agent", 0.95),
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

**Archivo:** `prismal/agents/subgraphs/multimodal_pipeline/__init__.py`

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
    """Construye el subgraph multimodal end-to-end.

    Nodos:
        - router_node      → classify_modality → rutea
        - vision_node      → wrap(VisionAgent)
        - audio_node       → wrap(AudioAgent)
        - video_node       → wrap(VideoAgent)
        - fusion_node      → MultimodalFusion.combine
        - output_formatter → texto | TTS | JSON estructurado

    Returns:
        SubgraphDefinition listo para registrar.
    """
    ...


def register_multimodal_pipeline(
    registry: SubgraphRegistry,
    *,
    settings: Settings | None = None,
) -> None:
    """Registro idempotente (mismo patrón que register_ml_pipeline)."""
    ...
```

---

## SPEC-MM-RAG-001: Multimodal RAG Engine

**Archivo:** `prismal/rag/multimodal.py`

### Tipos

```python
@dataclass(frozen=True)
class MultimodalRetrievedChunk:
    chunk_id: str
    content: str              # texto (para imagen: caption; para audio/video: transcript)
    modality: Modality
    source_uri: str           # path o URL al medio original
    score: float
    metadata: dict[str, Any]
```

### Clase Principal

```python
class MultimodalRAGEngine:
    """RAG con soporte cross-modal.

    Args:
        vector_store: ChromaVectorStore con metadata `modality` y `source_uri`.
        cross_modal_embedder: Embeddings cross-modales (CLIP-style). Si None,
            cae a embeddings textuales sobre captions/transcripts y emite warning.
        image_loader / audio_loader / video_loader: loaders inyectables.
        settings: Settings.

    Example::

        engine = MultimodalRAGEngine(vector_store=store)
        engine.index(Path("dataset/"))   # auto-detecta tipos
        result = await engine.search("perro en la playa",
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
        """Indexa un archivo o directorio recursivamente.

        Returns:
            Conteo de chunks indexados por modalidad.
        """
        ...

    async def search(
        self,
        query: str,
        *,
        k: int = 5,
        modalities: list[Modality] | None = None,
    ) -> list[MultimodalRetrievedChunk]:
        """Busca con filtro por modalidad.

        Args:
            query: Texto de búsqueda.
            k: Top-k a retornar.
            modalities: Filtro. None = todas las modalidades.

        Returns:
            Chunks ordenados por score descendente.
        """
        ...
```

---

## SPEC-MM-RAG-002: Multimodal Loaders

**Archivo:** `prismal/rag/loaders/{image,audio,video}_loader.py`

```python
class ImageLoader:
    """Carga imágenes y genera captions vía VLM."""

    def __init__(self, *, vision_agent: VisionAgent | None = None) -> None: ...

    async def load(self, path: Path) -> list[Document]:
        """Retorna 1 Document por imagen con caption como page_content y
        metadata={"modality": "image", "source_uri": str(path)}.
        """
        ...


class AudioLoader:
    """Carga audio y emite chunks por segmento transcrito."""

    def __init__(self, *, stt_client: STTClient | None = None,
                 segment_chunk_chars: int = 1000) -> None: ...

    async def load(self, path: Path) -> list[Document]:
        """Documents con `modality="audio"`, content = texto de segmento,
        metadata incluye `start_s`, `end_s`."""
        ...


class VideoLoader:
    """Compone AudioLoader + ImageLoader (frames sampleados)."""

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
        """Mix de Documents `modality="video_frame"` y `modality="audio"`,
        todos con `source_uri` apuntando al video original.
        """
        ...
```

---

## SPEC-MM-SEC-001: Media Validator

**Archivo:** `prismal/security/media_validator.py`

### Tipos

```python
class MediaKind(str, Enum):
    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"

@dataclass(frozen=True)
class MediaValidationResult:
    ok: bool
    reason: str | None             # None si ok=True
    detected_mime: str | None
    detected_kind: MediaKind | None
    size_bytes: int
    duration_s: float | None        # None si no aplica (imagen)
```

### Clase Principal

```python
# Magic bytes hardcoded para evitar dependencia opcional libmagic
_MAGIC_BYTES: dict[bytes, tuple[str, MediaKind]] = {
    b"\x89PNG\r\n\x1a\n":     ("image/png",  MediaKind.IMAGE),
    b"\xff\xd8\xff":           ("image/jpeg", MediaKind.IMAGE),
    b"GIF87a":                 ("image/gif",  MediaKind.IMAGE),
    b"GIF89a":                 ("image/gif",  MediaKind.IMAGE),
    b"RIFF":                   ("audio/wav",  MediaKind.AUDIO),    # validar WAVE header después
    b"ID3":                    ("audio/mpeg", MediaKind.AUDIO),
    b"\x00\x00\x00\x18ftyp":   ("video/mp4",  MediaKind.VIDEO),
    b"\x00\x00\x00\x20ftyp":   ("video/mp4",  MediaKind.VIDEO),
    b"\x1aE\xdf\xa3":          ("video/webm", MediaKind.VIDEO),
}


class MediaValidator:
    """Valida medios antes de pasar al agente.

    Verifica:
        - Magic bytes coinciden con `expected_kind`.
        - Tamaño ≤ límite por kind (configurable).
        - Duración ≤ límite por kind (audio/video).
        - EXIF/metadata sospechoso (opcional, modo strict).

    Args:
        max_image_bytes: Límite por imagen (default 10 MB).
        max_audio_bytes: Límite por audio (default 50 MB).
        max_video_bytes: Límite por video (default 200 MB).
        max_audio_duration_s: Default 600.
        max_video_duration_s: Default 300.
        strict: Si True, rechaza archivos con EXIF sospechoso.
        settings: Settings inyectables (cuando se proveen, los kwargs son override).

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
        """Valida los bytes/archivo.

        Args:
            media: Bytes o path.
            expected_kind: Si se especifica, falla si el kind detectado difiere.

        Returns:
            MediaValidationResult.
        """
        ...

    def sniff(self, media: bytes | Path) -> tuple[str | None, MediaKind | None]:
        """Detecta MIME y kind sin imponer límites (público para testing)."""
        ...
```

---

## Excepciones (`prismal/core/exceptions.py` — extensión)

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
    """Levantada cuando se pide un backend cuyo extra no está instalado."""
    extra_to_install: str
```

---

## Settings (`prismal/core/config.py` — extensión)

```python
# Multimodal toggles
multimodal_enabled: bool = Field(default=False, description="Habilita la capa multimodal completa.")
vision_enabled: bool = Field(default=False, description="Habilita VisionAgent y vision_node.")
audio_enabled: bool = Field(default=False, description="Habilita AudioAgent y audio_node.")
video_enabled: bool = Field(default=False, description="Habilita VideoAgent y video_node.")

# Modelos
vision_model: str = Field(default="", description="Modelo VLM (LiteLLM string). Reusa cua_vision_model si vacío.")
multimodal_model: str = Field(default="gemini/gemini-2.0-flash",
                              description="Modelo nativamente multimodal.")
cross_modal_embedding_model: str = Field(default="open_clip:ViT-B-32",
                                         description="Modelo de embeddings cross-modales.")

# Límites de medios (heredados por MediaValidator)
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

## Compatibilidad de Interfaces

### Protocolo común para agentes modales

```python
class ModalAgentProtocol(Protocol):
    """Contrato informal para integrarse al subgraph multimodal."""
    async def process(self, media: bytes | Path, *, state: AgentState | None = None) -> Any: ...
```

`VisionAgent.analyze`, `AudioAgent.process`, `VideoAgent.summarize` exponen métodos especializados pero todos retornan dataclasses frozen con `.description`/`.transcript`/`.summary` que el `fusion_node` consume vía `ModalContribution`.

### State namespacing

Todo metadata multimodal vive bajo `state["metadata"]["mm"]`:

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

Esto aísla la nueva capa del resto del state y simplifica auditoría.

---

## Historial de Cambios

| Versión | Fecha | Autor | Cambios |
|---|---|---|---|
| 1.0 | 2026-05-27 | Ernesto Crespo | Versión inicial — contratos para 14 módulos multimodales |

# Prismal Multimodal Agents — Technical Design Document

## Metadata

| Campo | Valor |
|---|---|
| **Autor** | Ernesto Crespo |
| **Estado** | `DRAFT` |
| **Versión** | 1.0 |
| **Fecha** | 2026-05-27 |
| **PLAN Relacionado** | `specs/multimodal-agents/PLAN.md` |
| **SPEC Relacionado** | `specs/multimodal-agents/SPEC.md` |
| **TASKS** | `specs/multimodal-agents/TASKS.md` |
| **Reviewers** | Tech Lead, AI Architect |

---

## 1. Contexto

Prismal opera sobre LangGraph como state machine SUPERVISOR con 26 agentes especialistas textuales. La auditoría de mayo 2026 confirmó:

- Visión: sólo `cua_agent.py` (vision-LLM para screenshots de UI).
- Audio: campos de configuración (`stt_provider`, `tts_provider`, `elevenlabs_api_key`) sin implementación que los use.
- Video: cero implementación.
- RAG: 7 engines exclusivamente textuales.

Este documento describe el diseño técnico de la **Fase F multimodal**, manteniendo todas las convenciones existentes: namespace package PEP 420, `SecurePromptBuilder`, providers vía `ProviderRegistry`, OTel spans, `get_logger()`, factory-injection para testeabilidad. El principio rector es **composición sobre extensión**: los nuevos módulos reutilizan `reflection_loop()`, `make_parallel_dispatcher()`, `mixture_of_agents.py` y `swarm_handoff()` siempre que sea posible.

---

## 2. Objetivos Técnicos

- **Correctitud:** Cada agente modal implementa su pipeline canónico (STT → razonamiento → TTS; frame extraction + transcribe + fusion; vision analyze + OCR).
- **Composabilidad:** Los agentes modales son nodos LangGraph válidos, registrables individualmente o como parte del subgraph `multimodal_pipeline`.
- **Aislamiento de providers:** Sólo `prismal/providers/` importa SDKs de Whisper/OpenAI/ElevenLabs/CLIP.
- **Seguridad por defecto:** Todo medio entrante pasa por `MediaValidator` antes de llegar al agente. Hash del medio en `AuditLogger`, nunca contenido.
- **Opt-in:** Toggles en `core/config.py` mantienen la nueva capa desactivada hasta que el operador la habilite. Dependencias pesadas son extras opcionales.

---

## 3. Arquitectura Propuesta

### 3.1 Diagrama de Alto Nivel — Módulos Nuevos

```
prismal/
├── providers/
│   ├── [EXISTENTE] registry.py            ← ProviderRegistry
│   ├── [EXISTENTE] anthropic.py / openai.py / gemini.py / ollama.py
│   ├── [NUEVO] stt.py                     ← get_stt() — Whisper API + local
│   ├── [NUEVO] tts.py                     ← get_tts() — pyttsx3 | openai | elevenlabs
│   ├── [NUEVO] vision.py                  ← get_vision_llm() (formalizado)
│   ├── [NUEVO] multimodal.py              ← get_multimodal_llm() (Gemini/GPT-4o/Sonnet)
│   └── [NUEVO] cross_modal_embeddings.py  ← get_cross_modal_embeddings() (CLIP)
│
├── agents/
│   ├── multimodal/                        ← [NUEVO directorio]
│   │   ├── __init__.py                    ← re-exports públicos
│   │   ├── vision_agent.py                ← VisionAgent
│   │   ├── audio_agent.py                 ← AudioAgent (STT → reason → TTS)
│   │   ├── video_agent.py                 ← VideoAgent (frames + transcript + fusion)
│   │   ├── modality_router.py             ← classify_modality() + router_node
│   │   └── multimodal_fusion.py           ← MultimodalFusion (reusa MoA)
│   │
│   └── subgraphs/
│       └── multimodal_pipeline/           ← [NUEVO subgraph]
│           ├── __init__.py
│           ├── builder.py                 ← build_multimodal_subgraph() + register_*()
│           ├── router_node.py
│           ├── vision_node.py
│           ├── audio_node.py
│           ├── video_node.py
│           ├── fusion_node.py
│           └── output_formatter_node.py
│
├── rag/
│   ├── [NUEVO] multimodal.py              ← MultimodalRAGEngine
│   └── loaders/                            ← [REFACTOR / NUEVO sub-paquete]
│       ├── __init__.py
│       ├── [MOVIDO] document_loader.py    (desde loaders.py raíz)
│       ├── [NUEVO] image_loader.py
│       ├── [NUEVO] audio_loader.py
│       └── [NUEVO] video_loader.py
│
├── security/
│   ├── [NUEVO] media_validator.py         ← MediaValidator (magic bytes, límites)
│   └── [EXTENSIÓN] sanitizer.py           ← InputSanitizer.sanitize_media()
│
└── core/
    └── [EXTENSIÓN] config.py              ← multimodal_* + vision_* + video_* settings
```

### 3.2 Estructura de Integración con el Grafo Principal

```
                  ┌─────────────────────────────────────┐
                  │        SUPERVISOR NODE              │
                  │     (agents/supervisor.py)           │
                  └────────────┬────────────────────────┘
                               │ routes (modality detected
                               │  by intent_router)
   ┌───────────────────────────┼──────────────────────────┐
   ▼                           ▼                          ▼
┌─────────┐         ┌────────────────────┐      ┌───────────────────┐
│ existing │         │ [NUEVO]            │      │ [NUEVO]           │
│ text     │         │ multimodal_router  │      │ multimodal_       │
│ agents   │         │ _node              │      │ pipeline_subgraph │
│ (26)     │         │  (modality_router) │      └────────┬──────────┘
└──────────┘         └──────────┬─────────┘               │
                                │ routes by               ▼
                                │ classified modality   builder.py wires:
              ┌─────────────────┼────────────────────┐    │
              ▼                 ▼                    ▼    ▼
      ┌────────────┐    ┌────────────┐      ┌────────────┐
      │ vision_    │    │ audio_     │      │ video_     │
      │ agent_node │    │ agent_node │      │ agent_node │
      └──────┬─────┘    └──────┬─────┘      └─────┬──────┘
             │                 │                  │
             └─────────────────┴──────────────────┘
                               │
                               ▼
                  ┌─────────────────────────┐
                  │ multimodal_fusion_node  │
                  │ (reusa MoA aggregator)  │
                  └────────────┬────────────┘
                               │
                               ▼
                  ┌─────────────────────────┐
                  │ output_formatter_node   │
                  │ (text | tts_audio |     │
                  │  structured response)   │
                  └────────────┬────────────┘
                               │
                               ▼
                          back to supervisor
                            or END

                  ╔═══════════════════════════════════════╗
                  ║   RAG MULTIMODAL LAYER (opt-in)       ║
                  ║   MultimodalRAGEngine                 ║
                  ║   ┌──────────┐ ┌──────────────┐      ║
                  ║   │ Text     │ │ Cross-modal  │      ║
                  ║   │ chunks   │ │ embeddings   │      ║
                  ║   └──────────┘ │ (CLIP/Image- │      ║
                  ║   ┌──────────┐ │  Bind)       │      ║
                  ║   │ Image    │ └──────────────┘      ║
                  ║   │ captions │ ┌──────────────┐      ║
                  ║   └──────────┘ │ ChromaVector │      ║
                  ║   ┌──────────┐ │ Store        │      ║
                  ║   │ Audio    │ │ + modality   │      ║
                  ║   │ transcr. │ │   metadata   │      ║
                  ║   └──────────┘ └──────────────┘      ║
                  ╚═══════════════════════════════════════╝
```

### 3.3 Componentes por Módulo

#### F1 — Providers

| Módulo | Clase / Función Principal | Backend(s) | Dependencias |
|--------|---------------------------|------------|--------------|
| `providers/stt.py` | `get_stt(provider, model)` → `STTClient` | `openai` (Whisper API), `local` (openai-whisper) | LiteLLM, opcional `openai-whisper` |
| `providers/tts.py` | `get_tts(provider)` → `TTSClient` | `pyttsx3` (default), `openai`, `elevenlabs` | `pyttsx3` (ya en config), `elevenlabs` (opcional) |
| `providers/vision.py` | `get_vision_llm(model)` → `BaseChatModel` con `invoke([{type:"image_url",...}])` | LiteLLM (Anthropic/OpenAI/Gemini) | Existente |
| `providers/multimodal.py` | `get_multimodal_llm(model)` → modelo con todas las modalidades | LiteLLM (Gemini 2.x, GPT-4o, Sonnet 4.6) | Existente |
| `providers/cross_modal_embeddings.py` | `get_cross_modal_embeddings(model)` → `Embeddings` | `open_clip_torch` / `sentence-transformers` (opcional) | Extra `[multimodal-embed]` |

#### F2 — Agentes Modales

| Módulo | Clase Principal | Patrón |
|--------|----------------|--------|
| `multimodal/vision_agent.py` | `VisionAgent` | Validate → VLM analyze → opcional OCR → return `VisionResult` |
| `multimodal/audio_agent.py` | `AudioAgent` | Validate → STT → LLM reason → opcional TTS → return `AudioResult` |
| `multimodal/video_agent.py` | `VideoAgent` | Validate → FFmpeg extract frames + audio (sandbox) → frame_descriptions + transcript → fusion LLM → `VideoResult` |
| `multimodal/modality_router.py` | `classify_modality()` + factory `make_modality_router_node()` | Inspect attachments + intent regex → enum `Modality` |
| `multimodal/multimodal_fusion.py` | `MultimodalFusion` | Compone outputs por modalidad y delega a `MixtureOfAgents` (aggregator) o moderator LLM |

#### F3 — Subgraph

`agents/subgraphs/multimodal_pipeline/builder.py` ensambla un `StateGraph[AgentState]` con:

| Nodo | Función | Edge condicional |
|---|---|---|
| `router_node` | Clasifica modalidad de la entrada | → `vision_node` / `audio_node` / `video_node` / `text_passthrough` |
| `vision_node` | Adapter de `VisionAgent` a estado LangGraph | → `fusion_node` |
| `audio_node` | Adapter de `AudioAgent` | → `fusion_node` |
| `video_node` | Adapter de `VideoAgent` | → `fusion_node` |
| `fusion_node` | `MultimodalFusion.combine()` | → `output_formatter_node` |
| `output_formatter_node` | Selecciona modalidad de salida (texto, TTS, JSON) según `state["metadata"]["mm"]["preferred_output"]` | → `END` |

El builder exporta `build_multimodal_subgraph(...)` (retorna `SubgraphDefinition`) y `register_multimodal_pipeline(registry)` idempotente, mismo patrón que `register_ml_pipeline`.

#### F4 — RAG Multimodal

| Módulo | Clase | Responsabilidad |
|--------|-------|-----------------|
| `rag/multimodal.py` | `MultimodalRAGEngine` | Indexa texto + descripción de imagen + transcripción audio/video; al buscar acepta `modalities: list[Modality]` y retorna `MultimodalRetrievedChunk` con campo `modality` y `source_uri` |
| `rag/loaders/image_loader.py` | `ImageLoader` | Carga imagen → genera caption con VLM → emite chunk con `modality=image` y URI |
| `rag/loaders/audio_loader.py` | `AudioLoader` | Carga audio → STT → emite chunks textuales por segmento + `modality=audio` |
| `rag/loaders/video_loader.py` | `VideoLoader` | Compone `AudioLoader` (pista de audio) + `ImageLoader` (frames sampleados) |

El vector store reutiliza `ChromaVectorStore`; se añade `modality` y `source_uri` a `metadata` de cada chunk.

#### F5 — Seguridad

| Módulo | Clase / Función | Responsabilidad |
|--------|----------------|-----------------|
| `security/media_validator.py` | `MediaValidator.validate(blob, kind)` | Verifica magic bytes (PNG, JPEG, MP3, WAV, MP4, WebM), tamaño máximo (configurable), duración (audio/video). Retorna `(ok: bool, reason: str)`. |
| `security/sanitizer.py` (ext) | `InputSanitizer.sanitize_media(blob, kind)` | Aplica `MediaValidator` + redacciones (EXIF strip en imágenes). |
| `security/action_interceptor.py` (ext) | `ActionInterceptor.check_media_op(op, path)` | Permisos antes de escribir/leer medios al disco. |
| `security/audit.py` (ext) | `AuditLogger.log_media(event, sha256, modality)` | Loguea hash y modalidad; nunca el contenido. |

### 3.4 Flujos de Datos Detallados

#### Flujo F1: Audio Agent (Voz a Voz)

```
audio_blob ──▶ [MediaValidator.validate(blob, "audio")]
            ──▶ [InputSanitizer.sanitize_media(blob, "audio")]
            ──▶ [AuditLogger.log_media("audio_in", sha256, "audio")]
            ──▶ [STTClient.transcribe(blob, language=settings.voice_language)]
            ──▶ transcript: str
            ──▶ [SecurePromptBuilder.build(transcript)]
            ──▶ [LLM.invoke(prompt) → response_text]
            ──▶ ¿se requiere voz?
                ├── NO ──▶ AudioResult(transcript, response_text, response_audio=None)
                └── SÍ ──▶ [TTSClient.synthesize(response_text)] ──▶ response_audio: bytes
                         ──▶ [AuditLogger.log_media("audio_out", sha256, "audio")]
                         ──▶ AudioResult(transcript, response_text, response_audio)
```

#### Flujo F2: Vision Agent

```
image ──▶ [MediaValidator.validate(image, "image")]
       ──▶ [InputSanitizer.sanitize_media(image, "image")]   # EXIF strip
       ──▶ [VLM.invoke([{"type":"image_url","image_url":...}, {"type":"text","text":prompt}])]
       ──▶ description: str + structured_objects: list
       ──▶ ¿requiere OCR? (settings.vision_ocr_enabled o por flag de llamada)
           ├── NO ──▶ VisionResult(description, objects, ocr_text=None)
           └── SÍ ──▶ [VLM second pass con prompt OCR específico] ──▶ ocr_text
                    ──▶ VisionResult(description, objects, ocr_text)
```

#### Flujo F3: Video Agent

```
video_path ──▶ [MediaValidator.validate(path, "video")]   # duración ≤ max_video_duration_s
            ──▶ [SandboxExecutor.run("ffmpeg -i ... -vf fps=N -t T frames/", limits)]
            ──▶ frames: list[Path]
            ──▶ [SandboxExecutor.run("ffmpeg -i ... -vn -ac 1 audio.wav")]
            ──▶ audio_track: Path
            ──▶ [imagehash dedup] ──▶ frames_dedup
            ──▶ asyncio.gather(
                    [VisionAgent.analyze(frame) for frame in frames_dedup],
                    AudioAgent.process(audio_track, with_tts=False)
                )
            ──▶ frame_descriptions + audio_result
            ──▶ [MultimodalFusion.combine_video(frame_descriptions, transcript)]
            ──▶ summary: str
            ──▶ VideoResult(transcript, frame_descriptions, summary)
```

#### Flujo F4: Modality Router

```
state.messages[-1] ──▶ [extract attachments + content]
                   ──▶ ¿hay attachments con MIME image/* o audio/* o video/*?
                       ├── SÍ ──▶ Modality según el primer adjunto compatible
                       └── NO ──▶ regex intent: "transcribe", "imagen", "video" → Modality
                   ──▶ ¿modalidades múltiples?
                       ├── NO ──▶ enrutar al agente modal correspondiente
                       └── SÍ ──▶ make_parallel_dispatcher() → Send(...) por modalidad
                                ──▶ fusion_node consolida los resultados
```

#### Flujo F5: Multimodal RAG Search

```
query + modalities=[text, image] ──▶ [embed(query) con cross_modal_embedder]
                                  ──▶ [ChromaVectorStore.similarity_search(emb,
                                          where={"modality":{"$in":["text","image"]}})]
                                  ──▶ list[MultimodalRetrievedChunk] (text + image captions)
                                  ──▶ ¿devolver con URIs originales?
                                      ├── SÍ ──▶ enrich chunks con source_uri para presentar
                                      │           imagen/clip al usuario
                                      └── NO ──▶ retornar tal cual
```

---

## 4. Decisiones de Diseño

### DD-MM-001: Cascaded Pipeline sobre Modelos Nativamente Multimodales

- **Decisión:** Por defecto el `multimodal_pipeline` ejecuta una cascada (STT → LLM textual → TTS; VLM → texto → opcional fusion) en vez de un solo modelo multimodal end-to-end.
- **Contexto:** Los modelos nativamente multimodales (Gemini 2.x, GPT-4o real-time) son más bajos en latencia para algunos casos pero menos observables y menos componibles.
- **Alternativas evaluadas:**

| Opción | Pros | Contras |
|---|---|---|
| **Cascaded (elegida)** | Observable (un OTel span por stage); permite insertar lógica de negocio entre stages; permite mezclar providers | Latencia más alta; más calls |
| End-to-end multimodal | Latencia más baja; menos calls | Menos observable; bloqueo a un provider; difícil insertar guardrails entre stages |

- **Justificación:** En 2026 cascaded sigue siendo el patrón pragmático para producción según los benchmarks; el end-to-end se ofrece via `get_multimodal_llm()` para casos opt-in (real-time voice).

### DD-MM-002: FFmpeg sólo a través de SandboxExecutor

- **Decisión:** Toda invocación de FFmpeg en `VideoAgent` y `VideoLoader` pasa por `SandboxExecutor` con backend docker/podman/nsjail/bwrap/firejail.
- **Contexto:** FFmpeg parsea binarios potencialmente maliciosos; ejecutarlo en el proceso principal es riesgo crítico.
- **Consecuencias:** Limits explícitos de CPU/RAM/tiempo por job; en CI se usa `bwrap` como fallback ligero.

### DD-MM-003: Cross-Modal Embeddings como Extra Opcional

- **Decisión:** `open_clip_torch` y modelos CLIP son extras `[multimodal-embed]`; sin instalar, `MultimodalRAGEngine` cae a indexar **descripciones textuales** generadas por VLM (no vectores cross-modales reales).
- **Contexto:** CLIP descarga ~1 GB y requiere PyTorch; muchos usuarios no lo querrán.
- **Consecuencias:** El engine retorna metadatos sobre qué método de embedding se usó para que el usuario decida si necesita upgrade.

### DD-MM-004: Validación de Medios Antes del Sanitizer

- **Decisión:** `MediaValidator.validate()` se ejecuta **antes** del `InputSanitizer`; no en lugar de.
- **Contexto:** El sanitizer histórico opera sobre texto; añadir validación de medios al sanitizer mezcla responsabilidades.
- **Consecuencias:** Nuevo módulo `security/media_validator.py`; `InputSanitizer.sanitize_media(blob, kind)` delega a `MediaValidator` internamente para mantener una sola superficie pública desde la capa agente.

### DD-MM-005: Modality Router con Heurística + Override de Settings

- **Decisión:** `classify_modality(message)` usa heurística determinista (MIME del adjunto si existe; regex sobre intent) antes de cualquier LLM. Se puede forzar modalidad con `state["metadata"]["mm"]["force_modality"]`.
- **Contexto:** Un LLM classifier añade latencia y costo; las heurísticas resuelven el 95% de los casos.
- **Consecuencias:** Si la heurística no determina (`Modality.UNKNOWN`), se llama a `get_multimodal_llm()` como fallback.

### DD-MM-006: Subgraph Opt-In via register_multimodal_pipeline()

- **Decisión:** El subgraph multimodal sigue el patrón de Fase A/B/C/D: NO se registra automáticamente en `graph.py`. Operación llama `register_multimodal_pipeline(registry)` cuando esté lista.
- **Consecuencias:** Es **aditivo**, no rompe nada. El intent router se extiende con un patrón opt-in que se activa cuando `settings.multimodal_enabled=True`.

### DD-MM-007: Callable Injection en Todos los Agentes

- **Decisión:** `VisionAgent`, `AudioAgent`, `VideoAgent` aceptan callables inyectables (`stt_fn`, `tts_fn`, `vision_fn`, `frame_extractor_fn`, `transcribe_fn`). Defaults usan `ProviderRegistry`.
- **Contexto:** Mismo patrón que `LATSAgent`, `LLMCompiler`, `ConstitutionalFilter`. Permite tests sin LLM/FFmpeg reales.
- **Consecuencias:** Coverage objetivo ≥ 80% sin requerir GPU ni binarios externos en CI.

### DD-MM-008: Hash-First Audit, Content-Never

- **Decisión:** `AuditLogger.log_media(event, sha256, modality, size_bytes, duration_s)` registra metadata pero **nunca el blob**.
- **Contexto:** Logs deben ser archivables sin riesgo de PII/datos sensibles del medio.
- **Consecuencias:** Forensics se hace recuperando el blob original por hash desde un store opcional, no del audit log.

---

## 5. Estructura del Código

```
prismal/
│
├── providers/
│   ├── __init__.py            ← añade re-exports nuevos
│   ├── stt.py                 ← STTClient + get_stt()
│   ├── tts.py                 ← TTSClient + get_tts()
│   ├── vision.py              ← get_vision_llm()
│   ├── multimodal.py          ← get_multimodal_llm()
│   └── cross_modal_embeddings.py
│
├── agents/
│   └── multimodal/
│       ├── __init__.py
│       ├── vision_agent.py
│       ├── audio_agent.py
│       ├── video_agent.py
│       ├── modality_router.py
│       └── multimodal_fusion.py
│
├── agents/subgraphs/multimodal_pipeline/
│   ├── __init__.py            ← exports build_*, register_*
│   ├── builder.py
│   ├── router_node.py
│   ├── vision_node.py
│   ├── audio_node.py
│   ├── video_node.py
│   ├── fusion_node.py
│   └── output_formatter_node.py
│
├── rag/
│   ├── multimodal.py          ← MultimodalRAGEngine
│   └── loaders/
│       ├── __init__.py
│       ├── document_loader.py (movido desde loaders.py)
│       ├── image_loader.py
│       ├── audio_loader.py
│       └── video_loader.py
│
├── security/
│   ├── media_validator.py
│   └── (extensiones a sanitizer.py, action_interceptor.py, audit.py)
│
tests/
├── unit/
│   ├── providers/
│   │   ├── test_stt.py
│   │   ├── test_tts.py
│   │   └── test_cross_modal_embeddings.py
│   ├── agents/multimodal/
│   │   ├── test_vision_agent.py
│   │   ├── test_audio_agent.py
│   │   ├── test_video_agent.py
│   │   ├── test_modality_router.py
│   │   └── test_multimodal_fusion.py
│   ├── rag/
│   │   ├── test_multimodal.py
│   │   └── test_loaders_image_audio_video.py
│   └── security/
│       └── test_media_validator.py
└── integration/
    ├── test_multimodal_pipeline_e2e.py     (LLM mockeado, FFmpeg real opcional)
    └── test_multimodal_rag_e2e.py
```

### Patrones Aplicados

| Patrón | Dónde | Por qué |
|---|---|---|
| Factory + Callable injection | Todos los agentes modales | Testeo sin LLM/FFmpeg reales |
| Strategy | `STTClient`, `TTSClient` backends | Backends intercambiables sin tocar agentes |
| Composite | `VideoAgent` | Compone `VisionAgent` + `AudioAgent` |
| Adapter | Nodos del subgraph | Adaptan agentes a la interfaz LangGraph (`AgentState → state_update`) |
| Facade | `MultimodalRAGEngine` | Unifica indexación cross-modal sobre Chroma + loaders |
| Cascade (chain) | `audio_agent.process()` | STT → LLM → TTS encadenados con guardrails entre etapas |

### Manejo de Errores

```python
# core/exceptions.py — extensiones nuevas
class MultimodalError(PrismalError): ...      # base
class STTError(MultimodalError): ...
class TTSError(MultimodalError): ...
class VisionAgentError(MultimodalError): ...
class AudioAgentError(MultimodalError): ...
class VideoAgentError(MultimodalError): ...
class ModalityRouterError(MultimodalError): ...
class MultimodalRAGError(RAGError): ...
class MediaValidationError(PrismalError): ...   # rechazo de medios inválidos
```

Política: cada agente modal **degrada graceful** por default (`degrade_gracefully=True`); los `*Error` se lanzan sólo si el caller explícitamente desactiva el degrade.

---

## 6. Seguridad

### 6.1 Superficie de Ataque — Nueva Capa

| Vector | Mitigación |
|---|---|
| Archivo malicioso (malware embebido en imagen/audio/video) | `MediaValidator` magic bytes + límite de tamaño; FFmpeg en sandbox |
| Prompt injection vía OCR de imagen o subtítulos de video | `SecurePromptBuilder` aplica a transcript y OCR antes de pasar al LLM |
| Filtrado de PII en transcripciones | `InputSanitizer` aplica al transcript igual que a texto entrante |
| Exfiltración vía audio sintetizado (data hiding) | TTS sólo se invoca con texto que pasó por `GuardrailsEngine` |
| EXIF con geolocalización en imágenes | `InputSanitizer.sanitize_media()` strip EXIF por default |
| FFmpeg con args inyectados | Argumentos hardcoded; nunca user-controlled; ejecutado en sandbox |
| Hashes de medios en logs delatan presencia | Hash es SHA-256, no del contenido; sin metadata de origen en el hash |
| RCE via deserialización (CLIP/Whisper local) | Modelos cargados desde HF con `trust_remote_code=False` |

### 6.2 Reglas Transversales

1. **Ningún módulo nuevo importa SDKs de provider directamente** — sólo `prismal/providers/`.
2. **Todo medio entrante pasa por `MediaValidator`** antes de llegar al agente.
3. **Hash en `AuditLogger`, nunca contenido** del medio.
4. **`ActionInterceptor.check_media_op()`** antes de escribir/leer medios al disco.
5. **`SecurePromptBuilder`** aplica también a transcripciones, OCR, descripciones de imagen antes de pasar al LLM downstream.
6. **FFmpeg sólo via `SandboxExecutor`**.

---

## 7. Observabilidad

### 7.1 OTel Spans por Etapa

| Componente | Spans |
|---|---|
| STT | `mm.stt.validate`, `mm.stt.transcribe` |
| TTS | `mm.tts.synthesize`, `mm.tts.audit` |
| Vision Agent | `mm.vision.validate`, `mm.vision.analyze`, `mm.vision.ocr` |
| Audio Agent | `mm.audio.stt`, `mm.audio.reason`, `mm.audio.tts` |
| Video Agent | `mm.video.validate`, `mm.video.extract_frames`, `mm.video.transcribe_audio`, `mm.video.fuse` |
| Modality Router | `mm.router.classify` |
| Fusion | `mm.fusion.combine` |
| Multimodal RAG | `mm.rag.index_image`, `mm.rag.index_audio`, `mm.rag.index_video`, `mm.rag.search` |
| Security | `mm.security.validate_media`, `mm.security.sanitize_media` |

### 7.2 Métricas Clave

```
# Contadores
mm_stt_requests_total{provider="openai|local", status="success|error"}
mm_tts_requests_total{provider="pyttsx3|openai|elevenlabs", status="..."}
mm_vision_analyze_total{ocr="enabled|disabled"}
mm_video_summarize_total
mm_router_classify_total{modality="text|audio|image|video|mixed|unknown"}
mm_fusion_combine_total{strategy="moa|moderator"}
mm_rag_search_total{modalities="..."}
mm_media_validation_rejected_total{reason="oversize|format|duration|magic_bytes"}

# Histogramas
mm_stt_latency_seconds{provider}
mm_tts_latency_seconds{provider}
mm_vision_latency_seconds
mm_video_latency_seconds
mm_pipeline_e2e_latency_seconds       ← latencia voz-a-voz total

# Gauges
mm_audio_bytes_processed_total
mm_video_seconds_processed_total
mm_image_bytes_processed_total
```

---

## 8. Testing Strategy

| Nivel | Cobertura | Herramientas | Qué cubre |
|---|---|---|---|
| Unit | ≥ 80% por módulo | pytest + `AsyncMock` + fixtures de medios mínimos | Validadores, parsers, dataclasses, factories con callables mockeados |
| Integration | Pipelines críticos | pytest + ChromaDB in-memory + FFmpeg opcional | Multimodal pipeline end-to-end con LLM mockeado |
| Live API | `@pytest.mark.live_api` | Skip por default | Validación real contra Whisper/ElevenLabs/CLIP |
| Security | `@pytest.mark.security` | pytest | `MediaValidator` rechaza ataques conocidos (zip-bomb, polyglot files) |

### Estrategia de Mock

- **STT/TTS:** `AsyncMock` retorna transcripts/audio_bytes deterministas.
- **VLM:** `AsyncMock` retorna `AIMessage(content="descripción mock")`.
- **FFmpeg:** `SandboxExecutor.run` mockeado para devolver paths de frames sintéticos (PNGs ≤1KB generados con `Pillow`).
- **CLIP:** `get_cross_modal_embeddings()` mockeado retorna vectores de dim configurable (default 512).

### Fixtures de medios

- `tests/fixtures/media/tiny.png` (1×1 PNG), `tiny.mp3` (1s silencio), `tiny.mp4` (1s pantalla negra).
- Todos < 1 KB.

---

## 9. Plan de Rollout

### 9.1 Estrategia de Integración

Fase F sigue la convención de Fase A/B/C: **registro aditivo, opt-in**.

1. `register_multimodal_pipeline(registry)` se llama desde el startup del operador si `settings.multimodal_enabled=True`.
2. `intent_router.py` añade patrones regex (`r"(?i)\b(transcribe|imagen|video|voz)\b"`) que rutean al `multimodal_router_node`.
3. `supervisor.py` añade `"multimodal_router"`, `"vision_agent"`, `"audio_agent"`, `"video_agent"` a `VALID_NEXT_NODES` cuando el toggle esté activo.
4. `tool_registry.py`: extensión de `DEFAULT_CAPABILITY_MAP` con entries multimodales:

| Node | Capabilities |
|---|---|
| `multimodal_router` | `["general"]` |
| `vision_agent` | `["vision", "general"]` |
| `audio_agent` | `["audio", "general"]` |
| `video_agent` | `["vision", "audio", "video", "general"]` |

5. `config/mcp_servers.yaml` se documenta con capabilities `vision`, `audio`, `video` para servidores MCP especializados (ej. OCR-as-a-service).

### 9.2 Backward Compatibility

- Sin `settings.multimodal_enabled=True`, el grafo se comporta idéntico a hoy.
- Sin extras `[multimodal]` instalados, los imports fallan con `MissingDependencyError` claro al construir el agente, no en import time.
- `MultimodalRAGEngine` con CLIP no instalado cae a captions textuales y emite warning.
- Los agentes de texto existentes no ven cambios en su `AgentState`; los nuevos campos viven en `state["metadata"]["mm"]`.

---

## 10. Preguntas Abiertas

- [ ] **STT local:** ¿`openai-whisper` o `faster-whisper` por default cuando `stt_provider="local"`? — Owner: AI Architect, Deadline: inicio F1.
- [ ] **TTS streaming:** ¿soportar streaming de chunks de audio (para latencia <1s) o sólo síntesis completa en v1? — Owner: Tech Lead, Deadline: inicio F2.
- [ ] **Cross-modal embeddings:** ¿CLIP base o SigLIP por default cuando esté disponible el extra? — Owner: AI Architect, Deadline: inicio F4.
- [ ] **Frame sampling:** ¿fps fijo (1/s) o adaptativo por detección de cambio? — Owner: Engineer, Deadline: inicio F2 (`VideoAgent`).
- [ ] **Output formatter:** ¿la decisión texto/voz se hace por settings, por flag en `state["metadata"]["mm"]["preferred_output"]`, o por intent classifier? — Owner: Tech Lead.
- [ ] **MediaValidator:** ¿integrar `python-magic` (libmagic) además de magic-bytes hardcoded para mayor robustez? — Owner: Tech Lead, Deadline: inicio F5.

---

## Historial de Cambios

| Versión | Fecha | Autor | Cambios |
|---|---|---|---|
| 1.0 | 2026-05-27 | Ernesto Crespo | Versión inicial — diseño técnico Fase F multimodal |

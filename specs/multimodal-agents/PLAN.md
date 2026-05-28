# Prismal — Multimodal Agents Expansion

## Strategic Plan / Product Requirements Document (PLAN)

| Campo | Valor |
|---|---|
| **Autor** | Ernesto Crespo |
| **Estado** | `DRAFT` |
| **Versión** | 1.0 |
| **Fecha** | 2026-05-27 |
| **Reviewers** | Tech Lead, AI Architect |
| **Documentos relacionados** | `ARCHITECTURE.md`, `SPEC.md`, `TASKS.md` |

---

## 1. Resumen Ejecutivo

Prismal hoy es un framework de agentes orientado exclusivamente a texto. Las modalidades no textuales aparecen sólo de forma fragmentaria: el `CUAgent` (`prismal/agents/cua_agent.py`) consume capturas de pantalla con un VLM para automatizar el navegador, y `core/config.py` declara campos para una interfaz de voz (`stt_provider`, `tts_provider`, `elevenlabs_api_key`) que no están conectados a ningún nodo ni subgraph. No existe procesamiento de video, ni RAG cross-modal, ni un agente multimodal de propósito general.

Este documento define los requisitos para integrar una **arquitectura multimodal completa (Fase F)** que cierre el hueco con el estado del arte 2026: agentes especializados para visión, audio y video; un subgraph orquestador que enruta y fusiona modalidades; ingesta y RAG multimodal; y proveedores STT/TTS/VLM consolidados detrás de `ProviderRegistry`. La nueva capa **no rompe** ninguna de las 19 arquitecturas Fase A/B/C/D/E ya en producción — se añade siguiendo el patrón factory-injection que el resto del repo ya emplea.

El entregable es un nuevo dominio `prismal/agents/multimodal/` + extensiones a `prismal/rag/` y `prismal/providers/`, registrable opt-in vía `register_multimodal_pipeline()`, alineado con las reglas críticas del repo (`SecurePromptBuilder`, `ActionInterceptor`, `AuditLogger`, providers aislados, namespace package PEP 420).

---

## 2. Contexto y Problema

### 2.1 Situación Actual

Auditoría del repositorio (mayo 2026):

- **Visión (parcial):** `cua_agent.py` consulta `provider_registry.get_vision_llm()` para interpretar screenshots del navegador, pero el método es opcional y no hay un agente de visión de propósito general (análisis de imágenes arbitrarias, OCR, descripción, clasificación).
- **Audio (sólo configuración):** `core/config.py` líneas 657-680 declara `stt_provider`, `tts_provider`, `elevenlabs_api_key`, `voice_language`, `voice_record_seconds`. **No hay implementación**: ningún nodo LangGraph, ningún `audio_agent`, ningún handler que materialice esa configuración.
- **Video:** Cero implementación. Sin loaders, sin extracción de frames, sin transcripción A/V, sin tools.
- **RAG cross-modal:** Las 7 engines de `prismal/rag/` son exclusivamente textuales (`ChromaVectorStore` con embeddings de texto; `loaders.py` sólo de documentos). No hay embeddings CLIP/ImageBind ni soporte de chunks no-texto en el vector store.
- **Orquestación:** El `supervisor_node` rutea entre 26 agentes textuales. No existe routing por modalidad ni fusión cross-modal.

### 2.2 Problema

Sin una arquitectura multimodal, Prismal no puede atender casos de uso donde la entrada es voz, imagen o video — ni siquiera con extensiones del usuario, porque las primitivas necesarias no existen. Esto excluye al framework de dominios completos: asistentes de voz, análisis de medios, accesibilidad, vigilancia/seguridad, e-commerce visual, soporte por screen-share, transcripción y resumen de reuniones, generación TTS para outputs largos, y RAG sobre repositorios mixtos (PDFs con imágenes, videos con subtítulos, podcasts).

### 2.3 Oportunidad

El estándar 2026 ya consolidó un patrón claro (cascaded multimodal pipeline event-driven con orquestador + expertos modales + fusión), y LangGraph soporta nativamente tipos de mensaje multimodales. La infraestructura base de Prismal (providers, security, monitoring, subgraph registry, factory injection) cubre el 80% del trabajo: sólo faltan los módulos y el wiring. El costo es acotado y el resultado posiciona a Prismal a paridad con frameworks como ADK multimodal, Gemini-LangGraph y los stacks de Salesforce Agentforce.

---

## 3. Usuarios Objetivo

### Persona 1: Multimodal AI Engineer
- **Descripción:** Construye asistentes de voz, sistemas de análisis de imágenes, pipelines de procesamiento de video.
- **Necesidad principal:** Componer agentes que acepten audio/imagen/video como entrada y emitan respuestas en la modalidad apropiada (texto, voz sintetizada).
- **Frecuencia de uso:** Diario.

### Persona 2: Accessibility/Voice UX Designer
- **Descripción:** Diseña experiencias accesibles (low-latency voice, descripción de imágenes para usuarios con discapacidad visual).
- **Necesidad principal:** Latencia voz-a-voz <800ms, transcripción confiable multi-idioma, fallback graceful entre modalidades.
- **Frecuencia de uso:** Semanal (diseño) / Diario (validación).

### Persona 3: Media/Knowledge-Base RAG Engineer
- **Descripción:** Indexa corpus mixtos (PDFs con figuras, videos con subtítulos, podcasts, imágenes de producto) para Q&A.
- **Necesidad principal:** Embeddings cross-modales, retrieval que devuelva la modalidad correcta para cada query, fusión de evidencia visual/auditiva/textual.
- **Frecuencia de uso:** Diario.

---

## 4. Objetivos y Métricas de Éxito

### 4.1 Objetivos del Negocio

| Objetivo | Métrica | Target | Plazo |
|---|---|---|---|
| Cobertura modal | Modalidades soportadas (texto/audio/imagen/video) | 4/4 con I/O completo | Fase F |
| Latencia voz-a-voz | p95 sobre 50 turnos | ≤ 1500 ms (objetivo aspiracional 800 ms) | Fase F |
| Recall RAG multimodal | Recall@5 sobre corpus mixto interno | ≥ +10% vs solo texto | Fase F |
| Composabilidad | Reutilizar primitivas Fase A/B/C | ≥ 60% del código nuevo apoya `reflection_loop`, `make_parallel_dispatcher`, `mixture_of_agents`, `swarm_handoff` | Fase F |
| Cobertura de tests | Branch coverage módulos nuevos | ≥ 80% | Global |

### 4.2 Objetivos de Usuario

| Objetivo del Usuario | Indicador |
|---|---|
| Hablar con el agente y recibir voz | Pipeline STT → supervisor → TTS funciona end-to-end con `pyttsx3` por default y `elevenlabs`/`openai` opcionales |
| Subir una imagen y obtener análisis | `VisionAgent.analyze(image)` retorna descripción, objetos detectados, OCR si aplica |
| Subir un video y obtener resumen | `VideoAgent.summarize(path)` extrae frames + transcripción + fusión LLM |
| Buscar en corpus mixto | `MultimodalRAGEngine.search(query, modalities=[...])` devuelve evidencia cross-modal |
| Decidir modalidad de salida | `modality_router_node` elige texto/voz/imagen según contexto |

---

## 5. Alcance

### 5.1 In Scope (Incluido — Fase F)

**F1 — Provider extensions (`prismal/providers/`):**
- [ ] `get_stt()` — wrapper LiteLLM para Whisper API + backend local opcional.
- [ ] `get_tts()` — wrapper para `pyttsx3` (default), `openai`, `elevenlabs`.
- [ ] `get_vision_llm()` — formalizado y documentado (hoy es método opcional en `ProviderRegistry`).
- [ ] `get_multimodal_llm()` — modelos nativamente multimodales (Gemini 2.x, GPT-4o, Claude Sonnet 4.6).
- [ ] `get_cross_modal_embeddings()` — CLIP / ImageBind para vectores cross-modales.

**F2 — Agent modales (`prismal/agents/multimodal/`):**
- [ ] `vision_agent.py` — `VisionAgent` para análisis de imágenes de propósito general (descripción, detección, OCR).
- [ ] `audio_agent.py` — `AudioAgent` con STT, segmentación, detección de hablante (opcional), TTS de respuesta.
- [ ] `video_agent.py` — `VideoAgent` con extracción de frames (FFmpeg vía sandbox), transcripción de pista de audio, resumen temporal.
- [ ] `modality_router.py` — clasifica entrada (texto/audio/imagen/video/mixta) y enruta.
- [ ] `multimodal_fusion.py` — agregador que sintetiza salidas de los expertos modales (reutiliza `mixture_of_agents.py`).

**F3 — Subgraph orquestador (`prismal/agents/subgraphs/multimodal_pipeline/`):**
- [ ] `modality_router → [vision | audio | video | text en paralelo] → fusion → response_formatter`.
- [ ] Soporte HITL en el router (`hitl_gate()`) si la modalidad de salida tiene impacto (ej. enviar audio).
- [ ] Output formatter que elige modalidad de respuesta (texto/voz/imagen generada).

**F4 — RAG multimodal (`prismal/rag/multimodal.py`):**
- [ ] `MultimodalRAGEngine` — indexa chunks textuales + descripciones de imagen + transcripciones; embeddings cross-modales.
- [ ] Soporte en `ChromaVectorStore` para metadata de modalidad (`modality: text|image|audio|video_frame`).
- [ ] Loaders nuevos: `image_loader.py`, `audio_loader.py`, `video_loader.py` bajo `prismal/rag/loaders/`.

**F5 — Seguridad multimodal (`prismal/security/`):**
- [ ] `MediaValidator` — verifica magic bytes, límites de tamaño/duración, formato permitido antes de pasar al pipeline.
- [ ] Extensión de `InputSanitizer` con `sanitize_media(blob, kind)`.
- [ ] `ActionInterceptor.check_media_op()` — permisos antes de operaciones sobre archivos de medios.
- [ ] Auditoría en `AuditLogger` con hash de medios (no contenido).

**F6 — Configuración (`prismal/core/config.py`):**
- [ ] Activar/extender campos existentes de voz; añadir `vision_*`, `video_*`, `multimodal_*`.
- [ ] Toggles `multimodal_enabled`, `vision_enabled`, `audio_enabled`, `video_enabled` (default `False`).
- [ ] Límites: `max_audio_duration_s`, `max_video_duration_s`, `max_image_bytes`, `max_frames_per_video`.

**F7 — Integración LangGraph (`prismal/agents/`):**
- [ ] Registrar nuevos nodos en `graph.py` (opt-in vía settings).
- [ ] Extender `intent_router.py` con detección de modalidad (regex sobre tipo MIME / sufijo de archivo en mensajes adjuntos).
- [ ] Añadir entries al `DEFAULT_CAPABILITY_MAP` del `tool_registry.py` para los nuevos nodos.

**Transversal:**
- [ ] OTel spans por etapa multimodal.
- [ ] Métricas: `multimodal_*_requests_total`, `multimodal_*_latency_seconds`, `multimodal_*_errors_total`.
- [ ] Tests unitarios y de integración (≥ 80% coverage).

### 5.2 Out of Scope (Excluido)

- **Síntesis de imagen/video generativa** (DALL·E, Sora, Stable Diffusion) — se evalúa en Fase G; aquí sólo *análisis* y *generación de voz* (TTS).
- **Streaming bidireccional WebRTC** — el pipeline trabaja sobre archivos o blobs; streaming de baja latencia (<300ms full-duplex) requiere arquitectura distinta y queda para Fase H.
- **Diarización de hablantes en tiempo real** — la versión inicial usa transcripción simple; diarización (`pyannote.audio`) es Fase G.
- **Fine-tuning de VLMs / TTS** — requiere infraestructura GPU dedicada.
- **UI/Frontend de captura de audio o cámara** — `prismal` es framework, no app. La captura es responsabilidad del consumidor (app `lightagent`/CLI/web).

### 5.3 Futuras Consideraciones (Fase G+)

- Streaming bidireccional sub-300ms (WebRTC + RTMS).
- Diarización y separación de fuentes.
- Generación de imágenes/video (modelos generativos visuales).
- Embeddings cross-modales especializados (SigLIP, AudioCLIP).
- Voice cloning y modulación emocional (ElevenLabs voice design).
- Comprensión de documentos largos con figuras (LongVila, PaliGemma).

---

## 6. Requisitos Funcionales (Resumen — detalle en `SPEC.md`)

| ID | Requisito | Prioridad |
|---|---|---|
| RF-MM-001 | STT vía `get_stt()` con backends Whisper API / local; idioma configurable | `MUST` |
| RF-MM-002 | TTS vía `get_tts()` con backends pyttsx3 / openai / elevenlabs | `MUST` |
| RF-MM-003 | `VisionAgent.analyze(image)` retorna descripción + objetos + OCR opcional | `MUST` |
| RF-MM-004 | `AudioAgent.process(audio)` ejecuta STT → razonamiento → TTS o texto | `MUST` |
| RF-MM-005 | `VideoAgent.summarize(video)` extrae frames + transcribe pista + sintetiza | `SHOULD` |
| RF-MM-006 | `modality_router_node` clasifica entrada y enruta a experto modal | `MUST` |
| RF-MM-007 | `multimodal_fusion` agrega salidas cross-modales con MoA o moderator | `MUST` |
| RF-MM-008 | `MultimodalRAGEngine.search(query, modalities)` devuelve evidencia mixta | `SHOULD` |
| RF-MM-009 | `MediaValidator` rechaza medios fuera de límites/formato | `MUST` |
| RF-MM-010 | Subgraph completo `multimodal_pipeline/` registrable vía `register_multimodal_pipeline()` | `MUST` |

---

## 7. Requisitos No Funcionales

### Rendimiento
- STT (clip ≤30s, Whisper API): ≤ 4 s p95.
- TTS (texto ≤500 chars, pyttsx3 local): ≤ 1.5 s p95; (elevenlabs): ≤ 3 s p95.
- Vision analyze (1 imagen ≤4 MB): ≤ 5 s p95.
- Video summarize (clip ≤2 min): ≤ 60 s p95.
- Multimodal pipeline end-to-end (1 imagen + query): ≤ 7 s p95.
- Latencia voz-a-voz objetivo: ≤ 1500 ms p95 (aspiracional 800 ms cuando todo el stack es local).

### Seguridad
- Todo medio entrante pasa por `MediaValidator` (magic bytes + límites) antes del agente.
- Hash SHA-256 del medio se loguea en `AuditLogger`; **nunca el contenido**.
- Prompts construidos a partir de transcripciones o descripciones pasan por `SecurePromptBuilder`.
- Ningún módulo importa SDKs de provider directamente — todo vía `prismal/providers/`.
- `ActionInterceptor.check_media_op()` antes de escribir/leer medios al disco.

### Disponibilidad
- Cada agente modal expone `degrade_gracefully=True` por default: si el provider falla, retorna `partial_result=True` en vez de exception.
- TTS con fallback en cascada: elevenlabs → openai → pyttsx3 local.

### Escalabilidad
- `MultimodalRAGEngine` soporta colecciones ChromaDB ≥ 1M vectores mixtos (limitación heredada del store).
- Procesamiento de video usa `SandboxExecutor` para FFmpeg con límite de CPU/RAM por job.

### Observabilidad
- OTel spans: `mm.stt`, `mm.tts`, `mm.vision.analyze`, `mm.video.extract_frames`, `mm.video.transcribe`, `mm.router.classify`, `mm.fusion`.
- Métricas mínimas por agente: `requests_total`, `latency_seconds`, `errors_total`, `bytes_processed_total`.

### Mantenibilidad
- Coverage ≥ 80% por módulo nuevo. `ruff check` y `mypy --strict` pasan. `bandit` sin HIGH/CRITICAL.
- Callable-injection en todas las factories (`stt_fn`, `tts_fn`, `vision_fn`, `frame_extractor_fn`) — tests sin dependencias externas reales.

---

## 8. Restricciones y Dependencias

### Restricciones Técnicas
- Python 3.13+, `uv` como gestor.
- `prismal/` sigue siendo namespace package PEP 420 (no `__init__.py` en la raíz).
- `_MAX_TOTAL_TOOLS = 120` — los nuevos agentes no deben inflar el pool.
- LangGraph `StateGraph` como motor único de orquestación.

### Dependencias Externas

| Dependencia | Tipo | Uso | Estado |
|---|---|---|---|
| `openai-whisper` (opcional) | Nueva | STT local | ☐ Añadir como extra `[multimodal-local]` |
| `pyttsx3` | Existente (config) | TTS offline | ☐ Verificar en pyproject extras |
| `elevenlabs` | Nueva opcional | TTS premium | ☐ Añadir como extra `[multimodal-premium]` |
| `Pillow` | Nueva | Validación y manipulación básica de imágenes | ☐ Añadir como extra `[multimodal]` |
| `ffmpeg` (binario sistema) | Externa | Extracción de frames + audio de video | ☐ Documentar requisito de OS |
| `ffmpeg-python` | Nueva | Wrapper sandboxed para FFmpeg | ☐ Añadir como extra `[multimodal]` |
| `open_clip_torch` / `sentence-transformers` | Nueva opcional | Embeddings cross-modales | ☐ Añadir como extra `[multimodal-embed]` |
| `imagehash` | Nueva opcional | Deduplicación de frames | ☐ Añadir como extra `[multimodal]` |

Todas las dependencias multimodales son **opcionales** (gated por extras) — instalación base de `prismal` no se infla.

---

## 9. User Stories (extracto)

### Épica F: Voz End-to-End

**US-MM-001:** Como AI Engineer, quiero un pipeline de voz funcional para construir asistentes de voz sin escribir el wiring STT/TTS.
- [ ] `AudioAgent.process(audio_blob)` retorna `AudioResult(transcript, response_text, response_audio)`.
- [ ] Configurable backend STT/TTS sin tocar código del agente.

### Épica F: Análisis Visual

**US-MM-002:** Como Knowledge-Base Engineer, quiero analizar imágenes y guardar su descripción para RAG futuro.
- [ ] `VisionAgent.analyze(image)` devuelve `VisionResult(description, objects, ocr_text)`.
- [ ] La descripción es indexable por `MultimodalRAGEngine`.

### Épica F: Video y Reuniones

**US-MM-003:** Como Media RAG Engineer, quiero subir un video y obtener su resumen + transcripción.
- [ ] `VideoAgent.summarize(path)` retorna `VideoResult(transcript, frame_descriptions, summary)`.
- [ ] Funciona en clips de hasta 2 minutos sin OOM.

### Épica F: Orquestación Cross-Modal

**US-MM-004:** Como Arquitecto IA, quiero un subgraph único que acepte mensajes con cualquier modalidad y rute automáticamente.
- [ ] `multimodal_pipeline` registrado vía `register_multimodal_pipeline()`.
- [ ] El router clasifica con confianza ≥ 0.85 en test set de modalidad.

---

## 10. Riesgos y Mitigaciones

| Riesgo | Probabilidad | Impacto | Mitigación |
|---|---|---|---|
| Latencia voz-a-voz por encima de objetivo | Alta | Alto | Permitir fallback a `pyttsx3` local; documentar trade-off; medir p95 en CI |
| Costos LLM con video (muchos frames) | Alta | Medio | Deduplicación por `imagehash`; sampling adaptativo (1 frame/s default, configurable) |
| FFmpeg en sandbox falla por permisos | Media | Alto | Documentar requisitos OS; usar `SandboxExecutor` con backend docker como default robusto |
| Embeddings cross-modales heavy (CLIP carga ~1 GB) | Alta | Medio | Lazy load; extras `[multimodal-embed]` opcional; fallback a indexar descripciones textuales |
| Validador de medios rechaza falsos positivos | Media | Medio | Lista de magic bytes documentada; modo permisivo opcional con warning |
| Privacidad: transcripciones contienen PII | Alta | Alto | `InputSanitizer` aplica a transcripciones igual que texto entrante; PII redacted en `AuditLogger` |
| Inflación de `tool_registry` con tools multimodales | Media | Medio | Capability routing (Fase E): cada agente recibe sólo tools relevantes a su modalidad |

---

## 11. Timeline Estimado

| Fase | Duración | Entregable |
|---|---|---|
| F1 — Providers (STT/TTS/Vision/Multimodal/Embeddings) | 1 semana | Wrappers en `prismal/providers/` + tests |
| F2 — Agentes modales (vision/audio/video/router/fusion) | 2 semanas | 5 módulos nuevos en `prismal/agents/multimodal/` |
| F3 — Subgraph `multimodal_pipeline/` | 1 semana | Pipeline registrable + tests |
| F4 — RAG multimodal + loaders | 1 semana | `MultimodalRAGEngine` + 3 loaders |
| F5 — Seguridad multimodal | 0.5 semana | `MediaValidator`, extensiones de Sanitizer/Interceptor/Audit |
| F6 — Config + toggles | 0.2 semana | Nuevos campos en `core/config.py` |
| F7 — Integración LangGraph + intent router + capability routing | 0.5 semana | Wiring opt-in en `graph.py` y `intent_router.py` |
| Hardening — Coverage, docs, security audit | 0.8 semana | ≥ 80% coverage; bandit clean; docs actualizadas |
| **Total** | **~7 semanas** | Arquitectura multimodal completa, 5 nuevos agentes + 1 subgraph + 1 RAG engine |

---

## 12. Definición de Done (Global de Fase F)

- [ ] 5 agentes multimodales + 1 subgraph + 1 RAG engine + 1 MediaValidator implementados.
- [ ] `uv run pytest -m "not live_api"` pasa al 100%.
- [ ] Coverage ≥ 80% sobre `prismal/agents/multimodal/`, `prismal/rag/multimodal.py`, y los nuevos loaders.
- [ ] `uv run ruff check .` y `uv run mypy prismal` sin errores.
- [ ] `uv run bandit -r prismal -c pyproject.toml` sin HIGH/CRITICAL.
- [ ] `CLAUDE.md` y `README.md` actualizados con la sección multimodal.
- [ ] `pyproject.toml` con extras `[multimodal]`, `[multimodal-local]`, `[multimodal-premium]`, `[multimodal-embed]`.
- [ ] `config/mcp_servers.yaml` extendido con capabilities `audio`, `vision`, `video` (opcional).
- [ ] PR mergeado a `main` con code review aprobado.

---

## Historial de Cambios

| Versión | Fecha | Autor | Cambios |
|---|---|---|---|
| 1.0 | 2026-05-27 | Ernesto Crespo | Versión inicial — Fase F multimodal (audio, video, vision, RAG, fusion) |

## Aprobaciones

| Rol | Nombre | Fecha | Estado |
|---|---|---|---|
| Tech Lead | — | | ☐ Pendiente |
| AI Architect | — | | ☐ Pendiente |

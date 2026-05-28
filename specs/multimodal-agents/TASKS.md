# Prismal Multimodal Agents — Implementation Plan (TASKS)

## Metadata

| Campo | Valor |
|---|---|
| **Autor** | Ernesto Crespo |
| **Estado** | `DRAFT` |
| **Versión** | 1.0 |
| **Fecha** | 2026-05-27 |
| **PLAN** | `specs/multimodal-agents/PLAN.md` |
| **Architecture** | `specs/multimodal-agents/ARCHITECTURE.md` |
| **SPEC** | `specs/multimodal-agents/SPEC.md` |

---

## 1. Resumen de Implementación

La Fase F multimodal se divide en **7 sub-fases ejecutables independientemente** más una de hardening:

- **F1 (semana 1):** Providers — STT, TTS, Vision, Multimodal LLM, Cross-Modal Embeddings.
- **F2 (semanas 2-3):** Agentes modales — Vision, Audio, Video, Modality Router, Multimodal Fusion.
- **F3 (semana 4):** Subgraph `multimodal_pipeline/` con builder y register idempotente.
- **F4 (semana 5):** RAG multimodal — `MultimodalRAGEngine` + 3 loaders.
- **F5 (semana 5.5):** Seguridad — `MediaValidator`, extensiones a Sanitizer/Interceptor/Audit.
- **F6 (semana 5.7):** Config + toggles + extras en `pyproject.toml`.
- **F7 (semana 6):** Integración LangGraph + intent router + capability routing.
- **Hardening (semana 7):** Coverage, docs, security audit, integration tests.

**Duración total estimada:** 7 semanas
**Equipo mínimo:** 1 engineer senior con LangGraph + experiencia multimedia (FFmpeg, audio basics).
**Fecha objetivo:** 2026-07-15

---

## 2. Pre-requisitos

| Pre-requisito | Owner | Estado | Fecha Límite |
|---|---|---|---|
| PLAN.md aprobado | Tech Lead | ☐ Pendiente | 2026-06-01 |
| ARCHITECTURE.md aprobado | Tech Lead + AI Architect | ☐ Pendiente | 2026-06-01 |
| SPEC.md aprobado | Tech Lead | ☐ Pendiente | 2026-06-01 |
| Decisión sobre STT local (`openai-whisper` vs `faster-whisper`) | AI Architect | ☐ Pendiente | Inicio F1 |
| Decisión sobre `python-magic` opcional para `MediaValidator` | Tech Lead | ☐ Pendiente | Inicio F5 |
| Extras en `pyproject.toml` documentados | Engineer | ☐ Pendiente | Inicio F1 |
| Branch `feature/multimodal-agents` creado | Engineer | ☐ Pendiente | Inicio F1 |
| Suite de tests existente pasa al 100% (688+ tests) | Engineer | ☐ Verificar | Inicio F1 |
| FFmpeg disponible en CI runner | DevOps | ☐ Verificar | Inicio F2 (`VideoAgent`) |

---

## 3. Fases de Implementación

---

### FASE F1 — Providers

**Duración:** 1 semana (semana 1) | **Objetivo:** wrappers limpios sobre STT/TTS/VLM/multimodal/embeddings cross-modales, todos aislados en `prismal/providers/`.

#### F1-01 — STT wrapper
**Estimación:** 1.5 días | **Archivo:** `prismal/providers/stt.py`

| ID | Tarea | Estimación | Dependencia | Estado |
|---|---|---|---|---|
| F1-01-01 | Crear `STTProvider` enum, `STTResult`, `STTSegment` dataclasses | 0.2d | — | ☐ |
| F1-01-02 | Implementar backend `openai` (Whisper API via LiteLLM) | 0.5d | F1-01-01 | ☐ |
| F1-01-03 | Implementar backend `local` (lazy import opcional) | 0.5d | F1-01-01 | ☐ |
| F1-01-04 | `get_stt()` factory con resolución por settings + override | 0.2d | F1-01-02, F1-01-03 | ☐ |
| F1-01-05 | Tests unitarios con `AsyncMock` (≥ 80% coverage) | 0.5d | F1-01-04 | ☐ |
| F1-01-06 | Excepción `STTError` en `core/exceptions.py` | 0.1d | — | ☐ |

**Criterios de Done:**
- `STTClient` protocol + 2 implementaciones funcionales.
- `get_stt(provider="openai")` retorna client funcional con LLM mockeado en tests.
- Coverage ≥ 80% en `providers/stt.py`.
- `ruff` + `mypy --strict` pasan.

---

#### F1-02 — TTS wrapper
**Estimación:** 1.5 días | **Archivo:** `prismal/providers/tts.py`

| ID | Tarea | Estimación | Dependencia | Estado |
|---|---|---|---|---|
| F1-02-01 | `TTSProvider` enum + `TTSResult` dataclass | 0.2d | — | ☐ |
| F1-02-02 | Backend `pyttsx3` (offline, baseline) | 0.5d | F1-02-01 | ☐ |
| F1-02-03 | Backend `openai` (gpt-4o-mini-tts via LiteLLM) | 0.3d | F1-02-01 | ☐ |
| F1-02-04 | Backend `elevenlabs` (lazy import opcional) | 0.5d | F1-02-01 | ☐ |
| F1-02-05 | `get_tts()` con cascada elevenlabs → openai → pyttsx3 | 0.3d | F1-02-02..04 | ☐ |
| F1-02-06 | Tests + excepción `TTSError` | 0.5d | F1-02-05 | ☐ |

**Criterios de Done:**
- Fallback en cascada verificado con tests (mockear fallo del primario).
- `pyttsx3` siempre disponible (no requiere extras).

---

#### F1-03 — Vision LLM wrapper
**Estimación:** 0.5 día | **Archivo:** `prismal/providers/vision.py`

| ID | Tarea | Estimación | Dependencia | Estado |
|---|---|---|---|---|
| F1-03-01 | Formalizar `get_vision_llm(model)` con LiteLLM | 0.3d | — | ☐ |
| F1-03-02 | Tests + integración con `provider_registry` legacy (compatibilidad CUA) | 0.2d | F1-03-01 | ☐ |

**Criterios de Done:**
- `CUAgent` sigue funcionando idénticamente (regresión zero).

---

#### F1-04 — Multimodal LLM wrapper
**Estimación:** 0.5 día | **Archivo:** `prismal/providers/multimodal.py`

| ID | Tarea | Estimación | Dependencia | Estado |
|---|---|---|---|---|
| F1-04-01 | `get_multimodal_llm(model)` con default `gemini/gemini-2.0-flash` | 0.3d | — | ☐ |
| F1-04-02 | Tests con mocks | 0.2d | F1-04-01 | ☐ |

---

#### F1-05 — Cross-Modal Embeddings wrapper
**Estimación:** 1 día | **Archivo:** `prismal/providers/cross_modal_embeddings.py`

| ID | Tarea | Estimación | Dependencia | Estado |
|---|---|---|---|---|
| F1-05-01 | `get_cross_modal_embeddings(model)` con backend `open_clip` (lazy) | 0.5d | — | ☐ |
| F1-05-02 | `MissingDependencyError` cuando extras no instalados | 0.2d | F1-05-01 | ☐ |
| F1-05-03 | Tests con embedder mockeado | 0.3d | F1-05-01 | ☐ |

**Criterios Globales F1:**
- 5 módulos en `prismal/providers/`, todos con tests ≥ 80% coverage.
- 0 imports directos de SDKs en módulos fuera de `providers/`.
- 3 excepciones nuevas en `core/exceptions.py`: `STTError`, `TTSError`, `MissingDependencyError`.

---

### FASE F2 — Agentes Modales

**Duración:** 2 semanas (semanas 2-3) | **Objetivo:** 5 agentes/routers en `prismal/agents/multimodal/`.

#### F2-01 — VisionAgent
**Estimación:** 2.5 días | **Archivo:** `prismal/agents/multimodal/vision_agent.py`

| ID | Tarea | Estimación | Dependencia | Estado |
|---|---|---|---|---|
| F2-01-01 | Crear directorio `agents/multimodal/` + `__init__.py` | 0.1d | — | ☐ |
| F2-01-02 | `VisionResult`, `DetectedObject` dataclasses | 0.2d | F2-01-01 | ☐ |
| F2-01-03 | `VisionAgent.__init__` con callables `vision_fn`, `ocr_fn` | 0.3d | F2-01-02 | ☐ |
| F2-01-04 | `analyze(image, with_ocr)` — validate → VLM → parse | 1d | F2-01-03, F1-03, F5-01 | ☐ |
| F2-01-05 | OCR path (segundo VLM call con prompt OCR) | 0.4d | F2-01-04 | ☐ |
| F2-01-06 | OTel spans + métricas | 0.2d | F2-01-04 | ☐ |
| F2-01-07 | Tests unitarios con VLM mockeado (15+ tests, ≥80% coverage) | 0.8d | F2-01-04, F2-01-05 | ☐ |
| F2-01-08 | Excepción `VisionAgentError` en `core/exceptions.py` | 0.1d | — | ☐ |

---

#### F2-02 — AudioAgent
**Estimación:** 2 días | **Archivo:** `prismal/agents/multimodal/audio_agent.py`

| ID | Tarea | Estimación | Dependencia | Estado |
|---|---|---|---|---|
| F2-02-01 | `AudioResult` dataclass | 0.1d | — | ☐ |
| F2-02-02 | `AudioAgent.__init__` con `stt_client`, `tts_client`, `reason_fn` | 0.3d | F1-01, F1-02 | ☐ |
| F2-02-03 | `process(audio, with_tts)` — validate → STT → reason → TTS opcional | 0.8d | F2-02-02 | ☐ |
| F2-02-04 | Audit logging (hash de audio in + out, nunca contenido) | 0.3d | F2-02-03, F5-04 | ☐ |
| F2-02-05 | OTel spans + métricas | 0.2d | F2-02-03 | ☐ |
| F2-02-06 | Tests unitarios (14+ tests, ≥80% coverage) | 0.6d | F2-02-03 | ☐ |
| F2-02-07 | Excepción `AudioAgentError` | 0.1d | — | ☐ |

---

#### F2-03 — VideoAgent
**Estimación:** 3.5 días | **Archivo:** `prismal/agents/multimodal/video_agent.py`

| ID | Tarea | Estimación | Dependencia | Estado |
|---|---|---|---|---|
| F2-03-01 | `VideoResult`, `FrameDescription` dataclasses | 0.2d | — | ☐ |
| F2-03-02 | `frame_extractor_fn` default usando `SandboxExecutor` + `ffmpeg-python` | 1d | F5-01 (validator) | ☐ |
| F2-03-03 | `summarize(video, fps, max_frames)` — extract → dedup → vision+audio en paralelo → fusion | 1d | F2-03-02, F2-01, F2-02 | ☐ |
| F2-03-04 | Dedup de frames con `imagehash` (opcional, gated por extra) | 0.4d | F2-03-03 | ☐ |
| F2-03-05 | OTel spans + métricas | 0.2d | F2-03-03 | ☐ |
| F2-03-06 | Tests unitarios con FFmpeg mockeado + frames sintéticos (12+ tests) | 0.7d | F2-03-03 | ☐ |
| F2-03-07 | Excepción `VideoAgentError` | 0.1d | — | ☐ |

**Riesgos F2-03:**
- FFmpeg en CI puede no estar disponible — tests core deben funcionar sin él (callable inyectado).
- Latencia: limitar `max_frames` default a 60 para evitar costos LLM altos en CI.

---

#### F2-04 — ModalityRouter
**Estimación:** 1.5 días | **Archivo:** `prismal/agents/multimodal/modality_router.py`

| ID | Tarea | Estimación | Dependencia | Estado |
|---|---|---|---|---|
| F2-04-01 | `Modality` enum + `ModalityClassification` dataclass | 0.2d | — | ☐ |
| F2-04-02 | `classify_modality()` heurística (MIME + regex) | 0.4d | F2-04-01 | ☐ |
| F2-04-03 | `make_modality_router_node()` factory LangGraph-compatible | 0.3d | F2-04-02 | ☐ |
| F2-04-04 | LLM fallback opcional (opt-in via `use_llm_fallback=True`) | 0.3d | F1-04, F2-04-03 | ☐ |
| F2-04-05 | Tests unitarios (10+ tests, cubrir mixed/unknown/text/each modality) | 0.3d | F2-04-03 | ☐ |
| F2-04-06 | Excepción `ModalityRouterError` | 0.1d | — | ☐ |

---

#### F2-05 — MultimodalFusion
**Estimación:** 1.5 días | **Archivo:** `prismal/agents/multimodal/multimodal_fusion.py`

| ID | Tarea | Estimación | Dependencia | Estado |
|---|---|---|---|---|
| F2-05-01 | `ModalContribution`, `FusionResult` dataclasses | 0.2d | — | ☐ |
| F2-05-02 | `MultimodalFusion.__init__` con strategy `moa|moderator|concat` | 0.2d | F2-05-01 | ☐ |
| F2-05-03 | `combine()` para strategy `concat` (baseline) | 0.2d | F2-05-02 | ☐ |
| F2-05-04 | `combine()` strategy `moderator` (delega a LLM call) | 0.3d | F2-05-02 | ☐ |
| F2-05-05 | `combine()` strategy `moa` (delega a `MixtureOfAgents.aggregate`) | 0.3d | F2-05-02 | ☐ |
| F2-05-06 | Tests unitarios (10+ tests cubriendo 3 strategies) | 0.3d | F2-05-03..05 | ☐ |
| F2-05-07 | Excepción `MultimodalFusionError` | 0.1d | — | ☐ |

**Criterios Globales F2:**
- 5 agentes/utilities, todos con tests ≥ 80% coverage.
- Reutilización demostrada: `MultimodalFusion` strategy=`moa` invoca `prismal/agents/patterns/mixture_of_agents.py`.
- Suite total no debe regresar: 688 tests previos + ~61 nuevos = ~749.

---

### FASE F3 — Subgraph `multimodal_pipeline/`

**Duración:** 1 semana (semana 4) | **Archivo:** `prismal/agents/subgraphs/multimodal_pipeline/`

| ID | Tarea | Estimación | Dependencia | Estado |
|---|---|---|---|---|
| F3-01 | Crear estructura del directorio + `__init__.py` | 0.2d | F2 done | ☐ |
| F3-02 | `router_node.py` — adapter de `make_modality_router_node()` | 0.3d | F2-04 | ☐ |
| F3-03 | `vision_node.py`, `audio_node.py`, `video_node.py` — adapters de los agentes | 0.5d | F2-01..03 | ☐ |
| F3-04 | `fusion_node.py` — adapter de `MultimodalFusion` | 0.3d | F2-05 | ☐ |
| F3-05 | `output_formatter_node.py` — decide texto / TTS / JSON según `state["metadata"]["mm"]["preferred_output"]` | 0.5d | F2-02 (TTS) | ☐ |
| F3-06 | `builder.py` — `build_multimodal_subgraph()` retorna `SubgraphDefinition` | 0.8d | F3-02..05 | ☐ |
| F3-07 | `register_multimodal_pipeline(registry)` idempotente | 0.2d | F3-06 | ☐ |
| F3-08 | Tests unitarios por nodo (20+ tests) | 0.8d | F3-02..05 | ☐ |
| F3-09 | Test integración end-to-end del subgraph (LLM/FFmpeg mockeados) | 0.7d | F3-06 | ☐ |
| F3-10 | Excepción `MultimodalSubgraphError` (reusa `MultimodalError`) | 0.1d | — | ☐ |

**Criterios Globales F3:**
- Subgraph registrable y testeable sin red.
- Conditional edges del router verificados: cada modalidad llega al nodo correcto.
- Test end-to-end pasa con audio + imagen mockeados.

---

### FASE F4 — RAG Multimodal

**Duración:** 1 semana (semana 5) | **Archivos:** `prismal/rag/multimodal.py` + `prismal/rag/loaders/`

#### F4-01 — Refactor de loaders existentes
**Estimación:** 0.5 día

| ID | Tarea | Estimación | Dependencia | Estado |
|---|---|---|---|---|
| F4-01-01 | Mover `prismal/rag/loaders.py` a `prismal/rag/loaders/document_loader.py` | 0.2d | — | ☐ |
| F4-01-02 | Crear `prismal/rag/loaders/__init__.py` con re-exports backward-compatible | 0.2d | F4-01-01 | ☐ |
| F4-01-03 | Verificar 0 regresiones en imports existentes | 0.1d | F4-01-02 | ☐ |

---

#### F4-02 — Loaders multimodales
**Estimación:** 2 días

| ID | Tarea | Estimación | Dependencia | Estado |
|---|---|---|---|---|
| F4-02-01 | `ImageLoader` — usa `VisionAgent` para caption | 0.5d | F2-01 | ☐ |
| F4-02-02 | `AudioLoader` — usa `STTClient` + segmentación por chars | 0.7d | F1-01 | ☐ |
| F4-02-03 | `VideoLoader` — compone `AudioLoader` + frames vía `VideoAgent` | 0.5d | F4-02-01, F4-02-02, F2-03 | ☐ |
| F4-02-04 | Tests por loader (15+ tests cumulativos) | 0.3d | F4-02-01..03 | ☐ |

---

#### F4-03 — MultimodalRAGEngine
**Estimación:** 2 días | **Archivo:** `prismal/rag/multimodal.py`

| ID | Tarea | Estimación | Dependencia | Estado |
|---|---|---|---|---|
| F4-03-01 | `MultimodalRetrievedChunk` dataclass | 0.1d | — | ☐ |
| F4-03-02 | `MultimodalRAGEngine.__init__` con loaders inyectables | 0.3d | F4-02 | ☐ |
| F4-03-03 | `index(path)` — auto-detecta tipo (vía `MediaValidator.sniff`) y delega a loader | 0.6d | F5-01, F4-02 | ☐ |
| F4-03-04 | `search(query, k, modalities)` con filtro por metadata `modality` | 0.5d | F4-03-03 | ☐ |
| F4-03-05 | Fallback a captions textuales cuando `cross_modal_embedder=None` + warning | 0.2d | F4-03-04 | ☐ |
| F4-03-06 | Tests unitarios (20+ tests) | 0.7d | F4-03-04 | ☐ |
| F4-03-07 | Excepción `MultimodalRAGError` (hereda de `RAGError`) | 0.1d | — | ☐ |

**Criterios Globales F4:**
- Loaders reutilizan agentes de F2 (no duplican lógica de VLM/STT).
- `MultimodalRAGEngine.search(modalities=[Modality.IMAGE])` filtra correctamente.
- Sin extras de embeddings: el engine sigue funcionando con captions textuales.

---

### FASE F5 — Seguridad

**Duración:** 0.5 semana | **Archivos:** `prismal/security/`

| ID | Tarea | Estimación | Dependencia | Estado |
|---|---|---|---|---|
| F5-01 | Crear `prismal/security/media_validator.py` con `MediaValidator`, `MediaKind`, `MediaValidationResult`, `_MAGIC_BYTES` | 1d | — | ☐ |
| F5-02 | Tests de `MediaValidator`: magic bytes correctos, falsos positivos, polyglot, oversize, duración | 0.5d | F5-01 | ☐ |
| F5-03 | Extender `InputSanitizer.sanitize_media(blob, kind)` con EXIF strip vía `Pillow` | 0.4d | F5-01 | ☐ |
| F5-04 | Extender `AuditLogger.log_media(event, sha256, modality, size_bytes, duration_s)` | 0.3d | — | ☐ |
| F5-05 | Extender `ActionInterceptor.check_media_op(op, path)` con permisos por kind | 0.3d | — | ☐ |
| F5-06 | Excepción `MediaValidationError` | 0.1d | — | ☐ |
| F5-07 | Tests integración: agente recibe medio inválido → bloqueado antes de LLM | 0.4d | F5-01..05 | ☐ |

**Criterios Globales F5:**
- Test específico: PNG con magic bytes JPEG es rechazado.
- Test específico: archivo de 100 MB con `max_image_bytes=10 MB` es rechazado.
- EXIF de geolocalización removido en imágenes procesadas (testeado).

---

### FASE F6 — Config + Toggles + Pyproject

**Duración:** 0.2 semana

| ID | Tarea | Estimación | Dependencia | Estado |
|---|---|---|---|---|
| F6-01 | Añadir campos `multimodal_*`, `vision_*`, `video_*`, `tts_max_chars`, `max_image_bytes`, etc. a `core/config.py` | 0.5d | — | ☐ |
| F6-02 | Extras en `pyproject.toml`: `[multimodal]`, `[multimodal-local]`, `[multimodal-premium]`, `[multimodal-embed]` | 0.3d | — | ☐ |
| F6-03 | `env.example` actualizado con nuevas variables `PRISMAL_MULTIMODAL_*` | 0.1d | F6-01 | ☐ |
| F6-04 | Tests de validación de settings (límites Pydantic) | 0.2d | F6-01 | ☐ |

---

### FASE F7 — Integración LangGraph + Intent Router + Capability Routing

**Duración:** 0.5 semana

| ID | Tarea | Estimación | Dependencia | Estado |
|---|---|---|---|---|
| F7-01 | `register_multimodal_pipeline()` invocado opt-in en startup cuando `settings.multimodal_enabled=True` | 0.3d | F3 | ☐ |
| F7-02 | Añadir `"multimodal_router"`, `"vision_agent"`, `"audio_agent"`, `"video_agent"` a `VALID_NEXT_NODES` (gated por toggle) | 0.3d | F3 | ☐ |
| F7-03 | Extender `intent_router.py` con regex `r"(?i)\b(transcribe|imagen|video|voz|audio)\b"` + detección de adjunto MIME | 0.4d | — | ☐ |
| F7-04 | Extender `DEFAULT_CAPABILITY_MAP` en `tool_registry.py` con entries `multimodal_router`, `vision_agent`, `audio_agent`, `video_agent` | 0.2d | — | ☐ |
| F7-05 | Documentar capabilities `audio`, `vision`, `video` en `config/mcp_servers.yaml` (entries con `enabled: false`) | 0.1d | F7-04 | ☐ |
| F7-06 | Tests integración con grafo compilado: query con adjunto imagen → llega a `vision_agent` | 0.7d | F7-01..04 | ☐ |

**Criterios Globales F7:**
- Sin `multimodal_enabled=True`, los 26 agentes textuales se comportan idénticos a hoy (regresión zero).
- Con toggle activo, los 4 nuevos nodos aparecen como destinos válidos del supervisor.

---

### HARDENING — Coverage, Docs, Security Audit

**Duración:** 1 semana (semana 7)

| ID | Tarea | Estimación | Dependencia | Estado |
|---|---|---|---|---|
| H-01 | Coverage audit: cada módulo nuevo ≥ 80% | 0.5d | F1..F7 | ☐ |
| H-02 | `bandit -r prismal -c pyproject.toml` HIGH=0 MEDIUM=0 | 0.3d | F1..F7 | ☐ |
| H-03 | Test integración end-to-end voz-a-voz con providers mockeados | 0.5d | F3, F7 | ☐ |
| H-04 | Test integración end-to-end RAG multimodal sobre corpus mixto pequeño | 0.5d | F4 | ☐ |
| H-05 | Test de regresión: 688 tests previos siguen pasando | 0.3d | F1..F7 | ☐ |
| H-06 | Actualizar `CLAUDE.md` con sección multimodal + módulos nuevos | 0.3d | F1..F7 | ☐ |
| H-07 | Actualizar `README.md` con sección "Multimodal" en features + arquitectura | 0.4d | F1..F7 | ☐ |
| H-08 | Actualizar `CHANGELOG.md` con entrada Fase F | 0.2d | — | ☐ |
| H-09 | Verificar `ruff check .` y `mypy --strict` clean en todo lo nuevo | 0.3d | F1..F7 | ☐ |
| H-10 | Crear `examples/multimodal_pipeline.py` ejecutable | 0.5d | F3, F7 | ☐ |
| H-11 | Code review interno (1 reviewer aprueba PR) | 1d | H-01..09 | ☐ |
| H-12 | Merge a `main` | 0.2d | H-11 | ☐ |

---

## 4. Dependencias Inter-Tareas

```
F1 (providers) ─┬──▶ F2 (agentes) ─┬──▶ F3 (subgraph) ─┐
                │                   │                   │
                └──▶ F4 (RAG) ──────┘                   │
                                                         ▶ F7 (integration)
F5 (security) ──┬───────────────────────────────────────┤
                │                                        │
F6 (config) ────┴────────────────────────────────────────┘
                                                          │
                                                          ▼
                                              HARDENING ─▶ MERGE
```

- F1 → F2 (agentes consumen wrappers).
- F2 → F3 (subgraph wrappea agentes).
- F5 → F2 (`MediaValidator` requerido antes de cualquier agente).
- F4 puede arrancar en paralelo con F3 si F2 ya está completo.
- F6 puede ir en paralelo desde día 1 (independiente).
- F7 espera F3 + F4 completos.

---

## 5. Matriz de Riesgos y Mitigaciones

| Riesgo | Probabilidad | Impacto | Mitigación | Owner |
|---|---|---|---|---|
| FFmpeg no disponible en CI | Media | Alto | Tests core con callable mockeado; CI marker `@pytest.mark.requires_ffmpeg` para los integration | Engineer |
| Latencia voz-a-voz por encima de 1500ms | Alta | Alto | Permitir cascada local (Whisper local + pyttsx3); medir p95 en CI | Engineer |
| Costos LLM en video (muchos frames) | Alta | Medio | Cap `max_frames_per_video=60` default; dedup por `imagehash`; sampling 1 fps default | Engineer |
| `open_clip_torch` añade 1 GB al instalar | Alta | Bajo | Extra opcional `[multimodal-embed]`; documentar | Engineer |
| Magic bytes hardcoded no cubren todos los formatos | Media | Medio | Documentar formatos soportados; modo permisivo opt-in; opción `[multimodal-magic]` con `python-magic` | Tech Lead |
| EXIF strip rompe metadata legítima | Baja | Bajo | Strip por default sólo en sanitizer; flag opt-in para preservar | Engineer |
| Regresión en `CUAgent` por refactor de `get_vision_llm` | Media | Alto | Tests existentes deben pasar 100% antes de aceptar F1-03 | Engineer |
| Inflación del pool de tools (>120) | Media | Medio | Capability routing (Fase E) — los agentes multimodales reciben sólo tools relevantes | Engineer |
| Estado `state["metadata"]["mm"]` colisiona con keys existentes | Baja | Bajo | Namespace `mm.*` reservado; grep en repo confirma 0 usos previos | Engineer |
| Audit log crece rápido con hashes de medios | Media | Bajo | Rotación de log existente (heredada); tamaño por entry ≤ 1 KB | Engineer |

---

## 6. Definición de Done (Global de Fase F)

Para cerrar Fase F como COMPLETED:

- [ ] 5 wrappers de providers (`stt`, `tts`, `vision`, `multimodal`, `cross_modal_embeddings`).
- [ ] 5 agentes/utilities en `prismal/agents/multimodal/`.
- [ ] 1 subgraph `multimodal_pipeline/` con builder + register idempotente.
- [ ] 1 RAG engine `MultimodalRAGEngine` + 3 loaders (image/audio/video).
- [ ] 1 `MediaValidator` + extensiones de Sanitizer/Interceptor/Audit.
- [ ] Settings/toggles nuevos + extras en `pyproject.toml`.
- [ ] Integración opt-in con `graph.py` / `supervisor.py` / `intent_router.py` / `tool_registry.py`.
- [ ] `uv run pytest -m "not live_api"` pasa al 100% (688+ existentes + ~140 nuevos = ~828+).
- [ ] Coverage ≥ 80% por módulo nuevo (`pytest --cov=prismal --cov-fail-under=80`).
- [ ] `uv run ruff check .` sin errores.
- [ ] `uv run mypy prismal` sin errores en strict mode.
- [ ] `uv run bandit -r prismal -c pyproject.toml` sin HIGH/CRITICAL.
- [ ] `CLAUDE.md`, `README.md`, `CHANGELOG.md` actualizados.
- [ ] `examples/multimodal_pipeline.py` ejecutable end-to-end con providers mockeados.
- [ ] PR mergeado a `main` con 1 reviewer aprobado.

---

## 7. Estimación de Esfuerzo por Sub-Fase

| Sub-Fase | Sub-tareas | Días | Semanas |
|---|---|---|---|
| F1 — Providers | 22 | 5 | 1 |
| F2 — Agentes modales | 36 | 11 | 2 |
| F3 — Subgraph | 10 | 5 | 1 |
| F4 — RAG multimodal | 14 | 5 | 1 |
| F5 — Seguridad | 7 | 3 | 0.5 |
| F6 — Config + extras | 4 | 1 | 0.2 |
| F7 — Integración | 6 | 2 | 0.5 |
| Hardening | 12 | 5 | 1 |
| **Total** | **~111** | **~37** | **~7** |

*Estimación basada en 1 engineer senior. Con 2 engineers: F1, F5, F6 pueden ir en paralelo desde semana 1; F4 en paralelo con F2-03 desde semana 2.*

---

## 8. Métricas de Éxito Operacionales

Tras merge a `main`, monitorear semana 1:

- `mm_pipeline_e2e_latency_seconds` p95 ≤ 1500 ms.
- `mm_media_validation_rejected_total` por reason — alertar si `magic_bytes` >5% del total (posible ataque).
- `mm_stt_requests_total{status="error"}` < 1% del total.
- `mm_tts_requests_total` por provider — confirma cascada funcional.
- Coverage continúa ≥ 80% al añadir features futuros (`fail_under=80` en `pytest.ini_options`).

---

## Historial de Cambios

| Versión | Fecha | Autor | Cambios |
|---|---|---|---|
| 1.0 | 2026-05-27 | Ernesto Crespo | Versión inicial — 111 sub-tareas en 8 fases, 7 semanas |

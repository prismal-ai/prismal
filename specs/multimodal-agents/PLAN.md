# Prismal — Multimodal Agents Expansion

## Strategic Plan / Product Requirements Document (PLAN)

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `IMPLEMENTED` |
| **Version** | 1.0 |
| **Date** | 2026-05-27 |
| **Reviewers** | Tech Lead, AI Architect |
| **Related documents** | `ARCHITECTURE.md`, `SPEC.md`, `TASKS.md` |

---

## 1. Executive Summary

Prismal today is an exclusively text-oriented agent framework. Non-textual modalities appear only in fragmentary form: the `CUAgent` (`prismal/agents/cua_agent.py`) consumes screenshots with a VLM to automate the browser, and `core/config.py` declares fields for a voice interface (`stt_provider`, `tts_provider`, `elevenlabs_api_key`) that are not connected to any node or subgraph. There is no video processing, no cross-modal RAG, and no general-purpose multimodal agent.

This document defines the requirements to integrate a **complete multimodal architecture (Phase F)** that closes the gap with the 2026 state of the art: specialized agents for vision, audio, and video; an orchestrator subgraph that routes and fuses modalities; multimodal ingestion and RAG; and consolidated STT/TTS/VLM providers behind `ProviderRegistry`. The new layer **does not break** any of the 19 Phase A/B/C/D/E architectures already in production — it is added following the factory-injection pattern that the rest of the repo already uses.

The deliverable is a new domain `prismal/agents/multimodal/` + extensions to `prismal/rag/` and `prismal/providers/`, registrable opt-in via `register_multimodal_pipeline()`, aligned with the repo's critical rules (`SecurePromptBuilder`, `ActionInterceptor`, `AuditLogger`, isolated providers, PEP 420 namespace package).

---

## 2. Context and Problem

### 2.1 Current Situation

Repository audit (May 2026):

- **Vision (partial):** `cua_agent.py` queries `provider_registry.get_vision_llm()` to interpret browser screenshots, but the method is optional and there is no general-purpose vision agent (analysis of arbitrary images, OCR, description, classification).
- **Audio (config only):** `core/config.py` lines 657-680 declares `stt_provider`, `tts_provider`, `elevenlabs_api_key`, `voice_language`, `voice_record_seconds`. **There is no implementation**: no LangGraph node, no `audio_agent`, no handler that materializes that configuration.
- **Video:** Zero implementation. No loaders, no frame extraction, no A/V transcription, no tools.
- **Cross-modal RAG:** The 7 engines in `prismal/rag/` are exclusively textual (`ChromaVectorStore` with text embeddings; `loaders.py` for documents only). There are no CLIP/ImageBind embeddings nor support for non-text chunks in the vector store.
- **Orchestration:** The `supervisor_node` routes among 26 text agents. There is no routing by modality nor cross-modal fusion.

### 2.2 Problem

Without a multimodal architecture, Prismal cannot serve use cases where the input is voice, image, or video — not even with user extensions, because the necessary primitives do not exist. This excludes the framework from entire domains: voice assistants, media analysis, accessibility, surveillance/security, visual e-commerce, screen-share support, meeting transcription and summarization, TTS generation for long outputs, and RAG over mixed repositories (PDFs with images, videos with subtitles, podcasts).

### 2.3 Opportunity

The 2026 standard has already consolidated a clear pattern (event-driven cascaded multimodal pipeline with an orchestrator + modal experts + fusion), and LangGraph natively supports multimodal message types. Prismal's base infrastructure (providers, security, monitoring, subgraph registry, factory injection) covers 80% of the work: only the modules and the wiring are missing. The cost is bounded and the result positions Prismal at parity with frameworks like multimodal ADK, Gemini-LangGraph, and the Salesforce Agentforce stacks.

---

## 3. Target Users

### Persona 1: Multimodal AI Engineer
- **Description:** Builds voice assistants, image analysis systems, video processing pipelines.
- **Primary need:** Compose agents that accept audio/image/video as input and emit responses in the appropriate modality (text, synthesized voice).
- **Usage frequency:** Daily.

### Persona 2: Accessibility/Voice UX Designer
- **Description:** Designs accessible experiences (low-latency voice, image description for visually impaired users).
- **Primary need:** Voice-to-voice latency <800ms, reliable multi-language transcription, graceful fallback between modalities.
- **Usage frequency:** Weekly (design) / Daily (validation).

### Persona 3: Media/Knowledge-Base RAG Engineer
- **Description:** Indexes mixed corpora (PDFs with figures, videos with subtitles, podcasts, product images) for Q&A.
- **Primary need:** Cross-modal embeddings, retrieval that returns the correct modality for each query, fusion of visual/auditory/textual evidence.
- **Usage frequency:** Daily.

---

## 4. Objectives and Success Metrics

### 4.1 Business Objectives

| Objective | Metric | Target | Timeframe |
|---|---|---|---|
| Modal coverage | Supported modalities (text/audio/image/video) | 4/4 with full I/O | Phase F |
| Voice-to-voice latency | p95 over 50 turns | ≤ 1500 ms (aspirational target 800 ms) | Phase F |
| Multimodal RAG recall | Recall@5 over internal mixed corpus | ≥ +10% vs text-only | Phase F |
| Composability | Reuse Phase A/B/C primitives | ≥ 60% of new code leverages `reflection_loop`, `make_parallel_dispatcher`, `mixture_of_agents`, `swarm_handoff` | Phase F |
| Test coverage | Branch coverage of new modules | ≥ 80% | Global |

### 4.2 User Objectives

| User Objective | Indicator |
|---|---|
| Talk to the agent and receive voice | The STT → supervisor → TTS pipeline works end-to-end with `pyttsx3` by default and optional `elevenlabs`/`openai` |
| Upload an image and get analysis | `VisionAgent.analyze(image)` returns description, detected objects, OCR if applicable |
| Upload a video and get a summary | `VideoAgent.summarize(path)` extracts frames + transcription + LLM fusion |
| Search a mixed corpus | `MultimodalRAGEngine.search(query, modalities=[...])` returns cross-modal evidence |
| Decide the output modality | `modality_router_node` chooses text/voice/image according to context |

---

## 5. Scope

### 5.1 In Scope (Included — Phase F)

**F1 — Provider extensions (`prismal/providers/`):**
- [ ] `get_stt()` — LiteLLM wrapper for Whisper API + optional local backend.
- [ ] `get_tts()` — wrapper for `pyttsx3` (default), `openai`, `elevenlabs`.
- [ ] `get_vision_llm()` — formalized and documented (today an optional method in `ProviderRegistry`).
- [ ] `get_multimodal_llm()` — natively multimodal models (Gemini 2.x, GPT-4o, Claude Sonnet 4.6).
- [ ] `get_cross_modal_embeddings()` — CLIP / ImageBind for cross-modal vectors.

**F2 — Modal agents (`prismal/agents/multimodal/`):**
- [ ] `vision_agent.py` — `VisionAgent` for general-purpose image analysis (description, detection, OCR).
- [ ] `audio_agent.py` — `AudioAgent` with STT, segmentation, speaker detection (optional), TTS response.
- [ ] `video_agent.py` — `VideoAgent` with frame extraction (FFmpeg via sandbox), audio track transcription, temporal summary.
- [ ] `modality_router.py` — classifies input (text/audio/image/video/mixed) and routes.
- [ ] `multimodal_fusion.py` — aggregator that synthesizes the modal experts' outputs (reuses `mixture_of_agents.py`).

**F3 — Orchestrator subgraph (`prismal/agents/subgraphs/multimodal_pipeline/`):**
- [ ] `modality_router → [vision | audio | video | text in parallel] → fusion → response_formatter`.
- [ ] HITL support in the router (`hitl_gate()`) if the output modality has impact (e.g. sending audio).
- [ ] Output formatter that chooses the response modality (text/voice/generated image).

**F4 — Multimodal RAG (`prismal/rag/multimodal.py`):**
- [ ] `MultimodalRAGEngine` — indexes textual chunks + image descriptions + transcriptions; cross-modal embeddings.
- [ ] Support in `ChromaVectorStore` for modality metadata (`modality: text|image|audio|video_frame`).
- [ ] New loaders: `image_loader.py`, `audio_loader.py`, `video_loader.py` under `prismal/rag/loaders/`.

**F5 — Multimodal security (`prismal/security/`):**
- [ ] `MediaValidator` — verifies magic bytes, size/duration limits, allowed format before passing to the pipeline.
- [ ] Extension of `InputSanitizer` with `sanitize_media(blob, kind)`.
- [ ] `ActionInterceptor.check_media_op()` — permissions before operations on media files.
- [ ] Auditing in `AuditLogger` with media hash (not content).

**F6 — Configuration (`prismal/core/config.py`):**
- [ ] Activate/extend existing voice fields; add `vision_*`, `video_*`, `multimodal_*`.
- [ ] Toggles `multimodal_enabled`, `vision_enabled`, `audio_enabled`, `video_enabled` (default `False`).
- [ ] Limits: `max_audio_duration_s`, `max_video_duration_s`, `max_image_bytes`, `max_frames_per_video`.

**F7 — LangGraph integration (`prismal/agents/`):**
- [ ] Register new nodes in `graph.py` (opt-in via settings).
- [ ] Extend `intent_router.py` with modality detection (regex over MIME type / file suffix in attached messages).
- [ ] Add entries to the `tool_registry.py` `DEFAULT_CAPABILITY_MAP` for the new nodes.

**Cross-cutting:**
- [ ] OTel spans per multimodal stage.
- [ ] Metrics: `multimodal_*_requests_total`, `multimodal_*_latency_seconds`, `multimodal_*_errors_total`.
- [ ] Unit and integration tests (≥ 80% coverage).

### 5.2 Out of Scope (Excluded)

- **Generative image/video synthesis** (DALL·E, Sora, Stable Diffusion) — evaluated in Phase G; here only *analysis* and *voice generation* (TTS).
- **Bidirectional WebRTC streaming** — the pipeline works over files or blobs; low-latency streaming (<300ms full-duplex) requires a different architecture and is left for Phase H.
- **Real-time speaker diarization** — the initial version uses simple transcription; diarization (`pyannote.audio`) is Phase G.
- **VLM / TTS fine-tuning** — requires dedicated GPU infrastructure.
- **Audio/camera capture UI/Frontend** — `prismal` is a framework, not an app. Capture is the consumer's responsibility (the `lightagent`/CLI/web app).

### 5.3 Future Considerations (Phase G+)

- Bidirectional sub-300ms streaming (WebRTC + RTMS).
- Diarization and source separation.
- Image/video generation (visual generative models).
- Specialized cross-modal embeddings (SigLIP, AudioCLIP).
- Voice cloning and emotional modulation (ElevenLabs voice design).
- Comprehension of long documents with figures (LongVila, PaliGemma).

---

## 6. Functional Requirements (Summary — detail in `SPEC.md`)

| ID | Requirement | Priority |
|---|---|---|
| RF-MM-001 | STT via `get_stt()` with Whisper API / local backends; configurable language | `MUST` |
| RF-MM-002 | TTS via `get_tts()` with pyttsx3 / openai / elevenlabs backends | `MUST` |
| RF-MM-003 | `VisionAgent.analyze(image)` returns description + objects + optional OCR | `MUST` |
| RF-MM-004 | `AudioAgent.process(audio)` runs STT → reasoning → TTS or text | `MUST` |
| RF-MM-005 | `VideoAgent.summarize(video)` extracts frames + transcribes track + synthesizes | `SHOULD` |
| RF-MM-006 | `modality_router_node` classifies input and routes to a modal expert | `MUST` |
| RF-MM-007 | `multimodal_fusion` aggregates cross-modal outputs with MoA or moderator | `MUST` |
| RF-MM-008 | `MultimodalRAGEngine.search(query, modalities)` returns mixed evidence | `SHOULD` |
| RF-MM-009 | `MediaValidator` rejects media outside limits/format | `MUST` |
| RF-MM-010 | Complete `multimodal_pipeline/` subgraph registrable via `register_multimodal_pipeline()` | `MUST` |

---

## 7. Non-Functional Requirements

### Performance
- STT (clip ≤30s, Whisper API): ≤ 4 s p95.
- TTS (text ≤500 chars, local pyttsx3): ≤ 1.5 s p95; (elevenlabs): ≤ 3 s p95.
- Vision analyze (1 image ≤4 MB): ≤ 5 s p95.
- Video summarize (clip ≤2 min): ≤ 60 s p95.
- Multimodal pipeline end-to-end (1 image + query): ≤ 7 s p95.
- Target voice-to-voice latency: ≤ 1500 ms p95 (aspirational 800 ms when the whole stack is local).

### Security
- All incoming media passes through `MediaValidator` (magic bytes + limits) before the agent.
- The medium's SHA-256 hash is logged in `AuditLogger`; **never the content**.
- Prompts built from transcriptions or descriptions pass through `SecurePromptBuilder`.
- No module imports provider SDKs directly — everything via `prismal/providers/`.
- `ActionInterceptor.check_media_op()` before writing/reading media to disk.

### Availability
- Each modal agent exposes `degrade_gracefully=True` by default: if the provider fails, it returns `partial_result=True` instead of an exception.
- TTS with cascading fallback: elevenlabs → openai → local pyttsx3.

### Scalability
- `MultimodalRAGEngine` supports ChromaDB collections ≥ 1M mixed vectors (limitation inherited from the store).
- Video processing uses `SandboxExecutor` for FFmpeg with a CPU/RAM limit per job.

### Observability
- OTel spans: `mm.stt`, `mm.tts`, `mm.vision.analyze`, `mm.video.extract_frames`, `mm.video.transcribe`, `mm.router.classify`, `mm.fusion`.
- Minimum metrics per agent: `requests_total`, `latency_seconds`, `errors_total`, `bytes_processed_total`.

### Maintainability
- Coverage ≥ 80% per new module. `ruff check` and `mypy --strict` pass. `bandit` with no HIGH/CRITICAL.
- Callable-injection in all factories (`stt_fn`, `tts_fn`, `vision_fn`, `frame_extractor_fn`) — tests without real external dependencies.

---

## 8. Constraints and Dependencies

### Technical Constraints
- Python 3.13+, `uv` as the manager.
- `prismal/` remains a PEP 420 namespace package (no `__init__.py` at the root).
- `_MAX_TOTAL_TOOLS = 120` — the new agents must not inflate the pool.
- LangGraph `StateGraph` as the single orchestration engine.

### External Dependencies

| Dependency | Type | Use | Status |
|---|---|---|---|
| `openai-whisper` (optional) | New | Local STT | ☐ Add as extra `[multimodal-local]` |
| `pyttsx3` | Existing (config) | Offline TTS | ☐ Verify in pyproject extras |
| `elevenlabs` | New optional | Premium TTS | ☐ Add as extra `[multimodal-premium]` |
| `Pillow` | New | Basic image validation and manipulation | ☐ Add as extra `[multimodal]` |
| `ffmpeg` (system binary) | External | Frame + audio extraction from video | ☐ Document OS requirement |
| `ffmpeg-python` | New | Sandboxed wrapper for FFmpeg | ☐ Add as extra `[multimodal]` |
| `open_clip_torch` / `sentence-transformers` | New optional | Cross-modal embeddings | ☐ Add as extra `[multimodal-embed]` |
| `imagehash` | New optional | Frame deduplication | ☐ Add as extra `[multimodal]` |

All multimodal dependencies are **optional** (gated by extras) — the base `prismal` installation is not inflated.

---

## 9. User Stories (excerpt)

### Epic F: End-to-End Voice

**US-MM-001:** As an AI Engineer, I want a functional voice pipeline to build voice assistants without writing the STT/TTS wiring.
- [ ] `AudioAgent.process(audio_blob)` returns `AudioResult(transcript, response_text, response_audio)`.
- [ ] Configurable STT/TTS backend without touching agent code.

### Epic F: Visual Analysis

**US-MM-002:** As a Knowledge-Base Engineer, I want to analyze images and store their description for future RAG.
- [ ] `VisionAgent.analyze(image)` returns `VisionResult(description, objects, ocr_text)`.
- [ ] The description is indexable by `MultimodalRAGEngine`.

### Epic F: Video and Meetings

**US-MM-003:** As a Media RAG Engineer, I want to upload a video and get its summary + transcription.
- [ ] `VideoAgent.summarize(path)` returns `VideoResult(transcript, frame_descriptions, summary)`.
- [ ] Works on clips up to 2 minutes without OOM.

### Epic F: Cross-Modal Orchestration

**US-MM-004:** As an AI Architect, I want a single subgraph that accepts messages with any modality and routes automatically.
- [ ] `multimodal_pipeline` registered via `register_multimodal_pipeline()`.
- [ ] The router classifies with confidence ≥ 0.85 on a modality test set.

---

## 10. Risks and Mitigations

| Risk | Probability | Impact | Mitigation |
|---|---|---|---|
| Voice-to-voice latency above target | High | High | Allow fallback to local `pyttsx3`; document the trade-off; measure p95 in CI |
| LLM costs with video (many frames) | High | Medium | Deduplication via `imagehash`; adaptive sampling (1 frame/s default, configurable) |
| FFmpeg in sandbox fails due to permissions | Medium | High | Document OS requirements; use `SandboxExecutor` with docker backend as a robust default |
| Heavy cross-modal embeddings (CLIP loads ~1 GB) | High | Medium | Lazy load; optional `[multimodal-embed]` extras; fallback to indexing textual descriptions |
| Media validator rejects false positives | Medium | Medium | Documented magic bytes list; optional permissive mode with warning |
| Privacy: transcriptions contain PII | High | High | `InputSanitizer` applies to transcriptions just like incoming text; PII redacted in `AuditLogger` |
| `tool_registry` inflation with multimodal tools | Medium | Medium | Capability routing (Phase E): each agent receives only tools relevant to its modality |

---

## 11. Estimated Timeline

| Phase | Duration | Deliverable |
|---|---|---|
| F1 — Providers (STT/TTS/Vision/Multimodal/Embeddings) | 1 week | Wrappers in `prismal/providers/` + tests |
| F2 — Modal agents (vision/audio/video/router/fusion) | 2 weeks | 5 new modules in `prismal/agents/multimodal/` |
| F3 — Subgraph `multimodal_pipeline/` | 1 week | Registrable pipeline + tests |
| F4 — Multimodal RAG + loaders | 1 week | `MultimodalRAGEngine` + 3 loaders |
| F5 — Multimodal security | 0.5 week | `MediaValidator`, Sanitizer/Interceptor/Audit extensions |
| F6 — Config + toggles | 0.2 week | New fields in `core/config.py` |
| F7 — LangGraph integration + intent router + capability routing | 0.5 week | Opt-in wiring in `graph.py` and `intent_router.py` |
| Hardening — Coverage, docs, security audit | 0.8 week | ≥ 80% coverage; bandit clean; docs updated |
| **Total** | **~7 weeks** | Complete multimodal architecture, 5 new agents + 1 subgraph + 1 RAG engine |

---

## 12. Definition of Done (Phase F Global)

- [ ] 5 multimodal agents + 1 subgraph + 1 RAG engine + 1 MediaValidator implemented.
- [ ] `uv run pytest -m "not live_api"` passes 100%.
- [ ] Coverage ≥ 80% over `prismal/agents/multimodal/`, `prismal/rag/multimodal.py`, and the new loaders.
- [ ] `uv run ruff check .` and `uv run mypy prismal` with no errors.
- [ ] `uv run bandit -r prismal -c pyproject.toml` with no HIGH/CRITICAL.
- [ ] `CLAUDE.md` and `README.md` updated with the multimodal section.
- [ ] `pyproject.toml` with extras `[multimodal]`, `[multimodal-local]`, `[multimodal-premium]`, `[multimodal-embed]`.
- [ ] `config/mcp_servers.yaml` extended with `audio`, `vision`, `video` capabilities (optional).
- [ ] PR merged to `main` with approved code review.

---

## Change History

| Version | Date | Author | Changes |
|---|---|---|---|
| 1.0 | 2026-05-27 | Ernesto Crespo | Initial version — multimodal Phase F (audio, video, vision, RAG, fusion) |

## Approvals

| Role | Name | Date | Status |
|---|---|---|---|
| Tech Lead | — | | ☐ Pending |
| AI Architect | — | | ☐ Pending |

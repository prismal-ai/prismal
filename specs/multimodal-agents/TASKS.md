# Prismal Multimodal Agents — Implementation Plan (TASKS)

## Metadata

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `IMPLEMENTED` |
| **Version** | 1.0 |
| **Date** | 2026-05-27 |
| **PLAN** | `specs/multimodal-agents/PLAN.md` |
| **Architecture** | `specs/multimodal-agents/ARCHITECTURE.md` |
| **SPEC** | `specs/multimodal-agents/SPEC.md` |

---

> **Implementation status (2026-05-30):** Phase F is **implemented**
> (opt-in, gated by `settings.multimodal_enabled`). Modules on disk:
> `providers/{stt,tts,vision,multimodal,cross_modal_embeddings}.py`,
> `agents/multimodal/`, `agents/subgraphs/multimodal_pipeline/`,
> `rag/multimodal.py`, `rag/loaders/{image,audio,video}_loader.py`,
> `security/media_validator.py`; `multimodal_*` settings in `core/config.py`;
> `[multimodal*]` extras in `pyproject.toml`. Recorded in `CHANGELOG.md`.
> Only caveat (DD-MM-006): wiring the modal agents as direct supervisor nodes
> in `graph.py` remains an operator opt-in. The `☐` checkboxes
> below are the original plan; they were not kept up to date during execution.

---

## 1. Implementation Summary

The multimodal Phase F is split into **7 independently executable sub-phases** plus a hardening one:

- **F1 (week 1):** Providers — STT, TTS, Vision, Multimodal LLM, Cross-Modal Embeddings.
- **F2 (weeks 2-3):** Modal agents — Vision, Audio, Video, Modality Router, Multimodal Fusion.
- **F3 (week 4):** `multimodal_pipeline/` subgraph with builder and idempotent register.
- **F4 (week 5):** Multimodal RAG — `MultimodalRAGEngine` + 3 loaders.
- **F5 (week 5.5):** Security — `MediaValidator`, extensions to Sanitizer/Interceptor/Audit.
- **F6 (week 5.7):** Config + toggles + extras in `pyproject.toml`.
- **F7 (week 6):** LangGraph integration + intent router + capability routing.
- **Hardening (week 7):** Coverage, docs, security audit, integration tests.

**Total estimated duration:** 7 weeks
**Minimum team:** 1 senior engineer with LangGraph + multimedia experience (FFmpeg, audio basics).
**Target date:** 2026-07-15

---

## 2. Prerequisites

| Prerequisite | Owner | Status | Deadline |
|---|---|---|---|
| PLAN.md approved | Tech Lead | ☐ Pending | 2026-06-01 |
| ARCHITECTURE.md approved | Tech Lead + AI Architect | ☐ Pending | 2026-06-01 |
| SPEC.md approved | Tech Lead | ☐ Pending | 2026-06-01 |
| Decision on local STT (`openai-whisper` vs `faster-whisper`) | AI Architect | ☐ Pending | Start of F1 |
| Decision on optional `python-magic` for `MediaValidator` | Tech Lead | ☐ Pending | Start of F5 |
| Extras in `pyproject.toml` documented | Engineer | ☐ Pending | Start of F1 |
| Branch `feature/multimodal-agents` created | Engineer | ☐ Pending | Start of F1 |
| Existing test suite passes 100% (688+ tests) | Engineer | ☐ Verify | Start of F1 |
| FFmpeg available in CI runner | DevOps | ☐ Verify | Start of F2 (`VideoAgent`) |

---

## 3. Implementation Phases

---

### PHASE F1 — Providers

**Duration:** 1 week (week 1) | **Objective:** clean wrappers over STT/TTS/VLM/multimodal/cross-modal embeddings, all isolated in `prismal/providers/`.

#### F1-01 — STT wrapper
**Estimate:** 1.5 days | **File:** `prismal/providers/stt.py`

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| F1-01-01 | Create `STTProvider` enum, `STTResult`, `STTSegment` dataclasses | 0.2d | — | ☐ |
| F1-01-02 | Implement `openai` backend (Whisper API via LiteLLM) | 0.5d | F1-01-01 | ☐ |
| F1-01-03 | Implement `local` backend (optional lazy import) | 0.5d | F1-01-01 | ☐ |
| F1-01-04 | `get_stt()` factory with settings-based resolution + override | 0.2d | F1-01-02, F1-01-03 | ☐ |
| F1-01-05 | Unit tests with `AsyncMock` (≥ 80% coverage) | 0.5d | F1-01-04 | ☐ |
| F1-01-06 | `STTError` exception in `core/exceptions.py` | 0.1d | — | ☐ |

**Done criteria:**
- `STTClient` protocol + 2 functional implementations.
- `get_stt(provider="openai")` returns a functional client with a mocked LLM in tests.
- Coverage ≥ 80% in `providers/stt.py`.
- `ruff` + `mypy --strict` pass.

---

#### F1-02 — TTS wrapper
**Estimate:** 1.5 days | **File:** `prismal/providers/tts.py`

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| F1-02-01 | `TTSProvider` enum + `TTSResult` dataclass | 0.2d | — | ☐ |
| F1-02-02 | `pyttsx3` backend (offline, baseline) | 0.5d | F1-02-01 | ☐ |
| F1-02-03 | `openai` backend (gpt-4o-mini-tts via LiteLLM) | 0.3d | F1-02-01 | ☐ |
| F1-02-04 | `elevenlabs` backend (optional lazy import) | 0.5d | F1-02-01 | ☐ |
| F1-02-05 | `get_tts()` with cascade elevenlabs → openai → pyttsx3 | 0.3d | F1-02-02..04 | ☐ |
| F1-02-06 | Tests + `TTSError` exception | 0.5d | F1-02-05 | ☐ |

**Done criteria:**
- Cascading fallback verified with tests (mock primary failure).
- `pyttsx3` always available (requires no extras).

---

#### F1-03 — Vision LLM wrapper
**Estimate:** 0.5 day | **File:** `prismal/providers/vision.py`

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| F1-03-01 | Formalize `get_vision_llm(model)` with LiteLLM | 0.3d | — | ☐ |
| F1-03-02 | Tests + integration with legacy `provider_registry` (CUA compatibility) | 0.2d | F1-03-01 | ☐ |

**Done criteria:**
- `CUAgent` keeps working identically (zero regression).

---

#### F1-04 — Multimodal LLM wrapper
**Estimate:** 0.5 day | **File:** `prismal/providers/multimodal.py`

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| F1-04-01 | `get_multimodal_llm(model)` with default `gemini/gemini-2.0-flash` | 0.3d | — | ☐ |
| F1-04-02 | Tests with mocks | 0.2d | F1-04-01 | ☐ |

---

#### F1-05 — Cross-Modal Embeddings wrapper
**Estimate:** 1 day | **File:** `prismal/providers/cross_modal_embeddings.py`

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| F1-05-01 | `get_cross_modal_embeddings(model)` with `open_clip` backend (lazy) | 0.5d | — | ☐ |
| F1-05-02 | `MissingDependencyError` when extras not installed | 0.2d | F1-05-01 | ☐ |
| F1-05-03 | Tests with mocked embedder | 0.3d | F1-05-01 | ☐ |

**Global F1 criteria:**
- 5 modules in `prismal/providers/`, all with tests ≥ 80% coverage.
- 0 direct SDK imports in modules outside `providers/`.
- 3 new exceptions in `core/exceptions.py`: `STTError`, `TTSError`, `MissingDependencyError`.

---

### PHASE F2 — Modal Agents

**Duration:** 2 weeks (weeks 2-3) | **Objective:** 5 agents/routers in `prismal/agents/multimodal/`.

#### F2-01 — VisionAgent
**Estimate:** 2.5 days | **File:** `prismal/agents/multimodal/vision_agent.py`

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| F2-01-01 | Create `agents/multimodal/` directory + `__init__.py` | 0.1d | — | ☐ |
| F2-01-02 | `VisionResult`, `DetectedObject` dataclasses | 0.2d | F2-01-01 | ☐ |
| F2-01-03 | `VisionAgent.__init__` with `vision_fn`, `ocr_fn` callables | 0.3d | F2-01-02 | ☐ |
| F2-01-04 | `analyze(image, with_ocr)` — validate → VLM → parse | 1d | F2-01-03, F1-03, F5-01 | ☐ |
| F2-01-05 | OCR path (second VLM call with OCR prompt) | 0.4d | F2-01-04 | ☐ |
| F2-01-06 | OTel spans + metrics | 0.2d | F2-01-04 | ☐ |
| F2-01-07 | Unit tests with mocked VLM (15+ tests, ≥80% coverage) | 0.8d | F2-01-04, F2-01-05 | ☐ |
| F2-01-08 | `VisionAgentError` exception in `core/exceptions.py` | 0.1d | — | ☐ |

---

#### F2-02 — AudioAgent
**Estimate:** 2 days | **File:** `prismal/agents/multimodal/audio_agent.py`

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| F2-02-01 | `AudioResult` dataclass | 0.1d | — | ☐ |
| F2-02-02 | `AudioAgent.__init__` with `stt_client`, `tts_client`, `reason_fn` | 0.3d | F1-01, F1-02 | ☐ |
| F2-02-03 | `process(audio, with_tts)` — validate → STT → reason → optional TTS | 0.8d | F2-02-02 | ☐ |
| F2-02-04 | Audit logging (hash of audio in + out, never content) | 0.3d | F2-02-03, F5-04 | ☐ |
| F2-02-05 | OTel spans + metrics | 0.2d | F2-02-03 | ☐ |
| F2-02-06 | Unit tests (14+ tests, ≥80% coverage) | 0.6d | F2-02-03 | ☐ |
| F2-02-07 | `AudioAgentError` exception | 0.1d | — | ☐ |

---

#### F2-03 — VideoAgent
**Estimate:** 3.5 days | **File:** `prismal/agents/multimodal/video_agent.py`

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| F2-03-01 | `VideoResult`, `FrameDescription` dataclasses | 0.2d | — | ☐ |
| F2-03-02 | Default `frame_extractor_fn` using `SandboxExecutor` + `ffmpeg-python` | 1d | F5-01 (validator) | ☐ |
| F2-03-03 | `summarize(video, fps, max_frames)` — extract → dedup → vision+audio in parallel → fusion | 1d | F2-03-02, F2-01, F2-02 | ☐ |
| F2-03-04 | Frame dedup with `imagehash` (optional, gated by extra) | 0.4d | F2-03-03 | ☐ |
| F2-03-05 | OTel spans + metrics | 0.2d | F2-03-03 | ☐ |
| F2-03-06 | Unit tests with mocked FFmpeg + synthetic frames (12+ tests) | 0.7d | F2-03-03 | ☐ |
| F2-03-07 | `VideoAgentError` exception | 0.1d | — | ☐ |

**F2-03 risks:**
- FFmpeg may not be available in CI — core tests must work without it (injected callable).
- Latency: cap `max_frames` default to 60 to avoid high LLM costs in CI.

---

#### F2-04 — ModalityRouter
**Estimate:** 1.5 days | **File:** `prismal/agents/multimodal/modality_router.py`

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| F2-04-01 | `Modality` enum + `ModalityClassification` dataclass | 0.2d | — | ☐ |
| F2-04-02 | `classify_modality()` heuristic (MIME + regex) | 0.4d | F2-04-01 | ☐ |
| F2-04-03 | `make_modality_router_node()` LangGraph-compatible factory | 0.3d | F2-04-02 | ☐ |
| F2-04-04 | Optional LLM fallback (opt-in via `use_llm_fallback=True`) | 0.3d | F1-04, F2-04-03 | ☐ |
| F2-04-05 | Unit tests (10+ tests, cover mixed/unknown/text/each modality) | 0.3d | F2-04-03 | ☐ |
| F2-04-06 | `ModalityRouterError` exception | 0.1d | — | ☐ |

---

#### F2-05 — MultimodalFusion
**Estimate:** 1.5 days | **File:** `prismal/agents/multimodal/multimodal_fusion.py`

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| F2-05-01 | `ModalContribution`, `FusionResult` dataclasses | 0.2d | — | ☐ |
| F2-05-02 | `MultimodalFusion.__init__` with strategy `moa|moderator|concat` | 0.2d | F2-05-01 | ☐ |
| F2-05-03 | `combine()` for strategy `concat` (baseline) | 0.2d | F2-05-02 | ☐ |
| F2-05-04 | `combine()` strategy `moderator` (delegates to LLM call) | 0.3d | F2-05-02 | ☐ |
| F2-05-05 | `combine()` strategy `moa` (delegates to `MixtureOfAgents.aggregate`) | 0.3d | F2-05-02 | ☐ |
| F2-05-06 | Unit tests (10+ tests covering 3 strategies) | 0.3d | F2-05-03..05 | ☐ |
| F2-05-07 | `MultimodalFusionError` exception | 0.1d | — | ☐ |

**Global F2 criteria:**
- 5 agents/utilities, all with tests ≥ 80% coverage.
- Reuse demonstrated: `MultimodalFusion` strategy=`moa` invokes `prismal/agents/patterns/mixture_of_agents.py`.
- Total suite must not regress: 688 previous tests + ~61 new = ~749.

---

### PHASE F3 — Subgraph `multimodal_pipeline/`

**Duration:** 1 week (week 4) | **File:** `prismal/agents/subgraphs/multimodal_pipeline/`

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| F3-01 | Create directory structure + `__init__.py` | 0.2d | F2 done | ☐ |
| F3-02 | `router_node.py` — adapter of `make_modality_router_node()` | 0.3d | F2-04 | ☐ |
| F3-03 | `vision_node.py`, `audio_node.py`, `video_node.py` — agent adapters | 0.5d | F2-01..03 | ☐ |
| F3-04 | `fusion_node.py` — adapter of `MultimodalFusion` | 0.3d | F2-05 | ☐ |
| F3-05 | `output_formatter_node.py` — decides text / TTS / JSON according to `state["metadata"]["mm"]["preferred_output"]` | 0.5d | F2-02 (TTS) | ☐ |
| F3-06 | `builder.py` — `build_multimodal_subgraph()` returns `SubgraphDefinition` | 0.8d | F3-02..05 | ☐ |
| F3-07 | Idempotent `register_multimodal_pipeline(registry)` | 0.2d | F3-06 | ☐ |
| F3-08 | Unit tests per node (20+ tests) | 0.8d | F3-02..05 | ☐ |
| F3-09 | End-to-end integration test of the subgraph (LLM/FFmpeg mocked) | 0.7d | F3-06 | ☐ |
| F3-10 | `MultimodalSubgraphError` exception (reuses `MultimodalError`) | 0.1d | — | ☐ |

**Global F3 criteria:**
- Subgraph registrable and testable without network.
- Router conditional edges verified: each modality reaches the correct node.
- End-to-end test passes with mocked audio + image.

---

### PHASE F4 — Multimodal RAG

**Duration:** 1 week (week 5) | **Files:** `prismal/rag/multimodal.py` + `prismal/rag/loaders/`

#### F4-01 — Refactor existing loaders
**Estimate:** 0.5 day

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| F4-01-01 | Move `prismal/rag/loaders.py` to `prismal/rag/loaders/document_loader.py` | 0.2d | — | ☐ |
| F4-01-02 | Create `prismal/rag/loaders/__init__.py` with backward-compatible re-exports | 0.2d | F4-01-01 | ☐ |
| F4-01-03 | Verify 0 regressions in existing imports | 0.1d | F4-01-02 | ☐ |

---

#### F4-02 — Multimodal loaders
**Estimate:** 2 days

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| F4-02-01 | `ImageLoader` — uses `VisionAgent` for caption | 0.5d | F2-01 | ☐ |
| F4-02-02 | `AudioLoader` — uses `STTClient` + char-based segmentation | 0.7d | F1-01 | ☐ |
| F4-02-03 | `VideoLoader` — composes `AudioLoader` + frames via `VideoAgent` | 0.5d | F4-02-01, F4-02-02, F2-03 | ☐ |
| F4-02-04 | Tests per loader (15+ cumulative tests) | 0.3d | F4-02-01..03 | ☐ |

---

#### F4-03 — MultimodalRAGEngine
**Estimate:** 2 days | **File:** `prismal/rag/multimodal.py`

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| F4-03-01 | `MultimodalRetrievedChunk` dataclass | 0.1d | — | ☐ |
| F4-03-02 | `MultimodalRAGEngine.__init__` with injectable loaders | 0.3d | F4-02 | ☐ |
| F4-03-03 | `index(path)` — auto-detects type (via `MediaValidator.sniff`) and delegates to loader | 0.6d | F5-01, F4-02 | ☐ |
| F4-03-04 | `search(query, k, modalities)` with filter by `modality` metadata | 0.5d | F4-03-03 | ☐ |
| F4-03-05 | Fallback to textual captions when `cross_modal_embedder=None` + warning | 0.2d | F4-03-04 | ☐ |
| F4-03-06 | Unit tests (20+ tests) | 0.7d | F4-03-04 | ☐ |
| F4-03-07 | `MultimodalRAGError` exception (inherits from `RAGError`) | 0.1d | — | ☐ |

**Global F4 criteria:**
- Loaders reuse the F2 agents (do not duplicate VLM/STT logic).
- `MultimodalRAGEngine.search(modalities=[Modality.IMAGE])` filters correctly.
- Without embeddings extras: the engine still works with textual captions.

---

### PHASE F5 — Security

**Duration:** 0.5 week | **Files:** `prismal/security/`

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| F5-01 | Create `prismal/security/media_validator.py` with `MediaValidator`, `MediaKind`, `MediaValidationResult`, `_MAGIC_BYTES` | 1d | — | ☐ |
| F5-02 | `MediaValidator` tests: correct magic bytes, false positives, polyglot, oversize, duration | 0.5d | F5-01 | ☐ |
| F5-03 | Extend `InputSanitizer.sanitize_media(blob, kind)` with EXIF strip via `Pillow` | 0.4d | F5-01 | ☐ |
| F5-04 | Extend `AuditLogger.log_media(event, sha256, modality, size_bytes, duration_s)` | 0.3d | — | ☐ |
| F5-05 | Extend `ActionInterceptor.check_media_op(op, path)` with per-kind permissions | 0.3d | — | ☐ |
| F5-06 | `MediaValidationError` exception | 0.1d | — | ☐ |
| F5-07 | Integration tests: agent receives invalid media → blocked before LLM | 0.4d | F5-01..05 | ☐ |

**Global F5 criteria:**
- Specific test: PNG with JPEG magic bytes is rejected.
- Specific test: 100 MB file with `max_image_bytes=10 MB` is rejected.
- Geolocation EXIF removed in processed images (tested).

---

### PHASE F6 — Config + Toggles + Pyproject

**Duration:** 0.2 week

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| F6-01 | Add `multimodal_*`, `vision_*`, `video_*`, `tts_max_chars`, `max_image_bytes`, etc. fields to `core/config.py` | 0.5d | — | ☐ |
| F6-02 | Extras in `pyproject.toml`: `[multimodal]`, `[multimodal-local]`, `[multimodal-premium]`, `[multimodal-embed]` | 0.3d | — | ☐ |
| F6-03 | `env.example` updated with new `PRISMAL_MULTIMODAL_*` variables | 0.1d | F6-01 | ☐ |
| F6-04 | Settings validation tests (Pydantic limits) | 0.2d | F6-01 | ☐ |

---

### PHASE F7 — LangGraph Integration + Intent Router + Capability Routing

**Duration:** 0.5 week

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| F7-01 | `register_multimodal_pipeline()` invoked opt-in at startup when `settings.multimodal_enabled=True` | 0.3d | F3 | ☐ |
| F7-02 | Add `"multimodal_router"`, `"vision_agent"`, `"audio_agent"`, `"video_agent"` to `VALID_NEXT_NODES` (gated by toggle) | 0.3d | F3 | ☐ |
| F7-03 | Extend `intent_router.py` with regex `r"(?i)\b(transcribe|imagen|video|voz|audio)\b"` + MIME attachment detection | 0.4d | — | ☐ |
| F7-04 | Extend `DEFAULT_CAPABILITY_MAP` in `tool_registry.py` with `multimodal_router`, `vision_agent`, `audio_agent`, `video_agent` entries | 0.2d | — | ☐ |
| F7-05 | Document `audio`, `vision`, `video` capabilities in `config/mcp_servers.yaml` (entries with `enabled: false`) | 0.1d | F7-04 | ☐ |
| F7-06 | Integration tests with compiled graph: query with image attachment → reaches `vision_agent` | 0.7d | F7-01..04 | ☐ |

**Global F7 criteria:**
- Without `multimodal_enabled=True`, the 26 text agents behave identically to today (zero regression).
- With the toggle active, the 4 new nodes appear as valid supervisor destinations.

---

### HARDENING — Coverage, Docs, Security Audit

**Duration:** 1 week (week 7)

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| H-01 | Coverage audit: each new module ≥ 80% | 0.5d | F1..F7 | ☐ |
| H-02 | `bandit -r prismal -c pyproject.toml` HIGH=0 MEDIUM=0 | 0.3d | F1..F7 | ☐ |
| H-03 | End-to-end voice-to-voice integration test with mocked providers | 0.5d | F3, F7 | ☐ |
| H-04 | End-to-end multimodal RAG integration test over a small mixed corpus | 0.5d | F4 | ☐ |
| H-05 | Regression test: 688 previous tests still pass | 0.3d | F1..F7 | ☐ |
| H-06 | Update `CLAUDE.md` with multimodal section + new modules | 0.3d | F1..F7 | ☐ |
| H-07 | Update `README.md` with a "Multimodal" section in features + architecture | 0.4d | F1..F7 | ☐ |
| H-08 | Update `CHANGELOG.md` with Phase F entry | 0.2d | — | ☐ |
| H-09 | Verify `ruff check .` and `mypy --strict` clean across everything new | 0.3d | F1..F7 | ☐ |
| H-10 | Create runnable `examples/multimodal_pipeline.py` | 0.5d | F3, F7 | ☐ |
| H-11 | Internal code review (1 reviewer approves PR) | 1d | H-01..09 | ☐ |
| H-12 | Merge to `main` | 0.2d | H-11 | ☐ |

---

## 4. Inter-Task Dependencies

```
F1 (providers) ─┬──▶ F2 (agents) ─┬──▶ F3 (subgraph) ─┐
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

- F1 → F2 (agents consume the wrappers).
- F2 → F3 (subgraph wraps the agents).
- F5 → F2 (`MediaValidator` required before any agent).
- F4 can start in parallel with F3 if F2 is already complete.
- F6 can run in parallel from day 1 (independent).
- F7 waits for F3 + F4 to be complete.

---

## 5. Risk and Mitigation Matrix

| Risk | Probability | Impact | Mitigation | Owner |
|---|---|---|---|---|
| FFmpeg not available in CI | Medium | High | Core tests with mocked callable; CI marker `@pytest.mark.requires_ffmpeg` for the integration ones | Engineer |
| Voice-to-voice latency above 1500ms | High | High | Allow local cascade (local Whisper + pyttsx3); measure p95 in CI | Engineer |
| LLM costs on video (many frames) | High | Medium | Cap `max_frames_per_video=60` default; dedup via `imagehash`; 1 fps sampling default | Engineer |
| `open_clip_torch` adds 1 GB on install | High | Low | Optional extra `[multimodal-embed]`; document | Engineer |
| Hardcoded magic bytes do not cover all formats | Medium | Medium | Document supported formats; opt-in permissive mode; `[multimodal-magic]` option with `python-magic` | Tech Lead |
| EXIF strip breaks legitimate metadata | Low | Low | Strip by default only in the sanitizer; opt-in flag to preserve | Engineer |
| Regression in `CUAgent` due to `get_vision_llm` refactor | Medium | High | Existing tests must pass 100% before accepting F1-03 | Engineer |
| Tool pool inflation (>120) | Medium | Medium | Capability routing (Phase E) — multimodal agents receive only relevant tools | Engineer |
| `state["metadata"]["mm"]` state collides with existing keys | Low | Low | Reserved `mm.*` namespace; grep in repo confirms 0 prior uses | Engineer |
| Audit log grows fast with media hashes | Medium | Low | Existing log rotation (inherited); size per entry ≤ 1 KB | Engineer |

---

## 6. Definition of Done (Phase F Global)

To close Phase F as COMPLETED:

- [ ] 5 provider wrappers (`stt`, `tts`, `vision`, `multimodal`, `cross_modal_embeddings`).
- [ ] 5 agents/utilities in `prismal/agents/multimodal/`.
- [ ] 1 `multimodal_pipeline/` subgraph with builder + idempotent register.
- [ ] 1 RAG engine `MultimodalRAGEngine` + 3 loaders (image/audio/video).
- [ ] 1 `MediaValidator` + Sanitizer/Interceptor/Audit extensions.
- [ ] New settings/toggles + extras in `pyproject.toml`.
- [ ] Opt-in integration with `graph.py` / `supervisor.py` / `intent_router.py` / `tool_registry.py`.
- [ ] `uv run pytest -m "not live_api"` passes 100% (688+ existing + ~140 new = ~828+).
- [ ] Coverage ≥ 80% per new module (`pytest --cov=prismal --cov-fail-under=80`).
- [ ] `uv run ruff check .` with no errors.
- [ ] `uv run mypy prismal` with no errors in strict mode.
- [ ] `uv run bandit -r prismal -c pyproject.toml` with no HIGH/CRITICAL.
- [ ] `CLAUDE.md`, `README.md`, `CHANGELOG.md` updated.
- [ ] `examples/multimodal_pipeline.py` runnable end-to-end with mocked providers.
- [ ] PR merged to `main` with 1 approving reviewer.

---

## 7. Effort Estimate per Sub-Phase

| Sub-Phase | Sub-tasks | Days | Weeks |
|---|---|---|---|
| F1 — Providers | 22 | 5 | 1 |
| F2 — Modal agents | 36 | 11 | 2 |
| F3 — Subgraph | 10 | 5 | 1 |
| F4 — Multimodal RAG | 14 | 5 | 1 |
| F5 — Security | 7 | 3 | 0.5 |
| F6 — Config + extras | 4 | 1 | 0.2 |
| F7 — Integration | 6 | 2 | 0.5 |
| Hardening | 12 | 5 | 1 |
| **Total** | **~111** | **~37** | **~7** |

*Estimate based on 1 senior engineer. With 2 engineers: F1, F5, F6 can run in parallel from week 1; F4 in parallel with F2-03 from week 2.*

---

## 8. Operational Success Metrics

After merging to `main`, monitor during week 1:

- `mm_pipeline_e2e_latency_seconds` p95 ≤ 1500 ms.
- `mm_media_validation_rejected_total` by reason — alert if `magic_bytes` >5% of the total (possible attack).
- `mm_stt_requests_total{status="error"}` < 1% of the total.
- `mm_tts_requests_total` by provider — confirms the cascade is functional.
- Coverage stays ≥ 80% as future features are added (`fail_under=80` in `pytest.ini_options`).

---

## Change History

| Version | Date | Author | Changes |
|---|---|---|---|
| 1.0 | 2026-05-27 | Ernesto Crespo | Initial version — 111 sub-tasks across 8 phases, 7 weeks |

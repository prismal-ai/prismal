# Prismal Multimodal Agents — Technical Design Document

## Metadata

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `IMPLEMENTED` |
| **Version** | 1.0 |
| **Date** | 2026-05-27 |
| **Related PLAN** | `specs/multimodal-agents/PLAN.md` |
| **Related SPEC** | `specs/multimodal-agents/SPEC.md` |
| **TASKS** | `specs/multimodal-agents/TASKS.md` |
| **Reviewers** | Tech Lead, AI Architect |

---

## 1. Context

Prismal operates on LangGraph as a SUPERVISOR state machine with 26 specialist text agents. The May 2026 audit confirmed:

- Vision: only `cua_agent.py` (vision-LLM for UI screenshots).
- Audio: configuration fields (`stt_provider`, `tts_provider`, `elevenlabs_api_key`) with no implementation that uses them.
- Video: zero implementation.
- RAG: 7 exclusively textual engines.

This document describes the technical design of the **multimodal Phase F**, keeping all existing conventions: PEP 420 namespace package, `SecurePromptBuilder`, providers via `ProviderRegistry`, OTel spans, `get_logger()`, factory-injection for testability. The guiding principle is **composition over extension**: the new modules reuse `reflection_loop()`, `make_parallel_dispatcher()`, `mixture_of_agents.py`, and `swarm_handoff()` wherever possible.

---

## 2. Technical Objectives

- **Correctness:** Each modal agent implements its canonical pipeline (STT → reasoning → TTS; frame extraction + transcribe + fusion; vision analyze + OCR).
- **Composability:** Modal agents are valid LangGraph nodes, registrable individually or as part of the `multimodal_pipeline` subgraph.
- **Provider isolation:** Only `prismal/providers/` imports Whisper/OpenAI/ElevenLabs/CLIP SDKs.
- **Security by default:** All incoming media passes through `MediaValidator` before reaching the agent. Hash of the medium in `AuditLogger`, never content.
- **Opt-in:** Toggles in `core/config.py` keep the new layer disabled until the operator enables it. Heavy dependencies are optional extras.

---

## 3. Proposed Architecture

### 3.1 High-Level Diagram — New Modules

```
prismal/
├── providers/
│   ├── [EXISTING] registry.py            ← ProviderRegistry
│   ├── [EXISTING] anthropic.py / openai.py / gemini.py / ollama.py
│   ├── [NEW] stt.py                       ← get_stt() — Whisper API + local
│   ├── [NEW] tts.py                       ← get_tts() — pyttsx3 | openai | elevenlabs
│   ├── [NEW] vision.py                    ← get_vision_llm() (formalized)
│   ├── [NEW] multimodal.py                ← get_multimodal_llm() (Gemini/GPT-4o/Sonnet)
│   └── [NEW] cross_modal_embeddings.py    ← get_cross_modal_embeddings() (CLIP)
│
├── agents/
│   ├── multimodal/                        ← [NEW directory]
│   │   ├── __init__.py                    ← public re-exports
│   │   ├── vision_agent.py                ← VisionAgent
│   │   ├── audio_agent.py                 ← AudioAgent (STT → reason → TTS)
│   │   ├── video_agent.py                 ← VideoAgent (frames + transcript + fusion)
│   │   ├── modality_router.py             ← classify_modality() + router_node
│   │   └── multimodal_fusion.py           ← MultimodalFusion (reuses MoA)
│   │
│   └── subgraphs/
│       └── multimodal_pipeline/           ← [NEW subgraph]
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
│   ├── [NEW] multimodal.py                ← MultimodalRAGEngine
│   └── loaders/                            ← [REFACTOR / NEW sub-package]
│       ├── __init__.py
│       ├── [MOVED] document_loader.py     (from root loaders.py)
│       ├── [NEW] image_loader.py
│       ├── [NEW] audio_loader.py
│       └── [NEW] video_loader.py
│
├── security/
│   ├── [NEW] media_validator.py           ← MediaValidator (magic bytes, limits)
│   └── [EXTENSION] sanitizer.py           ← InputSanitizer.sanitize_media()
│
└── core/
    └── [EXTENSION] config.py              ← multimodal_* + vision_* + video_* settings
```

### 3.2 Integration Structure with the Main Graph

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
│ existing │         │ [NEW]              │      │ [NEW]             │
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
                  │ (reuses MoA aggregator) │
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
                  ║   MULTIMODAL RAG LAYER (opt-in)       ║
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

### 3.3 Components by Module

#### F1 — Providers

| Module | Main Class / Function | Backend(s) | Dependencies |
|--------|---------------------------|------------|--------------|
| `providers/stt.py` | `get_stt(provider, model)` → `STTClient` | `openai` (Whisper API), `local` (openai-whisper) | LiteLLM, optional `openai-whisper` |
| `providers/tts.py` | `get_tts(provider)` → `TTSClient` | `pyttsx3` (default), `openai`, `elevenlabs` | `pyttsx3` (already in config), `elevenlabs` (optional) |
| `providers/vision.py` | `get_vision_llm(model)` → `BaseChatModel` with `invoke([{type:"image_url",...}])` | LiteLLM (Anthropic/OpenAI/Gemini) | Existing |
| `providers/multimodal.py` | `get_multimodal_llm(model)` → model with all modalities | LiteLLM (Gemini 2.x, GPT-4o, Sonnet 4.6) | Existing |
| `providers/cross_modal_embeddings.py` | `get_cross_modal_embeddings(model)` → `Embeddings` | `open_clip_torch` / `sentence-transformers` (optional) | Extra `[multimodal-embed]` |

#### F2 — Modal Agents

| Module | Main Class | Pattern |
|--------|----------------|--------|
| `multimodal/vision_agent.py` | `VisionAgent` | Validate → VLM analyze → optional OCR → return `VisionResult` |
| `multimodal/audio_agent.py` | `AudioAgent` | Validate → STT → LLM reason → optional TTS → return `AudioResult` |
| `multimodal/video_agent.py` | `VideoAgent` | Validate → FFmpeg extract frames + audio (sandbox) → frame_descriptions + transcript → fusion LLM → `VideoResult` |
| `multimodal/modality_router.py` | `classify_modality()` + factory `make_modality_router_node()` | Inspect attachments + intent regex → enum `Modality` |
| `multimodal/multimodal_fusion.py` | `MultimodalFusion` | Composes outputs by modality and delegates to `MixtureOfAgents` (aggregator) or moderator LLM |

#### F3 — Subgraph

`agents/subgraphs/multimodal_pipeline/builder.py` assembles a `StateGraph[AgentState]` with:

| Node | Function | Conditional edge |
|---|---|---|
| `router_node` | Classifies the input's modality | → `vision_node` / `audio_node` / `video_node` / `text_passthrough` |
| `vision_node` | Adapter of `VisionAgent` to LangGraph state | → `fusion_node` |
| `audio_node` | Adapter of `AudioAgent` | → `fusion_node` |
| `video_node` | Adapter of `VideoAgent` | → `fusion_node` |
| `fusion_node` | `MultimodalFusion.combine()` | → `output_formatter_node` |
| `output_formatter_node` | Selects the output modality (text, TTS, JSON) according to `state["metadata"]["mm"]["preferred_output"]` | → `END` |

The builder exports `build_multimodal_subgraph(...)` (returns `SubgraphDefinition`) and idempotent `register_multimodal_pipeline(registry)`, same pattern as `register_ml_pipeline`.

#### F4 — Multimodal RAG

| Module | Class | Responsibility |
|--------|-------|-----------------|
| `rag/multimodal.py` | `MultimodalRAGEngine` | Indexes text + image description + audio/video transcription; on search accepts `modalities: list[Modality]` and returns `MultimodalRetrievedChunk` with `modality` and `source_uri` fields |
| `rag/loaders/image_loader.py` | `ImageLoader` | Loads image → generates caption with VLM → emits chunk with `modality=image` and URI |
| `rag/loaders/audio_loader.py` | `AudioLoader` | Loads audio → STT → emits textual chunks per segment + `modality=audio` |
| `rag/loaders/video_loader.py` | `VideoLoader` | Composes `AudioLoader` (audio track) + `ImageLoader` (sampled frames) |

The vector store reuses `ChromaVectorStore`; `modality` and `source_uri` are added to each chunk's `metadata`.

#### F5 — Security

| Module | Class / Function | Responsibility |
|--------|----------------|-----------------|
| `security/media_validator.py` | `MediaValidator.validate(blob, kind)` | Verifies magic bytes (PNG, JPEG, MP3, WAV, MP4, WebM), maximum size (configurable), duration (audio/video). Returns `(ok: bool, reason: str)`. |
| `security/sanitizer.py` (ext) | `InputSanitizer.sanitize_media(blob, kind)` | Applies `MediaValidator` + redactions (EXIF strip on images). |
| `security/action_interceptor.py` (ext) | `ActionInterceptor.check_media_op(op, path)` | Permissions before writing/reading media to disk. |
| `security/audit.py` (ext) | `AuditLogger.log_media(event, sha256, modality)` | Logs hash and modality; never the content. |

### 3.4 Detailed Data Flows

#### Flow F1: Audio Agent (Voice to Voice)

```
audio_blob ──▶ [MediaValidator.validate(blob, "audio")]
            ──▶ [InputSanitizer.sanitize_media(blob, "audio")]
            ──▶ [AuditLogger.log_media("audio_in", sha256, "audio")]
            ──▶ [STTClient.transcribe(blob, language=settings.voice_language)]
            ──▶ transcript: str
            ──▶ [SecurePromptBuilder.build(transcript)]
            ──▶ [LLM.invoke(prompt) → response_text]
            ──▶ is voice required?
                ├── NO ──▶ AudioResult(transcript, response_text, response_audio=None)
                └── YES ─▶ [TTSClient.synthesize(response_text)] ──▶ response_audio: bytes
                         ──▶ [AuditLogger.log_media("audio_out", sha256, "audio")]
                         ──▶ AudioResult(transcript, response_text, response_audio)
```

#### Flow F2: Vision Agent

```
image ──▶ [MediaValidator.validate(image, "image")]
       ──▶ [InputSanitizer.sanitize_media(image, "image")]   # EXIF strip
       ──▶ [VLM.invoke([{"type":"image_url","image_url":...}, {"type":"text","text":prompt}])]
       ──▶ description: str + structured_objects: list
       ──▶ OCR required? (settings.vision_ocr_enabled or via call flag)
           ├── NO ──▶ VisionResult(description, objects, ocr_text=None)
           └── YES ─▶ [VLM second pass with specific OCR prompt] ──▶ ocr_text
                    ──▶ VisionResult(description, objects, ocr_text)
```

#### Flow F3: Video Agent

```
video_path ──▶ [MediaValidator.validate(path, "video")]   # duration ≤ max_video_duration_s
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

#### Flow F4: Modality Router

```
state.messages[-1] ──▶ [extract attachments + content]
                   ──▶ are there attachments with MIME image/* or audio/* or video/*?
                       ├── YES ─▶ Modality according to the first compatible attachment
                       └── NO ──▶ regex intent: "transcribe", "image", "video" → Modality
                   ──▶ multiple modalities?
                       ├── NO ──▶ route to the corresponding modal agent
                       └── YES ─▶ make_parallel_dispatcher() → Send(...) per modality
                                ──▶ fusion_node consolidates the results
```

#### Flow F5: Multimodal RAG Search

```
query + modalities=[text, image] ──▶ [embed(query) with cross_modal_embedder]
                                  ──▶ [ChromaVectorStore.similarity_search(emb,
                                          where={"modality":{"$in":["text","image"]}})]
                                  ──▶ list[MultimodalRetrievedChunk] (text + image captions)
                                  ──▶ return with original URIs?
                                      ├── YES ─▶ enrich chunks with source_uri to present
                                      │           image/clip to the user
                                      └── NO ──▶ return as is
```

---

## 4. Design Decisions

### DD-MM-001: Cascaded Pipeline over Natively Multimodal Models

- **Decision:** By default the `multimodal_pipeline` runs a cascade (STT → textual LLM → TTS; VLM → text → optional fusion) instead of a single end-to-end multimodal model.
- **Context:** Natively multimodal models (Gemini 2.x, GPT-4o real-time) are lower latency for some cases but less observable and less composable.
- **Alternatives evaluated:**

| Option | Pros | Cons |
|---|---|---|
| **Cascaded (chosen)** | Observable (one OTel span per stage); allows inserting business logic between stages; allows mixing providers | Higher latency; more calls |
| End-to-end multimodal | Lower latency; fewer calls | Less observable; lock-in to one provider; hard to insert guardrails between stages |

- **Rationale:** In 2026 cascaded remains the pragmatic pattern for production according to benchmarks; end-to-end is offered via `get_multimodal_llm()` for opt-in cases (real-time voice).

### DD-MM-002: FFmpeg Only Through SandboxExecutor

- **Decision:** Every FFmpeg invocation in `VideoAgent` and `VideoLoader` goes through `SandboxExecutor` with a docker/podman/nsjail/bwrap/firejail backend.
- **Context:** FFmpeg parses potentially malicious binaries; running it in the main process is a critical risk.
- **Consequences:** Explicit CPU/RAM/time limits per job; in CI, `bwrap` is used as a lightweight fallback.

### DD-MM-003: Cross-Modal Embeddings as an Optional Extra

- **Decision:** `open_clip_torch` and CLIP models are `[multimodal-embed]` extras; without installing them, `MultimodalRAGEngine` falls back to indexing **textual descriptions** generated by a VLM (not real cross-modal vectors).
- **Context:** CLIP downloads ~1 GB and requires PyTorch; many users won't want it.
- **Consequences:** The engine returns metadata about which embedding method was used so the user can decide whether they need an upgrade.

### DD-MM-004: Media Validation Before the Sanitizer

- **Decision:** `MediaValidator.validate()` runs **before** the `InputSanitizer`; not instead of it.
- **Context:** The historical sanitizer operates on text; adding media validation to the sanitizer mixes responsibilities.
- **Consequences:** New module `security/media_validator.py`; `InputSanitizer.sanitize_media(blob, kind)` delegates to `MediaValidator` internally to keep a single public surface from the agent layer.

### DD-MM-005: Modality Router with Heuristics + Settings Override

- **Decision:** `classify_modality(message)` uses deterministic heuristics (attachment MIME if present; regex over intent) before any LLM. The modality can be forced with `state["metadata"]["mm"]["force_modality"]`.
- **Context:** An LLM classifier adds latency and cost; heuristics resolve 95% of the cases.
- **Consequences:** If the heuristic cannot decide (`Modality.UNKNOWN`), `get_multimodal_llm()` is called as a fallback.

### DD-MM-006: Opt-In Subgraph via register_multimodal_pipeline()

- **Decision:** The multimodal subgraph follows the Phase A/B/C/D pattern: it is NOT automatically registered in `graph.py`. The operator calls `register_multimodal_pipeline(registry)` when ready.
- **Consequences:** It is **additive**, breaks nothing. The intent router is extended with an opt-in pattern that activates when `settings.multimodal_enabled=True`.

### DD-MM-007: Callable Injection in All Agents

- **Decision:** `VisionAgent`, `AudioAgent`, `VideoAgent` accept injectable callables (`stt_fn`, `tts_fn`, `vision_fn`, `frame_extractor_fn`, `transcribe_fn`). Defaults use `ProviderRegistry`.
- **Context:** Same pattern as `LATSAgent`, `LLMCompiler`, `ConstitutionalFilter`. Allows tests without real LLM/FFmpeg.
- **Consequences:** Coverage target ≥ 80% without requiring a GPU or external binaries in CI.

### DD-MM-008: Hash-First Audit, Content-Never

- **Decision:** `AuditLogger.log_media(event, sha256, modality, size_bytes, duration_s)` records metadata but **never the blob**.
- **Context:** Logs must be archivable without risk of PII/sensitive data from the medium.
- **Consequences:** Forensics is done by recovering the original blob by hash from an optional store, not from the audit log.

---

## 5. Code Structure

```
prismal/
│
├── providers/
│   ├── __init__.py            ← adds new re-exports
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
│       ├── document_loader.py (moved from loaders.py)
│       ├── image_loader.py
│       ├── audio_loader.py
│       └── video_loader.py
│
├── security/
│   ├── media_validator.py
│   └── (extensions to sanitizer.py, action_interceptor.py, audit.py)
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
    ├── test_multimodal_pipeline_e2e.py     (LLM mocked, real FFmpeg optional)
    └── test_multimodal_rag_e2e.py
```

### Patterns Applied

| Pattern | Where | Why |
|---|---|---|
| Factory + Callable injection | All modal agents | Testing without real LLM/FFmpeg |
| Strategy | `STTClient`, `TTSClient` backends | Interchangeable backends without touching agents |
| Composite | `VideoAgent` | Composes `VisionAgent` + `AudioAgent` |
| Adapter | Subgraph nodes | Adapt agents to the LangGraph interface (`AgentState → state_update`) |
| Facade | `MultimodalRAGEngine` | Unifies cross-modal indexing over Chroma + loaders |
| Cascade (chain) | `audio_agent.process()` | STT → LLM → TTS chained with guardrails between stages |

### Error Handling

```python
# core/exceptions.py — new extensions
class MultimodalError(PrismalError): ...      # base
class STTError(MultimodalError): ...
class TTSError(MultimodalError): ...
class VisionAgentError(MultimodalError): ...
class AudioAgentError(MultimodalError): ...
class VideoAgentError(MultimodalError): ...
class ModalityRouterError(MultimodalError): ...
class MultimodalRAGError(RAGError): ...
class MediaValidationError(PrismalError): ...   # rejection of invalid media
```

Policy: each modal agent **degrades gracefully** by default (`degrade_gracefully=True`); the `*Error`s are raised only if the caller explicitly disables the degrade.

---

## 6. Security

### 6.1 Attack Surface — New Layer

| Vector | Mitigation |
|---|---|
| Malicious file (malware embedded in image/audio/video) | `MediaValidator` magic bytes + size limit; FFmpeg in sandbox |
| Prompt injection via image OCR or video subtitles | `SecurePromptBuilder` applies to transcript and OCR before passing to the LLM |
| PII leakage in transcriptions | `InputSanitizer` applies to the transcript just like incoming text |
| Exfiltration via synthesized audio (data hiding) | TTS is only invoked with text that passed through `GuardrailsEngine` |
| EXIF with geolocation in images | `InputSanitizer.sanitize_media()` strips EXIF by default |
| FFmpeg with injected args | Hardcoded arguments; never user-controlled; executed in sandbox |
| Media hashes in logs reveal presence | Hash is SHA-256, not of the content; no origin metadata in the hash |
| RCE via deserialization (CLIP/Whisper local) | Models loaded from HF with `trust_remote_code=False` |

### 6.2 Cross-Cutting Rules

1. **No new module imports provider SDKs directly** — only `prismal/providers/`.
2. **All incoming media passes through `MediaValidator`** before reaching the agent.
3. **Hash in `AuditLogger`, never content** of the medium.
4. **`ActionInterceptor.check_media_op()`** before writing/reading media to disk.
5. **`SecurePromptBuilder`** also applies to transcriptions, OCR, image descriptions before passing to the downstream LLM.
6. **FFmpeg only via `SandboxExecutor`**.

---

## 7. Observability

### 7.1 OTel Spans per Stage

| Component | Spans |
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

### 7.2 Key Metrics

```
# Counters
mm_stt_requests_total{provider="openai|local", status="success|error"}
mm_tts_requests_total{provider="pyttsx3|openai|elevenlabs", status="..."}
mm_vision_analyze_total{ocr="enabled|disabled"}
mm_video_summarize_total
mm_router_classify_total{modality="text|audio|image|video|mixed|unknown"}
mm_fusion_combine_total{strategy="moa|moderator"}
mm_rag_search_total{modalities="..."}
mm_media_validation_rejected_total{reason="oversize|format|duration|magic_bytes"}

# Histograms
mm_stt_latency_seconds{provider}
mm_tts_latency_seconds{provider}
mm_vision_latency_seconds
mm_video_latency_seconds
mm_pipeline_e2e_latency_seconds       ← total voice-to-voice latency

# Gauges
mm_audio_bytes_processed_total
mm_video_seconds_processed_total
mm_image_bytes_processed_total
```

---

## 8. Testing Strategy

| Level | Coverage | Tools | What it covers |
|---|---|---|---|
| Unit | ≥ 80% per module | pytest + `AsyncMock` + minimal media fixtures | Validators, parsers, dataclasses, factories with mocked callables |
| Integration | Critical pipelines | pytest + in-memory ChromaDB + optional FFmpeg | Multimodal pipeline end-to-end with mocked LLM |
| Live API | `@pytest.mark.live_api` | Skipped by default | Real validation against Whisper/ElevenLabs/CLIP |
| Security | `@pytest.mark.security` | pytest | `MediaValidator` rejects known attacks (zip-bomb, polyglot files) |

### Mock Strategy

- **STT/TTS:** `AsyncMock` returns deterministic transcripts/audio_bytes.
- **VLM:** `AsyncMock` returns `AIMessage(content="mock description")`.
- **FFmpeg:** `SandboxExecutor.run` mocked to return synthetic frame paths (PNGs ≤1KB generated with `Pillow`).
- **CLIP:** `get_cross_modal_embeddings()` mocked returns vectors of configurable dim (default 512).

### Media Fixtures

- `tests/fixtures/media/tiny.png` (1×1 PNG), `tiny.mp3` (1s silence), `tiny.mp4` (1s black screen).
- All < 1 KB.

---

## 9. Rollout Plan

### 9.1 Integration Strategy

Phase F follows the Phase A/B/C convention: **additive, opt-in registration**.

1. `register_multimodal_pipeline(registry)` is called from the operator's startup if `settings.multimodal_enabled=True`.
2. `intent_router.py` adds regex patterns (`r"(?i)\b(transcribe|imagen|video|voz)\b"`) that route to the `multimodal_router_node`.
3. `supervisor.py` adds `"multimodal_router"`, `"vision_agent"`, `"audio_agent"`, `"video_agent"` to `VALID_NEXT_NODES` when the toggle is active.
4. `tool_registry.py`: extension of `DEFAULT_CAPABILITY_MAP` with multimodal entries:

| Node | Capabilities |
|---|---|
| `multimodal_router` | `["general"]` |
| `vision_agent` | `["vision", "general"]` |
| `audio_agent` | `["audio", "general"]` |
| `video_agent` | `["vision", "audio", "video", "general"]` |

5. `config/mcp_servers.yaml` is documented with `vision`, `audio`, `video` capabilities for specialized MCP servers (e.g. OCR-as-a-service).

### 9.2 Backward Compatibility

- Without `settings.multimodal_enabled=True`, the graph behaves identically to today.
- Without `[multimodal]` extras installed, imports fail with a clear `MissingDependencyError` when building the agent, not at import time.
- `MultimodalRAGEngine` with CLIP not installed falls back to textual captions and emits a warning.
- Existing text agents see no changes in their `AgentState`; the new fields live in `state["metadata"]["mm"]`.

---

## 10. Open Questions

- [ ] **Local STT:** `openai-whisper` or `faster-whisper` by default when `stt_provider="local"`? — Owner: AI Architect, Deadline: start of F1.
- [ ] **TTS streaming:** support streaming audio chunks (for latency <1s) or only full synthesis in v1? — Owner: Tech Lead, Deadline: start of F2.
- [ ] **Cross-modal embeddings:** CLIP base or SigLIP by default when the extra is available? — Owner: AI Architect, Deadline: start of F4.
- [ ] **Frame sampling:** fixed fps (1/s) or adaptive by change detection? — Owner: Engineer, Deadline: start of F2 (`VideoAgent`).
- [ ] **Output formatter:** is the text/voice decision made by settings, by a flag in `state["metadata"]["mm"]["preferred_output"]`, or by an intent classifier? — Owner: Tech Lead.
- [ ] **MediaValidator:** integrate `python-magic` (libmagic) in addition to hardcoded magic-bytes for greater robustness? — Owner: Tech Lead, Deadline: start of F5.

---

## Change History

| Version | Date | Author | Changes |
|---|---|---|---|
| 1.0 | 2026-05-27 | Ernesto Crespo | Initial version — multimodal Phase F technical design |

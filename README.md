# prismal-ai

[![PyPI version](https://badge.fury.io/py/prismal-ai.svg)](https://pypi.org/project/prismal-ai/)
[![Python 3.13+](https://img.shields.io/badge/python-3.13+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**Prismal AI Agent Framework** — the core engine powering multi-agent orchestration, security guardrails, RAG, MCP integration, and observability.

This package is the **agent framework layer** extracted from the larger monorepo as a standalone, publishable PyPI package. It provides everything needed to build and run AI agents without the web server, dashboard, or CLI. It was published as `lightagent-agents` through v2.x and **rebranded in v3.0.0**: the distribution is published on PyPI as `prismal-ai` while the import namespace is `prismal` (`lightagent.*` → `prismal.*`). End-user backward compatibility is provided by the deprecated `lightagent-agents` distribution, which now depends on `prismal-ai`. The sibling `lightagent` app package historically shared this import namespace and is rebranded/coordinated in tandem.

---

## Features

- **26 specialized AI agents** built on [LangGraph](https://langchain-ai.github.io/langgraph/) — coder, researcher, planner, critic, data_analyst, rag_agent, codeact_agent, cua_agent, and more
- **SUPERVISOR state machine** — central supervisor routes each turn to the right specialist, then back to `END`
- **Security-first (5-layer defense)** — `InputSanitizer` → `GuardrailsEngine` (+ NeMo Guardrails L3) → `ActionInterceptor` → `AuditLogger` (hash-chained) + `SecurePromptBuilder` + `PermissionManager`
- **Provider-agnostic** — Anthropic Claude, OpenAI GPT, Google Gemini, Ollama via LiteLLM (isolated in `prismal/providers/`)
- **7 RAG engines** — standard + CRAG, HyDE, RAG-Fusion (RRF), Hybrid (BM25 + semantic), Self-RAG, Parent-Child hierarchical, Multi-Vector, and Adaptive facade
- **7 agent reasoning patterns** — Tree of Thoughts, Debate, Constitutional AI, LATS (MCTS), LLM-Compiler (parallel DAG), Mixture of Agents, Swarm/Handoff
- **5 domain subgraph pipelines** — Customer Service, Document Generation, Data ETL, Code Review, Debate/Consensus — on top of the existing dev/ml/financial pipelines
- **Multimodal layer (implemented, opt-in)** — Vision / Audio / Video agents, modality router, multimodal fusion, multimodal subgraph, multimodal RAG engine with cross-modal embeddings, and `MediaValidator` security gate — gated by `settings.multimodal_enabled` (default `False`); see [`specs/multimodal-agents/`](./specs/multimodal-agents/)
- **Kokoro deliberation (implemented, opt-in)** — three Markdown-authored persona souls (spirit 魂 / mind 知 / heart 情) deliberate toward agreement and a `KokoroJudgeAgent` renders the final, accountable verdict (optionally executing one `ActionInterceptor`-gated action) — gated by `settings.kokoro_enabled` (default `False`); see [`docs/kokoro.md`](./docs/kokoro.md) and [`specs/kokoro-deliberation/`](./specs/kokoro-deliberation/)
- **Extension surface (implemented, opt-in)** — `prismal.langgraph` re-export, `@prismal_node` decorator (security/OTel/audit/retry middleware), `PrismalStateGraphBuilder` fluent API, plugin discovery via `importlib.metadata` entry points, `LangChainRunnableAdapter`, and formal `Protocol`s for ports (checkpoint/audit/embeddings/tools) — see [`docs/extension.md`](./docs/extension.md) and [`specs/extension-surface/`](./specs/extension-surface/)
- **MCP client with capability routing** — [Model Context Protocol](https://modelcontextprotocol.io) with auto-discovery and per-agent capability-based tool filtering (`config/mcp_servers.yaml`)
- **Process isolation** — `SandboxExecutor` with docker/podman/nsjail/bwrap/firejail backends
- **Human-in-the-Loop** — `hitl_gate()` with LangGraph `interrupt()` support
- **Composable primitives** — `reflection_loop()` (generate → critique → refine) and `make_parallel_dispatcher()` (fan-out via `Send()`)
- **Cron engine** — APScheduler + timezone-aware `DateTimeService` (single time source of truth)
- **Long-term memory** — PII-sanitized cross-session store (SQLite + ChromaDB; optional MongoDB)
- **Observability** — Langfuse traces, OpenTelemetry spans, structlog
- **Deterministic intent routing** — regex-based `match_intent()` ahead of LLM supervision
- **Tool provider injection (implemented)** — `ToolProviderPort` hexagonal port: the host composes MCP/Skills/stub providers and injects them (`set_tool_provider` or per-session via graph config); the agent core no longer imports `prismal.mcp`/`prismal.skills` — see [`docs/tool-providers.md`](./docs/tool-providers.md) and [`specs/tool-provider-injection/`](./specs/tool-provider-injection/)
- **120-tool global cap** enforced by the official `CompositeToolProvider` (legacy constants kept in `tool_registry.py`)
- **Graph visualization** — `to_mermaid()` / `visualize()` / `save_graph_image()` (from `prismal.langgraph`) render any compiled graph or `SubgraphDefinition`; `SubgraphDefinition.to_mermaid()` and `visualize_supervisor_graph()` are one-line shortcuts (see `examples/visualize_graphs.py`)

---

## Installation

```bash
pip install prismal-ai
# or with uv:
uv pip install prismal-ai
```

The distribution is named `prismal-ai`, but the import namespace is `prismal` (e.g. `from prismal.agents.graph import get_async_compiled_graph`).

### Optional extras

```bash
pip install "prismal-ai[postgres]"          # PostgreSQL checkpointing
pip install "prismal-ai[mongodb]"           # MongoDB long-term memory
pip install "prismal-ai[ollama]"            # Local LLMs via Ollama
pip install "prismal-ai[local-embeddings]"  # HuggingFace embeddings
pip install "prismal-ai[ml]"                # ML/AutoML pipeline
pip install "prismal-ai[ml-dl]"             # ML + PyTorch Lightning
pip install "prismal-ai[finance]"           # yfinance + pandas-ta
pip install "prismal-ai[analytics]"         # matplotlib + plotly
pip install "prismal-ai[datetime]"          # tzdata + NTP
pip install "prismal-ai[maintenance]"       # pip-audit
pip install "prismal-ai[multimodal]"         # Pillow + ffmpeg-python + imagehash (Phase F)
pip install "prismal-ai[multimodal-local]"   # faster-whisper (local STT)
pip install "prismal-ai[multimodal-premium]" # elevenlabs TTS
pip install "prismal-ai[multimodal-embed]"   # open-clip-torch (CLIP cross-modal embeddings)
pip install "prismal-ai[all]"                # Everything above
```

---

## Quick Start

```python
from prismal.agents.graph import get_async_compiled_graph
from prismal.agents.state import create_initial_state
from prismal.core.config import get_settings

async def main():
    settings = get_settings()
    graph = await get_async_compiled_graph()   # async contexts MUST use this

    state = create_initial_state(
        session_id="my-session",
        user_message="Analyse the sales data in data/sales.csv",
    )

    result = await graph.ainvoke(
        state,
        config={"configurable": {"thread_id": "my-session"}},
    )
    print(result["messages"][-1].content)
```

A synchronous `get_compiled_graph()` entry point is also available for non-async callers.

---

## Advanced architectures

The package ships 19 composable architectures under `specs/advanced-architectures/` (Phases A/B/C, ≥82% coverage per module, 0 bandit issues). Every component follows a **callable-injection pattern** — business logic accepts `generate_fn`, `evaluate_fn`, `reward_fn`, `plan_fn`, `tool_executor`, … so tests run without LLM backends. Defaults wire `ProviderRegistry().get_llm()` lazily.

### RAG engines (`prismal/rag/`)

| Engine | Module | Purpose |
|--------|--------|---------|
| **HyDE** | `hyde.py` | Generates a hypothetical answer and searches by its embedding (recall boost on abstract queries) |
| **RAG-Fusion** | `fusion.py` | N query variants + `reciprocal_rank_fusion()` (RRF, k=60) over parallel searches |
| **Hybrid Search** | `hybrid.py` | BM25 (`rank-bm25`) + semantic linear fusion with configurable `alpha` |
| **Self-RAG** | `self_rag.py` | LLM decides whether to retrieve (`RETRIEVE`/`NO_RETRIEVE`) and self-assesses support (`SUPPORTED`/`PARTIALLY_SUPPORTED`/`UNSUPPORTED`) + utility score |
| **Parent-Child** | `hierarchical.py` | Indexes small child chunks (~100 tok) for precision but returns parent context (~500 tok) to the LLM |
| **Multi-Vector** | `multi_vector.py` | Indexes each chunk plus a summary and N hypothetical questions per chunk |
| **Adaptive RAG** | `adaptive.py` | Facade that classifies queries (`FACTUAL_SIMPLE` / `ABSTRACT` / `AMBIGUOUS` / `MULTI_HOP` / `TECHNICAL` / `CONVERSATIONAL`) and routes to the engine above |

### Agent reasoning patterns (`prismal/agents/patterns/`)

| Pattern | Module | Purpose |
|---------|--------|---------|
| **Tree of Thoughts** | `tree_of_thoughts.py` | Explores a tree of candidate thoughts with BFS / DFS / beam search |
| **Debate** | `debate.py` | N-agent multi-round debate with moderator / majority-vote / weighted synthesis and Jaccard agreement score |
| **Constitutional AI** | `constitutional.py` | Principle-driven self-critique + revision loop with audit log (3 default principles: `no_harmful_content`, `factual_accuracy`, `no_pii_exposure`) |
| **LATS** | `lats.py` | Monte Carlo Tree Search (UCB1) over the action space — real backtracking when a branch fails |
| **LLM-Compiler** | `llm_compiler.py` | Compiles a DAG of tasks, validates with Kahn topological sort, executes independent tasks in parallel waves |
| **Mixture of Agents** | `mixture_of_agents.py` | Parallel proposers across multiple providers + aggregator synthesis layers |
| **Swarm/Handoff** | `swarm.py` | Decentralised agent-to-agent handoff with `HandoffRecord` audit trail and allow-list validation |

### Domain subgraph pipelines (`prismal/agents/subgraphs/`)

| Pipeline | Directory | Flow |
|----------|-----------|------|
| **Customer Service** | `customer_service/` | classifier → faq_retrieval → escalation_gate → response \| ticket_creator |
| **Document Generation** | `document_generation/` | planner → researcher → writer → editor → formatter (markdown/plain/html) |
| **Data ETL** | `data_etl/` | extractor → validator → (conditional gate) → transformer → loader → auditor |
| **Code Review** | `code_review/` | linter → security_scanner → logic_reviewer → suggester → report_generator |
| **Debate/Consensus** | `debate_consensus/` | proponent → opponent → moderator → consensus |

Each subgraph exports both `build_<name>_subgraph()` (returns a `SubgraphDefinition`) and an idempotent `register_<name>()` mirroring the existing `register_ml_pipeline`. Wiring into the top-level supervisor is opt-in operational work — the primitives are ready to register.

### MCP capability routing

`config/mcp_servers.yaml` declares each server's `capabilities: list[str]`. `MCPClientManager.get_all_langchain_tools(capabilities=…)` and `get_tools_for_agent(agent, required_capabilities=…)` filter the tool pool per agent. Servers tagged `general` are always included; omitting `capabilities` from a YAML entry defaults to `["general"]` for backward compatibility. The capability set is extended in Phase F to include `vision`, `audio`, and `video`.

See [`specs/advanced-architectures/SPEC.md`](./specs/advanced-architectures/SPEC.md) for the full interface contracts of Phases A/B/C/D/E.

### Multimodal layer (Phase F — implemented, opt-in)

The multimodal expansion described in [`specs/multimodal-agents/`](./specs/multimodal-agents/) adds voice, image, and video to the existing text-only stack without modifying any existing agent. It is **opt-in**: gated by `settings.multimodal_enabled` (default `False`) and registered via `register_multimodal_pipeline(registry)` when the operator is ready.

#### Provider wrappers (`prismal/providers/`)

| Wrapper | Module | Backends |
|---------|--------|----------|
| **STT** | `stt.py` | OpenAI Whisper API, local (`openai-whisper` / `faster-whisper`) |
| **TTS** | `tts.py` | `pyttsx3` (offline default), OpenAI, ElevenLabs — automatic cascade fallback |
| **Vision LLM** | `vision.py` | Any LiteLLM model with vision (Claude, GPT-4o, Gemini) |
| **Multimodal LLM** | `multimodal.py` | Gemini 2.x, GPT-4o, Sonnet 4.6 (native multimodal) |
| **Cross-modal embeddings** | `cross_modal_embeddings.py` | CLIP / `open_clip_torch` (opt-in extra) |

#### Modal agents (`prismal/agents/multimodal/`)

| Agent | Module | Purpose |
|-------|--------|---------|
| **VisionAgent** | `vision_agent.py` | General-purpose image analysis: description, object detection, optional OCR |
| **AudioAgent** | `audio_agent.py` | Voice-to-voice pipeline: STT → LLM reasoning → optional TTS |
| **VideoAgent** | `video_agent.py` | FFmpeg frame extraction (via `SandboxExecutor`) + audio transcript + fusion summary |
| **ModalityRouter** | `modality_router.py` | Heuristic classifier (MIME + regex) with optional LLM fallback |
| **MultimodalFusion** | `multimodal_fusion.py` | Combines outputs from modal agents using `moa`, `moderator`, or `concat` strategies (reuses `mixture_of_agents.py`) |

#### Multimodal subgraph (`prismal/agents/subgraphs/multimodal_pipeline/`)

```
router_node → [vision_node | audio_node | video_node | text passthrough] → fusion_node → output_formatter_node
```

Exports `build_multimodal_subgraph()` (returns `SubgraphDefinition`) and an idempotent `register_multimodal_pipeline()` matching the existing `register_ml_pipeline` pattern.

#### Multimodal RAG (`prismal/rag/`)

`MultimodalRAGEngine` indexes text + image captions + audio/video transcripts and exposes `search(query, modalities=[...])` with metadata-based modality filtering. Without the `[multimodal-embed]` extra it falls back to textual captions; with it, vectors come from CLIP-style cross-modal embeddings. New loaders: `loaders/image_loader.py`, `loaders/audio_loader.py`, `loaders/video_loader.py`.

#### Security (`prismal/security/`)

`MediaValidator` enforces magic-byte verification + size/duration limits before any media reaches an agent. `InputSanitizer.sanitize_media()` strips EXIF; `AuditLogger.log_media()` records SHA-256 + modality (never content); `ActionInterceptor.check_media_op()` gates filesystem media operations; FFmpeg always runs inside `SandboxExecutor`.

See [`specs/multimodal-agents/SPEC.md`](./specs/multimodal-agents/SPEC.md) for the full interface contracts of Phase F.

### Extension surface (Phase X — implemented, opt-in)

The extension surface (user guide: [`docs/extension.md`](./docs/extension.md); contracts: [`specs/extension-surface/`](./specs/extension-surface/)) exposes LangGraph as a first-class build target for users and third-party plugins, so you can write new patterns without forking prismal. All public symbols import from `prismal.agents.extension`. Five components:

#### `prismal.langgraph` — official re-export

```python
from prismal.langgraph import StateGraph, START, END, Send, interrupt, add_messages, AgentState, VERSION

graph = StateGraph(AgentState)
graph.add_node("my_node", my_node)
graph.add_edge(START, "my_node")
graph.add_edge("my_node", END)
compiled = graph.compile()
```

Importing from `prismal.langgraph` (rather than `langgraph.*` directly) guarantees the LangGraph version prismal was tested against, exposed as `VERSION`.

#### `@prismal_node` decorator

```python
from prismal.agents.extension import prismal_node

@prismal_node(name="my_classifier", capabilities=["general"], security="standard", audit=True)
async def my_classifier(state):
    last = state["messages"][-1].content
    label = await classify(last)
    return {"metadata": {"my_classifier": {"label": label}}}
```

Wraps any async `(state) → state_update` with a middleware chain: `InputSanitizer` + `SecurePromptBuilder` + `ActionInterceptor` → OTel span → structured logger bind → retry/backoff → timeout → user function → audit log → error mapping. Side effect: registers the node's capabilities in `tool_registry.DEFAULT_CAPABILITY_MAP`.

#### `PrismalStateGraphBuilder` — fluent API

```python
from prismal.agents.extension import PrismalStateGraphBuilder

builder = PrismalStateGraphBuilder("my_pipeline")
builder.add_node("classify", classify_fn)        # auto-wraps with @prismal_node if missing
builder.add_node("respond", respond_fn)
builder.add_edge("classify", "respond")
builder.set_entry_point("classify")
subgraph = builder.compile()                      # returns SubgraphDefinition
```

#### Plugin discovery via entry points

```toml
# prismal-x-healthcare/pyproject.toml
[project.entry-points."prismal.subgraphs"]
healthcare_triage = "prismal_x_healthcare:register_healthcare_pipeline"
```

After `pip install prismal-x-healthcare`, `discover_plugins()` auto-registers the subgraph. Allowlist/denylist via `settings.plugins_allowlist` / `plugins_denylist`. CLI: `python -m prismal.plugins list | info <name> | doctor`. Each plugin loads in isolation — individual failures do not abort startup.

#### `LangChainRunnableAdapter` — bridge for existing LangChain code

```python
from prismal.agents.extension import LangChainRunnableAdapter

adapter = LangChainRunnableAdapter(my_agent_executor)
node = adapter.as_node(name="legacy_research", capabilities=["research"])
```

Automatically maps `state["messages"]` ↔ the Runnable's input/output. Supports `Runnable`, `RunnableSequence`, `RunnableLambda`, `AgentExecutor`.

#### Formal ports (hexagonal)

`prismal/agents/extension/ports.py` declares `CheckpointPort`, `AuditPort`, `EmbeddingsPort`, `ToolPort`, `ToolProviderPort` as `Protocol`s. Existing implementations (`AsyncSqliteSaver`, `AuditLogger`, ChromaDB embeddings, `BaseTool`, the Phase Y tool providers) conform structurally; users substitute their own (Redis checkpointer, Splunk audit, custom tool source, etc.) without modifying the core.

See [`specs/extension-surface/SPEC.md`](./specs/extension-surface/SPEC.md) for the full interface contracts of Phase X.

### Tool provider injection (Phase Y — implemented)

Tool resolution is a hexagonal port (user guide: [`docs/tool-providers.md`](./docs/tool-providers.md); contracts: [`specs/tool-provider-injection/`](./specs/tool-provider-injection/)): the agent core asks an injected `ToolProviderPort` for tools and never imports `prismal.mcp` / `prismal.skills` (enforced by an architecture test). The host composes the providers and injects them at startup:

```python
from prismal.agents.extension import build_default_tool_provider
from prismal.agents.tool_registry import set_tool_provider

async def on_startup() -> None:                       # FastAPI lifespan or equivalent
    set_tool_provider(await build_default_tool_provider())   # MCP + Skills + stubs
```

- **Providers** (`prismal.agents.extension`): `McpToolProvider`, `SkillToolProvider`, `StubToolProvider`, `CompositeToolProvider` (merge with MCP→Skills→stubs priority, name dedupe, 60/120 tool caps, fixed-tool-agent exemption — exact parity with the historical registry) and `FakeToolProvider` for tests.
- **Variante A (global)** — `set_tool_provider()` once per process; nodes keep calling `get_tools_for_agent(name)` unchanged.
- **Variante B (multi-tenant)** — `get_async_compiled_graph(tool_provider=provider)` with `tool_provider_mode="context"` binds a per-session provider; nodes resolve via `get_tools_for_agent_ctx(name, config)`. No shared global state.
- **No provider?** The registry degrades to static stubs with a warning (`tool_provider_strict=True` raises `ToolProviderNotConfigured` instead).
- **Legacy shims** — `init_mcp()` / `get_mcp_tools()` / `get_skill_tools()` still work, emit `DeprecationWarning`, and will be removed in the next minor.

Runnable examples: [`examples/tool_provider_host.py`](./examples/tool_provider_host.py), [`examples/tool_provider_custom.py`](./examples/tool_provider_custom.py).

---

## Roadmap — features to build

Already implemented: extension surface (Phase X), advanced architectures (Phase A/B/C), multimodal (Phase F), and the dependency remediation (18/18 alerts in a terminal state).

What remains, **ordered from fast-and-necessary → complex-and-less-necessary**. Each feature has its SDD contract in [`specs/`](./specs/). Status: `spec ready` = ready to build (PLAN/ARCHITECTURE/SPEC/TASKS); `PRD seed` = PRD only, needs expansion before building.

1. **Finish Tool Provider Injection (Phase Y)** — *fast · necessary · in progress* — [`specs/tool-provider-injection/`](./specs/tool-provider-injection/). The Y1–Y5 code has already landed; what's left is closing Y6–Y8 (settings/observability, docs/examples, parity tests) and marking the spec `IMPLEMENTED`.
2. **Vector Store Port (Phase Z)** — *moderate · necessary · spec ready* — [`specs/vector-store-port/`](./specs/vector-store-port/). Removes the ChromaDB lock-in behind a `VectorStorePort` with adapters (Chroma default + LanceDB, sqlite-vec, Qdrant, pgvector). Reduces the security surface and opens up embedded backends.
3. **Runtime Composition Root (Phase R)** — *moderate · necessary · spec ready* — [`specs/composition-root/`](./specs/composition-root/). `build_runtime()` composes and injects every port (tools, vector store, embeddings, checkpoint, audit) in a single call; **unblocks `prismal-server` / `prismal-dashboard`**. Depends on Phase Y + Z.
4. **Cost & Budget Governance** — *fast-to-moderate · useful · PRD seed* — [`specs/cost-budget-governance/`](./specs/cost-budget-governance/). Per-run/session/tenant budgets + cost/token/call circuit-breakers in `react_loop` and the expensive patterns (debate, ToT, LATS, MoA). A cheap insurance policy against runaway spend.
5. **A2A / Agent Cards interop (Phase I)** — *complex · necessary (ecosystem) · spec ready* — [`specs/a2a-interop/`](./specs/a2a-interop/). Bidirectional agent-to-agent interop: expose prismal as an A2A agent (Agent Card at `/.well-known/agent-card.json`, JSON-RPC + SSE) and consume remote agents as nodes/tools. Complements MCP; closes the gap with MS Agent Framework / Google ADK.
6. **Agent Identity & Access Governance** — *complex · necessary (enterprise) · PRD seed* — [`specs/agent-identity-governance/`](./specs/agent-identity-governance/). Per-agent identity (W3C DID), scoped credentials, OAuth-on-behalf, and a `PolicyEngine`. The trust foundation that A2A consumes; an enterprise production blocker.
7. **Agent Evaluation & Reliability Harness** — *moderate-to-complex · useful (reliability) · PRD seed* — [`specs/agent-eval-harness/`](./specs/agent-eval-harness/). System-level evaluation of the graph (trajectories, tool usage, RAG groundedness), regression with a CI gate, and an adversarial suite. Closes the "scaffold gap".
8. **Polish (no spec yet)** — *variable · less urgent* — first-party observability UI (or a deep LangSmith/Langfuse integration) and per-node type safety (Pydantic validation of node I/O; evolution of `AgentState`).

### Framework or host? (where each feature lives)

Rule: **contract/logic → framework (`prismal/`); serving HTTP, authenticating, rendering, persisting config → host (`prismal-server` / `prismal-dashboard`).** That's why A2A and Identity are split across both.

| # | Feature | Framework (`prismal/`) | Host (`prismal-server` / `dashboard`) |
|---|---|---|---|
| 1 | Tool Provider (Phase Y) | ✅ ports/providers (`agents/extension`) | composes and injects at startup |
| 2 | Vector Store Port (Phase Z) | ✅ `rag/stores/` + `VectorStorePort` | picks the backend via config |
| 3 | Composition Root (Phase R) | ✅ `composition.py` / `build_runtime()` | calls it in the lifespan |
| 4 | Cost & Budget Governance | ✅ guard in `react_loop` + patterns | per-tenant quotas |
| 5 | A2A / Agent Cards (Phase I) | ✅ types · card · client · `A2AToolProvider` · handler | **HTTP endpoint (`/a2a`, `/.well-known/agent-card.json`) + auth** |
| 6 | Agent Identity & Governance | ✅ `PolicyEngine` + identity port (`security/`) | **IdP/OAuth + credential vault + DID issuance/rotation** |
| 7 | Agent Eval Harness | eval engine (module) | runs as a dev/CI tool (or a separate package) |
| 8 | Polish | per-node type safety (`AgentState`) | observability UI |

The framework defines the ports and logic; the host composes and exposes them. Details in [`docs/competitive-analysis.md`](./docs/competitive-analysis.md).

A full analysis and comparison with 2026 frameworks is in [`docs/competitive-analysis.md`](./docs/competitive-analysis.md).

---

## Development

Python 3.13+ is required. `uv` is the recommended package manager.

```bash
# Install with dev tools
uv pip install -e ".[dev]"
# or with dev + extras:
uv pip install -e ".[dev,all]"

# Run the test suite (pytest-asyncio auto-mode, filterwarnings="error")
uv run pytest                                      # full suite
uv run pytest tests/unit                           # one tier
uv run pytest -m unit                              # by marker (unit|integration|security|slow|live_api)
uv run pytest tests/unit/security/test_sanitizer.py::TestSanitizer::test_strip_controls  # single test
uv run pytest -n auto                              # parallel (pytest-xdist)
uv run pytest --cov=prismal --cov-report=term-missing   # coverage (fail_under = 80)

# Lint + format (ruff, line-length=100, target py313)
uv run ruff check .
uv run ruff check --fix .
uv run ruff format .

# Strict type-check (mypy strict mode, namespace_packages=true)
uv run mypy prismal

# Security linting
uv run bandit -r prismal -c pyproject.toml

# Build the distribution
uv run python -m build
```

`live_api` tests call real LLM APIs and require provider keys; skip them locally with `-m "not live_api"`. Integration tests under `tests/integration/` expect running services (sandbox backends, databases).

---

## Architecture

The core is a LangGraph `StateGraph[AgentState]` assembled in `prismal/agents/graph.py` following the **SUPERVISOR pattern**: a central `supervisor_node` routes each turn to one of 26 specialist agent nodes, each of which returns control to the supervisor; the supervisor routes to `END` when the task is complete. Checkpointing is handled by `AsyncSqliteSaver` (or PostgreSQL via the `[postgres]` extra).

```
prismal/                ← PEP 420 namespace package (NO __init__.py at root)
├── agents/                ← LangGraph state machine + 26 agent nodes
│   ├── graph.py           ← get_compiled_graph() / get_async_compiled_graph()
│   ├── supervisor.py      ← Central router
│   ├── state.py           ← AgentState (TypedDict; messages uses add_messages reducer)
│   ├── intent_router.py   ← Deterministic regex routing
│   ├── tool_registry.py   ← stable facade: delegates to the injected ToolProviderPort (Phase Y)
│   ├── patterns/
│   │   ├── reflection.py           ← reflection_loop()
│   │   ├── parallel.py             ← make_parallel_dispatcher() via Send()
│   │   ├── tree_of_thoughts.py     ← ToT with BFS/DFS/beam
│   │   ├── debate.py               ← N-agent multi-round debate + Jaccard
│   │   ├── constitutional.py       ← principle-driven self-revision + audit
│   │   ├── lats.py                 ← MCTS with UCB1
│   │   ├── llm_compiler.py         ← DAG compilation + Kahn validation + parallel waves
│   │   ├── mixture_of_agents.py    ← multi-provider proposers + aggregator
│   │   └── swarm.py                ← decentralised handoff with audit
│   ├── multimodal/                  ← (Phase F) vision / audio / video agents + router + fusion
│   │   ├── vision_agent.py
│   │   ├── audio_agent.py
│   │   ├── video_agent.py
│   │   ├── modality_router.py
│   │   └── multimodal_fusion.py
│   └── subgraphs/
│       ├── factory.py              ← SubgraphFactory
│       ├── registry.py             ← SubgraphRegistry
│       ├── gates.py                ← hitl_gate() with interrupt()
│       ├── dev_pipeline/           ← PO → Architect → Developer → Tests → QA → Reviewer
│       ├── ml_pipeline/            ← Ingester → EDA → Features → Trainer → Evaluator → Exporter
│       ├── financial/              ← Collector → Technical → Fundamental → Risk → Report
│       ├── customer_service/       ← classifier → faq_retrieval → gate → response | ticket
│       ├── document_generation/    ← planner → researcher → writer → editor → formatter
│       ├── data_etl/               ← extractor → validator → gate → transformer → loader → auditor
│       ├── code_review/            ← linter → security_scanner → logic_reviewer → suggester → report
│       ├── debate_consensus/       ← proponent → opponent → moderator → consensus
│       ├── multimodal_pipeline/    ← (Phase F) router → vision|audio|video → fusion → output_formatter
│       ├── analysis_orchestrator/
│       ├── engineering_orchestrator/
│       └── research_orchestrator/
├── core/                  ← Pydantic Settings, logging, exceptions, DB, user model
├── providers/             ← LiteLLM wrapper (ONLY location for provider-specific imports;
│                             Phase F adds stt/tts/vision/multimodal/cross_modal_embeddings)
├── memory/                ← Short-term history + long-term PII-sanitized store
├── mcp/                   ← MCP client, adapter, connection manager, capability routing
├── security/              ← 5-layer defense-in-depth (see below) + (Phase F) media_validator.py
├── rag/                   ← 7 retrieval engines:
│   ├── engine.py          ← standard RAGEngine
│   ├── crag.py            ← CRAG pipeline
│   ├── hyde.py            ← Hypothetical Document Embeddings
│   ├── fusion.py          ← RAG-Fusion (RRF)
│   ├── hybrid.py          ← BM25 + semantic hybrid search
│   ├── self_rag.py        ← Self-RAG (conditional retrieval + self-assessment)
│   ├── hierarchical.py    ← Parent-Child chunking
│   ├── multi_vector.py    ← chunk + summary + N hypothetical questions
│   ├── adaptive.py        ← facade routing by query type
│   ├── federated.py       ← federated search
│   ├── multimodal.py      ← (Phase F) MultimodalRAGEngine — text + image captions + audio/video transcripts
│   ├── loaders/           ← (Phase F) document/image/audio/video loaders
│   └── vector_store.py    ← ChromaDB vector store (extended with modality metadata in Phase F)
├── skills/                ← available/ (source) · active/ (gitignored) · custom/ (gitignored)
├── scheduler/             ← APScheduler CronExecutor, DateTimeService, Prefect flows
├── monitoring/            ← Langfuse, OpenTelemetry, structlog
├── data/                  ← DuckDB + Polars utilities
├── sandbox/               ← SandboxExecutor process isolation
├── utils/                 ← Shared utilities
└── events/                ← Event bus
```

### Namespace package

`prismal/` has **no `__init__.py`** — it is a PEP 420 implicit namespace package (renamed from `lightagent/` in v3.0.0). Both `prismal` and the sibling `lightagent` app package contribute modules into the same `prismal.*` namespace. Do not add `prismal/__init__.py`; it would break the sibling package.

### Security stack (5 layers)

| Layer | Component | Purpose |
|-------|-----------|---------|
| L1 | `InputSanitizer` | Strip control chars, normalize unicode, enforce `MAX_INPUT_LENGTH` |
| L2 | `GuardrailsEngine` | Regex pattern matching + risk scoring |
| L3 | `nemo_rails.py` | NVIDIA NeMo Guardrails integration |
| L4 | `ActionInterceptor` | LangChain callback, pre-tool permission checks |
| L5 | `AuditLogger` | Append-only JSONL audit log with xxhash chaining |
| Support | `SecurePromptBuilder` | User-input isolation with canary tokens |
| Support | `PermissionManager` | TTL-based SQLite permission grants |
| Support | `filesystem_guard.py` | Path confinement via `resolve().is_relative_to()` |

---

## Critical rules

1. **Never** concatenate user input into prompts — use `SecurePromptBuilder`. This applies to STT transcripts, OCR text, and image captions as well — they are user-controlled content.
2. **Never** bypass `GuardrailsEngine` / `ActionInterceptor`.
3. **Always** use `get_async_compiled_graph()` in async contexts (the sync variant wires a non-async SQLite saver).
4. **Never** add provider-specific imports (`anthropic`, `openai`, `google.generativeai`, `ollama`, `whisper`, `pyttsx3`, `elevenlabs`, `open_clip_torch`, …) outside `prismal/providers/`.
5. **Always** call `ActionInterceptor.check()` before tool calls that write files or execute code; call `ActionInterceptor.check_media_op()` before media filesystem operations (Phase F).
6. **Always** validate incoming media with `MediaValidator.validate()` before passing to a multimodal agent (Phase F); FFmpeg always runs inside `SandboxExecutor`.
7. **Never** add `__init__.py` to `prismal/` — it must remain a PEP 420 namespace package.

See [CLAUDE.md](./CLAUDE.md) for the full working guide (commands, testing notes, architectural context for contributors and AI assistants).

---

## Versioning

This package follows [Semantic Versioning](https://semver.org/).
Tag format for releases: `prismal/vMAJOR.MINOR.PATCH`

```bash
git tag prismal/v3.0.2
git push --tags
```

See [CHANGELOG.md](./CHANGELOG.md) for release history.

---

## Releasing (maintainers)

Run only after the full suite, linters and type/security checks are green.

```bash
# 0) Verify on the release branch
cd prismal && git switch main

# 1) Quality gates (must all pass)
uv pip install -e ".[dev,all]"
uv run pytest -m "not live_api"
uv run ruff check . && uv run mypy prismal && uv run bandit -r prismal -c pyproject.toml

# 2) Build + validate the prismal-ai distribution
rm -rf dist/ && python -m build && twine check dist/*
twine upload --repository testpypi dist/*          # validate on TestPyPI first

# 3) Push history and publish prismal-ai
git push origin main
twine upload dist/*                                 # publish to PyPI
git tag prismal/v3.0.2 && git push --tags         # tag format: prismal/vMAJOR.MINOR.PATCH

# 4) Publish the deprecated compatibility bridge (lightagent-agents -> prismal-ai)
cd compat/lightagent-agents
rm -rf dist/ && python -m build && twine check dist/*
twine upload dist/*                                 # publishes lightagent-agents 2.9.0
```

Post-release follow-ups: configure DNS/site for `prismal.dev`, coordinate the
sibling `lightagent` app package (the namespace rename breaks the shared PEP 420
namespace), and regenerate the branded binary assets (PDF/PPTX/HTML).

---

## License

MIT © Ernesto Crespo

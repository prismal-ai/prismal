# prismal

[![PyPI version](https://badge.fury.io/py/prismal.svg)](https://pypi.org/project/prismal/)
[![Python 3.13+](https://img.shields.io/badge/python-3.13+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**Prismal AI Agent Framework** — the core engine powering multi-agent orchestration, security guardrails, RAG, MCP integration, and observability.

This package is the **agent framework layer** extracted from the [Prismal](https://github.com/your-org/prismal) monorepo as a standalone, publishable PyPI package (v2.0.0). It provides everything needed to build and run AI agents without the web server, dashboard, or CLI. The sibling `prismal` app package depends on this one via `prismal>=2.0.0`.

---

## Features

- **26 specialized AI agents** built on [LangGraph](https://langchain-ai.github.io/langgraph/) — coder, researcher, planner, critic, data_analyst, rag_agent, codeact_agent, cua_agent, and more
- **SUPERVISOR state machine** — central supervisor routes each turn to the right specialist, then back to `END`
- **Security-first (5-layer defense)** — `InputSanitizer` → `GuardrailsEngine` (+ NeMo Guardrails L3) → `ActionInterceptor` → `AuditLogger` (hash-chained) + `SecurePromptBuilder` + `PermissionManager`
- **Provider-agnostic** — Anthropic Claude, OpenAI GPT, Google Gemini, Ollama via LiteLLM (isolated in `prismal/providers/`)
- **7 RAG engines** — standard + CRAG, HyDE, RAG-Fusion (RRF), Hybrid (BM25 + semantic), Self-RAG, Parent-Child hierarchical, Multi-Vector, and Adaptive facade
- **7 agent reasoning patterns** — Tree of Thoughts, Debate, Constitutional AI, LATS (MCTS), LLM-Compiler (parallel DAG), Mixture of Agents, Swarm/Handoff
- **5 domain subgraph pipelines** — Customer Service, Document Generation, Data ETL, Code Review, Debate/Consensus — on top of the existing dev/ml/financial pipelines
- **MCP client with capability routing** — [Model Context Protocol](https://modelcontextprotocol.io) with auto-discovery and per-agent capability-based tool filtering (`config/mcp_servers.yaml`)
- **Process isolation** — `SandboxExecutor` with docker/podman/nsjail/bwrap/firejail backends
- **Human-in-the-Loop** — `hitl_gate()` with LangGraph `interrupt()` support
- **Composable primitives** — `reflection_loop()` (generate → critique → refine) and `make_parallel_dispatcher()` (fan-out via `Send()`)
- **Cron engine** — APScheduler + timezone-aware `DateTimeService` (single time source of truth)
- **Long-term memory** — PII-sanitized cross-session store (SQLite + ChromaDB; optional MongoDB)
- **Observability** — Langfuse traces, OpenTelemetry spans, structlog
- **Deterministic intent routing** — regex-based `match_intent()` ahead of LLM supervision
- **120-tool global cap** enforced by `tool_registry.py`

---

## Installation

```bash
pip install prismal
# or with uv:
uv pip install prismal
```

### Optional extras

```bash
pip install "prismal[postgres]"          # PostgreSQL checkpointing
pip install "prismal[mongodb]"           # MongoDB long-term memory
pip install "prismal[ollama]"            # Local LLMs via Ollama
pip install "prismal[local-embeddings]"  # HuggingFace embeddings
pip install "prismal[ml]"                # ML/AutoML pipeline
pip install "prismal[ml-dl]"             # ML + PyTorch Lightning
pip install "prismal[finance]"           # yfinance + pandas-ta
pip install "prismal[analytics]"         # matplotlib + plotly
pip install "prismal[datetime]"          # tzdata + NTP
pip install "prismal[maintenance]"       # pip-audit
pip install "prismal[all]"               # Everything above
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

The package ships 19 composable architectures under `specs/advanced-architectures/` (Fases A/B/C, ≥82% coverage per module, 0 bandit issues). Every component follows a **callable-injection pattern** — business logic accepts `generate_fn`, `evaluate_fn`, `reward_fn`, `plan_fn`, `tool_executor`, … so tests run without LLM backends. Defaults wire `ProviderRegistry().get_llm()` lazily.

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

`config/mcp_servers.yaml` declares each server's `capabilities: list[str]`. `MCPClientManager.get_all_langchain_tools(capabilities=…)` and `get_tools_for_agent(agent, required_capabilities=…)` filter the tool pool per agent. Servers tagged `general` are always included; omitting `capabilities` from a YAML entry defaults to `["general"]` for backward compatibility.

See [`specs/advanced-architectures/SPEC.md`](./specs/advanced-architectures/SPEC.md) for the full interface contracts.

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
│   ├── tool_registry.py   ← MAX 120 tools global cap; capability-based MCP filtering
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
│       ├── analysis_orchestrator/
│       ├── engineering_orchestrator/
│       └── research_orchestrator/
├── core/                  ← Pydantic Settings, logging, exceptions, DB, user model
├── providers/             ← LiteLLM wrapper (ONLY location for provider-specific imports)
├── memory/                ← Short-term history + long-term PII-sanitized store
├── mcp/                   ← MCP client, adapter, connection manager, capability routing
├── security/              ← 5-layer defense-in-depth (see below)
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
│   └── vector_store.py    ← ChromaDB vector store
├── skills/                ← available/ (source) · active/ (gitignored) · custom/ (gitignored)
├── scheduler/             ← APScheduler CronExecutor, DateTimeService, Prefect flows
├── monitoring/            ← Langfuse, OpenTelemetry, structlog
├── data/                  ← DuckDB + Polars utilities
├── sandbox/               ← SandboxExecutor process isolation
├── utils/                 ← Shared utilities
└── events/                ← Event bus
```

### Namespace package

`prismal/` has **no `__init__.py`** — it is a PEP 420 implicit namespace package. Both `prismal` and the separate `prismal` app package contribute modules into the same `prismal.*` namespace. Do not add `prismal/__init__.py`; it would break the sibling package.

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

1. **Never** concatenate user input into prompts — use `SecurePromptBuilder`.
2. **Never** bypass `GuardrailsEngine` / `ActionInterceptor`.
3. **Always** use `get_async_compiled_graph()` in async contexts (the sync variant wires a non-async SQLite saver).
4. **Never** add provider-specific imports (`anthropic`, `openai`, `google.generativeai`, `ollama`, …) outside `prismal/providers/`.
5. **Always** call `ActionInterceptor.check()` before tool calls that write files or execute code.
6. **Never** add `__init__.py` to `prismal/` — it must remain a PEP 420 namespace package.

See [CLAUDE.md](./CLAUDE.md) for the full working guide (commands, testing notes, architectural context for contributors and AI assistants).

---

## Versioning

This package follows [Semantic Versioning](https://semver.org/).
Tag format for releases: `prismal/vMAJOR.MINOR.PATCH`

```bash
git tag prismal/v2.1.0
git push --tags
```

See [CHANGELOG.md](./CHANGELOG.md) for release history.

---

## License

MIT © Ernesto Crespo

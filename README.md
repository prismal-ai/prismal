# lightagent-agents

[![PyPI version](https://badge.fury.io/py/lightagent-agents.svg)](https://pypi.org/project/lightagent-agents/)
[![Python 3.13+](https://img.shields.io/badge/python-3.13+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**LightAgent AI Agent Framework** — the core engine powering multi-agent orchestration, security guardrails, RAG, MCP integration, and observability.

This package is the **agent framework layer** extracted from the [LightAgent](https://github.com/your-org/lightagent) monorepo as a standalone, publishable PyPI package (v2.0.0). It provides everything needed to build and run AI agents without the web server, dashboard, or CLI. The sibling `lightagent` app package depends on this one via `lightagent-agents>=2.0.0`.

---

## Features

- **26 specialized AI agents** built on [LangGraph](https://langchain-ai.github.io/langgraph/) — coder, researcher, planner, critic, data_analyst, rag_agent, codeact_agent, cua_agent, and more
- **SUPERVISOR state machine** — central supervisor routes each turn to the right specialist, then back to `END`
- **Security-first (5-layer defense)** — `InputSanitizer` → `GuardrailsEngine` (+ NeMo Guardrails L3) → `ActionInterceptor` → `AuditLogger` (hash-chained) + `SecurePromptBuilder` + `PermissionManager`
- **Provider-agnostic** — Anthropic Claude, OpenAI GPT, Google Gemini, Ollama via LiteLLM (isolated in `lightagent/providers/`)
- **RAG engine** — ChromaDB + CRAG pipeline + federated search + document loaders
- **MCP client** — [Model Context Protocol](https://modelcontextprotocol.io) with auto-discovery
- **Process isolation** — `SandboxExecutor` with docker/podman/nsjail/bwrap/firejail backends
- **Human-in-the-Loop** — `hitl_gate()` with LangGraph `interrupt()` support
- **Reflection loops** — composable generate-critique-refine pattern
- **Map-Reduce** — fan-out / fan-in with LangGraph `Send()`
- **Cron engine** — APScheduler + timezone-aware `DateTimeService` (single time source of truth)
- **Long-term memory** — PII-sanitized cross-session store (SQLite + ChromaDB; optional MongoDB)
- **Observability** — Langfuse traces, OpenTelemetry spans, structlog
- **Deterministic intent routing** — regex-based `match_intent()` ahead of LLM supervision
- **120-tool global cap** enforced by `tool_registry.py`

---

## Installation

```bash
pip install lightagent-agents
# or with uv:
uv pip install lightagent-agents
```

### Optional extras

```bash
pip install "lightagent-agents[postgres]"          # PostgreSQL checkpointing
pip install "lightagent-agents[mongodb]"           # MongoDB long-term memory
pip install "lightagent-agents[ollama]"            # Local LLMs via Ollama
pip install "lightagent-agents[local-embeddings]"  # HuggingFace embeddings
pip install "lightagent-agents[ml]"                # ML/AutoML pipeline
pip install "lightagent-agents[ml-dl]"             # ML + PyTorch Lightning
pip install "lightagent-agents[finance]"           # yfinance + pandas-ta
pip install "lightagent-agents[analytics]"         # matplotlib + plotly
pip install "lightagent-agents[datetime]"          # tzdata + NTP
pip install "lightagent-agents[maintenance]"       # pip-audit
pip install "lightagent-agents[all]"               # Everything above
```

---

## Quick Start

```python
from lightagent.agents.graph import get_async_compiled_graph
from lightagent.agents.state import create_initial_state
from lightagent.core.config import get_settings

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
uv run pytest --cov=lightagent --cov-report=term-missing   # coverage (fail_under = 80)

# Lint + format (ruff, line-length=100, target py313)
uv run ruff check .
uv run ruff check --fix .
uv run ruff format .

# Strict type-check (mypy strict mode, namespace_packages=true)
uv run mypy lightagent

# Security linting
uv run bandit -r lightagent -c pyproject.toml

# Build the distribution
uv run python -m build
```

`live_api` tests call real LLM APIs and require provider keys; skip them locally with `-m "not live_api"`. Integration tests under `tests/integration/` expect running services (sandbox backends, databases).

---

## Architecture

The core is a LangGraph `StateGraph[AgentState]` assembled in `lightagent/agents/graph.py` following the **SUPERVISOR pattern**: a central `supervisor_node` routes each turn to one of 26 specialist agent nodes, each of which returns control to the supervisor; the supervisor routes to `END` when the task is complete. Checkpointing is handled by `AsyncSqliteSaver` (or PostgreSQL via the `[postgres]` extra).

```
lightagent/                ← PEP 420 namespace package (NO __init__.py at root)
├── agents/                ← LangGraph state machine + 26 agent nodes
│   ├── graph.py           ← get_compiled_graph() / get_async_compiled_graph()
│   ├── supervisor.py      ← Central router
│   ├── state.py           ← AgentState (TypedDict; messages uses add_messages reducer)
│   ├── intent_router.py   ← Deterministic regex routing
│   ├── tool_registry.py   ← MAX 120 tools global cap
│   ├── patterns/
│   │   ├── reflection.py  ← reflection_loop()
│   │   └── parallel.py    ← make_parallel_dispatcher() via Send()
│   └── subgraphs/
│       ├── factory.py     ← SubgraphFactory
│       ├── registry.py    ← SubgraphRegistry
│       ├── gates.py       ← hitl_gate() with interrupt()
│       ├── dev_pipeline/       ← PO → Architect → Developer → Tests → QA → Reviewer
│       ├── ml_pipeline/        ← Ingester → EDA → Features → Trainer → Evaluator → Exporter
│       ├── financial/          ← Collector → Technical → Fundamental → Risk → Report
│       ├── analysis_orchestrator/
│       ├── engineering_orchestrator/
│       └── research_orchestrator/
├── core/                  ← Pydantic Settings, logging, exceptions, DB, user model
├── providers/             ← LiteLLM wrapper (ONLY location for provider-specific imports)
├── memory/                ← Short-term history + long-term PII-sanitized store
├── mcp/                   ← MCP client, adapter, connection manager
├── security/              ← 5-layer defense-in-depth (see below)
├── rag/                   ← RAG engine, CRAG pipeline, ChromaDB, federated search
├── skills/                ← available/ (source) · active/ (gitignored) · custom/ (gitignored)
├── scheduler/             ← APScheduler CronExecutor, DateTimeService, Prefect flows
├── monitoring/            ← Langfuse, OpenTelemetry, structlog
├── data/                  ← DuckDB + Polars utilities
├── sandbox/               ← SandboxExecutor process isolation
├── utils/                 ← Shared utilities
└── events/                ← Event bus
```

### Namespace package

`lightagent/` has **no `__init__.py`** — it is a PEP 420 implicit namespace package. Both `lightagent-agents` and the separate `lightagent` app package contribute modules into the same `lightagent.*` namespace. Do not add `lightagent/__init__.py`; it would break the sibling package.

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
4. **Never** add provider-specific imports (`anthropic`, `openai`, `google.generativeai`, `ollama`, …) outside `lightagent/providers/`.
5. **Always** call `ActionInterceptor.check()` before tool calls that write files or execute code.
6. **Never** add `__init__.py` to `lightagent/` — it must remain a PEP 420 namespace package.

See [CLAUDE.md](./CLAUDE.md) for the full working guide (commands, testing notes, architectural context for contributors and AI assistants).

---

## Versioning

This package follows [Semantic Versioning](https://semver.org/).
Tag format for releases: `lightagent-agents/vMAJOR.MINOR.PATCH`

```bash
git tag lightagent-agents/v2.1.0
git push --tags
```

See [CHANGELOG.md](./CHANGELOG.md) for release history.

---

## License

MIT © Ernesto Crespo

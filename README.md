# lightagent-agents

[![PyPI version](https://badge.fury.io/py/lightagent-agents.svg)](https://pypi.org/project/lightagent-agents/)
[![Python 3.13+](https://img.shields.io/badge/python-3.13+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**LightAgent AI Agent Framework** — the core engine powering multi-agent orchestration, security guardrails, RAG, MCP integration, and observability.

This package is the **agent framework layer** extracted from [LightAgent](https://github.com/your-org/lightagent). It provides everything needed to build and run AI agents without the web server, dashboard, or CLI.

---

## Features

- **26 specialized AI agents** built on [LangGraph](https://langchain-ai.github.io/langgraph/) — from code generation to financial analysis
- **Security-first** — NeMo Guardrails, `SecurePromptBuilder`, `SecurityGateway`, `ActionInterceptor`, audit logs
- **Provider-agnostic** — Anthropic Claude, OpenAI GPT, Google Gemini, Ollama via LiteLLM
- **RAG engine** — ChromaDB + CRAG pipeline + document loaders
- **MCP client** — [Model Context Protocol](https://modelcontextprotocol.io) with auto-discovery
- **Process isolation** — `SandboxExecutor` with docker/nsjail/bwrap/firejail backends (Phase 43)
- **Human-in-the-Loop** — `hitl_gate()` with `interrupt()` support
- **Reflection loops** — generate-critique-refine composable pattern
- **Map-Reduce** — fan-out / fan-in with LangGraph `Send()`
- **Cron engine** — APScheduler + timezone-aware `DateTimeService`
- **Long-term memory** — PII-sanitized cross-session memory store
- **Observability** — Langfuse traces, OpenTelemetry spans, structlog

---

## Installation

```bash
pip install lightagent-agents
# or with uv:
uv pip install lightagent-agents
```

### Optional extras

```bash
# PostgreSQL checkpointing
pip install "lightagent-agents[postgres]"

# ML/AutoML pipeline
pip install "lightagent-agents[ml]"

# Financial analysis
pip install "lightagent-agents[finance]"

# Everything
pip install "lightagent-agents[all]"
```

---

## Quick Start

```python
from lightagent.agents.graph import get_async_compiled_graph
from lightagent.agents.state import create_initial_state
from lightagent.core.config import get_settings

async def main():
    settings = get_settings()
    graph = await get_async_compiled_graph()

    state = create_initial_state(
        session_id="my-session",
        user_message="Analyse the sales data in data/sales.csv",
    )

    result = await graph.ainvoke(state)
    print(result["messages"][-1].content)
```

---

## Architecture

```
lightagent/
├── agents/          ← LangGraph state machine + 26 agent nodes
│   ├── graph.py     ← get_async_compiled_graph()
│   ├── supervisor.py
│   ├── state.py     ← AgentState
│   ├── intent_router.py   ← Deterministic routing (Phase 44)
│   ├── tool_registry.py   ← MAX 120 tools cap (Phase 44)
│   ├── patterns/
│   │   ├── reflection.py  ← reflection_loop()
│   │   └── parallel.py    ← make_parallel_dispatcher()
│   └── subgraphs/
│       ├── factory.py     ← SubgraphFactory
│       ├── registry.py    ← SubgraphRegistry
│       ├── gates.py       ← hitl_gate()
│       ├── dev_pipeline/  ← PO→Architect→Developer→Tests→QA→Reviewer
│       ├── ml_pipeline/   ← Ingester→EDA→Features→Trainer→Evaluator→Exporter
│       └── financial/     ← Collector→Technical→Fundamental→Risk→Report
├── core/            ← Config (Pydantic Settings), logging, exceptions
├── providers/       ← LiteLLM wrapper + per-provider configs
├── memory/          ← Short-term + long-term (SQLite + ChromaDB)
├── mcp/             ← MCP client, adapters, connection manager
├── security/        ← Guardrails, sanitizer, permissions, audit
├── rag/             ← RAG engine, CRAG pipeline, ChromaDB wrapper
├── skills/          ← available/ · active/ · custom/
├── scheduler/       ← CronExecutor (APScheduler), Prefect flows
├── monitoring/      ← Langfuse, OpenTelemetry, structlog
├── data/            ← DuckDB engine, Polars utilities
├── sandbox/         ← SandboxExecutor process isolation
├── utils/           ← Shared utilities
└── events/          ← Event bus
```

---

## Critical Rules (from CLAUDE.md)

1. **NEVER** concatenate user input into prompts — use `SecurePromptBuilder`.
2. **NEVER** bypass the `SecurityGateway`.
3. **ALWAYS** use `get_async_compiled_graph()` in async contexts.
4. **NEVER** add provider-specific imports outside `lightagent/providers/`.
5. **ALWAYS** use `ActionInterceptor.check()` before tool calls that write files or execute code.

See [CLAUDE.md](../../CLAUDE.md) for the complete rules.

---

## Versioning

This package follows [Semantic Versioning](https://semver.org/).
Tag format for releases: `lightagent-agents/vMAJOR.MINOR.PATCH`

```bash
git tag lightagent-agents/v2.1.0
git push --tags
```

---

## License

MIT © Ernesto Crespo

# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Package context

`prismal` is the **agent framework layer** extracted from the larger `lightagent` monorepo. It is a standalone, publishable PyPI package containing everything needed to build and run AI agents — no web server, dashboard, or CLI. It was published as `lightagent-agents` through v2.x and **rebranded to `prismal` in v3.0.0** (distribution name plus the `lightagent.*` → `prismal.*` import namespace; see `propuesta.md` / `PLAN_MIGRACION.md`). The sibling app package (still named `lightagent`) historically depended on this one and shared the import namespace — see the namespace note below.

## Common commands

All commands assume `uv` and Python 3.13+. Dev tooling lives in `pyproject.toml` under `[project.optional-dependencies].dev`.

```bash
# Install with dev tools
uv pip install -e ".[dev]"
# or with extras:
uv pip install -e ".[dev,all]"

# Test suite (pytest with asyncio_mode=auto, filterwarnings=error)
uv run pytest                                        # full suite
uv run pytest tests/unit                             # one tier
uv run pytest -m unit                                # by marker (unit|integration|security|slow|live_api)
uv run pytest tests/unit/security/test_sanitizer.py::TestSanitizer::test_strip_controls  # single test
uv run pytest -n auto                                # parallel (pytest-xdist)
uv run pytest --cov=prismal --cov-report=term-missing   # coverage (target fail_under=80)

# Lint + format (ruff is the only linter/formatter; line-length=100, target py313)
uv run ruff check .
uv run ruff check --fix .
uv run ruff format .

# Type-check (mypy strict mode; namespace_packages=true)
uv run mypy prismal

# Security linting
uv run bandit -r prismal -c pyproject.toml

# Build distribution
uv run python -m build
```

`live_api` tests call real LLM APIs and require provider keys; skip them locally with `-m "not live_api"`.

## Architecture

### Namespace package

`prismal/` has **no `__init__.py`** — it is a PEP 420 implicit namespace package (renamed from `lightagent/` in v3.0.0). Do not add `prismal/__init__.py`; it must stay an implicit namespace package.

During the migration a transitional shim `lightagent/__init__.py` redirected `lightagent.* → prismal.*`; it was **removed in v3.0.0** once all code, tests, and examples were migrated, so `from lightagent. …` no longer resolves in this repo. End-user backward compatibility is instead provided by the deprecated `lightagent-agents` distribution (a thin package that depends on `prismal`). The `lightagent.*` → `prismal.*` rename **breaks the sibling app package** that previously shared the namespace — it must be rebranded/coordinated in tandem (tracked as a post-migration step).

### LangGraph SUPERVISOR state machine

The core is a LangGraph `StateGraph[AgentState]` assembled in `prismal/agents/graph.py`. A central `supervisor_node` routes each turn to one of 26 specialist agent nodes (`coder`, `researcher`, `rag_agent`, `data_analyst`, `planner`, `critic`, `codeact_agent`, `cua_agent`, `file_manager`, `skill_manager`, `cron_manager`, `parallel_research`, `meta_learner`, `skill_creator`, `domain_supervisor`, `network_supervisor`, …), which each return control to the supervisor; the supervisor routes to `END` when done.

- **Entry points**: `get_compiled_graph()` (sync) and `get_async_compiled_graph()` (async, LRU-cached). Async contexts must use the async variant — it wires `AsyncSqliteSaver`.
- **State**: `AgentState` is a `TypedDict`. Only `messages` has a custom reducer (`add_messages`); all other fields use plain merge semantics.
- **Routing wrapper**: `_supervisor_router` in `graph.py` exists purely so LangGraph's `get_type_hints()` can resolve `AgentState` — do not remove it.
- **Checkpointing**: `build_checkpointer()` supports SQLite (default) and PostgreSQL (via `[postgres]` extra).
- **Tool cap**: `agents/tool_registry.py` enforces a global `_MAX_TOTAL_TOOLS = 120` (Phase 44).
- **Intent routing**: `agents/intent_router.py::match_intent()` is deterministic regex-based, ahead of LLM supervision.

### Subgraphs and patterns

`agents/subgraphs/` holds composed multi-node pipelines wrapped as reusable subgraphs: `dev_pipeline` (PO → Architect → Developer → Tests → QA → Reviewer), `ml_pipeline` (Ingester → EDA → Features → Trainer → Evaluator → Exporter), `financial` (Collector → Technical → Fundamental → Risk → Report), plus `analysis_orchestrator`, `engineering_orchestrator`, `research_orchestrator`. They are built by `SubgraphFactory` and registered in `SubgraphRegistry`.

**Advanced architectures (Fase A/B/C — `specs/advanced-architectures/`)** adds 7 RAG engines, 7 agent patterns, and 5 subgraph pipelines. All follow the same factory-injection pattern: business logic accepts callables (`generate_fn`, `evaluate_fn`, `reward_fn`, `plan_fn`, `tool_executor`, `linter_fn`, …) so tests run without LLM backends. Defaults wire `ProviderRegistry().get_llm()` lazily.

- `rag/hyde.py` — HyDE retriever (hypothetical doc embeddings).
- `rag/fusion.py` — RAG-Fusion (multi-query + `reciprocal_rank_fusion`).
- `rag/hybrid.py` — BM25 + semantic hybrid (`rank-bm25` dep).
- `rag/self_rag.py` — retrieval on demand + self-assessment.
- `rag/hierarchical.py` — parent/child chunk indexing.
- `rag/multi_vector.py` — chunk + summary + N hypothetical questions.
- `rag/adaptive.py` — facade routing to the above by query type.
- `agents/patterns/tree_of_thoughts.py` — ToT with beam / BFS / DFS.
- `agents/patterns/debate.py` — N-agent multi-round debate + Jaccard agreement.
- `agents/patterns/constitutional.py` — principle-driven self-revision + audit.
- `agents/patterns/lats.py` — MCTS (UCB1 balanced exploration).
- `agents/patterns/llm_compiler.py` — DAG of tasks, Kahn validation, parallel waves.
- `agents/patterns/mixture_of_agents.py` — parallel proposers + aggregator synthesis.
- `agents/patterns/swarm.py` — decentralised agent handoff with audit.
- `agents/subgraphs/customer_service/` — classifier → faq_retrieval → (escalation gate) → response | ticket.
- `agents/subgraphs/document_generation/` — planner → researcher → writer → editor → formatter.
- `agents/subgraphs/data_etl/` — extractor → validator → (gate) → transformer → loader → auditor.
- `agents/subgraphs/code_review/` — linter → security_scanner → logic_reviewer → suggester → report_generator.
- `agents/subgraphs/debate_consensus/` — proponent → opponent → moderator → consensus.

Each subgraph exports both `build_<name>_subgraph()` (returns `SubgraphDefinition`) and `register_<name>()` (idempotent registry install), mirroring the existing `register_ml_pipeline`.

`agents/patterns/` provides composable primitives: `reflection_loop()` (generate → critique → refine) and `make_parallel_dispatcher()` (fan-out via LangGraph `Send()`).

`agents/subgraphs/gates.py::hitl_gate()` uses `interrupt()` for Human-in-the-Loop pauses.

### Security (5-layer defense-in-depth)

All layers live in `prismal/security/` and are re-exported from its `__init__.py`:

- **L1 `InputSanitizer`** — strip control chars, normalize unicode, enforce `MAX_INPUT_LENGTH`.
- **L2 `GuardrailsEngine`** — regex + risk scoring; `nemo_rails.py` integrates NVIDIA NeMo Guardrails (L3).
- **L4 `ActionInterceptor`** — LangChain callback, pre-tool permission checks. Call `ActionInterceptor.check()` before any tool that writes files or executes code.
- **L5 `AuditLogger`** — append-only JSONL audit log with xxhash chaining.
- **`SecurePromptBuilder`** — isolates user input with canary tokens. All prompts built from user input MUST go through this; never f-string user data into a prompt template.
- **`PermissionManager`** — TTL-based SQLite permission grants.
- **`filesystem_guard.py`** — path confinement via `resolve().is_relative_to()` (Phase 31).

### Provider isolation

All LLM calls go through `prismal/providers/` (LiteLLM wrapper + per-provider configs). Provider-specific imports (`anthropic`, `openai`, `google.generativeai`, `ollama`, etc.) must live only inside this package — never import them from agents, memory, RAG, or elsewhere.

### Other subsystems (one-liners)

- `core/` — Pydantic Settings config (`get_settings()`), logging (`get_logger()`), exceptions, SQLAlchemy database, user model.
- `memory/` — short-term conversation history + long-term PII-sanitized store (SQLite + ChromaDB; optional MongoDB via `[mongodb]`).
- `mcp/` — Model Context Protocol client, adapter, connection manager.
- `rag/` — RAG engine, CRAG pipeline, ChromaDB vector store, document loaders, embeddings, federated search.
- `scheduler/` — APScheduler-based `CronExecutor`, `DateTimeService` (single time-of-truth, timezone-aware), Prefect flows.
- `sandbox/` — `SandboxExecutor` with docker/podman/nsjail/bwrap/firejail backends (Phase 43); AST denylist in `codeact_agent.py`.
- `monitoring/` — Langfuse traces, OpenTelemetry spans, structlog.
- `data/` — DuckDB + Polars utilities.
- `skills/` — `available/` (source, committed) · `active/` (runtime-enabled, gitignored) · `custom/` (AI-generated, gitignored).

## Critical rules

1. **Never** concatenate user input into prompts — use `SecurePromptBuilder`.
2. **Never** bypass `GuardrailsEngine` / `ActionInterceptor`; they are the gateway.
3. **Always** use `get_async_compiled_graph()` in async contexts (the sync variant uses a non-async SQLite saver).
4. **Never** add provider-specific imports outside `prismal/providers/`.
5. **Always** call `ActionInterceptor.check()` before tool calls that write files or execute code.
6. **Never** add `__init__.py` to `prismal/` — it must remain a PEP 420 namespace package. (The repo-local `lightagent/__init__.py` shim is a deliberate, temporary migration exception and is not shipped.)

## Testing notes

- `pytest.ini_options` sets `filterwarnings = ["error", …]`, so new `DeprecationWarning`s from our own code will fail tests. Add specific ignores only for third-party warnings.
- `tests/conftest.py` is minimal; most fixtures live in `tests/integration/conftest.py` and per-tier `conftest.py` files.
- Integration tests under `tests/integration/` expect running services (sandbox backends, databases). They are tagged `@pytest.mark.integration`.
- Ruff's per-file ignores relax rules for `tests/**` and `prismal/skills/{available,custom}/**` — assume the strict rules everywhere else.

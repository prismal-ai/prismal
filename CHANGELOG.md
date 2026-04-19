# Changelog — lightagent-agents

All notable changes to the `lightagent-agents` package are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [2.0.0] — 2026-04-18

### Added
- **Package extraction** — `lightagent-agents` extracted from the `lightagent` monolith as a standalone, publishable Python package (PEP 420 namespace package under `lightagent.*`).
- **26 agent nodes** for LangGraph state machine: `coder`, `researcher`, `codeact_agent`, `cua_agent`, `data_analyst`, `file_manager`, `skill_manager`, `rag_agent`, `planner`, `critic`, `cron_manager`, `parallel_research`, `meta_learner`, `skill_creator`, `domain_supervisor`, `network_supervisor`, and more.
- **Reflection Loop Framework** (Phase 33) — `reflection_loop()` composable pattern in `agents/patterns/reflection.py`.
- **Map-Reduce / Fan-out** (Phase 34) — `make_parallel_dispatcher()` with `Send()` in `agents/patterns/parallel.py`.
- **Human-in-the-Loop** (Phase 35) — `hitl_gate()` factory with `interrupt()` in `agents/subgraphs/gates.py`.
- **Configurable Checkpointing** (Phase 36) — `build_checkpointer()` supporting SQLite and PostgreSQL.
- **Long-Term Memory Store** (Phase 37) — `LongTermMemoryStore` with PII sanitization.
- **CodeAct Agent** (Phase 38) — direct Python code generation with AST denylist hardening (Phase 42).
- **SandboxExecutor** (Phase 43) — process isolation backends: docker, podman, nsjail, bwrap, firejail.
- **Deterministic Intent Router** (Phase 44) — `match_intent()` in `agents/intent_router.py` with global `_MAX_TOTAL_TOOLS = 120` cap.
- **ML/DL Pipeline** (Phase 26) — `Ingester → EDA → Features → Trainer → Evaluator → Exporter` subgraph.
- **Financial Analysis Pipeline** (Phase 27 + 39) — `Collector → Technical → Fundamental → Risk → Report` with quality gates.
- **Computer Use Agent** (Phase 41) — VLM + browser automation with HITL.
- **Hierarchical Multi-Agent Architecture** (Phase 40) — 3-level hierarchy with domain sub-orchestrators.
- **DateTime & Timezone-Aware Cron** (Phase 28) — `DateTimeService` as single time source of truth.
- **NeMo Guardrails** (Phase 20) — NVIDIA NeMo L3 guardrails integration.
- **RBAC & Multi-User** (Phase 19) — `require_role()` dependency, JWT auth, bcrypt passwords.
- **Autonomous v2.0** (Phase 21) — A2A protocol, MetaLearner, voice loop.
- **Native Cron Engine** (Phase 23) — APScheduler with `CronExecutor`, hot-sync via tools.
- **Dynamic Subgraphs** (Phase 24) — `SubgraphFactory.build()` + `SubgraphRegistry`.
- **Package Security & Maintenance** (Phase 30) — OSV API scanner, `uv pip check`, audit reports.
- **API Path Hardening** (Phase 31) — path confinement guards, `resolve().is_relative_to()`.
- **Prompt Quality Framework** (Phase 32) — 7-component system prompts for all 26 agents.

### Changed
- Converted `lightagent/` root to PEP 420 implicit namespace package (removed `__init__.py`).
- All agent, core, provider, memory, MCP, security, scheduler, monitoring, RAG, skills, data, and sandbox modules now live exclusively in this package.

### Notes
- The `lightagent` app package (API, dashboard, channels, CLI) depends on this package via `lightagent-agents>=2.0.0`.
- Install: `pip install lightagent-agents` or `uv pip install lightagent-agents`.
- For all optional extras: `pip install "lightagent-agents[all]"`.

---

## Previous versions

Versions prior to 2.0.0 were part of the `lightagent` monolithic package.
See the main [CHANGELOG.md](../../CHANGELOG.md) in the repository root for history.

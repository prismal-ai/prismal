# prismal documentation

Index of every guide in `docs/` for prismal-ai **v3.19.0**. The project overview, install and
feature list live in the root [README](../README.md); contributor workflow in
[CONTRIBUTING](../CONTRIBUTING.md); the design contract of each feature in
[`specs/`](../specs/roadmap.md) (one `SPEC` / `ARCHITECTURE` / `PLAN` / `TASKS` set per feature);
release history in the [CHANGELOG](../CHANGELOG.md).

Architecture diagrams: [`assets/prismal_architecture.svg`](../assets/prismal_architecture.svg)
(engine, 8 layers), [`assets/prismal_org_architecture.svg`](../assets/prismal_org_architecture.svg)
(the 8-repo organization) and [`prismal-ai-architecture.excalidraw`](./prismal-ai-architecture.excalidraw)
(editable).

## Core engine

| Guide | Covers |
|---|---|
| [agents.md](./agents.md) | Supervisor graph, the 30 routes (15 core + 15 opt-in), `AgentState`, intent router, hierarchical mode, agent module inventory |
| [patterns.md](./patterns.md) | Reasoning patterns (ToT, Debate, Constitutional, LATS, LLM-Compiler, MoA, Swarm) and the reflection / parallel primitives |
| [subgraphs.md](./subgraphs.md) | `SubgraphFactory`, `SubgraphRegistry`, `hitl_gate()` and the 15 pipeline packages |
| [rag.md](./rag.md) | The 11 RAG engines, loaders, context budget, embeddings |
| [vector-stores.md](./vector-stores.md) | `VectorStorePort` and its Chroma / LanceDB / sqlite-vec / Qdrant / pgvector adapters (Phase Z) |
| [memory.md](./memory.md) | Agentic memory v2 (Phase MEM) over the legacy short/long-term stores |
| [providers.md](./providers.md) | `ProviderRegistry`, LiteLLM isolation, cost, STT/TTS/vision/multimodal wrappers, prompt cache, batch |
| [mcp.md](./mcp.md) | MCP client, capability routing, stateless 2026-07-28 mode |
| [skills.md](./skills.md) | Skills tier (`available` / `active` / `custom`) and Kokoro souls |
| [sandbox.md](./sandbox.md) | `SandboxExecutor` and its isolation backends |
| [scheduler.md](./scheduler.md) | Cron executor, `DateTimeService`, Prefect flows |
| [events.md](./events.md) | AG-UI translator, file watcher, webhook dispatcher |
| [data-utilities.md](./data-utilities.md) | DuckDB/Polars utilities, `utils/` and `core/` infrastructure |

## Composition, configuration and extension

| Guide | Covers |
|---|---|
| [composition-root.md](./composition-root.md) | `build_runtime()` / `RuntimeContext` (Phase R) and the host read surface |
| [configuration.md](./configuration.md) | `ConfigSourcePort` and config sources (Phase W) |
| [settings-reference.md](./settings-reference.md) | Every `Settings` field with type and default (generated) |
| [tool-providers.md](./tool-providers.md) | `ToolProviderPort` and the MCP / Skills / stub providers (Phase Y) |
| [extension.md](./extension.md) | `@prismal_node`, `PrismalStateGraphBuilder`, plugins, adapters, ports (Phase X) |
| [node-typesafety.md](./node-typesafety.md) | Per-node Pydantic I/O contracts (Phase NTS) |

## Opt-in layers

| Guide | Covers |
|---|---|
| [kokoro.md](./kokoro.md) | Persona-driven deliberation (Phase K) |
| [skynet.md](./skynet.md) | Swarm supervisor and S+ specialist swarms (Phase S / S+) |
| [blind-review-pipeline.md](./blind-review-pipeline.md) | Two blind reviewers + deterministic synthesis (Phase BRP) |
| [budget.md](./budget.md) | Cost & budget governance (Phase C) |
| [a2a.md](./a2a.md) | Agent2Agent interop (Phase I) |
| [identity.md](./identity.md) | Agent identity & access governance (Phase IDN) |
| [eval.md](./eval.md) | Evaluation & reliability harness (Phase V) |
| [observability-integration.md](./observability-integration.md) | `ObservabilityPort`, run summaries (Phase OBS) |
| [loop-hardening.md](./loop-hardening.md) | Context compaction and phase-based tool gating (Phase LH) |
| [token-efficiency.md](./token-efficiency.md) | Prompt caching, tool gating, observation masking (Phase TKE) |
| [gateways.md](./gateways.md) | OpenRouter / OpenCode / OmniRoute gateways (Phase GW) |
| [decisions.md](./decisions.md) | Typed decision cascade, incl. the Ollama backend (Phase DEC) |
| [routing.md](./routing.md) | Adaptive model routing (Phase AMR) |
| [protocols-2026.md](./protocols-2026.md) | A2A v1.0, MCP stateless + tasks, AG-UI 1.0 (Phase PRT) |

## Security

| Guide | Covers |
|---|---|
| [security/runtime-hardening.md](./security/runtime-hardening.md) | Taint tracking, injection detection, output validation, tool policies (Phase H) |
| [security/guardrails-modernization.md](./security/guardrails-modernization.md) | NeMo rails config, safety classifier, `StructuredOutputGuard` (Phase GRD) |
| [security/hardening-and-harness-engineering.md](./security/hardening-and-harness-engineering.md) | Background analysis behind Phases H and V |

## Decisions, analyses and context

| Document | Covers |
|---|---|
| [adr/ADR-WAVE-001-llm-construction-contract.md](./adr/ADR-WAVE-001-llm-construction-contract.md) | Fixed `get_llm()` wrapper stack and cost precedence |
| [adr/ADR-PRT-001-protocol-pins.md](./adr/ADR-PRT-001-protocol-pins.md) | Wire-name pins for A2A v1.0 / MCP / AG-UI |
| [competitive-analysis.md](./competitive-analysis.md) | Comparison with 2026 agent frameworks |
| [gap-analysis-loops-harness-guardrails-2026-07.md](./gap-analysis-loops-harness-guardrails-2026-07.md) | July 2026 gap analysis (ES) — closed by LH / GRD / BRP |
| [gap-analysis-gateways-tokens-decisions-2026-10.md](./gap-analysis-gateways-tokens-decisions-2026-10.md) | October 2026 gap analysis — closed by wave 2026-10 |
| [mcp-skills-injection-analysis.md](./mcp-skills-injection-analysis.md) | Analysis that led to Phase Y |
| [running-the-ecosystem.md](./running-the-ecosystem.md) | Running the full 8-repo ecosystem locally (ES) |
| [blog-prismal-ecosystem.md](./blog-prismal-ecosystem.md) | Ecosystem overview blog post (ES) |
| [history/PLAN_MIGRACION.md](./history/PLAN_MIGRACION.md) | Historical `lightagent-agents` → `prismal` migration plan (ES) |

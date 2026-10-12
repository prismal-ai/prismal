# Contributing to prismal

Thank you for your interest in improving **prismal** (distribution `prismal-ai`, import namespace `prismal`)! This document explains how to propose changes, set up your environment, and get your contribution merged smoothly.

> Last updated: 2026-10-12 · prismal-ai **v3.19.0**

---

## Table of Contents

1. [Code of Conduct](#code-of-conduct)
2. [Ways to Contribute](#ways-to-contribute)
3. [Before You Start](#before-you-start)
4. [Development Setup](#development-setup)
5. [Project Layout](#project-layout)
6. [Spec-Driven Development (SDD)](#spec-driven-development-sdd)
7. [Opt-in Feature Flags](#opt-in-feature-flags)
8. [Making Changes](#making-changes)
9. [Settings and Generated Docs](#settings-and-generated-docs)
10. [Coding Standards](#coding-standards)
11. [Security Guidelines](#security-guidelines)
12. [Testing](#testing)
13. [Continuous Integration](#continuous-integration)
14. [Submitting a Pull Request](#submitting-a-pull-request)
15. [Release Process](#release-process)
16. [Getting Help](#getting-help)

---

## Code of Conduct

This project follows the [Contributor Covenant](https://www.contributor-covenant.org/version/2/1/code_of_conduct/) (v2.1). By participating you agree to uphold a welcoming and harassment-free environment for everyone, regardless of experience level, identity, or background. Violations can be reported to **ecrespo@gmail.com**.

---

## Ways to Contribute

| What | Where |
|---|---|
| Bug reports | [GitHub Issues](https://github.com/prismal-ai/prismal/issues) — include the full traceback, the exact command, and your prismal version |
| Feature requests | GitHub Issues — describe the use case; larger features go through an SDD (see [below](#spec-driven-development-sdd)) |
| Documentation fixes | PR against `README.md`, `docs/`, `examples/`, or docstrings |
| New agent nodes | See [Adding an Agent Node](#adding-an-agent-node) |
| New RAG engine | See [Adding a RAG Engine](#adding-a-rag-engine) |
| New subgraph pipeline | See [Adding a Subgraph Pipeline](#adding-a-subgraph-pipeline) — or ship it as an external plugin (`examples/plugin_template/`, `docs/extension.md`) |
| Security vulnerabilities | **Do not open a public issue** — email ecrespo@gmail.com directly |

---

## Before You Start

- Search existing [issues](https://github.com/prismal-ai/prismal/issues) and [pull requests](https://github.com/prismal-ai/prismal/pulls) to avoid duplicating work.
- For non-trivial changes (new agents, new subgraphs, new opt-in layers, architectural decisions), open an **issue first** and discuss the approach before writing code. Anything that adds a subsystem or a settings block is expected to land as an SDD under `specs/`.
- Read `CLAUDE.md` at the repo root: it is the most up-to-date map of the architecture, the critical rules, and the per-phase invariants.
- All contributions must be licensed under MIT and you must have the right to submit the code.

---

## Development Setup

**Requirements:** Python 3.13+ (`requires-python = ">=3.13"`), [uv](https://docs.astral.sh/uv/) package manager.

```bash
# 1. Fork and clone
git clone https://github.com/<your-fork>/prismal.git
cd prismal

# 2. Install with dev tools (add ",all" for every optional extra)
uv pip install -e ".[dev]"
uv pip install -e ".[dev,all]"
# CI uses the frozen lockfile instead: uv sync --extra dev --frozen

# 3. Install the git hooks (pre-commit, pre-push, commit-msg)
uv run pre-commit install

# 4. Verify the environment
uv run pytest tests/unit -m "not live_api" -q
uv run ruff check .
uv run mypy prismal
```

Optional extras (see `[project.optional-dependencies]` in `pyproject.toml`): `postgres`, `mongodb`, `local-embeddings`, `ollama`, `analytics`, `ml`, `ml-dl`, `finance`, `datetime`, `maintenance`, vector stores (`lancedb`, `sqlite-vec`, `qdrant`, `pgvector`), `a2a`, multimodal (`multimodal`, `multimodal-local`, `multimodal-premium`, `multimodal-embed`), `guardrails-ai`, `rerank`, `compression`, decisions (`laya`, `jev`), `graph-memory`, plus `all` and `dev`.

Configuration: every setting uses the `PRISMAL_` prefix. Copy [`env.example`](./env.example) to `.env` and uncomment what you need; the full list is in [`docs/settings-reference.md`](./docs/settings-reference.md). Both files are generated from `Settings` (see [Settings and Generated Docs](#settings-and-generated-docs)).

Integration tests that need live services are tagged `@pytest.mark.integration` and are skipped automatically if the services are not available.

---

## Project Layout

```
prismal/                            # repo root
├── prismal/                        # PEP 420 namespace package — NO __init__.py at this level
│   ├── a2a/                        # A2A interop: v0.3 + v1.0 dual-stack server/client, Agent Card, task store, OAuth
│   ├── agents/                     # Agent nodes, supervisor graph, intent router, tool registry
│   │   ├── graph.py                # LangGraph StateGraph assembly (30 routes: 15 core + 15 opt-in)
│   │   ├── extension/              # Public extension API: @prismal_node, builder, plugins, ports, tool providers
│   │   ├── kokoro/ · skynet/ · multimodal/   # Opt-in agent layers
│   │   ├── patterns/               # Composable reasoning patterns + primitives
│   │   └── subgraphs/              # Domain pipelines (15 packages) + SubgraphRegistry/factory
│   ├── budget/                     # Cost & budget governance (CostMeter, BudgetGuard)
│   ├── composition/                # Composition root: build_runtime(), RuntimeContext, host read surface
│   ├── core/                       # Settings, ConfigSourcePort, logging, exceptions, database helpers
│   ├── data/                       # DuckDB + Polars utilities
│   ├── decisions/                  # Typed decision models: DecisionPort, cascade, calibration, adapters
│   ├── eval/                       # Evaluation & red-team harness (python -m prismal.eval)
│   ├── events/                     # AG-UI 1.0 translator, file watcher, webhook dispatcher
│   ├── identity/                   # Agent DIDs, credential vaults, OBO delegation, PolicyEngine
│   ├── mcp/                        # Model Context Protocol client (legacy + 2026-07-28 stateless)
│   ├── memory/                     # Short/long-term memory + v2/ (agentic memory, opt-in)
│   ├── monitoring/                 # Langfuse, OpenTelemetry, structlog, observability port
│   ├── providers/                  # LLM providers (ALL provider SDK imports live here) + gateways/
│   ├── rag/                        # 11 RAG engines, VectorStorePort adapters (rag/stores/), loaders
│   ├── routing/                    # Adaptive model routing (RoutingPolicyPort, tiers, batch assignment)
│   ├── sandbox/                    # Process isolation backends for run_code
│   ├── scheduler/                  # Cron (APScheduler), DateTimeService, Prefect flows
│   ├── security/                   # 5-layer defense stack + runtime hardening + guardrails
│   ├── skills/                     # Skills tier: available/ (committed), active/ + custom/ (gitignored)
│   ├── souls/                      # Kokoro persona souls (available/{spirit,mind,heart}/SOUL.md)
│   ├── utils/                      # Shared helpers (TOON, token estimates, stable JSON, …)
│   ├── langgraph.py                # Official LangGraph re-export surface
│   └── plugins.py                  # python -m prismal.plugins (list | info | doctor)
├── specs/                          # SDDs: one folder per feature + roadmap.md
├── docs/                           # User guides, ADRs (docs/adr/), generated settings-reference.md
├── examples/                       # Runnable examples (see examples/README.md and examples.md)
├── scripts/                        # Maintenance scripts (gen_settings_docs.py)
├── config/                         # Shipped YAML config (MCP servers, NeMo rails, routing tiers, prompt cache, …)
├── tests/                          # unit/ · security/ · e2e/ · eval/ · integration/ · interop/ · live/ · fixtures/
├── compat/lightagent-agents/       # Deprecated lightagent-agents → prismal-ai bridge distribution
├── env.example                     # GENERATED from Settings — do not edit by hand
├── pyproject.toml                  # Build config, dependencies, tool config (single source of the version)
├── CHANGELOG.md                    # Keep-a-Changelog format
├── CLAUDE.md                       # Architecture map, critical rules, wave conventions
└── CONTRIBUTING.md                 # This file
```

**Critical constraints** (full list: `CLAUDE.md` → "Critical rules"):

- `prismal/__init__.py` must **never** be created — `prismal/` stays a PEP 420 implicit namespace package.
- Provider-specific imports (`anthropic`, `openai`, `google.generativeai`, `ollama`, `whisper`, `elevenlabs`, …) must **only** appear inside `prismal/providers/` (decision-backend SDKs `laya` / `typesafe_sdk` only inside `prismal/decisions/adapters/`).
- User input — including STT transcripts, OCR text, captions and Kokoro `SOUL.md` bodies — must **never** be f-string-concatenated into prompts; always use `SecurePromptBuilder`.
- `prismal/agents/**` must not import `prismal.mcp` / `prismal.skills`; tools reach agents only through the injected `ToolProviderPort` (AST-guarded; in tests inject `FakeToolProvider`).
- Async code uses `get_async_compiled_graph()`, never the sync variant.
- No new literal `os.getenv` / `os.environ` reads under `prismal/**`; configuration comes from the injected `ConfigSourcePort` (AST guard `tests/unit/core/test_no_env_reads.py`).

Several more import boundaries are enforced by AST tests (e.g. `routing/` imports no provider SDK, `events/agui.py` imports no HTTP stack, only `memory/v2/forget.py` may call `.purge(`). If one of these tests fails, fix the import rather than adding an exemption.

---

## Spec-Driven Development (SDD)

Every feature larger than a bug fix is designed before it is built. Each one lives in its own folder under `specs/` with four documents:

| File | Content |
|---|---|
| `specs/<feature>/PLAN.md` | Strategic plan / PRD: problem and context (code facts), target users, goals and success metrics, scope / out of scope, phases overview, risks, dependencies |
| `specs/<feature>/ARCHITECTURE.md` | Technical design: modules, data flows, migration path, numbered design decisions (`DD-<PHASE>-NNN`) |
| `specs/<feature>/SPEC.md` | Interface specification: one `SPEC-<PHASE>-<AREA>-NNN` section per module / contract (types, ports, settings, exceptions, OTel instruments, "disabled path unchanged"), plus an acceptance-criteria index |
| `specs/<feature>/TASKS.md` | Implementation plan: phases (`<PHASE>0` … `<PHASE>N`), each with a "done when" gate and a task table (`ID`, task, estimate, dependency, SPEC reference, status `TODO`/`WIP`/`DONE`/`BLOCKED`/`DEFERRED`) |

Some specs add `VERIFY.md` (recorded vendor facts), `DELTA-*.md` (post-release amendments, e.g. `specs/typed-decision-models/DELTA-2026-10-ollama-backend.md`) or `ADDENDUM-*.md`. Cross-cutting decisions go to `docs/adr/` (e.g. `ADR-WAVE-001-llm-construction-contract.md`, `ADR-PRT-001-protocol-pins.md`).

[`specs/roadmap.md`](./specs/roadmap.md) is the living index of every SDD — what shipped (with version and docs page), what is ready, and in which order pending work fits. A good reference SDD to copy is [`specs/agentic-memory/`](./specs/agentic-memory/).

Workflow:

1. Write PLAN → ARCHITECTURE → SPEC → TASKS; add the spec to `specs/roadmap.md` as `READY`.
2. **Verify before build:** facts marked **[verify]** (post-mid-2026, single-source or vendor-published) are checked in a phase-0 task and recorded where the spec says (`VERIFY.md`, an ADR, or `docs/<feature>.md#verified-facts`).
3. Implement phase by phase, strict TDD: every MUST maps to at least one task and one test. Keep `TASKS.md` statuses current.
4. On merge: set the spec `Status` to `IMPLEMENTED`, update `specs/roadmap.md`, add `docs/<feature>.md`, an example under `examples/`, a README Features entry, and the CHANGELOG entry.

---

## Opt-in Feature Flags

New layers ship **off by default**. The convention, used by every phase since Multimodal:

- One master flag (`<feature>_enabled: bool = False`) plus optional sub-flags that are only effective with the master on. Rollout-sensitive features add a mode (`shadow` first, then `enforce`).
- **Flag off ⇒ byte-for-byte unchanged:** with the flag off, the compiled supervisor graph, node outputs, tool lists, prompts and `get_llm()` kwargs must be identical to the last released minor. This is enforced by a graph-snapshot test per feature (`tests/unit/agents/test_graph_snapshot_<feature>.py`) and, where relevant, disabled-path contract tests (e.g. `tests/unit/providers/test_registry_contract.py`, `tests/unit/providers/test_tke_disabled_contract.py`).
- Snapshot / disabled-contract baselines are taken against **the last released minor at merge time**, not a version number hard-coded in the spec.
- New state lives under a namespaced key in `state["metadata"]` (e.g. `["mm"]`, `["kokoro"]`, `["routing"]`); live objects (meters, clients) never go into checkpointed state — use an in-process registry keyed by `session_id`.
- Security guards fail **closed**; lookups and optional enrichments fail **open**.

---

## Making Changes

### Branching

Branch off `main` (or `develop` for wave work) using the naming convention:

| Type | Branch name |
|---|---|
| Bug fix | `fix/<short-description>` |
| Feature | `feat/<short-description>` |
| Refactor | `refactor/<short-description>` |
| Docs | `docs/<short-description>` |
| Security | `security/<short-description>` |

### Commit Messages

Follow [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<scope>): <short description>

[optional body]

[optional footer: Closes #123]
```

Types: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`, `security`.
Scope examples: `agents`, `rag`, `security`, `providers`, `memory`, `graph`, `decisions`, `routing`, `a2a`, `mcp`, `budget`.

### Wave conventions (shared hot files)

When several phases are developed in parallel (see `specs/wave-2026-10-implementation-plan.md` and `CLAUDE.md` → "Wave 2026-10 conventions"):

- Each phase adds exactly **one contiguous `# --- <PHASE> ---` settings block** plus **one `_validate_<phase>` validator** in `prismal/core/config.py`, **one exception subtree** in `prismal/core/exceptions.py`, and **one `_register_<phase>_metrics()` helper** in `prismal/monitoring/otel.py`. Rebase parallel branches on `develop` before each merge.
- LLM construction is fixed by `docs/adr/ADR-WAVE-001-llm-construction-contract.md`: the `ProviderRegistry.get_llm` signature, the wrapper stack (`GatewayChatLiteLLM` innermost, `CacheAwareChatModel` outer, adaptive routing only picks the model string, `with_fallbacks` outermost) and the `compute_cost_usd` precedence. **Extend** the shared `tests/unit/providers/test_registry_contract.py`; never replace it.
- Verify volatile vendor facts before building (phase 0), as described in the SDD workflow.

### Adding an Agent Node

1. Create `prismal/agents/<name>.py` following the pattern of existing nodes (e.g. `coder.py`, `planner.py`); resolve tools with `get_tools_for_agent_ctx(name, config)` — never import MCP/skills.
2. Register the node in `prismal/agents/graph.py` (add it to the `StateGraph` and to the supervisor's valid routes). An opt-in node must be gated on its flag and come with a graph-snapshot test.
3. Declare its capabilities in the tool registry — the global cap is **120 tools**, enforced by `CompositeToolProvider`.
4. Write unit tests in `tests/unit/agents/` with an injected `FakeToolProvider` and a mocked LLM / `ProviderRegistry`.
5. Update the route counts in `README.md`, `CLAUDE.md` and `docs/agents.md`, and add a CHANGELOG entry.

### Adding a RAG Engine

1. Create `prismal/rag/<name>.py` following the interface of existing engines (e.g. `hyde.py`, `fusion.py`).
2. Type the store against `VectorStorePort` and build defaults via `VectorStoreFactory` — never construct a backend directly.
3. Register the engine in `prismal/rag/adaptive.py` (the routing facade) if it should be auto-selected.
4. Business logic must accept callables (`generate_fn`, `retrieve_fn`, …) so tests run without LLM backends; test with stub callables or `FakeVectorStore`.
5. Document it in `docs/rag.md`.

### Adding a Subgraph Pipeline

1. Create `prismal/agents/subgraphs/<domain>/` with `__init__.py`.
2. Export both `build_<name>_subgraph()` (returns `SubgraphDefinition`) and `register_<name>()` (idempotent registry install).
3. Mirror the pattern of existing pipelines (e.g. `customer_service`, `code_review`).
4. Register it in `SubgraphRegistry`, document it in `docs/subgraphs.md`, and add a CHANGELOG entry.

Third-party pipelines do not need to live in this repo: package them as a plugin with entry points (`examples/plugin_template/`, `docs/extension.md`).

---

## Settings and Generated Docs

`env.example` and `docs/settings-reference.md` are **generated** from the pydantic models in `prismal/core/config.py` (`Settings` and `MaintenanceSettings`). Never edit them by hand.

After adding or changing a `Settings` field (name, type, default, alias, or `Field(description=...)`):

```bash
uv run python scripts/gen_settings_docs.py          # rewrite env.example + docs/settings-reference.md
uv run python scripts/gen_settings_docs.py --check  # exit 1 if they are stale
```

`tests/unit/core/test_settings_docs_sync.py` runs the check in the unit suite, so CI fails if the generated files drift. Write a meaningful `Field(description=...)` — it becomes the documentation. Section headings come from the `# ── Section ──` comments in `config.py`.

---

## Coding Standards

All code is checked automatically in CI. Run these locally before pushing (the pre-commit hooks run most of them for you):

```bash
# Format and lint (ruff is the only linter/formatter; line-length=100, target py313)
uv run ruff format .
uv run ruff check --fix .

# Type checking (strict mypy, namespace_packages=true)
uv run mypy prismal
uv run ty check --output-format=concise prismal/      # informational

# Complexity
uv run radon cc prismal/ -n E -s                       # no rank E/F functions (CC >= 21)
uv run lizard prismal/ --CCN 40 --arguments 15 --length 350 -w
uv run flake8 --select=CCR001 --max-cognitive-complexity=100 prismal/

# Security
uv run bandit -r prismal -c pyproject.toml --severity-level=medium
uv run pip-audit --skip-editable                       # CI adds the documented --ignore-vuln list
```

Key style rules:

- **Type annotations** are required on all public functions and methods (mypy strict).
- **Docstrings** are required on public classes, functions, and modules.
- **Ruff McCabe complexity** per function: 40 (`[tool.ruff.lint.mccabe]`).
- **Maximum cognitive complexity** (flake8-cognitive-complexity, CCR001): 100 (`setup.cfg`; long-term target 12).
- Lizard limits: CCN 40, 15 arguments, 350 lines per function.
- Tests use `pytest.mark` — tag tests with the relevant markers: `unit`, `integration`, `security`, `e2e`, `slow`, `live_api`, `eval`, `redteam`, `interop`, `ollama` (`--strict-markers` is on).

---

## Security Guidelines

Security is a core feature of this project. Any contribution that touches the security stack requires extra care:

- **Never bypass** `GuardrailsEngine` or `ActionInterceptor`. They are the mandatory gateway to all tool calls and LLM interactions.
- **Always call** `ActionInterceptor.check()` before tool calls that write files or execute code, and `ActionInterceptor.check_media_op()` before media read/write.
- **Always use** `SecurePromptBuilder` for any prompt that incorporates user-controlled input. Changes to the prompt layout must keep red-team canary-leak parity (`tests/unit/security/test_canary_leak_cache_stable.py`).
- **Validate media** with `MediaValidator.validate()` before it reaches a multimodal agent; run FFmpeg only through `SandboxExecutor`.
- **Untrusted text** reaches decision models only through `DecisionStateBuilder` and memory only as tainted episodes. Remote A2A / MCP / tool content is L1-sanitized before touching `AgentState`.
- **Secrets** stay `SecretStr` and never reach state, logs, URLs or `repr`; resolve them through a vault or the injected `ConfigSourcePort`.
- **Filesystem writes** must be validated through `filesystem_guard.py` path confinement.
- **New sandbox backends** must pass the security test suite in `tests/unit/security/` and `tests/security/`.
- Security corpora are append-only (e.g. `tests/security/decisions/test_injection_corpus.py`).

If you discover a security vulnerability, please **do not open a public GitHub issue**. Email ecrespo@gmail.com with a description of the issue and steps to reproduce it. You will receive a response within 48 hours.

---

## Testing

```bash
# Fast unit tests (no external services) — what CI runs, in parallel, with coverage
uv run pytest tests/unit -m "not live_api" -n auto --cov=prismal --cov-branch --cov-report=term-missing

# Security, end-to-end and evaluation tiers
uv run pytest tests/security -m security
uv run pytest tests/e2e -m e2e
uv run pytest tests/unit/eval -m "not live_api"

# Integration tests (need running services; run in their own pytest session)
uv run pytest tests/integration -m "integration and not live_api"

# Opt-in suites
PRISMAL_INTEROP=1 uv run pytest -m interop        # official protocol conformance
PRISMAL_OLLAMA_CONTRACT=1 uv run pytest -m ollama tests/live/decisions/test_ollama_contract.py

# A single test
uv run pytest tests/unit/security/test_sanitizer.py::test_strip_removes_null_byte
```

**Test requirements:**

- Every new public function needs at least one unit test; coverage must stay at or above **80 %** (`fail_under = 80`).
- New agent nodes require tests with mocked `generate_fn` / `ProviderRegistry` and an injected `FakeToolProvider`.
- New opt-in layers require a graph-snapshot test proving the flag-off path is unchanged.
- New security components require tests in `tests/unit/security/` (and containment cases in `tests/security/` where relevant).
- Do not add `DeprecationWarning`s from project code — `filterwarnings = ["error", …]` is set globally.
- Integration tests must be decorated with `@pytest.mark.integration` and must not fail if services are unavailable. Never run `tests/integration` in the same pytest session as `tests/unit` (their conftests replace modules such as `apscheduler` in `sys.modules`).
- `live_api` tests call real LLM APIs and require provider keys; skip them locally with `-m "not live_api"`.

---

## Continuous Integration

`.github/workflows/ci.yml` runs on every pull request and on pushes to `main` / `develop` (pipeline shape: lint → typecheck → complexity → security → test):

| Stage | Job | Command / tool | Blocking |
|---|---|---|---|
| Lint | `lint` | `ruff check .` + `ruff format --check .` | yes |
| Type checking | `mypy` | `mypy prismal` | yes |
| | `ty` | `ty check prismal/` | no (informational) |
| Complexity | `complexity-radon` | `radon cc` report + rank E/F gate | no (informational) |
| | `complexity-lizard` | `lizard --CCN 40 --arguments 15 --length 350` | yes |
| | `complexity-cognitive` | `flake8 --select=CCR001 --max-cognitive-complexity=100` | yes |
| Security | `security-bandit` | `bandit -r prismal --severity-level=medium` | yes |
| | `security-gitleaks` | gitleaks (pinned binary, `.gitleaks.toml`) | yes |
| | `security-pip-audit` | `pip-audit` with the documented ignore list | yes |
| | `security-semgrep` | semgrep `p/python`, `p/secrets`, `p/owasp-top-ten` | yes |
| | `security-trivy` | trivy fs (pinned + checksum-verified), HIGH/CRITICAL gate, `.trivyignore` | yes |
| Tests | `test-unit` | `pytest tests/unit -m "not live_api" -n auto --cov` | yes |
| | `test-security` | `pytest tests/security -m security` | yes |
| | `test-e2e` | `pytest tests/e2e -m e2e` | yes |
| | `test-eval` | `pytest tests/unit/eval -m "not live_api"` (eval + red-team with fakes) | yes |
| | `test-integration` | `pytest tests/integration -m "integration and not live_api"` | no (optional services) |

Other workflows: `mutation.yml` (mutmut over `prismal/security/`, weekly + manual, non-blocking), `release.yml` and `docker-publish.yml` (see [Release Process](#release-process)). Ignored CVEs must be mirrored across `ci.yml`, `.trivyignore` and `.pre-commit-config.yaml`, each with a justification and a re-evaluation trigger.

---

## Submitting a Pull Request

1. Ensure all local checks pass (ruff, mypy, complexity, bandit, pytest) and, if you touched `Settings`, that `scripts/gen_settings_docs.py --check` is clean.
2. Update `CHANGELOG.md` under `## [Unreleased]` with a brief description of your change.
3. For a feature, update its spec (`TASKS.md` statuses), `specs/roadmap.md`, the relevant `docs/` page and, if user-visible, `README.md` and `examples/`.
4. Push your branch and open a PR against `main` (or `develop` for wave work). Link the issue, describe the change, and list testing steps.
5. A maintainer will review within a few business days. Please be responsive to feedback.
6. PRs require:
   - All blocking CI jobs green.
   - At least one approving review from a maintainer.
   - No unresolved review comments.

Once approved, a maintainer will merge using **squash merge** to keep the history clean.

---

## Release Process

Releases are managed by the maintainers (see also README → "Releasing (maintainers)"):

1. Update `version` in `pyproject.toml` (single source of truth) following [Semantic Versioning](https://semver.org/) — new opt-in functionality is a minor bump.
2. Move `## [Unreleased]` entries to a new `## [X.Y.Z] — YYYY-MM-DD` section in `CHANGELOG.md` (the GitHub Release notes are extracted from this section).
3. Run the quality gates: `uv run pytest -m "not live_api"`, `uv run ruff check .`, `uv run mypy prismal`, `uv run bandit -r prismal -c pyproject.toml`.
4. Commit (`chore(release): bump version to X.Y.Z`) and push to `main`.
5. Tag and push: `git tag prismal/vX.Y.Z && git push --tags` (tag format `prismal/vMAJOR.MINOR.PATCH`).

Automation:

- `release.yml` builds the sdist/wheel (`python -m build` + `twine check`) and publishes to PyPI on every push to `main` and on `prismal/v*` tags, with `skip-existing` (a no-op when the version is unchanged). It authenticates with an API token stored as a GitHub secret (`PYPI_API_TOKEN` in the `pypi` environment), not OIDC. A manual `workflow_dispatch` run can target TestPyPI (`TEST_PYPI_API_TOKEN`) for a dry run.
- On a `prismal/vX.Y.Z` tag, `release.yml` also creates the GitHub Release (notes from the CHANGELOG section, built artifacts attached) and `docker-publish.yml` pushes the container image to `ghcr.io/prismal-ai/prismal` tagged `X.Y.Z` + `latest`.
- The deprecated `compat/lightagent-agents` bridge is built and uploaded manually, as described in the README.

Contributors do not need to manage releases.

---

## Getting Help

- **Discussions:** Open a [GitHub Discussion](https://github.com/prismal-ai/prismal/discussions) for questions about usage or architecture.
- **Issues:** Open a [GitHub Issue](https://github.com/prismal-ai/prismal/issues) for bugs or feature requests.
- **Docs:** `docs/` holds one guide per subsystem; `CLAUDE.md` is the architecture map.
- **Email:** ecrespo@gmail.com for security reports or private matters.

We appreciate every contribution, no matter how small. Thank you for helping make prismal better!

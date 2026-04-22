# Contributing to lightagent-agents

Thank you for your interest in improving **lightagent-agents**! This document explains how to propose changes, set up your environment, and get your contribution merged smoothly.

---

## Table of Contents

1. [Code of Conduct](#code-of-conduct)
2. [Ways to Contribute](#ways-to-contribute)
3. [Before You Start](#before-you-start)
4. [Development Setup](#development-setup)
5. [Project Layout](#project-layout)
6. [Making Changes](#making-changes)
7. [Coding Standards](#coding-standards)
8. [Security Guidelines](#security-guidelines)
9. [Testing](#testing)
10. [Submitting a Pull Request](#submitting-a-pull-request)
11. [Release Process](#release-process)
12. [Getting Help](#getting-help)

---

## Code of Conduct

This project follows the [Contributor Covenant](https://www.contributor-covenant.org/version/2/1/code_of_conduct/) (v2.1). By participating you agree to uphold a welcoming and harassment-free environment for everyone, regardless of experience level, identity, or background. Violations can be reported to **ecrespo@gmail.com**.

---

## Ways to Contribute

| What | Where |
|---|---|
| Bug reports | [GitHub Issues](https://github.com/your-org/lightagent/issues) — use the **bug** template |
| Feature requests | GitHub Issues — use the **enhancement** template |
| Documentation fixes | PR against `README.md`, `CHANGELOG.md`, or docstrings |
| New agent nodes | See [Adding an Agent Node](#adding-an-agent-node) below |
| New RAG engine | See [Adding a RAG Engine](#adding-a-rag-engine) below |
| Security vulnerabilities | **Do not open a public issue** — email ecrespo@gmail.com directly |

---

## Before You Start

- Search existing [issues](https://github.com/your-org/lightagent/issues) and [pull requests](https://github.com/your-org/lightagent/pulls) to avoid duplicating work.
- For non-trivial changes (new agents, new subgraphs, architectural decisions), open an **issue first** and discuss the approach before writing code.
- All contributions must be licensed under MIT and you must have the right to submit the code.

---

## Development Setup

**Requirements:** Python 3.13+, [uv](https://docs.astral.sh/uv/) package manager.

```bash
# 1. Fork and clone
git clone https://github.com/<your-fork>/lightagent.git
cd lightagent/lightagent-agents

# 2. Install with all dev extras
uv pip install -e ".[dev,all]"

# 3. Verify the environment
uv run pytest tests/unit -q
uv run ruff check lightagent/
uv run mypy lightagent
```

Optional service dependencies for integration tests (Docker recommended):

```bash
# PostgreSQL checkpointing
uv pip install -e ".[postgres]"

# MongoDB long-term memory
uv pip install -e ".[mongodb]"
```

Integration tests that need live services are tagged `@pytest.mark.integration` and will be skipped automatically if the services are not available.

---

## Project Layout

```
lightagent-agents/
├── lightagent/               # PEP 420 namespace package — NO __init__.py at root
│   ├── agents/               # Agent nodes, graph, intent router, tool registry
│   │   ├── graph.py          # LangGraph StateGraph assembly
│   │   ├── patterns/         # Composable reasoning primitives
│   │   └── subgraphs/        # Domain pipelines
│   ├── providers/            # LLM provider wrappers (ALL provider imports live here)
│   ├── security/             # 5-layer defense stack
│   ├── rag/                  # RAG engines
│   ├── memory/               # Short- and long-term memory
│   ├── mcp/                  # Model Context Protocol client
│   ├── scheduler/            # Cron + APScheduler
│   ├── sandbox/              # Process isolation backends
│   ├── monitoring/           # Observability (Langfuse, OTEL)
│   └── core/                 # Settings, logging, exceptions, database
├── tests/
│   ├── unit/                 # Fast, no-external-service tests
│   └── integration/          # Tests that need running services
├── pyproject.toml            # Build config, dependencies, tool config
├── CHANGELOG.md              # Keep-a-Changelog format
└── CONTRIBUTING.md           # This file
```

**Critical constraints:**

- `lightagent/__init__.py` must **never** be created — it would break the sibling `lightagent` app package (PEP 420 namespace).
- Provider-specific imports (`anthropic`, `openai`, `google.generativeai`, `ollama`, …) must **only** appear inside `lightagent/providers/`.
- User input must **never** be f-string-concatenated into prompts — always use `SecurePromptBuilder`.

---

## Making Changes

### Branching

Branch off `main` using the naming convention:

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
Scope examples: `agents`, `rag`, `security`, `providers`, `memory`, `graph`.

### Adding an Agent Node

1. Create `lightagent/agents/<name>_agent.py` following the pattern in existing nodes.
2. Register the node in `lightagent/agents/graph.py` (add to `StateGraph` and to the supervisor routing table).
3. Add any new tools to `tool_registry.py` — the global cap is **120 tools** (`_MAX_TOTAL_TOOLS`).
4. Write unit tests in `tests/unit/agents/test_<name>_agent.py`.
5. Update the agent count in `README.md` and document the new node in `CHANGELOG.md`.

### Adding a RAG Engine

1. Create `lightagent/rag/<name>.py` following the interface of existing engines (e.g., `hyde.py`, `fusion.py`).
2. Register the engine in `lightagent/rag/adaptive.py` (the routing facade).
3. Business logic must accept callables (`generate_fn`, `retrieve_fn`, …) so tests run without LLM backends.
4. Write unit tests using stub callables, not real LLMs.

### Adding a Subgraph Pipeline

1. Create `lightagent/agents/subgraphs/<domain>/` with `__init__.py`.
2. Export both `build_<name>_subgraph()` (returns `SubgraphDefinition`) and `register_<name>()` (idempotent registry install).
3. Mirror the pattern of existing pipelines (e.g., `ml_pipeline`, `financial`).
4. Add the subgraph to `SubgraphRegistry` and document in `CHANGELOG.md`.

---

## Coding Standards

All code is checked automatically in CI. Run these locally before pushing:

```bash
# Format and lint (line-length=100, target py313)
uv run ruff format lightagent/ tests/
uv run ruff check --fix lightagent/ tests/

# Type checking (strict mypy, namespace_packages=true)
uv run mypy lightagent

# Security linting
uv run bandit -r lightagent -c pyproject.toml
```

Key style rules:

- **Type annotations** are required on all public functions and methods (mypy strict).
- **Docstrings** are required on public classes, functions, and modules.
- **Maximum cognitive complexity** (flake8-cognitive-complexity): 100 (setup.cfg).
- **Ruff McCabe complexity** per function: 40.
- Tests use `pytest.mark` — tag every test with at least one of: `unit`, `integration`, `security`, `slow`, `live_api`.

---

## Security Guidelines

Security is a core feature of this project. Any contribution that touches the security stack requires extra care:

- **Never bypass** `GuardrailsEngine` or `ActionInterceptor`. They are the mandatory gateway to all tool calls and LLM interactions.
- **Always call** `ActionInterceptor.check()` before tool calls that write files or execute code.
- **Always use** `SecurePromptBuilder` for any prompt that incorporates user-controlled input.
- **Never use** f-strings or string concatenation to build prompts from user data.
- **Filesystem writes** must be validated through `filesystem_guard.py` path confinement.
- **New sandbox backends** must pass the security test suite in `tests/unit/security/`.

If you discover a security vulnerability, please **do not open a public GitHub issue**. Email ecrespo@gmail.com with a description of the issue and steps to reproduce it. You will receive a response within 48 hours.

---

## Testing

```bash
# Fast unit tests (no external services)
uv run pytest tests/unit -v

# Security-focused tests
uv run pytest -m security -v

# All tests (requires running services for integration)
uv run pytest

# Parallel execution
uv run pytest -n auto

# Coverage report (target: 80% minimum, enforced in CI)
uv run pytest --cov=lightagent --cov-report=term-missing

# Skip tests that call real LLM APIs
uv run pytest -m "not live_api"
```

**Test requirements:**

- Every new public function needs at least one unit test.
- New agent nodes require tests with mocked `generate_fn` / `ProviderRegistry`.
- New security components require tests in `tests/unit/security/`.
- Do not add `DeprecationWarning` from project code — `filterwarnings = ["error"]` is set globally.
- Integration tests must be decorated with `@pytest.mark.integration` and must not fail if services are unavailable.

---

## Submitting a Pull Request

1. Ensure all local checks pass (ruff, mypy, bandit, pytest).
2. Update `CHANGELOG.md` under `## [Unreleased]` with a brief description of your change.
3. Push your branch and open a PR against `main`.
4. Fill in the PR template completely — link the issue, describe the change, and list testing steps.
5. A maintainer will review within a few business days. Please be responsive to feedback.
6. PRs require:
   - All CI checks green (quality gate → build → test).
   - At least one approving review from a maintainer.
   - No unresolved review comments.

Once approved, a maintainer will merge using **squash merge** to keep the history clean.

---

## Release Process

Releases are managed by the maintainers:

1. Update `version` in `pyproject.toml` following [Semantic Versioning](https://semver.org/).
2. Move `## [Unreleased]` entries to a new `## [X.Y.Z] — YYYY-MM-DD` section in `CHANGELOG.md`.
3. Commit: `chore(release): bump version to X.Y.Z`.
4. Tag: `git tag lightagent-agents/vX.Y.Z && git push --tags`.
5. GitHub Actions publishes automatically to PyPI via Trusted Publisher (OIDC) and creates a GitHub Release.

Contributors do not need to manage releases.

---

## Getting Help

- **Discussions:** Open a [GitHub Discussion](https://github.com/your-org/lightagent/discussions) for questions about usage or architecture.
- **Issues:** Open a [GitHub Issue](https://github.com/your-org/lightagent/issues) for bugs or feature requests.
- **Email:** ecrespo@gmail.com for security reports or private matters.

We appreciate every contribution, no matter how small. Thank you for helping make lightagent-agents better!

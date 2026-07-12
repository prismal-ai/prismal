# Prismal Skynet S+ — Implementation Plan (TASKS)

## Metadata

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `READY` (not implemented) |
| **Version** | 1.0 |
| **Date** | 2026-07-11 |
| **Phase** | S+ |
| **Target package version** | `3.12.0` |
| **PLAN** | `specs/skynet-swarm-plus/PLAN.md` |
| **SPEC** | `specs/skynet-swarm-plus/SPEC.md` |
| **Architecture** | `specs/skynet-swarm-plus/ARCHITECTURE.md` |

---

## 1. Implementation Summary

S+ is delivered in six phases (SP1–SP6), each independently testable and landing
behind opt-in flags (`skynet_specialists_enabled`, `skynet_remote_workers_enabled`;
metering activates under the existing `skynet_token_budget` / `budget_enabled`) so
`main` stays green and the Phase-S swarm is byte-for-byte unchanged until the
final wiring. Every component keeps **callable injection** (`plan_fn`,
`worker_fn`, `evaluate_fn`, `reduce_fn`, plus new `role_registry` and `send_fn`),
so all unit tests run with **no LLM backend and no network**. S+ composes shipped
primitives — Phase C `CostMeter`/`BudgetGuard` and Phase I `A2AClient`/
`A2AConnectionManager` — plus the Phase-S subgraph; no new LangGraph capability.

Status legend: `TODO` · `WIP` · `DONE` · `BLOCKED`. **Nothing implemented yet —
this is the ready-to-execute TDD plan.**

## 2. TDD Workflow (applies to every task)

Strict **red → green → refactor**, matching repo conventions (`uv run pytest`,
`asyncio_mode=auto`, `filterwarnings=error`, `--cov=prismal`, `fail_under=80`):
write the named test first, run it, confirm it fails for the *expected* reason,
write the minimal code, then `uv run ruff check --fix . && ruff format . &&
mypy prismal && bandit -r prismal -c pyproject.toml` clean before marking `DONE`.
Measure package coverage via the `coverage` CLI (`coverage run --source=... -m
pytest -o addopts="" ...`) — `pytest --cov` with a dotted `--cov=` triggers a
pydantic/coverage `KeyError` in this repo.

## 3. Prerequisites

- Branch off the current default feature branch.
- Reuse, do **not** fork: `agents/patterns/parallel.py` (dispatcher),
  `budget/{meter,guard,resolve}.py` (Phase C), `a2a/{client,provider}.py` +
  `A2AConnectionManager` (Phase I), `security/{prompt_builder,action_interceptor,
  audit,sanitizer}.py`, `agents/subgraphs/registry.py`, `agents/intent_router.py`,
  the Phase-S `skynet/` nodes and `_helpers.py`.
- Confirm the Phase-S suite (`tests/unit/agents/skynet/**`,
  `tests/unit/agents/subgraphs/skynet/**`, `test_graph_skynet.py`,
  `test_supervisor_skynet.py`) is green on the base branch so any later failure
  is attributable to S+.
- Read `agents/skynet/worker.py` + `supervisor.py` + `subgraphs/skynet/builder.py`
  together — the shared-meter fix (SP3) touches all three.

## 4. Implementation Phases

### PHASE SP1 — Types, role registry, settings, exceptions

| ID | Task | Test(s) first | Est. | Dep | Status |
|---|---|---|---|---|---|
| SP1-01 | `types.py`: add `WorkerResult.usage/role/remote` + `SwarmResult.usage` (defaults; round-trip preserved) (SPEC-SP-TYP-001) | `tests/unit/agents/skynet/test_types_plus.py::test_worker_result_usage_defaults_and_roundtrip` | 0.3 d | — | TODO |
| SP1-02 | `roles.py`: `SpecialistRole` + `DEFAULT_ROLE` + `RoleRegistry.resolve` fallback (SPEC-SP-REG-001) | `tests/unit/agents/skynet/test_roles.py::test_resolve_{known,unknown_falls_back}` | 0.4 d | — | TODO |
| SP1-03 | `RoleRegistry.from_yaml` load + `config/skynet_roles.example.yaml`; malformed → `SkynetRoleError` | `test_roles.py::test_from_yaml_{loads,missing_file_empty,malformed_raises}` | 0.4 d | SP1-02 | TODO |
| SP1-04 | `core/exceptions.py`: `SkynetRoleError(SkynetError)` | `tests/unit/core/test_exceptions.py::test_skynet_role_error_hierarchy` | 0.1 d | — | TODO |
| SP1-05 | `core/config.py`: `skynet_specialists_enabled`, `skynet_roles_path`, `skynet_remote_workers_enabled`, `skynet_remote_allowlist` (SPEC-SP-CFG-001) | `tests/unit/core/test_config.py::test_skynet_plus_settings_defaults` | 0.3 d | SP1-04 | TODO |
| SP1-06 | `_validate_skynet`: remote-enabled-without-a2a → WARNING (not raise) | `test_config.py::test_skynet_remote_without_a2a_warns` | 0.2 d | SP1-05 | TODO |

**Done when:** value objects round-trip with defaults; `resolve` never raises;
`from_yaml` loads/falls-back/raises-on-malformed; settings parse from `PRISMAL_*`;
remote-without-a2a logs a warning.

### PHASE SP2 — Specialist planner + worker (S+1)

| ID | Task | Test(s) first | Est. | Dep | Status |
|---|---|---|---|---|---|
| SP2-01 | `supervisor.plan()` assigns roles when `skynet_specialists_enabled` (default planner prompt lists `known_roles()`; bad tag → `"worker"`) | `tests/unit/agents/skynet/test_supervisor_plus.py::test_plan_assigns_roles_when_enabled` (fake plan_fn) | 0.5 d | SP1 | TODO |
| SP2-02 | Specialists disabled ⇒ every `order.role == "worker"` (Phase-S unchanged) | `test_supervisor_plus.py::test_plan_all_worker_when_disabled` | 0.2 d | SP2-01 | TODO |
| SP2-03 | `worker.execute()` resolves per-role model + persona + capabilities via `RoleRegistry` (SPEC-SP-WRK-001) | `tests/unit/agents/skynet/test_worker_plus.py::test_worker_uses_role_model_persona_and_caps` (fake registry + provider) | 0.6 d | SP1 | TODO |
| SP2-04 | Two distinct roles → two distinct models resolved (fake `ProviderRegistry` spy) | `test_worker_plus.py::test_two_roles_resolve_two_models` | 0.3 d | SP2-03 | TODO |
| SP2-05 | Role `"worker"` path byte-for-byte Phase S (same model, empty persona) | `test_worker_plus.py::test_worker_role_matches_phase_s` | 0.3 d | SP2-03 | TODO |

**Done when:** an injected planner tags ≥2 roles when enabled and only `"worker"`
when disabled; the worker resolves a role's model+persona+tools; the `"worker"`
role reproduces Phase-S behaviour.

### PHASE SP3 — Metered workers + budget enforcement (S+2)

| ID | Task | Test(s) first | Est. | Dep | Status |
|---|---|---|---|---|---|
| SP3-01 | `SwarmWorker(meter=...)` records its response into the injected shared `CostMeter`; populate `WorkerResult.usage` | `test_worker_plus.py::test_worker_records_usage_into_shared_meter` (fake LLM w/ usage_metadata) | 0.5 d | SP2 | TODO |
| SP3-02 | `SkynetSupervisor(meter=...)` accepts an injected meter (else builds its own — Phase S) | `test_supervisor_plus.py::test_supervisor_accepts_injected_meter` | 0.2 d | SP1 | TODO |
| SP3-03 | `reduce_results(meter=...)` meters the default synthesis reducer (concat/first_success unchanged) | `tests/unit/agents/skynet/test_reduce_plus.py::test_default_reducer_records_usage` | 0.3 d | SP1 | TODO |
| SP3-04 | `enforce_token_budget()` now truthful: worker tokens counted → `SkynetBudgetExceeded(used, limit)` when `used>=budget` | `test_supervisor_plus.py::test_budget_counts_worker_tokens` | 0.4 d | SP3-01,02 | TODO |
| SP3-05 | Optional `budget_guard_fn` (Phase C `make_budget_guard_fn`): soft → stop dispatching / degrade; hard → raise | `test_worker_plus.py::test_budget_guard_soft_degrades_hard_raises` (fake guard) | 0.5 d | SP3-01 | TODO |
| SP3-06 | `SwarmResult.usage == planner+evaluator+reducer+Σworkers` (single shared meter) | `tests/unit/agents/subgraphs/skynet/test_builder_plus.py::test_swarm_result_usage_is_whole_swarm` (e2e fakes) | 0.4 d | SP3-01..04, SP5-01 | TODO |

**Done when:** a worker records into the shared meter; `enforce_token_budget`
sees worker tokens; `SwarmResult.usage` is the whole-swarm total; a soft cap
degrades and a hard cap raises `SkynetBudgetExceeded`.

### PHASE SP4 — Remote workers over A2A (S+3)

| ID | Task | Test(s) first | Est. | Dep | Status |
|---|---|---|---|---|---|
| SP4-01 | `remote.py::make_remote_send_fn` delegates one order via `A2AConnectionManager.get_client(url).send_task(...)`; concat + sanitize + audit (SPEC-SP-RMT-001) | `tests/unit/agents/skynet/test_remote.py::test_send_fn_delegates_sanitizes_audits` (fake manager/client) | 0.6 d | SP1 | TODO |
| SP4-02 | `worker.execute()` routes a remote-bound role to `send_fn` when `skynet_remote_workers_enabled`; `WorkerResult.remote=True` | `test_worker_plus.py::test_remote_role_delegates_via_send_fn` (spy send_fn) | 0.5 d | SP2-03, SP4-01 | TODO |
| SP4-03 | `send_fn` raising `A2AAgentUnavailable` ⇒ `WorkerResult(success=False, remote=True)`; swarm still reduces | `test_worker_plus.py::test_remote_failure_contained` | 0.3 d | SP4-02 | TODO |
| SP4-04 | Allowlist denial (`A2AConnectionManager` strict deny-all) ⇒ contained failure, audited | `test_remote.py::test_denied_by_allowlist_contained` | 0.3 d | SP4-01 | TODO |
| SP4-05 | Remote disabled (flag off / `a2a_enabled` off) ⇒ remote-bound role degrades to local + `skynet.remote_disabled` warning | `test_worker_plus.py::test_remote_disabled_degrades_local` | 0.3 d | SP4-02 | TODO |

**Done when:** an injected `send_fn` round-trips one order (sanitized + audited);
a remote failure/denial is contained; with the flag off a remote role runs local.

### PHASE SP5 — Builder + supervisor wiring (gated) + snapshot

| ID | Task | Test(s) first | Est. | Dep | Status |
|---|---|---|---|---|---|
| SP5-01 | `build_skynet_subgraph(...)` builds ONE `CostMeter`, threads it into supervisor+worker+reducer; accepts `role_registry`/`send_fn`/`budget_guard_fn` | `test_builder_plus.py::test_builder_shares_single_meter` | 0.5 d | SP2, SP3, SP4 | TODO |
| SP5-02 | End-to-end (fakes): specialist + metered + (faked) remote run reduces + evaluates | `test_builder_plus.py::test_e2e_specialist_metered_remote_with_fakes` | 0.5 d | SP5-01 | TODO |
| SP5-03 | **Snapshot: graph + skynet subgraph byte-for-byte unchanged with all S+ flags off** | `tests/unit/agents/test_graph_snapshot_skynet_plus.py::test_snapshot_unchanged_with_splus_disabled` | 0.4 d | SP5-01 | TODO |
| SP5-04 | AST guard still green: `agents/skynet/**` imports no `prismal.mcp`/`skills` (extend/confirm) | `tests/unit/agents/skynet/test_no_mcp_skills_imports.py::test_skynet_no_mcp_skills` | 0.2 d | SP5-01 | TODO |

**Done when:** the builder shares one meter; an e2e specialist+metered+remote run
works with fakes; the snapshot proves zero drift when off; the AST guard passes.

### PHASE SP6 — Tests, docs, example, packaging

| ID | Task | Est. | Dep | Status |
|---|---|---|---|---|
| SP6-01 | Coverage sweep to ≥80% on the new/changed skynet modules (`coverage` CLI) | 0.4 d | SP1–SP5 | TODO |
| SP6-02 | `docs/skynet.md`: Specialist roles / Metered workers / Remote workers sections + settings table | 0.4 d | SP5 | TODO |
| SP6-03 | `examples/skynet_specialist_swarm.py` — specialist + metered + faked-remote demo (no network) | 0.4 d | SP5 | TODO |
| SP6-04 | `config/skynet_roles.example.yaml` documented | 0.1 d | SP1-03 | TODO |
| SP6-05 | `README.md` + `CHANGELOG.md` (v3.12.0) entries | 0.2 d | SP5 | TODO |
| SP6-06 | Flip `specs/skynet-swarm-plus/*` Status → `IMPLEMENTED`; note S+ in `specs/roadmap.md`; add S++ follow-ups (PLAN §6.3) to the parent's future list | 0.2 d | SP6-05 | TODO |

**Done when:** `uv run pytest -m unit` green; `ruff`/`mypy --strict`/`bandit`
clean; coverage ≥80% on the new package; docs + example shipped; version bumped.

## 5. Risk Register (implementation)

| Risk | Mitigation |
|---|---|
| Double-counting worker tokens vs. planner | Single shared meter injected by the builder; each seam records once (DD-SP-003) |
| Behavior drift when flags off | Role defaults `"worker"`; all paths gated; snapshot SP5-03 |
| Remote prompt injection / exfiltration | L1-sanitize remote output before state; allowlist + strict deny-all; audited (DD-SP-008) |
| Budget cutoff mid-fan-out loses work | Reduce over completed workers + carry unmet as `deferred` (reuse Phase-S deferral) |
| Meter threading misses the reducer | SP3-03 meters the default reducer explicitly; SP3-06 asserts the whole-swarm total |
| Coverage tooling (`pytest --cov` KeyError) | Use the `coverage` CLI with `-o addopts=""` (documented §2) |

## 6. Definition of Done (feature)

- [ ] RF-SP-01…RF-SP-10 implemented, each covered by a test written before its code.
- [ ] A run assigns ≥2 roles → ≥2 models; worker tokens counted into
      `skynet_token_budget`; a (faked) remote worker round-trips and its failure
      is contained.
- [ ] All S+ flags off ⇒ compiled graph + skynet subgraph byte-for-byte unchanged
      (SP5-03), and the Phase-S skynet suite stays green.
- [ ] No `prismal.mcp`/`skills` or provider-SDK import in `agents/skynet/**`.
- [ ] `ruff` + `mypy --strict` + `bandit` clean; coverage ≥80% on the new package.
- [ ] `PLAN`/`SPEC`/`ARCHITECTURE`/`TASKS` flipped to `IMPLEMENTED`;
      `README`/`CHANGELOG`/`specs/roadmap.md` updated.

## 7. Effort Summary

| Phase | Focus | Est. |
|---|---|---|
| SP1 | Types + roles + settings + exceptions | ~1.7 d |
| SP2 | Specialist planner + worker | ~1.9 d |
| SP3 | Metering + budget enforcement | ~2.3 d |
| SP4 | Remote workers (A2A) | ~2.0 d |
| SP5 | Builder wiring + snapshot | ~1.6 d |
| SP6 | Tests + docs + packaging | ~1.7 d |
| **Total** | | **~11.2 d** |

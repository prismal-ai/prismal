# Documented Open Items — Fix Plan (2026-10)

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `DRAFT` — Phase 1 done; Phase 2 awaiting approval |
| **Date** | 2026-10-12 |
| **Source** | Sweep of `docs/`, `specs/` and `CHANGELOG.md` for documented open items, each verified against the code on 2026-10-12 |

## 0. Already resolved (the docs were stale)

- `python-jose` → `PyJWT` (2026-07-11), every GitHub Action pinned to a SHA, and ID8
  (`PermissionManager` grants by DID, v3.10.0).

## 1. Phase 1 — Spec hygiene (DONE, commit `ba6c103`)

- `specs/node-io-typesafety/TASKS.md`: 26 rows TODO → DONE.
- `specs/dependency-security-remediation/TASKS.md`: closure note plus the re-verified items.
- AMR/MEM notebooks marked as external (`prisma-notebooks`), like GW6-07.

## 2. Phase 2 — Code fixes (strict TDD, one branch + PR to `develop` per item)

Every item is opt-in or additive. For each one the rule is: flags off ⇒ the compiled graph is
byte-for-byte unchanged, against the last released minor (see CLAUDE.md, testing notes).
Order is by how much each item unblocks.

### 2.1 Meter-based token capture + committed baseline (TKE1-08, unblocks TKE9-04) — P1

- **Problem.** `eval/trajectory.py::capture_trajectory` sums `usage_metadata` from the messages
  in the `updates` stream. Internal LLM calls (supervisor routing) and rebuilt direct answers
  carry no `usage_metadata` there, so a live run records 0 tokens. That leaves
  `tests/eval/baselines/tokens.json` missing and the CI `token-gate` unable to block.
- **Fix.** The `CostMeter` already sees every call. When `capture_trajectory` finishes a case,
  it reads the run's `Usage` from the per-run meter or `get_run_summary(session_id)`. It prefers
  that source and falls back to the message sum when no meter or summary exists, so behaviour
  stays as it is today. The `Trajectory` gets a `usage_source` field (`meter` | `messages`).
  `build_token_report` stays unchanged.
- **Tests first.**
  - A fake graph emits messages without usage while a seeded meter records N tokens: the
    trajectory reports N with `usage_source="meter"`.
  - With no meter, the result equals the current one.
  - A `token-gate` CLI round-trip.
- **Then.** One live run against local Ollama with `PRISMAL_LLM_PROVIDER` set, then commit
  `tests/eval/baselines/tokens.json` and record the numbers in `docs/token-efficiency.md`
  (removing the "Known limitation" box). Keep `--report-only` until two green runs exist,
  then make the gate blocking.
- **Est.** 1 d of code + the live run (needs local Ollama: user action).

### 2.2 Emit AG-UI `subagent` payloads (PRT follow-up) — P2

- **Problem.** `events/agui.py` translates a `subagent` custom payload into `SUBAGENT_*` events,
  but nothing emits that payload. Live Skynet and MoA runs therefore show no sub-agent events.
- **Fix.** The Skynet `SwarmWorker` and the MoA proposers write
  `{"type": "subagent", "phase": start|end|error, "id", "name"}` through LangGraph's
  `get_stream_writer()`. The write is guarded so that outside a streaming context it is a no-op
  and never raises. It is emitted only when the run has already opted in to AG-UI streaming.
  No new setting is needed: the writer is a no-op unless the stream mode includes `custom`.
- **Tests first.**
  - A worker run under `stream_mode="custom"` yields start/end payloads.
  - Without a writer, nothing breaks.
  - A golden transcript gains `SUBAGENT_STARTED`/`SUBAGENT_FINISHED`; regenerate it with
    `python -m tests.unit.events._transcripts --write`.
  - The Skynet graph snapshot stays unchanged.
- **Est.** 1 d.

### 2.3 Public `CronExecutor` API for scheduling a callable (MEM follow-up) — P2

- **Problem.** `memory/v2/consolidation.py::register_consolidation_job` reaches into
  `executor._scheduler.add_job` because `scheduler/executor.py::CronExecutor` has no public
  method that schedules a Python callable.
- **Fix.** Add `CronExecutor.add_callable(name, func, *, cron, timezone=None, replace=True)`.
  It validates the cron expression the same way `add` does, uses the `DateTimeService` timezone
  and returns the job id. Then refactor `register_consolidation_job` to use it. The existing
  `_cron_trigger` seam stays, because the test gotcha patches module seams and never
  `apscheduler` itself.
- **Tests first.**
  - Unit tests on `add_callable` (valid/invalid cron, replace semantics, timezone).
  - The consolidation tests pass unchanged.
  - An AST/grep guard: no `._scheduler` access outside `scheduler/`.
- **Est.** 0.5 d. Update the `docs/memory.md` and `docs/scheduler.md` open items.

### 2.4 Agent adoption of `memory_block_for_prompt` (MEM follow-up) — P3

- **Problem.** The library seam `memory/v2/render.py::memory_block_for_prompt` exists, but no
  agent node puts recalled memory into its prompt.
- **Fix.** Wire it first into one pilot specialist. The researcher is the best candidate, since
  recall is most useful there. Behind `memory_v2_enabled` and `memory_mode == "enforce"`, the
  node recalls via `RuntimeContext.memory`, renders the block, and places it in the **tail**
  `<turn_context>` (never the cached prefix, per the MEM invariant).
- **Tests first.**
  - The memory graph snapshot stays unchanged with the flag off.
  - A pilot node test with `InMemoryBackend` checks that the block sits in the tail and the
    cache-stable prefix is byte-identical.
  - Red-team: recalled text that contains `</memory>` or a canary cannot escape the fence.
- **Est.** 1 d. Needs a design decision first: which node or nodes adopt it.

### 2.5 Shared cross-tenant budget quota counters — P3, spec first

- `docs/budget.md` marks this as a follow-up. It needs a durable store and a concurrency design,
  so write a short DELTA spec (`specs/cost-budget-governance/DELTA-…`) before any code.
- **Est.** spec 0.5 d, then to be decided.

### 2.6 NTS.1 — migrate the remaining specialist nodes to I/O models — P3, spec first

- Migrate node by node, owner-driven (`specs/node-io-typesafety/ARCHITECTURE.md` §489).
  Needs a DELTA spec that picks the batch (e.g. `coder`, `researcher`, `critic`).

## 3. Phase 3 — Data / ops (not code; owner action)

- DEC3-07: ≥ 300 labelled Spanish cases per decision. DEC3-09: per-question latency profile.
- DEC6-07: re-run the injection corpus against the real Ollama models.
- DEC0-08: reproduce the Laya benchmarks on our hardware.
- Refresh `config/opencode_models.yaml`, which is illustrative today.

## 4. Deferred by design (no action)

TKE10 (code mode for MCP), the BRP+ LLM judge, MCP server-side upgrades, the SQLite A2A task
store, and `NodeIOValidatorPort` (DD-NTS-004).

## 5. Definition of done per item

- Red → green → refactor commits.
- `ruff`, `ruff format --check`, `mypy prismal`, `bandit`, plus the unit tier, run with
  `PRISMAL_LLM_PROVIDER=""`.
- A flags-off snapshot test where the graph is touched.
- The matching CHANGELOG `[Unreleased]` entry, and the related "Known limitation" / "Open items"
  note removed from `docs/`.

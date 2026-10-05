# Prismal Agentic Memory — Implementation Plan (TASKS)

## Metadata

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `READY` |
| **Version** | 1.0 |
| **Date** | 2026-10-04 |
| **Phase** | MEM |
| **Target package version** | `3.18.0` (SemVer minor — new opt-in functionality, not yet started) |
| **PLAN** | `specs/agentic-memory/PLAN.md` |
| **SPEC** | `specs/agentic-memory/SPEC.md` |
| **Architecture** | `specs/agentic-memory/ARCHITECTURE.md` |

---

## 1. Implementation Summary

Seven phases (MEM0–MEM6). MEM0 verifies every **[verify]** fact and the legacy memory signatures. MEM1 lands the port, types, backend adapters over the unchanged legacy stores, facades, backfill, tenancy and the rule/HITL-only forget path — the safety invariants exist before any gate. MEM2–MEM5 add the write gate (with taint rules), budgeted recall, consolidation and playbooks, each gated and shadow-first. MEM6 adds the optional graph adapter, eval subset, observability, snapshot and docs. TDD; fakes only (`FakeDecisionPort`, `InMemoryBackend`, fake clock).

Status legend: `TODO` · `WIP` · `DONE` · `BLOCKED`.

## 2. Prerequisites

- Shipped: DEC1–DEC3 (`DecisionPort`, cascade, calibration, shadow, audit), TKE2/TKE5 (layout, TOON, token heuristics), R (`build_runtime(org_id)`), H (`TaintRegistry`), IDN (human principals). Optional: AMR5 (`BatchExecutor`).
- Reuse, do not fork: `memory/long_term.py`, `memory/long_term_store.py` (wrapped, never edited), `composition/config_sources.py::collection_for`, `agents/subgraphs/gates.py::hitl_gate`, `PermissionManager`, `AuditLogger`, `scheduler/` `CronExecutor`.

| ID | Task (verify before build) | Estimate | Dependency | SPEC | Status |
|---|---|---|---|---|---|
| MEM0-01 | Verify Jev-Mem (arXiv 2609.23986) figures (+11 % LoCoMo, 6.6× build, −36.7 % latency) and the write/read decision set (no forget decisions) | 0.1 d | — | WRT-001, RCL-001 | TODO |
| MEM0-02 | Verify Mem0 (> 90 % tokens), Zep/Graphiti (DMR 94.8 %), sleep-time (≈ 5×), ACE (+10.6 %) figures | 0.2 d | — | CON-001, PBK-001 | TODO |
| MEM0-03 | Verify Graphiti API, current version, licence and Python support for `[graph-memory]` | 0.2 d | — | GRF-001 | TODO |
| MEM0-04 | Verify exact legacy signatures of `LongTermMemory`, `LongTermMemoryStore`, profile/preferences/history consumers; record in `docs/memory.md` | 0.2 d | — | ADP-001, ADP-002 | TODO |
| MEM0-05 | Verify LoCoMo dataset licence; select a fixed redistributable subset or a generator | 0.1 d | — | EVL-001 | TODO |

## 3. Implementation Phases

### PHASE MEM1 — Port, types, adapters, migration, tenancy, forget

| ID | Task | Estimate | Dependency | SPEC | Status |
|---|---|---|---|---|---|
| MEM1-01 | `core/config.py`: `memory_*` settings + `_validate_memory` | 0.4 d | — | CFG-001 | TODO |
| MEM1-02 | `core/exceptions.py`: `AgenticMemoryError` hierarchy | 0.1 d | — | ERR-001 | TODO |
| MEM1-03 | `memory/v2/types.py` (records, provenance, scope validation, outcomes, selectors) | 0.5 d | MEM1-01 | TYP-001, TYP-002 | TODO |
| MEM1-04 | `memory/v2/ports.py`: `MemoryPort`, `MemoryBackendPort`, `MemoryGraphPort` | 0.3 d | MEM1-03 | PRT-001, PRT-002 | TODO |
| MEM1-05 | `LongTermMemoryBackend` (`mv2_*` metadata, legacy-record mapping) | 0.6 d | MEM1-04, MEM0-04 | ADP-001 | TODO |
| MEM1-06 | `LangGraphStoreBackend` (tenant namespaces) | 0.6 d | MEM1-04, MEM0-04 | ADP-001, TEN-001 | TODO |
| MEM1-07 | `InMemoryBackend` (deterministic fake) | 0.2 d | MEM1-04 | ADP-001 | TODO |
| MEM1-08 | `MemoryService` + per-tenant composition (`RuntimeContext.memory`), scope filters, violation drop + audit | 0.6 d | MEM1-05..07 | TEN-001, PRT-001 | TODO |
| MEM1-09 | `ForgetPolicy`: request / execute, actor matrix, approval resolution, `include_derived`, audit; AST guard "only ForgetPolicy calls purge" | 0.7 d | MEM1-08 | DEL-001 | TODO |
| MEM1-10 | `LegacyLongTermFacade`, `LegacyFactStoreFacade` (destructive ⇒ pending `ForgetRequest`) | 0.5 d | MEM1-09 | ADP-002 | TODO |
| MEM1-11 | `migrate_legacy_records` (dry run, idempotent, annotate only) | 0.5 d | MEM1-05, MEM1-06 | ADP-002 | TODO |

**Done when:** v2 write via adapter is visible to unchanged legacy recall; legacy records map to `SEMANTIC/legacy/unknown`; empty `org_id` raises; cross-tenant record is dropped and audited; agent/decision/LLM actors get `MemoryForgetDenied`; backfill twice ⇒ second run annotates 0.

### PHASE MEM2 — Decision-gated writes + taint

| ID | Task | Estimate | Dependency | SPEC | Status |
|---|---|---|---|---|---|
| MEM2-01 | Pre-filters: PII sanitizer reuse, length bounds, exact-hash dedupe ⇒ `merged` | 0.3 d | MEM1 | WRT-001 | TODO |
| MEM2-02 | Worth (Noul) + type (Choice) in one `decide()`; `GatePolicy` acceptance; `SecurePromptBuilder` question text | 0.7 d | MEM2-01, DEC1 | WRT-001 | TODO |
| MEM2-03 | Neighbors + batched relation Choices; edges; `UPDATES` supersession via `may_supersede` | 0.6 d | MEM2-02 | WRT-001, TNT-001 | TODO |
| MEM2-04 | `memory/v2/taint.py`: T1–T7 (`candidate_taint`, `allowed_types`, `may_supersede`, `may_promote`) | 0.6 d | MEM1-03 | TNT-001 | TODO |
| MEM2-05 | Low-confidence policy (`skip`/`llm`), `memory_write_gate` modes `off`/`llm` | 0.4 d | MEM2-02 | WRT-001 | TODO |
| MEM2-06 | Shadow mode (`status="shadow"`, storage as gate `off`), hash-first audit | 0.4 d | MEM2-05 | WRT-001, SNP-001 | TODO |

**Done when:** each gate mode produces the specified status; a tainted candidate labelled `SEMANTIC` by the fake decision is stored `EPISODIC`; a tainted `UPDATES` never supersedes an untainted record; shadow storage equals gate `off`.

### PHASE MEM3 — Budgeted recall + sufficiency/stop

| ID | Task | Estimate | Dependency | SPEC | Status |
|---|---|---|---|---|---|
| MEM3-01 | View routing Choice (top ≤ 2, p ≥ 0.2; abstain ⇒ default views) | 0.5 d | MEM1, DEC1 | RCL-001 | TODO |
| MEM3-02 | Budgeted multi-hop retrieval + `traversal_score`; superseded/`as_of` handling | 0.8 d | MEM3-01 | RCL-001 | TODO |
| MEM3-03 | Sufficiency Noul + all five stop reasons | 0.5 d | MEM3-02 | RCL-001 | TODO |
| MEM3-04 | `render_items` (TOON / compact lines, `[untrusted]`), hard cap never exceeded | 0.5 d | MEM3-02, TKE5 | RCL-002, TNT-001 | TODO |
| MEM3-05 | `memory_block_for_prompt` into the tail turn-context block; agent adoption behind flag | 0.4 d | MEM3-04 | RCL-002 | TODO |

**Done when:** recall never exceeds `budget_tokens` (property test); each stop reason reachable with fakes; tainted items rendered marked and absent from the prefix.

### PHASE MEM4 — Sleep-time consolidation

| ID | Task | Estimate | Dependency | SPEC | Status |
|---|---|---|---|---|---|
| MEM4-01 | `register_consolidation_job` on `CronExecutor` (per org, `memory_consolidation_cron`) | 0.4 d | MEM1 | CON-001 | TODO |
| MEM4-02 | Duplicate merge (hash / sim ≥ 0.95 + `SUPPORTS`) via supersession | 0.5 d | MEM4-01 | CON-001 | TODO |
| MEM4-03 | Contradiction resolution: newest-wins with provenance precedence | 0.5 d | MEM4-02, MEM2-04 | CON-001, TNT-001 | TODO |
| MEM4-04 | Episodic → semantic summarization (T3 gate; `BatchExecutor` or small tier) | 0.7 d | MEM4-02, AMR5 (opt.) | CON-001 | TODO |
| MEM4-05 | Retention rules → `ForgetPolicy.execute(actor=rule)` | 0.3 d | MEM1-09 | DEL-001 | TODO |
| MEM4-06 | Watermark + item cap; idempotency (second run all zeros) | 0.3 d | MEM4-02 | CON-001 | TODO |

**Done when:** consolidation never calls `purge`; batch path used when available and metered; second run reports zeros.

### PHASE MEM5 — Playbooks (procedural memory)

| ID | Task | Estimate | Dependency | SPEC | Status |
|---|---|---|---|---|---|
| MEM5-01 | `Playbook`/`PlaybookDelta` types, deterministic `curate`, version history + rollback | 0.6 d | MEM1 | PBK-001 | TODO |
| MEM5-02 | `generate` (trajectory reflections) + `reflect` (deltas) via small tier / `SecurePromptBuilder` | 0.9 d | MEM5-01 | PBK-001 | TODO |
| MEM5-03 | `render_playbook` within budget; version pinning; prefix vs. tail placement by stability | 0.5 d | MEM5-01, TKE2 | PBK-001 | TODO |
| MEM5-04 | Delta discipline (> 50 % rewrite rejected, ≤ max deltas) + T7 source exclusion | 0.3 d | MEM5-02, MEM2-04 | PBK-001, TNT-001 | TODO |

**Done when:** a rewrite-style curator output is rejected; playbook bytes in the prefix are identical across turns of a run; tainted sources never cited.

### PHASE MEM6 — Graph (optional), eval, observability, docs

| ID | Task | Estimate | Dependency | SPEC | Status |
|---|---|---|---|---|---|
| MEM6-01 | `SqliteMemoryGraph` (temporal edges, `as_of`, tenant scope) | 0.8 d | MEM1-04 | GRF-001, TEN-001 | TODO |
| MEM6-02 | `GraphitiMemoryGraph` (`[graph-memory]`, deferred import) | 0.6 d | MEM6-01, MEM0-03 | GRF-001 | TODO |
| MEM6-03 | `monitoring/otel.py`: 7 instruments + 3 spans | 0.3 d | MEM1 | OTEL-001 | TODO |
| MEM6-04 | LoCoMo-style eval subset + legacy vs. v2 report in the eval harness | 0.8 d | MEM3, MEM0-05 | EVL-001 | TODO |
| MEM6-05 | Snapshot: flags off ⇒ graph identical, no job registered, legacy call recording identical | 0.4 d | MEM5 | SNP-001, ADP-002 | TODO |
| MEM6-06 | Integration + security tests (injection → deletion attempt, two-tenant isolation, erasure completeness) | 0.6 d | MEM4, MEM5 | DEL-001, TEN-001, TNT-001 | TODO |
| MEM6-07 | `docs/memory.md` (incl. migration guide), `examples/agentic_memory.py`, notebook | 0.6 d | MEM6-06 | ADP-002 | TODO |
| MEM6-08 | README/CHANGELOG (planned); AST guards (no graph SDK outside adapters, no provider imports) | 0.3 d | MEM6-07 | PRT-002 | TODO |

**Done when:** `uv run pytest -m unit` green; ruff / mypy --strict / bandit clean; coverage ≥ 80 % on `memory/v2/`; eval report meets PLAN §4 targets in shadow comparison.

## 4. Test Inventory

| Test module | Covers |
|---|---|
| `tests/unit/core/test_config_memory.py` · `tests/unit/memory/v2/test_types_ports.py` | MEM1-01..04 |
| `tests/unit/memory/v2/test_adapters.py` · `test_facades_migration.py` | MEM1-05..07 · MEM1-10, MEM1-11 |
| `tests/unit/memory/v2/test_tenancy.py` · `test_forget_policy.py` | MEM1-08 · MEM1-09, MEM4-05 |
| `tests/unit/memory/v2/test_write_gate.py` · `test_taint_rules.py` | MEM2-01..03, MEM2-05, MEM2-06 · MEM2-04 |
| `tests/unit/memory/v2/test_recall.py` · `test_render.py` | MEM3-01..03 · MEM3-04, MEM3-05 |
| `tests/unit/memory/v2/test_consolidation.py` · `test_playbooks.py` | MEM4-01..06 · MEM5-01..04 |
| `tests/unit/memory/v2/test_graph_sqlite.py` · `test_graph_graphiti.py` (skip w/o extra) | MEM6-01 · MEM6-02 |
| `tests/unit/agents/test_graph_snapshot_memory.py` | MEM6-05 |
| `tests/integration/memory/test_memory_v2_end_to_end.py` · `tests/security/test_memory_security.py` | MEM6-06 |
| `tests/eval/test_memory_locomo_subset.py` (`slow`) | MEM6-04 |
| `tests/unit/memory/v2/test_import_guards.py` | MEM1-09 (purge guard), MEM6-08 |

## 5. Risk Register

| Risk | Mitigation |
|---|---|
| Legacy signatures differ from assumptions | MEM0-04 blocks MEM1-05/06/10 |
| Gate thresholds miscalibrated for Spanish traffic | DEC calibration per language before `enforce`; shadow agreement report |
| Erasure misses a derived artefact | `include_derived` + all-backend search test (MEM6-06) |
| Consolidation contention with live writes | Watermark + supersession only (no deletes); per-org job serialization |
| Graphiti API churn | Optional extra, adapter isolated, SQLite graph as default |

## 6. Definition of Done

- [ ] All MUST requirements implemented and traced (§8); GRF-001 (SHOULD) implemented or deferred with a note.
- [ ] MEM0 verification closed; **[verify]** markers resolved or retained with dated notes.
- [ ] Legacy APIs unchanged; flags off ⇒ snapshot identical; facades and backfill documented.
- [ ] Taint T1–T7, tenancy and forget actor matrix proven by tests; no non-rule/non-human deletion path exists.
- [ ] Eval subset meets PLAN §4 targets; docs, example and notebook published.

## 7. Effort Summary

| Phase | Focus | Est. |
|---|---|---|
| MEM0 | Verification | 0.8 d |
| MEM1 | Port, types, adapters, migration, tenancy, forget | 5.0 d |
| MEM2 | Decision-gated writes + taint | 3.0 d |
| MEM3 | Budgeted recall + sufficiency/stop | 2.7 d |
| MEM4 | Consolidation | 2.7 d |
| MEM5 | Playbooks | 2.3 d |
| MEM6 | Graph, eval, observability, docs | 4.4 d |
| **Total** | | **20.9 d** |

## 8. Traceability Matrix

| SPEC id | Tasks | Tests |
|---|---|---|
| SPEC-MEM-TYP-001 / TYP-002 | MEM1-03 | `test_types_ports.py` |
| SPEC-MEM-PRT-001 / PRT-002 | MEM1-04, MEM1-08, MEM6-08 | `test_types_ports.py`, `test_import_guards.py` |
| SPEC-MEM-ADP-001 | MEM0-04, MEM1-05..07 | `test_adapters.py` |
| SPEC-MEM-ADP-002 | MEM1-10, MEM1-11, MEM6-05, MEM6-07 | `test_facades_migration.py`, `test_graph_snapshot_memory.py` |
| SPEC-MEM-TEN-001 | MEM1-06, MEM1-08, MEM6-01, MEM6-06 | `test_tenancy.py`, `test_memory_security.py` |
| SPEC-MEM-TNT-001 | MEM2-03, MEM2-04, MEM3-04, MEM4-03, MEM5-04, MEM6-06 | `test_taint_rules.py`, `test_render.py`, `test_memory_security.py` |
| SPEC-MEM-WRT-001 | MEM2-01..03, MEM2-05, MEM2-06 | `test_write_gate.py` |
| SPEC-MEM-RCL-001 | MEM3-01..03 | `test_recall.py` |
| SPEC-MEM-RCL-002 | MEM3-04, MEM3-05 | `test_render.py` |
| SPEC-MEM-DEL-001 | MEM1-09, MEM4-05, MEM6-06 | `test_forget_policy.py`, `test_memory_security.py`, `test_import_guards.py` |
| SPEC-MEM-CON-001 | MEM4-01..04, MEM4-06 | `test_consolidation.py` |
| SPEC-MEM-PBK-001 | MEM5-01..04 | `test_playbooks.py` |
| SPEC-MEM-GRF-001 (SHOULD) | MEM0-03, MEM6-01, MEM6-02 | `test_graph_sqlite.py`, `test_graph_graphiti.py` |
| SPEC-MEM-CFG-001 / ERR-001 | MEM1-01, MEM1-02 | `test_config_memory.py`, `test_types_ports.py` |
| SPEC-MEM-OTEL-001 | MEM6-03 | `test_memory_v2_end_to_end.py` (counter assertions) |
| SPEC-MEM-SNP-001 | MEM2-06, MEM6-05 | `test_graph_snapshot_memory.py` |
| SPEC-MEM-EVL-001 | MEM0-05, MEM6-04 | `test_memory_locomo_subset.py` |

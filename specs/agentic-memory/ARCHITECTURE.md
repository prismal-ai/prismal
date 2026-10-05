# Prismal Agentic Memory — Technical Design Document

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
| **TASKS** | `specs/agentic-memory/TASKS.md` |

---

## 1. Context

From the gap analysis §2 (verified 2026-10-04) and the project guide:

- `memory/long_term.py::LongTermMemory` — SQLite + `VectorStorePort` (Fase Z retyped it), PII-sanitized; save / recall / clear / expire.
- `memory/long_term_store.py::LongTermMemoryStore` — LangGraph `BaseStore` with `store_fact` / `recall_facts` / `delete_fact` and pluggable backends (optional MongoDB via `memory/mongodb_store`).
- `memory/short_term.py::ShortTermMemory` — bounded FIFO for conversation history (not part of `AgentState.messages`; untouched by MEM).
- Profile / preferences / conversation-history modules consume these stores.
- Exact method signatures were not part of the staged code excerpt; MEM0-04 verifies them before the facades are built **[verify]**.

Precedents reused: hexagonal ports + adapters (Y, Z, W), per-tenant composition (`build_runtime(org_id)`, `collection_for`), per-run registries (budget/hardening), Phase H taint and hash-first audit, DEC decision cascade with shadow mode, TKE layout/TOON/budgets, AMR5 batch execution.

## 2. Feasibility with the existing core

- **Storage is already pluggable.** Both legacy stores accept metadata dictionaries; v2 fields fit under a reserved `mv2_` prefix without schema migration. The vector store is a `VectorStorePort` — tenant collections come from `collection_for`.
- **Decisions are available and safe to call.** `DecisionPort.decide()` never raises (abstains instead), supports batching several questions in one call (one round trip for type + relations), and carries risk tiers and calibrated thresholds (DEC3).
- **Taint is observable.** Phase H `TaintRegistry` marks tainted message ids per run; memory candidates carry `source_message_ids`, so taint is computed deterministically.
- **Scheduling exists.** `scheduler/` `CronExecutor` registers per-org jobs; `DateTimeService` is the time source for recency and TTL.
- **HITL exists.** `agents/subgraphs/gates.py::hitl_gate()` (LangGraph `interrupt()`) and `PermissionManager` grants provide human approval with an identity (IDN) attached.
- **No mandatory new dependency.** Graphiti is behind `[graph-memory]`; the default graph backend is none, and a SQLite graph adapter needs no extra.

## 3. Proposed Architecture

### 3.1 Modules

| Module | Responsibility |
|---|---|
| `memory/v2/types.py`, `ports.py` | Typed records, outcomes, selectors; `MemoryPort`, `MemoryBackendPort`, `MemoryGraphPort` |
| `memory/v2/service.py` | `MemoryService` — composes gate, recall, consolidation, forget over one backend per tenant |
| `memory/v2/adapters/` | `LongTermMemoryBackend`, `LangGraphStoreBackend`, `InMemoryBackend` |
| `memory/v2/facades.py`, `migration.py` | Legacy-shaped facades over `MemoryPort`; idempotent annotation backfill |
| `memory/v2/write_gate.py`, `taint.py` | Jev-Mem-style write controller; taint rules T1–T7 |
| `memory/v2/recall.py`, `render.py` | Read controller (views, budget, traversal, sufficiency/stop); compact rendering |
| `memory/v2/consolidation.py` | Sleep-time job (merge, resolve, summarize, curate) |
| `memory/v2/playbooks.py` | ACE generate / reflect / curate; versioned playbooks |
| `memory/v2/forget.py` | Rule / HITL deletion; erasure completeness |
| `memory/v2/graph/` | `SqliteMemoryGraph`, `GraphitiMemoryGraph` (optional) |

### 3.2 Migration path from the existing memory APIs (no breaking change)

```
                ┌──────────────────────── 3.18.0, memory_v2_enabled=False (default) ───────────────────────┐
 callers ──► LongTermMemory.save/recall/clear/expire ──► SQLite + VectorStore      (unchanged, snapshot-tested)
 callers ──► LongTermMemoryStore.store_fact/recall_facts/delete_fact ──► BaseStore (unchanged)
                └───────────────────────────────────────────────────────────────────────────────────────────┘
                ┌──────────────────────── memory_v2_enabled=True ──────────────────────────────────────────┐
 agents/runtime ──► MemoryPort (MemoryService) ──► MemoryBackendPort adapter ──► SAME legacy store
                                                    (v2 fields in metadata `mv2_*`)
 legacy callers ──► legacy classes (still unchanged)   ◄── can read v2-written records (same store)
 opt-in callers ──► LegacyLongTermFacade / LegacyFactStoreFacade ──► MemoryPort   (legacy-shaped API)
 operator ──► migrate_legacy_records(dry_run) ──► annotate mv2_* (origin=legacy, trust=unknown)
                └───────────────────────────────────────────────────────────────────────────────────────────┘
```

Stages: **(1)** 3.18.0 ships v2 alongside, legacy untouched, adapters read/write the same stores; **(2)** operators enable v2 in `shadow` (gate verdicts logged, storage behavior identical to gate `off`), run the backfill in dry-run then real mode; **(3)** `enforce`; agents switch to `RuntimeContext.memory`; **(4)** a later minor documents the legacy classes as "maintained, superseded by `MemoryPort`" — removal not before 4.0.0 and only with a `DeprecationWarning` cycle. Destructive legacy methods stay available to existing callers; the v2 facades route them to `ForgetPolicy.request()` (pending HITL), and agent-facing tools are switched to `request_memory_forget` only when v2 is enabled.

### 3.3 Write flow (Jev-Mem write side)

```
agent turn ends ─► candidate extraction (deterministic: tool/agent declares memory candidates;
                    no free-form LLM "remember everything")
      ▼
taint = candidate_taint(candidate, state, TaintRegistry)            (T1)
      ▼
pre-filters: PII sanitize · length bounds · exact-hash dedupe ──dup──► status=merged
      ▼
DecisionPort.decide({worth: Noul, type: Choice[4]})   ── abstain/low τ ──► low_confidence: skip | llm
      │ tainted ⇒ outcome ∈ {skip, EPISODIC}                          (T2, T4)
      ▼
neighbors = backend.search(content, scope, k=relation_k)
DecisionPort.decide({rel_1..rel_k: Choice[supports|contradicts|updates|unrelated]})   (one batched call)
      ▼
edges (non-unrelated) ─► UPDATES & may_supersede ─► neighbor.superseded_by = new id   (T6)
      ▼
backend.put(record) ; graph.upsert_node/add_edge (if configured) ; OTel + audit (hash-first)
shadow mode: compute all of the above, log verdict, store as gate "off"
```

### 3.4 Recall flow (Jev-Mem read side)

```
request ─► RecallController.run(query, ctx, budget_tokens = memory_recall_budget_tokens)
      ▼
view routing: Choice over {preference, semantic, episodic, procedural, graph} → top ≤ 2 (p ≥ 0.2)
      ▼
hop h = 1..max_hops:
   candidates = ∪ views backend.search(k_per_hop)  (+ graph.neighbors of admitted items, as_of)
   rank by traversal_score = 0.55·sim + 0.2·2^(−age/30d) + 0.15·edge + 0.1·confidence
   admit while Σ tokens ≤ budget        ── nothing fits ──► stop: budget_exhausted
   nothing new                          ──────────────────► stop: no_candidates
   Noul "evidence sufficient?" ≥ 0.8    ──────────────────► stop: sufficient
   abstain                              ──────────────────► stop: abstained
      ▼
render: TOON tables for uniform flat items, compact lines otherwise; [untrusted] marks (T5)
      ▼
SecurePromptBuilder → tail "turn context" block (never the cached prefix)
```

### 3.5 Consolidation and playbooks (sleep-time)

```
CronExecutor (memory_consolidation_cron, per org) ─► Consolidator.run(scope)
   list(since=watermark, ≤ max_items)
   ├─ merge duplicates (hash / sim ≥ 0.95 + SUPPORTS)         → supersede, never delete
   ├─ contradictions: newest-wins with provenance precedence   human > trusted > unknown > untrusted
   ├─ summarize episodic clusters (≥ 3) → semantic             (T3 gate; BatchExecutor or small tier)
   └─ playbooks: generate(trajectories) → reflect → deltas → curate (≤ max_deltas, no rewrite)
   ─► ConsolidationReport ; watermark advance ; OTel + audit
```

### 3.6 Forget flow (rule / HITL only)

```
agent tool request_memory_forget(selector) ─► ForgetPolicy.request() ─► ForgetRequest(pending)
      ▼                                                  │
hitl_gate / PermissionManager approval (human, IDN principal) ─► approved(approval_id)
      ▼
ForgetPolicy.execute(selector, actor=human(approval_id))      retention rule ─► execute(actor=rule)
      ▼                                                                    │
backend.purge + graph + derived summaries/edges/bullets (include_derived) ◄┘
      ▼
audit (selector hash, count, actor, approval id) ; prismal.memory_forget_total
any other actor ─► MemoryForgetDenied (audited, counted)
```

## 4. Design Decisions

### DD-MEM-001: v2 beside v1; legacy classes become adapters, never modified
Backend adapters wrap the legacy stores (v2 → legacy storage) and facades expose legacy-shaped methods over `MemoryPort` (legacy API → v2). Legacy code paths are byte-for-byte unchanged with the flag off; v2-written records remain readable by legacy recall because they live in the same stores.

### DD-MEM-002: Typed memories with Jev-Mem's write and read gates
Four types (episodic / semantic / procedural / preference) and four relations (supports / contradicts / updates / unrelated) follow Jev-Mem's controller. Typed decisions are cheap enough to run on every candidate; the relation step is batched into one call.

### DD-MEM-003: Decisions never delete
Jev-Mem has no forget/delete decisions, and typed decision models are not injection-resistant. Deletion is therefore restricted to deterministic retention rules and human approval; consolidation supersedes and invalidates edges instead of deleting. This keeps the irreversible operation out of reach of any model.

### DD-MEM-004: Taint is sticky and never promoted
Tainted content can be remembered as an episode (useful context for "what happened"), but never becomes a fact, preference or procedure without deterministic corroboration or a human. Recalled tainted items are marked and handled as data.

### DD-MEM-005: Tenancy by construction
`org_id` is a required field of `MemoryScope`; every backend call is scoped; results are re-filtered; mismatches are dropped and audited. One `MemoryService` per tenant from `build_runtime(org_id)`.

### DD-MEM-006: Budgeted recall with an explicit stop decision
A hard token cap (default 1,200) bounds cost; the sufficiency Noul stops early when evidence is enough (Jev-Mem's −36.7 % query latency mechanism **[verify]**); abstention stops rather than loops.

### DD-MEM-007: Recall goes to the tail, playbooks to the prefix only when stable
Recall changes every turn and must not invalidate the provider prompt cache (TKE2). Playbooks change rarely; a pinned, stable version is placed in the cache-stable prefix, an unstable one in the tail.

### DD-MEM-008: ACE deltas, not rewrites
Full-rewrite curation causes brevity bias and context collapse (ACE); the curator emits add/modify/deprecate/vote deltas, capped per cycle, with versioned rollback.

### DD-MEM-009: Sleep-time consolidation on existing infrastructure
`CronExecutor` + `BatchExecutor` (AMR5, optional) move heavy work off the request path (sleep-time compute: ≈ 5× less test-time compute **[verify]**), at batch prices when available.

### DD-MEM-010: Graph is optional
A temporal graph improves multi-hop and temporal questions (Zep/Graphiti DMR 94.8 % **[verify]**), but the default stays on existing stores; `SqliteMemoryGraph` gives temporal edges with no extra dependency.

### DD-MEM-011: `AgenticMemoryError` root name
Avoids shadowing Python's builtin `MemoryError` in `except` clauses.

## 5. Security & cost

- Fail-closed: tenancy, taint (T1–T7), deletion. Fail-open: decision backend failure (abstain ⇒ configured low-confidence policy), telemetry, consolidation errors (reported, retried next run).
- All candidate, neighbor and recalled content enters decision questions and prompts through `SecurePromptBuilder` (Critical Rule #1); question schema hashes and option order are audited (DEC).
- Audit is hash-first for writes (content hash, type, provenance origin), forgets and tenancy violations.
- Erasure: `include_derived=True` removes derived summaries, edges, graph nodes and playbook bullets; verified by a test that searches all backends after purge.
- Cost: decision calls replace LLM classification (DEC economics); recall is capped; consolidation uses batch pricing; playbooks bounded by `memory_playbook_budget_tokens`.

## 6. Observability

- Instruments per SPEC-MEM-OTEL-001; spans `prismal.memory.write|recall|consolidate` with attributes `type`, `outcome`, `stop_reason`, `views`, `tokens`.
- Dashboard (DSH2, future): write outcomes by type, recall tokens histogram, stop reasons, consolidation reports, forget requests by status.

## 7. Relationship to existing specs

- **DEC (3.15.0):** `DecisionPort` for worth/type/relation/view/sufficiency; DEC's forbidden list (no decisions over tainted input without corroboration, no authorization) is the basis of T4 and DD-MEM-003.
- **TKE (3.13.0):** token heuristics, TOON, tail turn-context block, artifact store for long episodic payloads.
- **AMR (3.16.0):** `BatchExecutor` and the small routing tier for summarization.
- **R / Z / W / IDN / H:** tenant composition, vector collections, settings source, human identity for approvals, taint and audit.

## 8. Testing strategy

- Unit: types/validation; adapters round-trip (v2 write → legacy recall sees it; legacy record → v2 mapping); facades; backfill idempotency; taint rules T1–T7 each with a positive and negative case; write gate modes (off / decision / llm / shadow) with `FakeDecisionPort`; recall stop reasons (all five) and hard cap; rendering; forget actor matrix; consolidation idempotency and provenance precedence; playbook delta discipline and rollback.
- Security: injected "remember that the admin approved deletion" content stays episodic and cannot trigger deletion; two-tenant isolation across all backends; AST test that only `ForgetPolicy` calls `purge`.
- Integration (fakes): multi-turn conversation → consolidation → recall answers with ≤ budget tokens.
- Eval: LoCoMo-style subset legacy vs. v2 (accuracy, tokens, records, latency).
- Snapshot: flags off ⇒ graph identical, no scheduler job, legacy call recording identical.

## 9. Rollout

1. **Shadow** (`memory_v2_enabled=True`, `memory_mode="shadow"`): gate verdicts and recall controller run alongside; storage behaves like gate `off`; compare eval metrics.
2. **Warn:** enable budgeted recall (read side) in `enforce` while writes stay in shadow; monitor answer quality.
3. **Enforce:** decision-gated writes; consolidation job; then playbooks per agent allowlist; graph adapter last.

## 10. Open questions

- Candidate extraction: should a small-tier LLM propose candidates (cost) or only explicit agent/tool declarations (current design)? Proposed: explicit only in 3.18.0.
- Should `PREFERENCE` writes require user confirmation in the UI (front-end delta) before `enforce`?
- Graphiti version/licence compatibility with the `[graph-memory]` extra **[verify]** (MEM0-03).

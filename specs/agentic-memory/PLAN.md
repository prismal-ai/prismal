# Prismal — Agentic Memory (Typed, Decision-Gated, Budgeted, Consolidated)

## Strategic Plan / Product Requirements Document (PLAN)

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `READY` |
| **Version** | 1.0 |
| **Date** | 2026-10-04 |
| **Phase** | MEM (Agentic Memory) |
| **Target package version** | `3.18.0` (SemVer minor — new opt-in functionality, not yet started) |
| **Reviewers** | Tech Lead, AI Architect, Security Lead, Data Protection Officer |
| **Priority** | P2 (quality + token cost on long-lived assistants) |
| **Related** | `docs/gap-analysis-gateways-tokens-decisions-2026-10.md` (§2 row "Memory", §3.2 Jev-Mem, §3.4 rows "Agentic memory" / "ACE playbooks"), `specs/typed-decision-models/` (DEC `DecisionPort`), `specs/token-efficiency/` (TKE budgets, TOON, artifacts), `specs/composition-root/` (R per-org runtime), `specs/adaptive-model-routing/` (AMR5 `BatchExecutor`, optional), `specs/runtime-hardening/` (H `TaintRegistry`), `specs/vector-store-port/` (Z) |

---

## 1. Executive Summary

Prismal's memory layer stores whatever it is given and returns whatever is similar. `LongTermMemory` (SQLite + vector store) and `LongTermMemoryStore` (LangGraph `BaseStore`) are **untyped** and **ungated**: there is no decision about *whether* something is worth remembering, *what kind* of memory it is, *how* it relates to what is already known, *how much* should be recalled, or *when* recall has found enough. Nothing consolidates duplicates or contradictions over time, and nothing learns reusable procedures.

The 2025–2026 literature converged on four mechanisms, each with measured gains: typed, gated writes and reads controlled by a System-One decision model (Jev-Mem, arXiv 2609.23986: +11 % on LoCoMo, 6.6× faster memory build, −36.7 % query latency **[verify]**); compact extracted memories instead of full context (Mem0: > 90 % token savings **[verify]**); temporal knowledge graphs (Zep/Graphiti: 94.8 % on DMR **[verify]**); offline "sleep-time" consolidation (≈ 5× less test-time compute **[verify]**); and incremental procedural playbooks (ACE, arXiv 2510.04618: +10.6 % on agent benchmarks, avoiding brevity bias and context collapse via generate/reflect/curate deltas **[verify]**).

**Agentic Memory (Phase MEM)** introduces `prismal/memory/v2/`: a `MemoryPort` with typed records (episodic / semantic / procedural / preference), a decision-gated write pipeline, a budgeted recall controller with sufficiency/stop decisions, sleep-time consolidation on the existing scheduler, ACE-style playbooks as procedural memory, and an optional temporal-graph adapter. The existing APIs keep working unchanged and become **adapters** (backends and facades) — no breaking change. Tenancy is mandatory, taint is never promoted, and **deletion is rule-based or human-approved only**. Everything is opt-in behind `memory_v2_enabled=False`.

---

## 2. Context and Problem (code facts, gap analysis §2)

- **Untyped, ungated writes.** `memory/long_term.py::LongTermMemory` exposes save / recall / clear / expire over SQLite + a `VectorStorePort` collection; `memory/long_term_store.py::LongTermMemoryStore` exposes `store_fact` / `recall_facts` / `delete_fact` over a LangGraph `BaseStore` with pluggable backends (exact signatures **[verify]** in MEM0). Every call stores; there is no type, no relation, no provenance beyond PII sanitization.
- **Recall has no budget and no stop.** Recall returns top-k by similarity; the caller decides how much to inject. There is no token cap, no "enough evidence" decision, and no view routing (preferences vs. facts vs. episodes).
- **No consolidation.** Duplicates and contradictions accumulate; nothing turns repeated episodes into stable facts; `expire` is the only lifecycle operation.
- **No procedural memory.** Agents do not accumulate reusable strategies; prompt evolution is manual.
- **Untrusted content can become "fact".** Tool outputs, web/RAG passages, MCP and A2A responses carry Phase H taint, but the memory layer does not read it — injected text can be stored and later recalled as a trusted fact.
- **Deletion is unconstrained.** `clear` / `delete_fact` can be called by any code path, including agent tools; Jev-Mem explicitly does **not** model forget/delete decisions, and typed decision models are not injection-resistant (gap analysis §3.2).
- **Building blocks now exist.** `DecisionPort` (DEC, 3.15.0) supplies Choice/Score/Noul; TKE (3.13.0) supplies token heuristics, TOON and the cache-stable layout; R (`build_runtime(org_id)`, `collection_for`) supplies per-tenant composition; `scheduler/` `CronExecutor` runs jobs; AMR5 (3.16.0) supplies `BatchExecutor`.

---

## 3. Target Users

- **AI Engineer:** wants assistants that remember the right things, recall only what is needed, and improve their procedures over time.
- **Platform Host (`prismal-server`):** wants per-tenant memory isolation, configuration via `ConfigSourcePort`, and zero change unless enabled.
- **Security Lead / DPO:** wants taint never laundered into trusted memory, every deletion attributable to a rule or a human, and erasure requests satisfiable across all backends.
- **FinOps:** wants memory context bounded in tokens and consolidation run at batch prices.

---

## 4. Goals and Success Metrics

| Goal | Metric | Target |
|---|---|---|
| Bounded memory context | Tokens injected from memory per turn | ≤ `memory_recall_budget_tokens` (1,200) always |
| Token savings | Memory tokens vs. full-history injection on the eval subset | ≥ −80 % |
| Recall quality | LoCoMo-style subset QA accuracy vs. legacy recall | ≥ +5 pts |
| Write efficiency | Stored records vs. ungated writes, same QA accuracy | ≥ −40 % |
| Latency | Recall p95 excluding LLM fallback | ≤ 250 ms (Laya local) / ≤ 900 ms (Jev) |
| Safety | Cross-tenant reads · tainted promotions · non-rule/non-human deletions | 0 · 0 · 0 (tests) |
| Consolidation cost | Batch vs. on-demand cost of nightly consolidation | ≥ −50 % when AMR5 available |
| Back-compat | Legacy API behavior and graph snapshot with flags off | 100 % unchanged |

---

## 5. Scope

### In Scope
- **MEM1** — `memory/v2/` types, `MemoryPort`, `MemoryBackendPort`; backend adapters over `LongTermMemory` / `LongTermMemoryStore` / in-memory fake; legacy facades; read-only backfill; mandatory tenancy; rule/HITL-only `forget`.
- **MEM2** — decision-gated write pipeline (worth-remembering Noul, type Choice, relation Choice vs. top-k neighbors, edges), taint rules, low-confidence policy, shadow mode.
- **MEM3** — recall controller (view routing, budgeted retrieval, traversal scoring, sufficiency Noul + stop), compact rendering (TOON for tabular facts), hard token cap, prompt injection via `SecurePromptBuilder`.
- **MEM4** — sleep-time consolidation job (merge duplicates, resolve contradictions newest-wins + provenance, summarize episodic → semantic), retention rules, `BatchExecutor` when available.
- **MEM5** — ACE playbooks (generate / reflect / curate deltas, never full rewrites), versioned, budgeted, cache-stable placement.
- **MEM6** — optional temporal graph adapter `[graph-memory]` behind `MemoryGraphPort`; LoCoMo-style eval subset; OTel; docs `docs/memory.md`; notebook; example.

### Out of Scope
- Removing or deprecating the legacy memory APIs in 3.18.0 (no `DeprecationWarning` — `filterwarnings=error`).
- Decision-model or LLM-initiated deletion (explicitly forbidden).
- GEPA / DSPy prompt optimization (future).
- A hosted Mem0/Zep service dependency (patterns adopted, not services).
- Multimodal memories (text only in 3.18.0).

---

## 6. Phases Overview

| Phase | Focus | Depends on | Est. |
|---|---|---|---|
| MEM0 | Verify legacy signatures, paper figures, Graphiti API, LoCoMo license | — | 0.8 d |
| MEM1 | Port, types, adapters, migration, tenancy, forget | R, Z, H | 5.0 d |
| MEM2 | Decision-gated writes + taint | MEM1, DEC | 3.0 d |
| MEM3 | Budgeted recall + sufficiency/stop | MEM1, DEC, TKE | 2.7 d |
| MEM4 | Consolidation job | MEM2, AMR5 (optional) | 2.7 d |
| MEM5 | Playbooks (procedural memory) | MEM2, TKE2 | 2.3 d |
| MEM6 | Graph adapter (optional), eval, OTel, docs | MEM1–5 | 4.4 d |

---

## 7. Risks and Mitigations (summary)

| Risk | Mitigation |
|---|---|
| Gate drops important memories | Shadow mode logs gate verdicts while legacy writes continue; recall-quality eval before enforce |
| Prompt injection becomes "fact" | Taint rules T1–T7 (fail-closed); typed decisions never promote tainted content |
| Unattributed or malicious deletion | `forget` accepts only `rule` and `human` actors; audit hash chain; supersede instead of delete |
| Context collapse in playbooks | Delta-only curation, op cap per cycle, versioned snapshots, rollback |
| Cross-tenant leakage | `org_id` mandatory, namespace + collection per tenant, two-tenant tests |
| Consolidation cost spikes | Batch execution, item cap per run, watermark idempotency |
| Legacy behavior drift | Legacy classes untouched; adapters are new types; snapshot + contract tests |

---

## 8. Dependencies

- `memory/long_term.py`, `memory/long_term_store.py`, `memory/short_term.py`, profile / preferences / conversation-history modules (adapted, not modified).
- `decisions/` (`DecisionPort`, `GatePolicy`, cascade) — DEC 3.15.0.
- `utils/toon.py`, token heuristics, cache-stable layout — TKE 3.13.0.
- `composition/runtime.py::build_runtime(org_id)`, `composition/config_sources.py::collection_for` — R.
- `security/` `TaintRegistry`, `SecurePromptBuilder`, `AuditLogger`, `PermissionManager`; `agents/subgraphs/gates.py::hitl_gate`.
- `scheduler/` `CronExecutor`; `routing/batch.py::BatchExecutor` (AMR5, optional).

---

## 9. References

- Jev-Mem — https://arxiv.org/abs/2609.23986
- Mem0 — https://arxiv.org/abs/2504.19413
- Zep / Graphiti temporal knowledge graph — https://arxiv.org/abs/2501.13956
- Sleep-time compute — https://arxiv.org/abs/2504.13171
- ACE (Agentic Context Engineering) — https://arxiv.org/abs/2510.04618

---

## Change History

| Version | Date | Author | Changes |
|---|---|---|---|
| 1.0 | 2026-10-04 | Ernesto Crespo | Initial full SDD from the 2026-10 gap analysis (§2 Memory, §3.2, §3.4) |

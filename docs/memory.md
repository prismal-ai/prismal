# Agentic memory v2 (Phase MEM)

> Since **3.18.0**. Opt-in: `PRISMAL_MEMORY_V2_ENABLED=true` (default `false`).
> With the flag off nothing changes: the supervisor graph is identical
> (snapshot-tested), no scheduler job is registered, and the legacy memory
> classes are byte-for-byte unchanged.
> Spec: `specs/agentic-memory/`.

Memory v2 adds typed, decision-gated, budgeted and tenant-scoped memory on top
of the stores you already have. It follows the Jev-Mem controller design: typed
decisions on the write and read paths, plus a sleep-time consolidation pass
that never deletes. Deletion is reachable only from a retention rule or an
approved human (DD-MEM-003).

```python
from prismal.composition import build_runtime
from prismal.memory.v2 import MemoryCandidate, MemoryContext, MemoryScope, RecallQuery

ctx = await build_runtime(settings, org_id="acme", mode="context")
memory = ctx.memory  # MemoryService for exactly one tenant (None when disabled)

mctx = MemoryContext(scope=MemoryScope(org_id="acme", user_id="u1"), session_id="s1", actor="coder")
await memory.write(MemoryCandidate("The user prefers dark mode", ("msg-1",)), ctx=mctx)
result = await memory.recall(RecallQuery("ui preferences"), ctx=mctx, budget_tokens=1200)
```

A runnable walk-through is in `examples/agentic_memory.py`.

## Concepts

| Piece | Module | What it does |
|---|---|---|
| `MemoryPort` | `memory/v2/ports.py` | `write` / `recall` / `consolidate` / `forget`. `write` and `recall` never raise on decision failures. |
| `MemoryService` | `memory/v2/service.py` | The per-tenant port. It composes the pieces below over one backend (and an optional graph). |
| Backends | `memory/v2/adapters/` | `LongTermMemoryBackend` and `LangGraphStoreBackend` wrap the legacy stores. `InMemoryBackend` is the deterministic fake. |
| Write gate | `write_gate.py`, `taint.py` | Pre-filters (PII, length, exact-hash dedupe ⇒ `merged`), then worth (Noul) + type (Choice) in one decision call, then a batched relation Choice over `memory_relation_k` neighbours (`UPDATES` supersedes only when T6 allows). Taint rules T1–T7 apply throughout. |
| Recall | `recall.py`, `render.py` | View routing (top ≤ 2, p ≥ 0.2), budgeted multi-hop retrieval ranked by `traversal_score`, and a sufficiency Noul. Five stop reasons. The result is rendered as TOON or compact lines, with tainted items marked `[untrusted]`. |
| Consolidation | `consolidation.py` | A cron job per org: merges duplicates, resolves contradictions newest-wins with provenance precedence (human > trusted > unknown > untrusted), summarizes episodic clusters (≥ 3) into semantic records (from the cluster's untainted members only — tainted text never reaches the summarizer), and applies retention rules through `ForgetPolicy`. It supersedes, never deletes. It is idempotent: a second run reports zeros. |
| Forget | `forget.py` | `request` creates a pending request (the only agent-facing path). `approve` is the HITL step. `execute` accepts only `actor=rule` (with selectors issued by `retention_rules()`) or `actor=human` (with an approved, single-use, equal-selector request). `include_derived` also erases summaries, superseded predecessors, edges, graph nodes and playbook bullets. Deletion matches the selector scope exactly (a narrow request never deletes wider shared records); an optional `session_id` matches `provenance.session_id`. A failed derived purge raises `MemoryErasureIncomplete` and leaves the request `approved` (retryable); a retry with the same `record_ids` reaches the graph even after a restart. |
| Playbooks | `playbooks.py` | ACE generate / reflect / curate as deltas only. More than 50 % rewrite, more than `memory_playbook_max_deltas`, or an uncited, tainted, non-trusted or narrower-than-shared source (user/thread-private, or another agent's) is rejected. Versions can be pinned per run. |
| Graph | `graph/sqlite.py`, `graph/graphiti.py` | Optional temporal graph: edges are invalidated (never deleted) and support `as_of` queries. `SqliteMemoryGraph` uses the stdlib. Graphiti needs the `[graph-memory]` extra. |
| DEC bridge | `decision_bridge.py` | `CascadeDecisionPort` lets the runtime's `DecisionCascade` serve the memory controllers (integration `memory`). It re-fences and re-scans every state field, so DEC's fail-closed taint rules still apply. |

### Safety invariants

- **Tenancy (fail-closed).** A `MemoryService` serves exactly one `org_id`. Any call whose scope names another org raises `MemoryTenancyError`, increments `memory_tenancy_violations` and is audited with hashed org ids. Backends also re-filter every read with `MemoryScope.covers`. Derived records keep the exact scope of their sources and are never widened.
- **Taint.** Content from tools, RAG, A2A, or any candidate carrying injection or authority-claim flags is stored only as an `EPISODIC` episode. It never becomes a fact, preference or procedure, and never supersedes untainted memory. Recalled tainted items are rendered `[untrusted]`, only in the tail turn-context block.
- **Untrusted text reaches decisions only as state.** Candidate, neighbour and recalled content goes through `DecisionStateBuilder.add_untrusted` (sanitized, fenced, scanned). Question texts are developer constants. This is how the SPEC's "via SecurePromptBuilder" is implemented for typed decisions. Recall rendering into an LLM prompt (`memory_block_for_prompt`) and the playbook curator use `SecurePromptBuilder`.
- **Deletion.** An AST test (`tests/unit/memory/v2/test_import_guards.py`) allows `.purge(` calls only in `memory/v2/forget.py`. Agents, decision models, LLMs and consolidation get `MemoryForgetDenied`.
- **Audit.** Writes, forgets and tenancy violations are logged hash-first: content and selector hashes, never content.
- **Erasure of superseded records (intended).** With `include_derived`, forget also erases records that were superseded by a contradiction when they derive from the erased source. A superseded record is still personal data derived from that source, so the right to erasure covers it (decision 2026-10-10).
- **Provenance is a host contract.** `origin="human"` ranks highest in provenance precedence (it wins contradictions and may supersede other records). Memory v2 trusts the `origin` it receives. A host must set `origin` from its own authenticated context and **never forward a caller-controlled `origin`** (for example from a request body or an A2A message) into `MemoryCandidate.origin` (`MemoryService.write`, the facades).

## Configuration

| Setting (`PRISMAL_…`) | Default | Notes |
|---|---|---|
| `MEMORY_V2_ENABLED` | `false` | Master flag. `build_runtime(org_id=…)` then exposes `RuntimeContext.memory`. An `org_id` is required (fail-closed). |
| `MEMORY_MODE` | `shadow` | `shadow` computes and logs gate verdicts, but storage behaves like gate `off` (`status="shadow"`). |
| `MEMORY_V2_BACKEND` | `legacy_long_term` | `legacy_long_term` · `langgraph_store` · `memory`. Named `memory_v2_backend` because the legacy `LongTermMemoryStore` already owns `memory_backend`. |
| `MEMORY_V2_DATA_DIR` | `""` | Directory of the per-tenant SQLite files (`memory_<hash(org)>.db` for `legacy_long_term`, `graph_<hash(org)>.db` for the SQLite graph). Empty means `<legacy data dir>/memory_v2`. It is never the shared legacy `memory.db`, whose `expire()` / `clear()` would otherwise delete v2 rows outside `ForgetPolicy`. |
| `MEMORY_WRITE_GATE` | `decision` | `off` · `decision` · `llm`. `decision` in `enforce` mode requires `DECISIONS_ENABLED=true` (checked at load). |
| `MEMORY_WRITE_LOW_CONFIDENCE` | `skip` | `skip` or `llm` (an injected classifier). |
| `MEMORY_RELATION_K` | `5` | Neighbours typed per write. |
| `MEMORY_RECALL_BUDGET_TOKENS` | `1200` | Hard cap; items are never cut mid-item. |
| `MEMORY_RECALL_MAX_HOPS` · `MEMORY_SUFFICIENCY_TAU` | `3` · `0.8` | |
| `MEMORY_CONSOLIDATION_CRON` | `0 3 * * *` | Validated as five crontab fields at load. |
| `MEMORY_CONSOLIDATION_USE_BATCH` · `MEMORY_CONSOLIDATION_MAX_ITEMS` | `true` · `5000` | Summarization goes through `BatchExecutor` (AMR) when one is injected. |
| `MEMORY_RETENTION_DAYS_BY_TYPE` | `{"episodic": 90}` | Rule-based TTL per type (`0` = keep). |
| `MEMORY_SUPERSEDED_RETENTION_DAYS` | `30` | Purge superseded records after N days. |
| `MEMORY_GRAPH_BACKEND` | `none` | `none` · `sqlite` · `graphiti`. Graphiti must be injected: `build_runtime(..., memory_graph=GraphitiMemoryGraph(client))`. |
| `MEMORY_PLAYBOOKS_ENABLED` · `MEMORY_PLAYBOOK_BUDGET_TOKENS` · `MEMORY_PLAYBOOK_MAX_DELTAS` · `MEMORY_PLAYBOOK_MIN_STABLE_RUNS` · `MEMORY_PLAYBOOK_KEEP_VERSIONS` | `false` · `600` · `10` · `5` · `10` | |

### Host wiring

```python
ctx = await build_runtime(
    settings,
    org_id="acme",
    memory_backend=None,      # default: chosen by MEMORY_V2_BACKEND
    memory_graph=None,        # required for MEMORY_GRAPH_BACKEND=graphiti
    memory_hooks=MemoryHooks( # host callables; memory v2 never builds an LLM itself
        llm_classifier=None,  # required for MEMORY_WRITE_GATE / _LOW_CONFIDENCE = llm
        summarizer=None,      # episodic → semantic consolidation (small routing tier)
        batch=None,           # BatchExecutor, used when MEMORY_CONSOLIDATION_USE_BATCH
        curator=None,         # playbook curation step: async (scope) -> deltas applied
    ),
)
# ctx.aclose() closes the memory graph connection.

# Nightly consolidation (one job, every org in turn, max_instances=1):
from prismal.memory.v2 import register_consolidation_job
register_consolidation_job(
    cron_executor, orgs=list_orgs, settings=settings,
    consolidator_for=lambda org: runtimes[org].memory.consolidator,
)

# Forget: agents only file requests; a human approves (HITL / PermissionManager).
req = ctx.memory.request_forget(selector, requested_by="coder")
await ctx.memory.forget_policy.approve(req.request_id, principal="dpo@acme")
await ctx.memory.forget(selector, actor=ForgetActor("human", "dpo@acme", req.request_id))
```

`build_memory_service(settings, org_id=, hooks=MemoryHooks(...))` takes the same hooks.

- Without a `summarizer` (or `batch`), consolidation does not summarize.
- Without a `curator`, it does not curate playbooks. `PlaybookCurator(llm_fn=…)` also needs an injected LLM callable.
- An `llm` write gate or low-confidence policy without an `llm_classifier` fails at composition (`MemoryConfigError` / `RuntimeCompositionError("memory")`) instead of silently skipping every write.

## Migration from the legacy memory APIs (no breaking change)

1. **3.18.0 (flag off):** nothing changes.
2. **Shadow:** set `MEMORY_V2_ENABLED=true` and `MEMORY_MODE=shadow`. v2 writes go to the **same** legacy stores, with v2 fields under a reserved `mv2_` prefix, so the unchanged `LongTermMemory.recall()` / `LongTermMemoryStore.recall_facts()` still see them.
   - Run the backfill: `migrate_legacy_records(backend, scope=…, dry_run=True)`, then with `dry_run=False`. It only annotates (`origin=legacy`, `trust=unknown`, type `SEMANTIC`), never deletes, and a second run annotates 0.
3. **Enforce:** set `MEMORY_MODE=enforce` and `MEMORY_WRITE_GATE=decision` (needs DEC). Agents switch to `RuntimeContext.memory`.
4. **Later minor:** the legacy classes are documented as superseded. They will not be removed before 4.0.0, and only after a `DeprecationWarning` cycle.

The opt-in facades `LegacyLongTermFacade` and `LegacyFactStoreFacade` (`memory/v2/facades.py`) keep the legacy method names over `MemoryPort`. Their documented differences:

- `clear()` / `delete_fact()` return a **pending forget request id** instead of deleting.
- `expire()` is a no-op returning 0, because expiry belongs to the retention rules.
- `ttl_days` is ignored.

### Adapter notes

- `LongTermMemoryBackend` keeps `mv2_*` metadata in a side table, `memory_v2_meta(id, org_id, meta JSON)`, in the same SQLite file. The legacy `memory_entries` columns are unchanged.
- Un-annotated legacy rows have no tenant. They are visible only to the org passed as `legacy_org_id=` and hidden from everyone otherwise (fail-closed) until the backfill assigns them.
- `LangGraphStoreBackend` namespaces are `("prismal", org_id, user_id or "_", type)`. Scans are unbounded by default; an explicit `max_scan` raises `MemoryScanLimitError` instead of returning a truncated (and, for erasure, incomplete) result. `LongTermMemoryBackend` writes v2 rows with a far-future legacy `expires_at`, so the legacy TTL sweep never deletes them (v2 retention is `ForgetPolicy`'s job).
- Both adapters reach into private attributes of the legacy classes (`_db`, `_store`, `_retention`), because the legacy classes expose no other entry point.

## Observability

All instruments are opt-in through the existing OTel manager. Metric calls never raise.

- Counters:
  - `prismal.memory_writes_total{type,outcome}`
  - `prismal.memory_recall_stops_total{reason}`
  - `prismal.memory_consolidations_total{status}`
  - `prismal.memory_forget_total{actor,outcome}`
  - `prismal.memory_taint_blocks_total{rule}`
  - `prismal.memory_tenancy_violations_total`
- Histogram: `prismal.memory_recall_tokens`.
- Spans: `prismal.memory.write`, `.recall`, `.consolidate`.

## Evaluation

`prismal/eval/memory_locomo.py` generates a seeded, MIT-licensed, LoCoMo-style set (single-hop, multi-hop, temporal, open-domain and adversarial questions). The real LoCoMo is CC BY-NC and is never committed. Questions are scored as retrieval QA without an LLM.

`tests/eval/test_memory_locomo_subset.py` (`slow`) runs the real `MemoryService` (gate `off`, no decision backend) against an ungated top-5 legacy recall, on 5 conversations × 200 sessions with seed 7:

| Metric | Legacy (ungated top-5) | v2 | PLAN §4 target |
|---|---|---|---|
| QA accuracy | 0.80 | 1.00 (**+20 pts**) | ≥ +5 pts |
| Memory tokens / turn vs full history | — | 131 vs 1,221 (**−89 %**) | ≤ −80 % |
| Stored records | 1,030 | 55 (**−95 %**) | ≤ −40 % |
| Max memory tokens | 47 | 150 (≤ 1,200) | ≤ budget |
| Recall p95 | — | < 1 ms (fakes) | ≤ 250 ms |

These numbers measure the deterministic layers only: dedupe, typed storage and budgeted multi-hop recall. Decision-gated numbers need a calibrated DEC backend (Laya or Jev) and a real eval set; they are **not measured yet**.

## Verified facts (MEM0, checked 2026-10-06)

- **MEM0-01 Jev-Mem** (arXiv 2609.23986, submitted 2026-09-21): +11 % is *relative* (LoCoMo judge 0.777 vs 0.700). Memory build is 6.6× faster (158 s vs 1,044 s). Average query latency is −36.7 % (0.93 s vs 1.47 s). Mem0 and Zep were not among the baselines.
  - Decision set: write, read, and a periodic **non-destructive consolidation** step (App. B.3). The paper has **no forget/delete decision**, which supports DD-MEM-003.
- **MEM0-02:**
  - Mem0 (2504.19413): "more than 90 % token cost" saved vs full context.
  - Zep/Graphiti (2501.13956): DMR 94.8 % vs 93.4 %.
  - Sleep-time compute (2504.13171): ≈ 5× less test-time compute.
  - ACE (2510.04618): +10.6 % agents, +8.6 % finance.
- **MEM0-03 Graphiti:**
  - `graphiti-core` 0.30.2 (2026-09-08), Apache-2.0, `requires_python >=3.10,<4`. CI tests only 3.10, so 3.13 is allowed but not CI-tested upstream.
  - LLM, embedder and reranker clients default to OpenAI unless injected, so `GraphitiMemoryGraph` requires an injected client.
  - `KuzuDriver` is deprecated: it emits a `DeprecationWarning`, which our `filterwarnings=error` turns into a failure. The supported embedded driver is FalkorDB Lite.
  - There is no `as_of` parameter (point-in-time uses `SearchFilters`), so the adapter applies `as_of` itself.
  - The extra also pulls in `neo4j` and `posthog`. graphiti-core sends anonymous telemetry unless `GRAPHITI_TELEMETRY_ENABLED=false`; set it in the host environment.
- **MEM0-04 legacy signatures:**
  - `LongTermMemory(db_path=None, vector_store=None, retention_days=None)` has async `save(content, session_id) -> str`, `recall(query, session_id=None, k=5) -> list[MemoryEntry]`, `clear(session_id=None) -> int` and `expire() -> int`.
  - `LongTermMemoryStore(backend=None)` (`memory` | `sqlite` | `postgresql`; no `BaseStore` injection) has async `store_fact(user_id, namespace, key, value, ttl_days=None)`, `recall_facts(namespace, query, limit=5) -> list[dict]` and `delete_fact(namespace, key)`. `list_namespaces` is documented but not implemented.
  - The supervisor uses a module-level `LongTermMemoryStore()` singleton.
  - `PreferencesManager`, `ProfileManager`, `ConversationHistory` and `ShortTermMemory` do not use these stores, and v2 does not cover them.
- **MEM0-05 LoCoMo:** CC BY-NC 4.0. It is not redistributed in this MIT repo; a synthetic generator is used instead.

## Deviations from the SPEC

- `memory_backend` is renamed `memory_v2_backend`, because the legacy setting name is taken.
- With **no** decision port, recall searches every type view. The SPEC's default `("preference","semantic")` still applies when the router *abstains*.
- A rule actor is accepted only for selectors issued by `retention_rules()`, and only once.
- Contradictions are resolved from existing `contradicts` / `updates` edges.
- `register_consolidation_job` takes a `consolidator_for(org)` factory and schedules the job through `CronExecutor.add_callable`.
- Playbooks reject `add`/`modify` deltas without trusted citations, unless the curator runs with `allow_uncited=True`. A single edit per cycle is always allowed (the > 50 % rule has a floor of 1).
- Graphiti stores records as episodes in hashed per-org groups. Typed edges and `as_of` queries live in a local SQLite graph inside the adapter, and tainted records are never sent to Graphiti. The record → episode index is persisted in that SQLite file, so erasure still finds episodes after a restart; a removal failure raises `MemoryErasureIncomplete`.

## Open items

- Decision-gated eval numbers with a calibrated backend.
- Agent adoption of `memory_block_for_prompt` (the library seam exists; no agent node is wired yet).
- The notebook (tracked in the separate `prisma-notebooks` repository).

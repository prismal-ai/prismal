# Prismal Agentic Memory — Interface Specification

## Metadata

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `IMPLEMENTED` (2026-10-07, v3.18.0) |
| **Version** | 1.0 |
| **Date** | 2026-10-04 |
| **Phase** | MEM |
| **Target package version** | `3.18.0` (SemVer minor — new opt-in functionality, not yet started) |
| **PLAN** | `specs/agentic-memory/PLAN.md` |
| **Architecture** | `specs/agentic-memory/ARCHITECTURE.md` |
| **TASKS** | `specs/agentic-memory/TASKS.md` |

---

## Conventions

- All modules use `from __future__ import annotations`; frozen dataclasses for value objects; constructors accept `settings: Settings | None = None`.
- `MemoryPort` methods are `async`. Write and recall **must not raise** on decision/telemetry failures (fail-open to the configured fallback). Tenancy, taint and deletion checks are **fail-closed** and raise.
- New code lives in `prismal/memory/v2/`; legacy modules (`memory/long_term.py`, `memory/long_term_store.py`, `memory/short_term.py`, profile / preferences / conversation history) are **not modified**.
- No provider SDK imports outside `prismal/providers/`; graph backends import their SDK lazily inside `memory/v2/graph/` adapters behind the `[graph-memory]` extra (AST guard extended).
- The root exception is `AgenticMemoryError` (the name `MemoryError` shadows a Python builtin).
- Acceptance criteria use EARS. Priority: **MUST** unless marked **SHOULD**.

---

## Module Summary

| Module | Purpose |
|---|---|
| `memory/v2/types.py` | `MemoryType`, `RelationKind`, `Provenance`, `MemoryScope`, `MemoryContext`, `MemoryCandidate`, `MemoryEdge`, `MemoryRecord`, outcomes, selectors |
| `memory/v2/ports.py` | `MemoryPort`, `MemoryBackendPort`, `MemoryGraphPort` |
| `memory/v2/service.py` | `MemoryService` (implements `MemoryPort`), seeding helpers |
| `memory/v2/adapters/{long_term,langgraph_store,in_memory}.py` | Backend adapters over legacy stores + fake |
| `memory/v2/facades.py` | `LegacyLongTermFacade`, `LegacyFactStoreFacade` |
| `memory/v2/migration.py` | `migrate_legacy_records` |
| `memory/v2/write_gate.py`, `memory/v2/taint.py` | Decision-gated write pipeline; taint rules T1–T7 |
| `memory/v2/recall.py`, `memory/v2/render.py` | Recall controller; compact rendering |
| `memory/v2/forget.py` | `ForgetPolicy`, `ForgetRequest` (rule / HITL only) |
| `memory/v2/consolidation.py` | `Consolidator`, scheduler job registration |
| `memory/v2/playbooks.py` | `Playbook`, `PlaybookDelta`, `PlaybookCurator` |
| `memory/v2/graph/{sqlite,graphiti}.py` | Optional temporal graph adapters |
| `core/config.py` / `core/exceptions.py` / `monitoring/otel.py` | settings, `AgenticMemoryError` hierarchy, instruments |

---

## SPEC-MEM-TYP-001: Value objects (`memory/v2/types.py`)

```python
class MemoryType(StrEnum):
    EPISODIC = "episodic"; SEMANTIC = "semantic"; PROCEDURAL = "procedural"; PREFERENCE = "preference"
class RelationKind(StrEnum):
    SUPPORTS = "supports"; CONTRADICTS = "contradicts"; UPDATES = "updates"; UNRELATED = "unrelated"
Origin = Literal["conversation", "tool", "rag", "a2a", "consolidation", "playbook", "legacy", "human"]
Trust = Literal["trusted", "untrusted", "unknown"]
@dataclass(frozen=True)
class MemoryScope:
    org_id: str                          # mandatory, non-empty (SPEC-MEM-TEN-001)
    user_id: str | None = None
    agent: str | None = None
    thread_id: str | None = None
@dataclass(frozen=True)
class Provenance:
    origin: Origin
    source_message_ids: tuple[str, ...]
    session_id: str | None; agent: str | None; created_at: float
    tainted: bool; trust: Trust
    taint_sources: tuple[str, ...] = ()  # e.g. ("tool:web_search", "mcp:files")
@dataclass(frozen=True)
class MemoryContext:
    scope: MemoryScope; session_id: str
    actor: str                           # agent name or principal id
    risk_tier: RiskTier = RiskTier.LOW  # prismal.decisions.types.RiskTier (DEC)
@dataclass(frozen=True)
class MemoryCandidate:
    content: str
    source_message_ids: tuple[str, ...]
    origin: Origin = "conversation"
    suggested_type: MemoryType | None = None
    metadata: Mapping[str, str] = field(default_factory=dict)
@dataclass(frozen=True)
class MemoryEdge:
    src_id: str; dst_id: str
    relation: RelationKind               # never UNRELATED (not materialized)
    confidence: float
    valid_from: float; valid_to: float | None = None   # temporal invalidation, never deletion
@dataclass(frozen=True)
class MemoryRecord:
    id: str                              # ulid
    type: MemoryType
    content: str
    scope: MemoryScope
    provenance: Provenance
    confidence: float; version: int = 1
    expires_at: float | None = None
    superseded_by: str | None = None
    edges: tuple[MemoryEdge, ...] = ()
    legacy_ref: str | None = None        # id in the legacy store, when adapted/migrated
```

**Acceptance:** THE SYSTEM SHALL reject a `MemoryScope` with an empty `org_id` at construction (`MemoryTenancyError`).

## SPEC-MEM-TYP-002: Outcomes and selectors

```python
WriteStatus = Literal["stored", "merged", "skipped", "quarantined", "shadow"]
@dataclass(frozen=True)
class WriteOutcome:
    status: WriteStatus; record_id: str | None; type: MemoryType | None
    edges: tuple[MemoryEdge, ...]
    backend: str                         # decision backend: "rule"|"laya"|"jev"|"llm"|"none"
    reason: str
StopReason = Literal["sufficient", "budget_exhausted", "max_hops", "no_candidates", "abstained"]
@dataclass(frozen=True)
class RecallQuery:
    text: str; types: frozenset[MemoryType] | None = None
    as_of: float | None = None           # temporal view (graph backends)
@dataclass(frozen=True)
class RecalledItem:
    record: MemoryRecord; score: float; tokens: int
@dataclass(frozen=True)
class RecallResult:
    items: tuple[RecalledItem, ...]
    rendered: str                        # compact text/TOON, ≤ budget_tokens
    tokens: int; stop_reason: StopReason; views: tuple[str, ...]; hops: int
@dataclass(frozen=True)
class ConsolidationReport:
    scope: MemoryScope
    merged: int; superseded: int; summarized: int; playbook_deltas: int; quarantined: int
    used_batch: bool
    errors: tuple[str, ...]
@dataclass(frozen=True)
class ForgetSelector:
    record_ids: tuple[str, ...] = ()
    scope: MemoryScope | None = None
    types: frozenset[MemoryType] | None = None
    older_than: float | None = None
    include_derived: bool = True         # summaries, edges, playbook bullets derived from the records
    reason: str = ""
@dataclass(frozen=True)
class ForgetActor:
    kind: Literal["rule", "human"]
    principal: str                       # rule id or human principal (IDN identity)
    approval_id: str | None = None       # mandatory when kind == "human"
```

## SPEC-MEM-PRT-001: `MemoryPort` (`memory/v2/ports.py`)

```python
@runtime_checkable
class MemoryPort(Protocol):
    async def write(self, candidate: MemoryCandidate, *, ctx: MemoryContext) -> WriteOutcome: ...
    async def recall(self, query: RecallQuery, *, ctx: MemoryContext, budget_tokens: int) -> RecallResult: ...
    async def consolidate(self, scope: MemoryScope) -> ConsolidationReport: ...
    async def forget(self, selector: ForgetSelector, *, actor: ForgetActor) -> int: ...
```

**Acceptance:** `write` and `recall` SHALL NOT raise on decision-backend failure or timeout; `forget` SHALL raise `MemoryForgetDenied` for any actor not satisfying SPEC-MEM-DEL-001.

## SPEC-MEM-PRT-002: `MemoryBackendPort` (storage) and `MemoryGraphPort`

```python
@runtime_checkable
class MemoryBackendPort(Protocol):
    name: str
    async def put(self, record: MemoryRecord) -> str: ...
    async def get(self, record_id: str, *, scope: MemoryScope) -> MemoryRecord | None: ...
    async def search(self, text: str, *, scope: MemoryScope, types: frozenset[MemoryType] | None,
                     k: int, include_superseded: bool = False) -> Sequence[tuple[MemoryRecord, float]]: ...
    async def update(self, record: MemoryRecord) -> None: ...
    async def list(self, *, scope: MemoryScope, since: float | None = None, limit: int = 1000) -> Sequence[MemoryRecord]: ...
    async def purge(self, record_ids: Sequence[str], *, scope: MemoryScope, _authorized: ForgetActor) -> int: ...
@runtime_checkable
class MemoryGraphPort(Protocol):
    async def upsert_node(self, record: MemoryRecord) -> None: ...
    async def add_edge(self, edge: MemoryEdge, *, scope: MemoryScope) -> None: ...
    async def invalidate_edge(self, src_id: str, dst_id: str, *, at: float, scope: MemoryScope) -> None: ...
    async def neighbors(self, record_id: str, *, scope: MemoryScope, as_of: float | None,
                        relations: frozenset[RelationKind] | None = None) -> Sequence[MemoryEdge]: ...
```

- `purge` is reachable only from `ForgetPolicy` (SPEC-MEM-DEL-001); an AST test forbids any other caller in `prismal/**`.

## SPEC-MEM-ADP-001: Backend adapters over legacy stores (`memory/v2/adapters/`)

```python
class LongTermMemoryBackend:            # wraps memory/long_term.py::LongTermMemory (+ its VectorStorePort)
    name = "legacy_long_term"
    def __init__(self, ltm: LongTermMemory, *, settings: Settings | None = None) -> None: ...
class LangGraphStoreBackend:            # wraps memory/long_term_store.py::LongTermMemoryStore (BaseStore)
    name = "langgraph_store"
    def __init__(self, store: LongTermMemoryStore, *, settings: Settings | None = None) -> None: ...
class InMemoryBackend:                  # deterministic fake, no I/O
    name = "memory"
```

- v2 fields are stored in the legacy record's metadata under the reserved prefix `mv2_` (`mv2_type`, `mv2_org`, `mv2_prov`, `mv2_edges`, `mv2_superseded_by`, `mv2_version`) so legacy `recall()` / `recall_facts()` keep returning v2-written content unchanged in shape.
- **Acceptance:** WHEN a record is written through `LongTermMemoryBackend`, THE SYSTEM SHALL make it visible to the unchanged legacy `LongTermMemory` recall API for the same tenant; WHEN a legacy record without `mv2_*` metadata is read by v2, THE SYSTEM SHALL map it to `MemoryType.SEMANTIC`, `origin="legacy"`, `trust="unknown"`.

## SPEC-MEM-ADP-002: Legacy facades and migration (`memory/v2/facades.py`, `memory/v2/migration.py`)

```python
class LegacyLongTermFacade:             # same method names/signatures as LongTermMemory (verified in MEM0)
    def __init__(self, port: MemoryPort, *, ctx_factory: Callable[[], MemoryContext]) -> None: ...
class LegacyFactStoreFacade:            # same method names/signatures as LongTermMemoryStore fact API
    def __init__(self, port: MemoryPort, *, ctx_factory: Callable[[], MemoryContext]) -> None: ...
async def migrate_legacy_records(backend: MemoryBackendPort, *, scope: MemoryScope,
                                 dry_run: bool = True, batch_size: int = 500) -> MigrationReport: ...
@dataclass(frozen=True)
class MigrationReport:
    scanned: int; annotated: int; skipped: int; dry_run: bool
```

- Facades are **opt-in** new classes: save/store map to `write()`; recall maps to `recall()`; destructive methods (`clear`, `delete_fact`) map to `ForgetPolicy.request()` and return a pending `ForgetRequest` id instead of deleting (documented difference; legacy classes themselves are untouched).
- `migrate_legacy_records` is idempotent (records already carrying `mv2_version` are skipped), never deletes or rewrites content, and only adds `mv2_*` metadata.
- **Acceptance:** WHEN `memory_v2_enabled` is False, THE SYSTEM SHALL leave every legacy memory call path byte-for-byte unchanged (contract test over recorded store calls).

## SPEC-MEM-TEN-001: Tenancy (fail-closed)

- THE SYSTEM SHALL derive backend namespaces from `MemoryScope`: LangGraph store namespace `("prismal", org_id, user_id or "_", type)`; vector collection `collection_for("memory_v2", org_id)`.
- THE SYSTEM SHALL apply an `org_id` filter on every `search`, `list`, `get`, `neighbors` and `purge`; IF a backend returns a record whose `scope.org_id` differs from the request, THEN THE SYSTEM SHALL drop it, increment `prismal.memory_tenancy_violations_total` and audit the event.
- `consolidate(scope)` and playbook curation SHALL operate within exactly one `org_id`.
- `MemoryService` instances are composed per tenant by `build_runtime(org_id)` and exposed as `RuntimeContext.memory`.

## SPEC-MEM-TNT-001: Taint rules (`memory/v2/taint.py`, fail-closed)

```python
def candidate_taint(candidate: MemoryCandidate, *, state: AgentState | None,
                    registry: TaintRegistry | None) -> tuple[bool, tuple[str, ...]]: ...
def allowed_types(provenance: Provenance) -> frozenset[MemoryType]: ...
def may_supersede(newer: MemoryRecord, older: MemoryRecord) -> bool: ...
def may_promote(records: Sequence[MemoryRecord], target: MemoryType) -> bool: ...
```

- **T1** A candidate is tainted WHEN any source message is tainted in the Phase H `TaintRegistry` OR `origin ∈ {"tool","rag","a2a"}` from an untrusted source.
- **T2** WHEN a candidate is tainted, THE SYSTEM SHALL store it only as `EPISODIC` with `tainted=True`, `trust="untrusted"`, regardless of any decision output.
- **T3** THE SYSTEM SHALL NOT promote tainted or `trust="unknown"` records to `SEMANTIC`/`PROCEDURAL`/`PREFERENCE` unless ≥ 2 independent untainted sources agree (normalized-content match) OR a human approves.
- **T4** Decisions over tainted content SHALL only select between `skip` and `store-as-episodic`.
- **T5** WHEN tainted items are recalled, THE SYSTEM SHALL render them marked `[untrusted]`, pass them through `SecurePromptBuilder` as data and never place them in the cache-stable prefix.
- **T6** A tainted record SHALL NOT supersede or invalidate an untainted record (`UPDATES`/`CONTRADICTS` edges are kept, no supersession).
- **T7** Playbook deltas SHALL NOT cite tainted or `unknown` records as sources.

## SPEC-MEM-WRT-001: Decision-gated write pipeline (`memory/v2/write_gate.py`)

```python
@dataclass(frozen=True)
class WriteGatePolicy:
    tau_worth: float = 0.6; tau_type: float = 0.6; tau_relation: float = 0.7
    relation_k: int = 5
    low_confidence: Literal["skip", "llm"] = "skip"
class WriteGate:
    def __init__(self, *, decisions: DecisionPort | None, backend: MemoryBackendPort,
                 policy: WriteGatePolicy, settings: Settings | None = None,
                 llm_classifier: Callable[[MemoryCandidate], Awaitable[MemoryType | None]] | None = None) -> None: ...
    async def evaluate(self, candidate: MemoryCandidate, *, ctx: MemoryContext,
                       tainted: bool) -> tuple[WriteOutcome, MemoryRecord | None]: ...
```

Pipeline: (1) deterministic pre-filters — existing PII sanitizer, length bounds, exact-hash dedupe (⇒ `merged`); (2) Noul "Is this worth remembering for future interactions?"; (3) Choice over the four `MemoryType` values; (4) top-`relation_k` neighbors from `backend.search` in the same scope; (5) one batched `decide()` with a Choice per neighbor over `RelationKind`; (6) edges for non-`UNRELATED` relations; `UPDATES` supersedes the neighbor when `may_supersede`.

- WHEN `memory_write_gate="off"`, THE SYSTEM SHALL store every candidate (after pre-filters) as `suggested_type or EPISODIC` with no decision call.
- WHEN `memory_write_gate="decision"` and a decision is accepted per `GatePolicy`, THE SYSTEM SHALL apply it; WHEN it abstains or falls below τ, THE SYSTEM SHALL apply `low_confidence` (`skip` ⇒ `status="skipped"`; `llm` ⇒ `llm_classifier`).
- WHEN `memory_write_gate="llm"`, THE SYSTEM SHALL use `llm_classifier` for steps 2–3 and skip relation typing beyond exact-hash dedupe.
- WHEN `memory_mode="shadow"`, THE SYSTEM SHALL compute and log the gate verdict (`status="shadow"`), and store exactly as `memory_write_gate="off"` would.
- IF the candidate is tainted, THEN T2/T4 override any type decision.
- All question texts embed candidate/neighbor content via `SecurePromptBuilder`; question schema hashes are audited (DEC `audit.py`).

## SPEC-MEM-RCL-001: Recall controller (`memory/v2/recall.py`)

```python
@dataclass(frozen=True)
class RecallPolicy:
    views: tuple[str, ...] = ("preference", "semantic", "episodic", "procedural", "graph")
    max_views: int = 2; max_hops: int = 3; k_per_hop: int = 8
    tau_sufficient: float = 0.8; recency_half_life_days: float = 30.0
    weights: tuple[float, float, float, float] = (0.55, 0.2, 0.15, 0.1)   # similarity, recency, edge, confidence
class RecallController:
    def __init__(self, *, decisions: DecisionPort | None, backend: MemoryBackendPort,
                 graph: MemoryGraphPort | None, policy: RecallPolicy, settings: Settings | None = None) -> None: ...
    async def run(self, query: RecallQuery, *, ctx: MemoryContext, budget_tokens: int) -> RecallResult: ...
def traversal_score(sim: float, age_days: float, edge_weight: float, confidence: float,
                    *, policy: RecallPolicy) -> float: ...
```

- **View routing:** Choice over `policy.views` (graph only when a graph backend is configured); take the top `max_views` options whose probability ≥ 0.2; abstain ⇒ default views `("preference","semantic")`.
- **Budgeted retrieval:** per hop retrieve `k_per_hop` per view, rank by `traversal_score = w1·sim + w2·2^(−age/half_life) + w3·edge + w4·confidence`, admit items while cumulative estimated tokens ≤ `budget_tokens`; superseded records excluded unless `as_of` predates the supersession.
- **Sufficiency/stop:** after each hop, Noul "Is this evidence sufficient to answer the request?"; THE SYSTEM SHALL stop with `sufficient` WHEN P ≥ `tau_sufficient`, `budget_exhausted` WHEN no further item fits, `max_hops` at the cap, `no_candidates` when a hop admits nothing new, and `abstained` WHEN the decision abstains (no further hops).
- THE SYSTEM SHALL never return `RecallResult.tokens > budget_tokens` (hard cap; items are never cut mid-item).

## SPEC-MEM-RCL-002: Rendering and injection (`memory/v2/render.py`)

```python
def render_items(items: Sequence[RecalledItem], *, budget_tokens: int, toon: bool = True) -> tuple[str, int]: ...
def memory_block_for_prompt(result: RecallResult, *, builder: SecurePromptBuilder) -> str: ...
```

- Uniform flat items (preferences, key/value facts) render as TOON tables (TKE `utils/toon.py`); others as one compact line per item `[type|date|conf]: content`; tainted items prefixed `[untrusted]`.
- Recalled memory enters prompts only through `SecurePromptBuilder` in the tail "turn context" block (TKE2 layout), never in the cache-stable prefix.

## SPEC-MEM-DEL-001: Forget — rule-based or HITL only (`memory/v2/forget.py`)

```python
@dataclass(frozen=True)
class ForgetRequest:
    request_id: str; selector: ForgetSelector; created_at: float
    requested_by: str                    # agent or principal; informational
    status: Literal["pending", "approved", "rejected", "executed"]
class ForgetPolicy:
    def __init__(self, *, backend: MemoryBackendPort, graph: MemoryGraphPort | None,
                 audit: AuditPort, permissions: PermissionManager | None, settings: Settings | None = None) -> None: ...
    def request(self, selector: ForgetSelector, *, requested_by: str) -> ForgetRequest: ...
    async def execute(self, selector: ForgetSelector, *, actor: ForgetActor) -> int: ...
    def retention_rules(self) -> Sequence[ForgetSelector]: ...
```

- THE SYSTEM SHALL accept `actor.kind="rule"` only for selectors produced by `retention_rules()` (TTL / `memory_retention_days_by_type` / `memory_superseded_retention_days`).
- THE SYSTEM SHALL accept `actor.kind="human"` only with an `approval_id` that resolves to an approved `ForgetRequest` (approved through `hitl_gate` / `PermissionManager`) whose selector equals the executed selector.
- IF any other actor (agent, decision model, LLM, consolidation) attempts deletion, THEN THE SYSTEM SHALL raise `MemoryForgetDenied`, audit the attempt and increment `prismal.memory_forget_total{actor,outcome="denied"}`.
- WHEN `include_derived` is True, THE SYSTEM SHALL also purge summaries, edges, graph nodes and playbook bullets whose provenance cites the purged records (erasure completeness).
- Agent-facing memory tools SHALL expose only `request_memory_forget` (creates a pending request); no tool deletes directly.
- Every execution writes a hash-first audit record (selector hash, count, actor, approval id) — never content.

## SPEC-MEM-CON-001: Sleep-time consolidation (`memory/v2/consolidation.py`)

```python
class Consolidator:
    def __init__(self, *, backend: MemoryBackendPort, decisions: DecisionPort | None,
                 summarizer: Callable[[Sequence[MemoryRecord]], Awaitable[str]] | None = None,
                 batch: BatchExecutor | None = None, settings: Settings | None = None) -> None: ...
    async def run(self, scope: MemoryScope) -> ConsolidationReport: ...
def register_consolidation_job(executor: CronExecutor, *, orgs: Callable[[], Sequence[str]],
                               settings: Settings) -> str: ...
```

- Steps (per org, from the last watermark, ≤ `memory_consolidation_max_items`): merge duplicates (normalized hash or similarity ≥ 0.95 with `SUPPORTS`); resolve contradictions newest-wins **with provenance precedence** human > trusted > unknown > untrusted (T6); summarize clusters of ≥ 3 related episodic records into one semantic record when T3 allows; run playbook curation (MEM5).
- THE SYSTEM SHALL supersede (`superseded_by`, edge `valid_to`) and SHALL NOT delete during consolidation.
- WHERE `memory_consolidation_use_batch` is True and a `BatchExecutor` is available, THE SYSTEM SHALL submit summarization through it; otherwise it SHALL use the small routing tier synchronously.
- THE SYSTEM SHALL be idempotent: re-running with no new records yields a report with all counters 0.

## SPEC-MEM-PBK-001: Playbooks — procedural memory (`memory/v2/playbooks.py`)

```python
@dataclass(frozen=True)
class PlaybookBullet:
    bullet_id: str; section: str; text: str; helpful: int; harmful: int; source_record_ids: tuple[str, ...]
@dataclass(frozen=True)
class Playbook:
    agent: str; org_id: str; version: int; bullets: tuple[PlaybookBullet, ...]; updated_at: float
@dataclass(frozen=True)
class PlaybookDelta:
    op: Literal["add", "modify", "deprecate", "vote"]
    bullet_id: str | None; section: str; text: str | None; rationale: str
    source_record_ids: tuple[str, ...]
class PlaybookCurator:
    async def generate(self, trajectory: Sequence[BaseMessage], *, agent: str) -> Sequence[str]: ...   # reflections
    async def reflect(self, reflections: Sequence[str], playbook: Playbook) -> Sequence[PlaybookDelta]: ...
    def curate(self, playbook: Playbook, deltas: Sequence[PlaybookDelta]) -> Playbook: ...             # deterministic merge
def render_playbook(playbook: Playbook, *, budget_tokens: int) -> str: ...
```

- THE SYSTEM SHALL apply only deltas (≤ `memory_playbook_max_deltas` per cycle); a curator output that rewrites > 50 % of bullets in one cycle SHALL be rejected (`PlaybookDeltaError`, counted).
- THE SYSTEM SHALL pin the playbook version at run start; WHERE the version has been stable for ≥ `memory_playbook_min_stable_runs` runs, the rendered playbook goes into the cache-stable prefix, otherwise into the tail block.
- THE SYSTEM SHALL keep the last `memory_playbook_keep_versions` versions for rollback.

## SPEC-MEM-GRF-001: Optional temporal graph (`memory/v2/graph/`) — SHOULD

- `SqliteMemoryGraph` (no extra) and `GraphitiMemoryGraph` (`[graph-memory]`, deferred import, API **[verify]** — verified 2026-10-06, see `docs/memory.md#verified-facts`) implement `MemoryGraphPort` with `valid_from`/`valid_to` edges and `as_of` queries. Absent extra ⇒ `MemoryBackendUnavailable` at startup only when `memory_graph_backend="graphiti"`.

## SPEC-MEM-CFG-001: Settings (`core/config.py`)

| Setting | Type | Default | Purpose |
|---|---|---|---|
| `memory_v2_enabled` | `bool` | `False` | Master flag |
| `memory_mode` | `Literal["shadow","enforce"]` | `"shadow"` | Shadow logs gate verdicts, stores as gate `off` |
| `memory_backend` | `Literal["legacy_long_term","langgraph_store","memory"]` | `"legacy_long_term"` | Storage adapter |
| `memory_write_gate` | `Literal["off","decision","llm"]` | `"decision"` | Write gate mode |
| `memory_write_low_confidence` | `Literal["skip","llm"]` | `"skip"` | Abstain/low-τ policy |
| `memory_relation_k` | `int` | `5` | Neighbors for relation typing |
| `memory_recall_budget_tokens` | `int` | `1200` | Hard recall cap |
| `memory_recall_max_hops` / `memory_sufficiency_tau` | `int` / `float` | `3` / `0.8` | Recall stop controls |
| `memory_consolidation_cron` | `str` | `"0 3 * * *"` | Scheduler expression (per org) |
| `memory_consolidation_use_batch` / `memory_consolidation_max_items` | `bool` / `int` | `True` / `5000` | Consolidation |
| `memory_retention_days_by_type` | `dict[str, int]` | `{"episodic": 90}` | Rule-based retention (0 = keep) |
| `memory_superseded_retention_days` | `int` | `30` | Purge superseded after |
| `memory_graph_backend` | `Literal["none","sqlite","graphiti"]` | `"none"` | Graph adapter |
| `memory_playbooks_enabled` | `bool` | `False` | Procedural memory |
| `memory_playbook_budget_tokens` / `memory_playbook_max_deltas` | `int` | `600` / `10` | Playbook bounds |
| `memory_playbook_min_stable_runs` / `memory_playbook_keep_versions` | `int` | `5` / `10` | Placement + rollback |

`_validate_memory` rejects: budgets ≤ 0; τ ∉ (0,1); `memory_write_gate="decision"` with `decisions_enabled=False` while `memory_mode="enforce"`; invalid cron; unknown types in retention map.

## SPEC-MEM-ERR-001: Exceptions (`core/exceptions.py`)

```python
class AgenticMemoryError(PrismalError): ...
class MemoryConfigError(AgenticMemoryError): ...          # load time
class MemoryTenancyError(AgenticMemoryError): ...         # empty/mismatched org_id (fail-closed)
class MemoryForgetDenied(AgenticMemoryError): ...         # actor not rule/human-approved
class MemoryBackendUnavailable(AgenticMemoryError): ...   # missing extra / backend down at startup
class PlaybookDeltaError(AgenticMemoryError): ...         # curator output violates delta discipline
```

## SPEC-MEM-OTEL-001: Instruments (`monitoring/otel.py`)

`prismal.memory_writes_total{type,outcome}`, `prismal.memory_recall_tokens` (histogram), `prismal.memory_recall_stops_total{reason}`, `prismal.memory_consolidations_total{status}`, `prismal.memory_forget_total{actor,outcome}`, `prismal.memory_taint_blocks_total{rule}`, `prismal.memory_tenancy_violations_total`. Spans `prismal.memory.write`, `prismal.memory.recall`, `prismal.memory.consolidate`.

## SPEC-MEM-SNP-001: Disabled path unchanged

- WHEN `memory_v2_enabled` and `memory_playbooks_enabled` are False, THE SYSTEM SHALL compile a supervisor graph byte-for-byte identical to 3.17.0, register no scheduler job, and leave all legacy memory calls unchanged.

## SPEC-MEM-EVL-001: Evaluation

- THE SYSTEM SHALL ship a LoCoMo-style eval subset (dataset licence **[verify]** — verified 2026-10-06: LoCoMo is CC BY-NC, never committed; see `docs/memory.md#verified-facts`) runnable through the eval harness, reporting QA accuracy, memory tokens per turn, stored-record count, recall p95 and stop-reason distribution for legacy vs. v2.

## Acceptance Criteria Index

All requirements are **MUST** except SPEC-MEM-GRF-001 (**SHOULD**). Each EARS clause maps to ≥ 1 task and ≥ 1 test in `TASKS.md` §8 (traceability matrix).

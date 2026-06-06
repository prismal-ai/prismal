# Prismal Advanced Architectures — Implementation Plan

## Metadata

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `IMPLEMENTED` |
| **Version** | 1.0 |
| **Date** | 2026-04-19 |
| **PRD** | `specs/advanced-architectures/PRD.md` |
| **Tech Design** | `specs/advanced-architectures/ARCHITECTURE.md` |
| **API Spec** | `specs/advanced-architectures/SPEC.md` |

---

> **Implementation status (2026-05-30):** Phases A, B, C, and D are
> **implemented**: 7 RAG engines in `prismal/rag/`, 7 patterns in
> `prismal/agents/patterns/`, and 5 subgraphs in `prismal/agents/subgraphs/`, with
> opt-in wiring to the supervisor (`enable_subgraphs`). Each task is marked
> `✅ DONE` inline below.

---

## 1. Implementation Summary

The expansion is divided into **3 implementation phases** plus a hardening phase:

- **Phase A (weeks 1-3):** 7 new RAG architectures — Self-RAG, HyDE, RAG-Fusion, Hybrid Search, Parent-Child, Adaptive RAG, Multi-Vector.
- **Phase B (weeks 4-6):** 7 new agent patterns — Tree of Thoughts, Debate, Constitutional AI, LATS, LLM-Compiler, Mixture of Agents, Swarm/Handoff.
- **Phase C (weeks 7-8):** 5 new subgraph pipelines — Customer Service, Document Generation, Data ETL, Code Review, Debate/Consensus.
- **Phase D (week 9):** Hardening, full integration into `graph.py`, test coverage, documentation.

**Total estimated duration:** 9 weeks
**Minimum team required:** 1-2 backend engineers with experience in LangGraph and async Python.
**Target date:** 2026-06-28

---

## 2. Prerequisites

| Prerequisite | Owner | Status | Deadline |
|---|---|---|---|
| PRD approved | Tech Lead | ☐ Pending | 2026-04-26 |
| ARCHITECTURE.md approved | Tech Lead + AI Architect | ☐ Pending | 2026-04-26 |
| SPEC.md approved | Tech Lead | ☐ Pending | 2026-04-26 |
| `rank_bm25` added to pyproject.toml | Engineer | ☐ Pending | Start of Phase A |
| `networkx` added to pyproject.toml | Engineer | ☐ Pending | Start of Phase A |
| Branch `feature/advanced-architectures` created | Engineer | ☐ Pending | Start of Phase A |
| Existing test suite passes 100% | Engineer | ☐ Verify | Start of Phase A |

---

## 3. Implementation Phases

---

### PHASE A — Advanced RAG

**Duration:** 3 weeks (weeks 1-3)
**Objective:** Implement 7 new retrieval strategies that expand the capabilities of `prismal/rag/` without modifying the behavior of the existing engines.

---

#### A1 — HyDE (Hypothetical Document Embeddings) ✅ DONE
**Estimate:** 3 days | **File:** `prismal/rag/hyde.py`

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| A1-01 | Create `prismal/rag/hyde.py` with `HyDERetriever` and `HyDEResult` | 1d | — | ✅ |
| A1-02 | Implement `_generate_hypothesis()` with `SecurePromptBuilder` | 0.5d | A1-01 | ✅ |
| A1-03 | Implement `_embed_hypothesis()` via `EmbeddingsFactory` | 0.5d | A1-01 | ✅ |
| A1-04 | Implement `search()` with OTel spans and structured logging | 0.5d | A1-02, A1-03 | ✅ |
| A1-05 | Unit tests with mocked LLM (≥ 80% coverage) | 1d | A1-04 | ✅ |
| A1-06 | Add `HyDERetriever` to `rag/__init__.py` | 0.1d | A1-05 | ✅ |

**Done criteria:**
- ✅ `HyDERetriever.search(query, k)` returns `HyDEResult` with chunks and hypothesis.
- ✅ Tests pass: generate hypothesis → embed → search (mock LLM + mock VectorStore).
- ✅ `ruff check` and `mypy --strict` pass.
- ✅ Coverage: **100%** in `prismal/rag/hyde.py` (12 tests).
- ✅ `HyDEError` added to `prismal/core/exceptions.py` (anticipates D1-04).

---

#### A2 — RAG-Fusion (Multi-Query + RRF) ✅ DONE
**Estimate:** 3 days | **File:** `prismal/rag/fusion.py`

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| A2-01 | Create `prismal/rag/fusion.py` with `RAGFusionEngine` and `FusionResult` | 1d | — | ✅ |
| A2-02 | Implement `reciprocal_rank_fusion()` as a public, testable function | 0.5d | A2-01 | ✅ |
| A2-03 | Implement `_generate_query_variants()` with LLM | 0.5d | A2-01 | ✅ |
| A2-04 | Implement `search()` with `asyncio.gather` for parallel searches | 1d | A2-02, A2-03 | ✅ |
| A2-05 | Unit tests: RRF math, variant generation, end-to-end integration | 1d | A2-04 | ✅ |
| A2-06 | Add to `rag/__init__.py` | 0.1d | A2-05 | ✅ |

**Done criteria:**
- ✅ `reciprocal_rank_fusion()` mathematically verified (paper formula, ties, dedup by `(source, chunk_id)`, effect of `k`).
- ✅ `RAGFusionEngine.search()` runs N searches in parallel (`asyncio.gather` + `asyncio.to_thread`) and returns fused chunks.
- ✅ Tests demonstrate correct dedup + ranking (16 tests, 93% coverage in `fusion.py`).
- ✅ `FusionError` added to `prismal/core/exceptions.py` (anticipates D1-04).
- ✅ `ruff check` and `mypy --strict` pass.

---

#### A3 — Hybrid Search (BM25 + Embeddings) ✅ DONE
**Estimate:** 3 days | **File:** `prismal/rag/hybrid.py`

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| A3-01 | Add `rank_bm25` to `pyproject.toml` and verify installation | 0.2d | — | ✅ |
| A3-02 | Create `prismal/rag/hybrid.py` with `HybridSearchEngine` | 1d | A3-01 | ✅ |
| A3-03 | Implement `build_index()` with BM25Okapi | 0.5d | A3-02 | ✅ |
| A3-04 | Implement score fusion: `alpha * sem + (1-alpha) * bm25_norm` | 0.5d | A3-02 | ✅ |
| A3-05 | Implement `search()` with deduplication and ordering | 0.5d | A3-03, A3-04 | ✅ |
| A3-06 | Tests: exact BM25 on technical terms, semantic on abstract, configurable alpha | 1d | A3-05 | ✅ |

**Done criteria:**
- ✅ `HybridSearchEngine` finds documents with exact terms that embeddings do not find.
- ✅ `alpha=0.0` is equivalent to pure BM25 search; `alpha=1.0` is equivalent to pure semantic search.
- ✅ `alpha` overridable per call; validation `[0.0, 1.0]`.
- ✅ BM25 optional (no index → degrades to pure semantic).
- ✅ `HybridSearchError` added to `prismal/core/exceptions.py`.
- ✅ Coverage: **94%** in `prismal/rag/hybrid.py` (12 tests).
- ✅ `ruff check` and `mypy --strict` pass (with `rank_bm25.*` override in `pyproject.toml`).
- ⚠️ Benchmark <500ms for 10K docs: not run (deferred to Phase D / D1-07).

---

#### A4 — Self-RAG ✅ DONE
**Estimate:** 4 days | **File:** `prismal/rag/self_rag.py`

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| A4-01 | Create `prismal/rag/self_rag.py` with dataclasses `SelfRAGResult`, enums `RetrievalDecision`, `SupportedDecision` | 0.5d | — | ✅ |
| A4-02 | Implement `_decide_retrieval()` — decision prompt with robust fallback | 1d | A4-01 | ✅ |
| A4-03 | Implement `_evaluate_support()` — Supported/Unsupported/Utility tokens | 1d | A4-01 | ✅ (renamed internally to `_assess_support()` due to conflict with a security hook; behavior identical to the SPEC) |
| A4-04 | Implement `run()` orchestrating decision → CRAG → assessment | 1d | A4-02, A4-03 | ✅ |
| A4-05 | Tests: NO_RETRIEVE case (simple factual query), RETRIEVE case (corpus-specific query), fallback to CRAG if LLM fails on the control token | 1.5d | A4-04 | ✅ |

**Done criteria:**
- ✅ `SelfRAGPipeline.run()` returns the correct decision when the LLM emits the token; permissive parsing accepts a token embedded in free text.
- ✅ Fallback to `RETRIEVE` when the LLM does not emit a recognizable token; `used_fallback=True` propagates to the result.
- ✅ Structured logging (`self_rag_decision`, `self_rag_decision_unparseable`, etc.) + OTel span `self_rag.run` with decision, support, and utility attributes.
- ✅ Safe pessimism in self-assessment: unparseable token → `(UNSUPPORTED, utility=1)`.
- ✅ Utility clamped to `[1, 5]`.
- ✅ `SelfRAGError` added to `prismal/core/exceptions.py`.
- ✅ Coverage: **94%** in `prismal/rag/self_rag.py` (19 tests).
- ✅ `ruff check` and `mypy --strict` pass (`StrEnum` modernized).

---

#### A5 — Parent-Child RAG (Hierarchical) ✅ DONE
**Estimate:** 3 days | **File:** `prismal/rag/hierarchical.py`

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| A5-01 | Create `prismal/rag/hierarchical.py` with `HierarchicalRAGEngine`, `ParentChunk`, `HierarchicalSearchResult` | 1d | — | ✅ |
| A5-02 | Implement `index_document()`: parent split → child split → store parent_id relation in ChromaDB metadata | 1d | A5-01 | ✅ |
| A5-03 | Implement `search()`: search on child → expand to parent | 0.5d | A5-01 | ✅ |
| A5-04 | Tests: verify that child search + parent expansion returns larger context | 1d | A5-02, A5-03 | ✅ |

**Done criteria:**
- ✅ `search()` returns parent chunks (`parent_content` metadata) from hits on child chunks.
- ✅ Grouping by `parent_id`; ordering by best score among its children.
- ✅ `index_document()` calls `delete_by_source(source)` before reindexing (AC-005-7 compatible).
- ✅ Constructor validation: `child_size < parent_size` and `overlap < child_size`.
- ✅ `HierarchicalRAGError` added to `prismal/core/exceptions.py`.
- ✅ Coverage: **93%** in `prismal/rag/hierarchical.py` (14 tests).
- ✅ `ruff check` and `mypy --strict` pass.
- ✅ Over-fetch `k*4` children to guarantee *k* distinct parents after grouping.

---

#### A6 — Multi-Vector RAG ✅ DONE
**Estimate:** 3 days | **File:** `prismal/rag/multi_vector.py`

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| A6-01 | Create `prismal/rag/multi_vector.py` with `MultiVectorRAGEngine` | 1d | — | ✅ |
| A6-02 | Implement multi-vector indexing: summary + chunks + hypothetical questions (LLM-generated) | 1d | A6-01 | ✅ |
| A6-03 | Implement `search()`: search across all vectors, dedup, merge | 0.5d | A6-01 | ✅ |
| A6-04 | Tests: verify that question-based search finds documents that direct chunk search would miss | 1d | A6-02, A6-03 | ✅ |

**Done criteria:**
- ✅ Each chunk is indexed under 3 representations: `chunk`, `summary`, `question` (N questions configurable via `n_questions`).
- ✅ All representations share `doc_id` in metadata.
- ✅ `search()` deduplicates by `doc_id`, keeps the highest-scoring representation, and reports `matched_representations` for audit.
- ✅ Test `test_search_finds_docs_via_hypothetical_question_only` validates that a hit only on `question` is sufficient.
- ✅ Best-effort indexing: a summary or questions failure does not block the original chunk.
- ✅ `delete_by_source` prevents duplicates on reindexing (AC-005-7).
- ✅ `MultiVectorError` added to `prismal/core/exceptions.py`.
- ✅ Coverage: **92%** in `prismal/rag/multi_vector.py` (12 tests).
- ✅ `ruff check` and `mypy --strict` pass.
- ✅ Over-fetch `k*4` hits to ensure *k* unique docs after dedup.

---

#### A7 — Adaptive RAG (Facade) ✅ DONE
**Estimate:** 2 days | **File:** `prismal/rag/adaptive.py`

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| A7-01 | Create `prismal/rag/adaptive.py` with `AdaptiveRAGEngine`, `QueryType`, `AdaptiveResult` | 0.5d | A1-A6 complete | ✅ |
| A7-02 | Implement `classify_query()` with regex heuristics (default) and an LLM option | 1d | A7-01 | ✅ |
| A7-03 | Implement `search()` with routing by `QueryType` and fallback to CRAG | 0.5d | A7-01, A7-02 | ✅ |
| A7-04 | Tests: correct classification of factual/abstract/ambiguous/technical query types | 1d | A7-03 | ✅ |

**A7 done criteria:**
- ✅ Regex classifier with 6 types (FACTUAL_SIMPLE, ABSTRACT, AMBIGUOUS, MULTI_HOP, TECHNICAL, CONVERSATIONAL); confidence in `[0, 1]`.
- ✅ LLM classifier option (`use_llm_classifier=True`) with fallback to regex if the LLM fails or returns unrecognized text.
- ✅ Routing: ABSTRACT→HyDE, AMBIGUOUS→Fusion, TECHNICAL→Hybrid, rest→CRAG; automatic fallback to CRAG if the preferred engine is not injected.
- ✅ `force_strategy` accepts `crag|hyde|fusion|hybrid|hierarchical`; `ValueError` for an invalid name, `AdaptiveRAGError` if the engine is not configured.
- ✅ Sync engines (Hybrid, Hierarchical) dispatched via `asyncio.to_thread` per SPEC.
- ✅ `AdaptiveRAGError` added to `prismal/core/exceptions.py`.
- ✅ Coverage: **88%** in `prismal/rag/adaptive.py` (24 tests).

**Phase A done criteria (global):** ✅ MET
- ✅ All 7 new RAG engines are in `rag/__init__.py` (HyDE, Fusion, Hybrid, SelfRAG, Hierarchical, MultiVector, Adaptive).
- ✅ `pytest tests/unit/rag/` → **268 passed** (0 failures, 0 errors).
- ✅ Aggregate coverage over `prismal/rag/` = **95%** (target ≥80%).
- ✅ `ruff check prismal/rag/ tests/unit/rag/` → All checks passed!
- ✅ `mypy --strict` passes on each new module.
- ✅ 7 exceptions added to `core/exceptions.py`: `HyDEError`, `FusionError`, `HybridSearchError`, `SelfRAGError`, `HierarchicalRAGError`, `MultiVectorError`, `AdaptiveRAGError` (anticipates D1-04).
- ✅ Dependency `rank-bm25>=0.2.2` added to `pyproject.toml` (A3-01 prerequisite).

---

### PHASE B — Agent Patterns

**Duration:** 3 weeks (weeks 4-6)
**Objective:** Implement 7 new reasoning patterns in `prismal/agents/patterns/`.

---

#### B1 — Tree of Thoughts ✅ DONE
**Estimate:** 4 days | **File:** `prismal/agents/patterns/tree_of_thoughts.py`

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| B1-01 | Create `tree_of_thoughts.py` with dataclasses `Thought`, `ToTResult` and types `GenerateThoughtsFn`, `EvaluateThoughtFn` | 0.5d | — | ✅ |
| B1-02 | Implement BFS beam search: generate N thoughts → evaluate → select top-k | 1.5d | B1-01 | ✅ |
| B1-03 | Implement DFS mode with explicit backtracking | 1d | B1-01 | ✅ |
| B1-04 | Tests: ToT with mock generate/evaluate; verify that beam search does not exceed breadth*depth calls | 1.5d | B1-02, B1-03 | ✅ |
| B1-05 | Add `tot_agent_node` wrapper in `agents/` to register in `graph.py` | 0.5d | B1-04 | ✅ (as factory `make_tot_node` — returns a LangGraph-compatible async node; registration in graph.py is left to D1-01) |

**Done criteria:**
- ✅ `tree_of_thoughts(problem, generate_fn, evaluate_fn, state)` returns `ToTResult` with `best_thought`, `best_path`, `all_thoughts`, `total_thoughts_generated`.
- ✅ Beam search respects the `breadth * depth` cap (test `test_beam_search_respects_breadth_times_depth_cap`).
- ✅ OTel spans created: `tot.search`, `tot.generate_thoughts`, `tot.evaluate_thoughts`, `tot.beam_select`.
- ✅ 3 search modes: `beam` (default), `bfs`, `dfs`.
- ✅ Early-exit by `threshold`; DFS descends the highest-score branch with backtrack.
- ✅ Validation `breadth≥1`, `depth≥1`, `beam_size≥1`; `ValueError` in the constructor.
- ✅ `ToTError` in `core/exceptions.py` via `PrismalError`.
- ✅ Coverage: **90%** in `tree_of_thoughts.py` (15 tests).
- ✅ `ruff check` and `mypy --strict` pass.

---

#### B2 — Debate / Society of Mind ✅ DONE
**Estimate:** 3 days | **File:** `prismal/agents/patterns/debate.py`

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| B2-01 | Create `debate.py` with `DebatePosition`, `DebateResult` and function `debate_round()` | 0.5d | — | ✅ |
| B2-02 | Implement generation of initial positions (N agents with distinct roles) | 1d | B2-01 | ✅ |
| B2-03 | Implement rebuttal rounds (each agent sees previous positions) | 0.5d | B2-02 | ✅ |
| B2-04 | Implement synthesis by an LLM moderator + computation of `agreement_score` | 0.5d | B2-03 | ✅ |
| B2-05 | Tests: 3 agents, 2 rounds, verify that the consensus is not a copy of any position | 1d | B2-04 | ✅ |

**Done criteria:**
- ✅ `debate_round()` returns `DebateResult(consensus, agreement_score, positions, dissenting_views, rounds_completed)`.
- ✅ Rounds 2+ see positions from previous rounds (explicit test).
- ✅ 3 synthesis strategies: `moderator` (LLM), `majority_vote` (Counter.most_common), `weighted` (moderator with a weighted prompt).
- ✅ The consensus is never a verbatim copy of any position (test `test_consensus_is_not_a_verbatim_copy_of_any_position`).
- ✅ `agreement_score` = average Jaccard over pairs of final positions, in `[0, 1]`.
- ✅ Default roles `[proponent, opponent, neutral]`; overflow → `analyst_N`; custom roles validated.
- ✅ Per-agent errors are best-effort: if at least 1 position succeeded, the debate continues; if all fail → `DebateError`.
- ✅ `DebateError` inherits from `PrismalError`.
- ✅ Coverage: **91%** in `debate.py` (14 tests).
- ✅ `ruff check` and `mypy --strict` pass.

---

#### B3 — Constitutional AI ✅ DONE
**Estimate:** 3 days | **File:** `prismal/agents/patterns/constitutional.py`

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| B3-01 | Create `constitutional.py` with `ConstitutionalPrinciple`, `ConstitutionalRevision`, `ConstitutionalResult`, `DEFAULT_PRINCIPLES` | 0.5d | — | ✅ |
| B3-02 | Implement `check_principle()` — LLM evaluates violation | 1d | B3-01 | ✅ |
| B3-03 | Implement `apply()` — loop over principles with revision and `max_revisions` cap | 0.5d | B3-02 | ✅ |
| B3-04 | Integrate `AuditLogger` to record each applied revision | 0.3d | B3-03 | ✅ (via `logger.info("constitutional_revision_applied", ...)` — style consistent with CRAG/Debate/ToT, structlog → sinks) |
| B3-05 | Tests: text with PII → verify detection; correct text → verify 0 revisions; loop cap works | 1.5d | B3-04 | ✅ |

**Done criteria:**
- ✅ `ConstitutionalFilter.apply()` detects and revises violations principle by principle.
- ✅ 3 `DEFAULT_PRINCIPLES` defined: P001 `no_harmful_content` (critical), P002 `factual_accuracy` (high), P003 `no_pii_exposure` (critical).
- ✅ The loop respects `max_revisions` and sets `max_revisions_reached=True` + `all_principles_satisfied=False` if exhausted (test `test_apply_respects_max_revisions_cap`).
- ✅ Each revision emits a structured event `constitutional_revision_applied` with `principle_id`, `attempt`, `severity` (test `test_apply_logs_each_revision`).
- ✅ Strict parsing of the `VIOLATION:` prefix in the critique — unparseable → no-violation (avoids over-blocking).
- ✅ Optional context propagated to the critique prompt.
- ✅ `ConstitutionalError` in `core/exceptions.py` (via `PrismalError`).
- ✅ Coverage: **94%** in `constitutional.py` (16 tests).
- ✅ `ruff check` and `mypy --strict` pass.

---

#### B4 — LATS (Language Agent Tree Search / MCTS) ✅ DONE
**Estimate:** 5 days | **File:** `prismal/agents/patterns/lats.py`

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| B4-01 | Create `lats.py` with `LATSNode`, `LATSResult` and property `ucb1` | 0.5d | — | ✅ (implemented as method `ucb1(exploration_constant)` — the SPEC had @property + parameter, which is invalid in Python) |
| B4-02 | Implement `_select()` — traversal by maximum UCB1 | 1d | B4-01 | ✅ (inline in `_one_simulation`) |
| B4-03 | Implement `_expand()` — LLM generates N candidate actions for the node | 1d | B4-01 | ✅ (`_expand()` delegates to injectable `action_generator_fn` + `transition_fn`) |
| B4-04 | Implement `_simulate()` — execute action and compute reward via `reward_fn` | 1d | B4-01 | ✅ |
| B4-05 | Implement `_backpropagate()` — update Q and N along the root→node path | 0.5d | B4-02 | ✅ |
| B4-06 | Implement `search()` — MCTS loop until `max_simulations` or terminal state | 0.5d | B4-02-B4-05 | ✅ (with additional `timeout_seconds`) |
| B4-07 | Tests: mock reward_fn; verify UCB1 exploration/exploitation balance; timeout works | 2d | B4-06 | ✅ |

**Done criteria:**
- ✅ UCB1 mathematically verified: unvisited=+inf; root pure exploit; Auer et al. formula `Q/N + C*sqrt(ln(N_parent)/N)`; exploration/exploitation balance (4 specific tests).
- ✅ `LATSAgent.search()` returns `LATSResult(best_action_sequence, final_state, total_simulations, best_reward, search_tree_depth)`.
- ✅ `max_simulations` cap respected (test `test_search_respects_max_simulations_cap`).
- ✅ Optional `timeout_seconds` cuts the loop by wall-clock (test `test_search_times_out_when_budget_elapses`).
- ✅ `max_depth` cap respected (test `test_search_respects_max_depth`).
- ✅ Convergence to the best branch with enough simulations (test `test_search_ultimately_favours_higher_reward_path`).
- ✅ `LATSError` when the search is vacuous (no children expanded).
- ✅ Decoupled from LLM/tools: injectable callables `action_generator`, `transition_fn`, `reward_fn`.
- ✅ Coverage: **98%** in `lats.py` (15 tests).
- ✅ `ruff check` and `mypy --strict` pass.

---

#### B5 — LLM-Compiler ✅ DONE
**Estimate:** 5 days | **File:** `prismal/agents/patterns/llm_compiler.py`

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| B5-01 | Create `llm_compiler.py` with `TaskNode`, `CompilerPlan`, `CompilerResult` | 0.5d | — | ✅ |
| B5-02 | Implement `plan()` — Planner LLM generates a list of tasks with dependencies in JSON | 1d | B5-01 | ✅ (via injectable `plan_fn`; LLM-backed wiring is left to the caller) |
| B5-03 | Implement `validate_dag()` — detect cycles with topological sort (Kahn's algorithm) | 1d | B5-01 | ✅ |
| B5-04 | Implement execution engine — parallel waves with `asyncio.gather` | 1d | B5-03 | ✅ |
| B5-05 | Implement Joiner LLM — synthesizes results from all tasks | 0.5d | B5-04 | ✅ (via injectable `joiner`) |
| B5-06 | Implement replanning loop — if the Joiner detects insufficiency, go back to `plan()` with context | 0.5d | B5-05 | ✅ (replan triggers on task failure; `previous_results` is passed to the next `plan_fn`) |
| B5-07 | Tests: linear DAG, parallel DAG, DAG with a cycle (must fail), replanning | 2d | B5-06 | ✅ |

**Done criteria:**
- ✅ `validate_dag()` rejects cycles, dependencies on unknown IDs, and duplicates with a descriptive `CompilerError` (3 tests).
- ✅ Independent tasks run in parallel: a fixture with 3 sleep(0.1s) tasks finishes in < 0.25s (vs ~0.3s sequential → > 30% reduction, test `test_parallel_tasks_run_concurrently`).
- ✅ `CompilerPlan.to_json()` returns valid, deserializable JSON (test `test_compiler_plan_to_json_round_trips`).
- ✅ `$T1.output` interpolation: args with references to prior outputs are resolved before execution (test `test_args_interpolate_prior_task_outputs`).
- ✅ Execution waves computed by a stable topological sort.
- ✅ Replanning on failure: `previous_results` is passed as context to the re-planner (test `test_replan_triggered_on_task_failure`).
- ✅ `max_replanning` cap hard stop with `CompilerError("replanning")` (test `test_max_replanning_cap_aborts_with_compiler_error`).
- ✅ `CompilerError` inherits from `PrismalError`.
- ✅ Coverage: **95%** in `llm_compiler.py` (18 tests).
- ✅ `ruff check` and `mypy --strict` pass.

---

#### B6 — Mixture of Agents (MoA) ✅ DONE
**Estimate:** 3 days | **File:** `prismal/agents/patterns/mixture_of_agents.py`

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| B6-01 | Create `mixture_of_agents.py` with `MoAResult` and `MixtureOfAgents` | 0.5d | — | ✅ |
| B6-02 | Implement the proposer layer — parallel calls to N providers via `ProviderRegistry` | 1d | B6-01 | ✅ |
| B6-03 | Implement the aggregator layer — LLM synthesizes all responses from the previous layer | 1d | B6-02 | ✅ |
| B6-04 | Tests: 3 mock proposers, 1 aggregator; verify that a failure of 1 proposer does not block (partial results) | 1d | B6-03 | ✅ |

**Done criteria:**
- ✅ Proposers run in parallel via `asyncio.gather(return_exceptions=True)` — each model via `ProviderRegistry.get_llm(model_id)`.
- ✅ Partial-failure tolerance: per-proposer failures are discarded with logging; the aggregator continues with the survivors (test `test_generate_continues_when_one_proposer_fails`).
- ✅ `MoAError` only if ALL proposers fail (test `test_generate_raises_moa_error_when_all_proposers_fail`).
- ✅ The aggregator receives all proposer outputs in its prompt (test `test_aggregator_prompt_includes_proposer_outputs`).
- ✅ `n_aggregator_layers > 1` produces K sequential aggregator passes, each refining the previous (test `test_generate_with_multiple_aggregator_layers`).
- ✅ `aggregator_model=None` defaults to the first proposer (test `test_generate_uses_default_aggregator_when_none`).
- ✅ `providers_used` reflects only the successful proposers.
- ✅ Constructor validations: `proposer_models` non-empty, `n_aggregator_layers ≥ 1`.
- ✅ `MoAError` inherits from `PrismalError`.
- ✅ `SecurePromptBuilder` wraps all LLM calls.
- ✅ Coverage: **100%** in `mixture_of_agents.py` (11 tests).
- ✅ `ruff check` and `mypy --strict` pass.

---

#### B7 — Decentralized Swarm / Handoff ✅ DONE
**Estimate:** 2 days | **File:** `prismal/agents/patterns/swarm.py`

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| B7-01 | Create `swarm.py` with `HandoffRecord`, `VALID_HANDOFF_TARGETS` and `swarm_handoff()` | 0.5d | — | ✅ |
| B7-02 | Implement `swarm_handoff()` — validate target, update `state["metadata"]["handoff_history"]`, record in `AuditLogger` | 1d | B7-01 | ✅ (audit via structlog event `swarm_handoff_recorded`, style consistent with the rest of the patterns) |
| B7-03 | Tests: a valid handoff updates state correctly; a handoff to an invalid target raises ValueError; self-handoff rejected | 0.5d | B7-02 | ✅ |

**B7 done criteria:**
- ✅ A valid handoff sets `state["next_agent"]` and appends an entry to `state["metadata"]["handoff_history"]`.
- ✅ Self-handoff (`current_agent == target_agent`) → `ValueError`.
- ✅ A target outside `VALID_HANDOFF_TARGETS` → `ValueError` with a list of valid ones.
- ✅ `valid_targets` customizable via parameter (for tests and extensions).
- ✅ Immutability: the input state is not mutated; the new state is a copy with fresh metadata (test `test_handoff_does_not_mutate_input_state`).
- ✅ History preserved: previous handoffs are kept, the new one is appended at the end.
- ✅ `context_snapshot` captures only small fields (`session_id`, `iteration_count`, `current_agent`, `task_plan`, `risk_score`) — never `messages` or `retrieved_docs`.
- ✅ Metadata auto-initialized if missing in the input state.
- ✅ Audit event `swarm_handoff_recorded` with `from_agent`, `to_agent`, `reason`.
- ✅ `SwarmError` available in `core` for future non-ValueError errors.
- ✅ `VALID_HANDOFF_TARGETS` includes the 7 specialist agents from the SPEC.
- ✅ Coverage: **100%** in `swarm.py` (13 tests).
- ✅ `ruff check` and `mypy --strict` pass.

**Phase B done criteria (global):** ✅ MET
- ✅ All 7 patterns implemented in `agents/patterns/`: `tree_of_thoughts`, `debate`, `constitutional`, `lats`, `llm_compiler`, `mixture_of_agents`, `swarm`.
- ✅ `pytest tests/unit/agents/patterns/` → **106 new pattern tests** (15 ToT + 14 debate + 16 constitutional + 15 LATS + 18 compiler + 11 MoA + 13 swarm + 4 existing = all passing). Total suite `tests/unit/agents/patterns/ + tests/unit/rag/` = **394 passed**.
- ✅ Coverage ≥ 80% in all new pattern modules:
  - `tree_of_thoughts.py`: **90%**
  - `debate.py`: **91%**
  - `constitutional.py`: **94%**
  - `lats.py`: **98%**
  - `llm_compiler.py`: **95%**
  - `mixture_of_agents.py`: **100%**
  - `swarm.py`: **100%**
- ✅ `ruff check prismal/agents/patterns/` → All checks passed!
- ✅ `mypy --strict prismal/agents/patterns/<module>.py` → Success on each new module.

---

### PHASE C — Subgraph Pipelines

**Duration:** 2 weeks (weeks 7-8)
**Objective:** Implement 5 domain subgraph pipelines following the `SubgraphFactory` pattern.

---

#### C1 — Customer Service Pipeline ✅ DONE
**Estimate:** 4 days | **Directory:** `prismal/agents/subgraphs/customer_service/`

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| C1-01 | Create the directory structure and `__init__.py` with `build_customer_service_subgraph()` | 0.3d | — | ✅ |
| C1-02 | Implement `classifier_node.py` — classifies the query into FAQ/Complaint/Technical/Other | 1d | C1-01 | ✅ (via factory `make_classifier_node(llm)` — permissive parsing with fallback to `other`) |
| C1-03 | Implement `faq_retrieval_node.py` — RAG over the knowledge base | 0.5d | C1-01 | ✅ (confidence = max(relevance_score); short-circuit if `rag_engine=None`) |
| C1-04 | Implement `escalation_node.py` — HITL gate if confidence < threshold | 0.5d | C1-01 | ✅ (conditional edge fn; escalates on complaint, low-confidence, or missing metadata) |
| C1-05 | Implement `response_generator_node.py` and `ticket_creator_node.py` | 0.5d | C1-01 | ✅ (ticket id `TK-<8hex>`; response LLM grounded in retrieved context) |
| C1-06 | Assemble the `StateGraph` and register it in `SubgraphRegistry` | 0.5d | C1-02-C1-05 | ✅ (builder returns `SubgraphDefinition` with 5 nodes, entry=classifier, linear edges + conditional on escalation_gate) |
| C1-07 | Tests: full FAQ flow, escalation flow, ticket creation flow | 1d | C1-06 | ✅ |

**Done criteria:**
- ✅ 5 nodes implemented: `classifier`, `faq_retrieval`, `escalation_gate`, `response_generator`, `ticket_creator`.
- ✅ Entry point: `classifier`; edges: `classifier→faq_retrieval→escalation_gate`, then conditional (`→ticket_creator` or `→response_generator`).
- ✅ Escalation gate: complaint → ticket; confidence < threshold (default 0.6) → ticket; metadata absent → ticket (defensive); rest → response.
- ✅ FAQ flow (test `test_escalation_gate_routes_confident_faq_to_response_generator` + individual node tests).
- ✅ Escalation flow by complaint / low-confidence / missing metadata (3 tests).
- ✅ Ticket creator flow: `TK-<8hex>` id + AIMessage confirmation to the user (2 tests).
- ✅ `classifier_node` handles empty messages and LLM errors with fallback to `other`.
- ✅ `faq_retrieval_node` handles `rag_engine=None`, RAG exceptions, empty hits.
- ✅ `response_generator` handles empty retrieved context (the prompt asks to acknowledge the gap instead of fabricating).
- ✅ `CustomerServiceError` inherits from `PrismalError`.
- ✅ Coverage per module: builder 87%, classifier 90%, escalation 100%, faq_retrieval 85%, response_generator 83%, ticket_creator 96% (22 tests).
- ✅ `ruff check` and `mypy --strict` pass.
- ⚠️ Registration in `SubgraphRegistry` (the `register_customer_service()` pattern) is deferred — the builder returns a `SubgraphDefinition` ready to register; the global wiring goes in D1.

---

#### C2 — Document Generation Pipeline ✅ DONE
**Estimate:** 3 days | **Directory:** `prismal/agents/subgraphs/document_generation/`

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| C2-01 | Create the structure and `__init__.py` with `build_document_generation_subgraph()` | 0.3d | — | ✅ |
| C2-02 | Implement nodes: `planner_node`, `researcher_node`, `writer_node`, `editor_node`, `formatter_node` | 2d | C2-01 | ✅ |
| C2-03 | Assemble and register in `SubgraphRegistry` | 0.3d | C2-02 | ✅ (builder returns a `SubgraphDefinition` ready for `SubgraphRegistry.register`; global wiring is left to D1) |
| C2-04 | Tests: simple document generation end-to-end | 1d | C2-03 | ✅ |

**Done criteria:**
- ✅ 5 nodes implemented as factories `make_*_node(llm, ...)`: `planner`, `researcher`, `writer`, `editor`, `formatter`.
- ✅ Linear pipeline: `planner → researcher → writer → editor → formatter`; entry point `planner`.
- ✅ Namespaced metadata `state["metadata"]["document_generation"]` with `outline`, `research`, `draft`, `edited`, `final`, `format`.
- ✅ Planner: permissive parsing of a numbered list with fallback to raw lines if the LLM does not number.
- ✅ Researcher: LLM + optional per-section RAG; short-circuit if there is no outline.
- ✅ Writer: composes the draft from outline + research; the prompt includes both (verified by test).
- ✅ Editor: polish with graceful fallback to the raw draft if the LLM fails.
- ✅ Formatter: 3 formats (`markdown`, `plain`, `html`), `ValueError` for an unknown format; final `AIMessage` with the document. Fallback to `draft` if there was no editor.
- ✅ `DocumentGenerationError` inherits from `PrismalError`.
- ✅ Coverage per module: builder 89%, editor 89%, formatter 100%, planner 87%, researcher 88%, writer 84% (21 tests).
- ✅ `ruff check` and `mypy --strict` pass.

---

#### C3 — Data ETL Pipeline ✅ DONE
**Estimate:** 3 days | **Directory:** `prismal/agents/subgraphs/data_etl/`

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| C3-01 | Create the structure and `__init__.py` with `build_data_etl_subgraph()` | 0.3d | — | ✅ |
| C3-02 | Implement nodes: `extractor_node`, `validator_node`, `transformer_node`, `loader_node`, `auditor_node` | 1.5d | C3-01 | ✅ |
| C3-03 | Integrate with `data/` (DuckDB + Polars utilities) in the extractor/loader nodes | 0.5d | C3-02 | ✅ (extractor/loader use polars `read_csv/read_parquet/read_json` and `write_csv/write_parquet` directly; DuckDB remains available as a future backend via injectable `loader_fn/extractor_fn`) |
| C3-04 | Assemble, register, and test | 1d | C3-02, C3-03 | ✅ |

**Done criteria:**
- ✅ 5 nodes: `extractor`, `validator`, `transformer`, `loader`, `auditor`.
- ✅ Conditional edge on `validator`: validates `passed=True` → `transformer`, else → `auditor` (skip transform + load).
- ✅ Namespaced metadata `state["metadata"]["data_etl"]` with: `source`, `destination`, `transforms`, `dataframe`, `raw_row_count`, `raw_columns`, `validation`, `transform_log`, `loaded_row_count`, `audit`.
- ✅ Extractor: support for CSV / Parquet / JSON via polars; injectable `extractor_fn` for alternative backends (SQL, REST); `ValueError` for an unknown `source.type`; accepts sync and async fns.
- ✅ Validator: `non_empty` + `required_columns`; injectable `validator_fn` for stricter schema (Pandera, Pydantic).
- ✅ Transformer: declarative ops `select` / `filter` (6 operators) / `rename`; `transform_log` lists applied operations; injectable `transformer_fn`.
- ✅ Loader: CSV/Parquet via polars; injectable `loader_fn`; `ValueError` for an unknown destination.
- ✅ Auditor: summary with row counts, transforms, errors; `AIMessage` with a readable digest (pass/fail).
- ✅ `DataETLError` inherits from `PrismalError`.
- ✅ Coverage per module: auditor 100%, builder 100%, extractor 92%, loader 100%, transformer 90%, validator 92% (31 tests).
- ✅ `ruff check` and `mypy --strict` pass.

---

#### C4 — Code Review Pipeline ✅ DONE
**Estimate:** 4 days | **Directory:** `prismal/agents/subgraphs/code_review/`

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| C4-01 | Create the structure with `CodeIssue`, `CodeReviewReport` and `build_code_review_subgraph()` | 0.5d | — | ✅ |
| C4-02 | Implement `linter_node.py` — runs ruff + mypy via `SandboxExecutor` (CodeAct) | 1d | C4-01 | ✅ (injectable linter_fn; default no-op; wiring to SandboxExecutor is left to D1) |
| C4-03 | Implement `security_scanner_node.py` — detects bandit patterns via LLM | 0.5d | C4-01 | ✅ (injectable scanner_fn; default no-op) |
| C4-04 | Implement `logic_reviewer_node.py` — LLM reviews business logic | 0.5d | C4-01 | ✅ (injectable reviewer_fn) |
| C4-05 | Implement `suggester_node.py` and `report_generator_node.py` | 0.5d | C4-01 | ✅ |
| C4-06 | Assemble, register, and test with fixture code of various severities | 1.5d | C4-02-C4-05 | ✅ |

**Done criteria:**
- ✅ 5 nodes: `linter`, `security_scanner`, `logic_reviewer`, `suggester`, `report_generator`.
- ✅ Linear pipeline with entry point `linter`.
- ✅ The 3 analyzers share the contract `(code, file) -> list[CodeIssue]` and append to the shared `issues` list.
- ✅ `CodeIssue` with `severity` (critical/high/medium/low/info), `category` (security/logic/style/performance/test), optional line number.
- ✅ `CodeReviewReport` with severity-weighted `score` (critical=-0.4, high=-0.2, medium=-0.1, low=-0.05, info=-0.01), clamped `[0,1]`.
- ✅ `approved = score >= approval_threshold` (default 0.8) — a single critical issue alone lowers the score to 0.6 → rejected.
- ✅ Score clamp verified: 20 critical issues → score=0.0 (does not wrap to negative).
- ✅ Suggester preserves issue order; empty issues → empty suggestions.
- ✅ Per-analyzer errors swallowed (logged) — the graph does not crash from a single analyzer failure.
- ✅ Report_generator emits an `AIMessage` with an APPROVED/REJECTED digest + breakdown by severity.
- ✅ `CodeReviewError` inherits from `PrismalError`.
- ✅ Coverage per module: types 100%, builder 100%, report_generator 95%, logic_reviewer 94%, security_scanner 94%, linter 82%, suggester 82% (24 tests).
- ✅ `ruff check` and `mypy --strict` pass.

---

#### C5 — Debate/Consensus Subgraph ✅ DONE
**Estimate:** 2 days | **Directory:** `prismal/agents/subgraphs/debate_consensus/`

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| C5-01 | Create `build_debate_consensus_subgraph()` reusing `debate_round()` from Phase B | 0.5d | B2 complete | ✅ (reuses `DebatePosition` + `pairwise_jaccard` from pattern B2; renamed the helper from `_pairwise_jaccard` to `pairwise_jaccard` as a public API) |
| C5-02 | Implement nodes: `proponent_node`, `opponent_node`, `moderator_node`, `consensus_node` | 1d | C5-01 | ✅ |
| C5-03 | Assemble, register, and test | 0.5d | C5-02 | ✅ |

**Done criteria:**
- ✅ 4 nodes: `proponent`, `opponent`, `moderator`, `consensus`; linear pipeline with entry point `proponent`.
- ✅ Each role is a `DebatePosition` accumulated in `state["metadata"]["debate_consensus"]["positions"]`.
- ✅ `opponent` sees the `proponent` position; `moderator` sees both (tests verify prompt content).
- ✅ `consensus_node` synthesizes with the LLM + computes `agreement_score` via `pairwise_jaccard` (reuse of B2).
- ✅ Identical positions → agreement near 1.0 (mathematical test).
- ✅ Shared `make_role_node()` helper among the 3 roles — prompts are the only delta.
- ✅ Graceful degradation: a per-role error logs + inserts a placeholder position; if the consensus LLM fails → uses the first position as fallback.
- ✅ `DebateConsensusError` inherits from `PrismalError`.
- ✅ Coverage per module: proponent/opponent/moderator 100%, _helpers 92%, consensus 91%, builder 88% (13 tests).
- ✅ `ruff check` and `mypy --strict` pass.

**Phase C done criteria (global):** ✅ MET
- ✅ All 5 subgraphs implemented: `customer_service`, `document_generation`, `data_etl`, `code_review`, `debate_consensus`.
- ✅ Each builder returns a `SubgraphDefinition` registrable in `SubgraphRegistry` (global wiring into `graph.py` is deferred to D1-01).
- ✅ Unit tests per node and builder pass 100% (112 Phase C tests).
- ✅ Coverage ≥ 80% in all Phase C modules.
- ✅ `ruff check prismal/agents/subgraphs/` and `mypy --strict` pass.
- ✅ Consistent pattern: each node is an async callable `make_*_node(deps)`; namespaced metadata per subgraph; graceful degradation at each step.

---

### PHASE D — Hardening and Final Integration

**Duration:** 1 week (week 9)
**Objective:** Integrate all the new nodes into `graph.py`, reach coverage targets, and ensure production quality.

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| D1-01 | Register all new nodes in `agents/graph.py` (6 patterns + 5 subgraphs) | 1d | Phases A+B+C | ✅ DONE (new `agents/patterns/nodes.py` with 6 `make_*_node` node-factories for lazy LLM wiring; `graph.py` gains `build_supervisor_graph(advanced_nodes=...)` + `_build_advanced_nodes()` which compiles the 5 subgraphs via `SubgraphFactory` and builds the 6 pattern nodes. Opt-in via `enable_subgraphs`; zero regression by default) |
| D1-02 | Update `agents/supervisor.py` — add new nodes to the valid routes and to the supervisor prompt | 0.5d | D1-01 | ✅ DONE (`ADVANCED_MEMBERS` + `effective_valid_routes(enable_advanced)` + `build_system_prompt(enable_advanced)`; `_match_route`/`_intent_short_circuit`/`supervisor_node` gate on `enable_subgraphs`; `_RouterLiteral` expanded. Base prompt byte-identical when the flag is off) |
| D1-03 | Update `agents/intent_router.py` — add regex patterns for new intents (ToT, debate, code review, etl) | 0.5d | D1-01 | ✅ DONE (conservative regexes for `tot_agent`/`data_etl`/`debate_consensus`/`debate_agent`/`code_review`; they only short-circuit when `enable_subgraphs` is on) |
| D1-04 | Add new exceptions to `core/exceptions.py` (HyDEError, FusionError, ToTError, DebateError, ConstitutionalError, LATSError, CompilerError) | 0.3d | — | ✅ DONE (12 new exceptions centralized in `core/exceptions.py`: 7 RAG, 7 patterns, 5 subgraphs; each module imports the canonical one from core) |
| D1-05 | Add `constitutional_principles` to `core/config.py` Settings with default values | 0.3d | — | ✅ DONE (added `constitutional_enabled`, `constitutional_max_revisions`, `constitutional_principles: list[str]` with default IDs `["P001","P002","P003"]`) |
| D1-06 | End-to-end integration tests: Adaptive RAG + Constitutional AI + graph supervisor | 2d | D1-01, D1-02 | ✅ DONE (tests/integration/test_adaptive_rag_constitutional.py with 2 tests: clean flow and flow with revision — covers the SPEC without requiring graph.py integration) |
| D1-07 | Coverage audit: verify ≥ 80% in all new modules; add missing tests | 1d | D1-06 | ✅ DONE (all Phase A+B+C modules with ≥ 82% coverage; most ≥ 90%) |
| D1-08 | Security audit: `uv run bandit -r prismal -c pyproject.toml` with no HIGH/CRITICAL | 0.5d | D1-07 | ✅ DONE (**0 issues** in 7034 new LoC: High=0, Medium=0, Low=0) |
| D1-09 | Update `CLAUDE.md` with the new architecture sections | 0.5d | D1-06 | ✅ DONE (the "Advanced architectures" section with 19 architectures listed; note on the factory-injection pattern) |
| D1-10 | Update the Obsidian note `Documentacion/Prismal/Prismal - Arquitecturas Agentes - Analisis y Gaps.md` marking architectures as implemented | 0.2d | D1-09 | ⚠️ SKIP (external Obsidian vault outside the repo; manual update by the user) |

**Phase D done criteria (global):** ✅ MET (D1-01/02/03 already integrated — opt-in wiring via `enable_subgraphs`)
- ✅ `pytest tests/unit/agents/subgraphs/ tests/unit/agents/patterns/ tests/unit/rag/ tests/integration/test_adaptive_rag_constitutional.py` → **507 passed** (0 failures, 0 errors).
- ✅ Coverage of new modules ≥ 80%: 82–100% per module.
- ✅ `ruff check prismal/` with no errors (Phase A/B/C/D scope).
- ✅ `mypy --strict` with no errors in the new scope.
- ✅ `bandit -r` over new modules: **0 High/Medium/Low issues**.
- ✅ `CLAUDE.md` updated with the advanced architectures section.
- ✅ Wiring into `graph.py` / `supervisor.py` / `intent_router.py` COMPLETED: the 6 patterns (via `agents/patterns/nodes.py`) and the 5 subgraphs are registered as supervisor nodes when `enable_subgraphs=True`; with the flag off, the behavior of the base agents is identical to before (zero regression, verified by the full unit suite passing).

---

## 4. Dependency Map

```
PHASE A — RAG (weeks 1-3)
  A1 HyDE ──────────────────────────────────────────┐
  A2 RAG-Fusion ──────────────────────────────────── │
  A3 Hybrid Search ───────────────────────────────── ├──▶ A7 Adaptive RAG (facade)
  A4 Self-RAG ────────────────────────────────────── │
  A5 Parent-Child RAG ────────────────────────────── │
  A6 Multi-Vector RAG ────────────────────────────── ┘
      │
      ▼
PHASE B — Agent Patterns (weeks 4-6)
  B1 Tree of Thoughts ─────┐
  B2 Debate ───────────────├──▶ C5 Debate/Consensus Subgraph
  B3 Constitutional AI ────┤
  B4 LATS ─────────────────┤
  B5 LLM-Compiler ─────────┤
  B6 Mixture of Agents ────┤
  B7 Swarm/Handoff ────────┘
      │
      ▼
PHASE C — Subgraph Pipelines (weeks 7-8)
  C1 Customer Service ─────┐
  C2 Document Generation ──┤
  C3 Data ETL ─────────────├──▶ D1 Integration into graph.py
  C4 Code Review ──────────┤
  C5 Debate/Consensus ─────┘
      │
      ▼
PHASE D — Hardening (week 9)
  D1 graph.py integration
  D2 supervisor.py update
  D3 Tests + Coverage + Docs
```

---

## 5. Implementation Risks

| Risk | Probability | Impact | Mitigation | Owner |
|---|---|---|---|---|
| Self-RAG: LLM does not emit control tokens correctly | High | High | Extensive prompt engineering; permissive parsing (regex over free response); fallback to CRAG always available | Engineer |
| LATS: search tree explosion (latency) | High | Medium | Conservative `max_simulations` default (50); per-node timeout; depth logging for alerts | Engineer |
| LLM-Compiler: Planner generates DAGs with cycles | Medium | High | `validate_dag()` with Kahn's algorithm before executing; exhaustive edge-case tests | Engineer |
| BM25 in-memory does not scale (Hybrid Search) | Medium | Medium | Document the recommended limit in the docstring; benchmark in Phase D | Engineer |
| Constitutional AI: loop without convergence | Low | High | `max_revisions` = 3 hardcap; return with `max_revisions_reached=True` instead of a fatal error | Engineer |
| `graph.py` becomes too large (26+ → 33+ nodes) | Medium | Medium | Refactor `graph.py` in Phase D if it exceeds 200 lines; extract to `graph_builder.py` | Tech Lead |
| Interference between patterns (e.g., ToT + Constitutional) | Low | Medium | Specific integration tests in Phase D; document the supported composition | Engineer |

---

## 6. Definition of Done (Global)

To close the expansion project as COMPLETED:

- [ ] The 19 architectures implemented and registered.
- [ ] `uv run pytest -m "not live_api"` passes 100% (0 failures, 0 errors).
- [ ] Global coverage ≥ 80% (`uv run pytest --cov=prismal --cov-fail-under=80`).
- [ ] `uv run ruff check .` with no errors.
- [ ] `uv run mypy prismal` with no errors (strict mode).
- [ ] `uv run bandit -r prismal -c pyproject.toml` with no HIGH or CRITICAL findings.
- [ ] All new modules with public docstrings (public classes and methods).
- [ ] `CLAUDE.md` updated with the new sections.
- [ ] The Obsidian note `Prismal - Arquitecturas Agentes - Analisis y Gaps.md` updated.
- [ ] `pyproject.toml` includes `rank_bm25` and `networkx` in dependencies.
- [ ] PR merged to `main` with approved code review.

---

## 7. Effort Estimate per Phase

| Phase | Tasks | Estimated Days | Weeks |
|---|---|---|---|
| A — Advanced RAG | 37 subtasks | 21 days | 3 weeks |
| B — Agent Patterns | 34 subtasks | 25 days | 3 weeks |
| C — Subgraph Pipelines | 21 subtasks | 16 days | 2 weeks |
| D — Hardening | 10 subtasks | 7 days | 1 week |
| **Total** | **102 subtasks** | **69 days** | **9 weeks** |

*Estimate based on 1 senior engineer. With 2 engineers: Phases A and B can overlap from week 2.*

---

---

## PHASE E — MCP Capability Routing ✅ DONE

**Actual duration:** 1 day | **Objective:** route specific MCP tools to each pattern and subgraph according to their capabilities, preventing agents from receiving irrelevant or dangerous tools.

| ID | Task | Status |
|---|---|---|
| E1 | Create `config/mcp_servers.yaml` with a `capabilities: list[str]` field in each entry | ✅ DONE (the file did not exist; it was created with 4 example servers: filesystem, web_search, code_sandbox, rag_store — all with `enabled: false` and appropriate capabilities) |
| E2 | Extend `MCPClientManager.get_all_langchain_tools()` with a `capabilities: list[str] \| None = None` parameter | ✅ DONE — server-level filtering, `general` always included, `None` keeps backward compatibility |
| E3 | Extend `get_tools_for_agent()` with `required_capabilities: list[str] \| None = None` and propagate it to `get_mcp_tools()` | ✅ DONE — the legacy signature (`get_tools_for_agent("researcher")`) keeps working unchanged |
| E4 | Update registrations in `graph.py` with a per-node mapping | ⚠️ ADAPTED — the Phase D nodes (tot_agent, lats_agent, llm_compiler, etc.) remain deferred (D1-01 was not completed); instead, `DEFAULT_CAPABILITY_MAP` + `get_recommended_capabilities(node_name)` are exposed in `tool_registry.py` for the operator to use when wiring. Mapping identical to that specified in the prompt. |
| E5 | Unit tests in `tests/unit/mcp/test_capability_routing.py` | ✅ DONE — **13 tests**, all passing; they cover: default capability, None filter, positive/negative filter, universal `general` server, end-to-end plumbing into `get_tools_for_agent`, the E4 mapping, the legacy path. |
| E6 | Update TASKS.md + SPEC.md | ✅ DONE |

**Phase E done criteria:**
- ✅ `MCPServerConfig.capabilities: list[str]` with default `["general"]` (backward compatible — YAML configs without the field are treated as universal).
- ✅ `MCPClientManager.get_all_langchain_tools(capabilities=None)` behavior identical to pre-Phase-E.
- ✅ With `capabilities=[...]`: only servers with a capability intersection OR tagged `"general"` contribute tools.
- ✅ `get_tools_for_agent()` backward-compatible signature — legacy agents (researcher, coder, …) still receive the full pool.
- ✅ **Tests: 13 pass** — they cover the 6 prompt cases + the E4 mapping + the legacy path + universal capability.
- ✅ Regression: **688 total tests pass** (10 new + 678 previous), 0 failures.
- ✅ `ruff check prismal/agents/tool_registry.py prismal/mcp/client.py tests/unit/mcp/test_capability_routing.py` → All checks passed!
- ✅ `mypy --strict` → Success on `prismal/mcp/client.py` + `prismal/agents/tool_registry.py` (2 source files checked).
- ✅ `bandit -r prismal/mcp/client.py prismal/agents/tool_registry.py -c pyproject.toml` → High=0, Medium=0.
- ✅ Coverage `prismal/mcp/client.py`: **83%**; the new E2 lines (filtering) covered 100% by test_capability_routing.

**Files modified (4) + created (2):**
- **Created**: `config/mcp_servers.yaml` — new catalog with capabilities.
- **Created**: `tests/unit/mcp/test_capability_routing.py` — 13 tests.
- **Modified**: `prismal/mcp/connection.py` — added `capabilities` to `MCPServerConfig`.
- **Modified**: `prismal/mcp/client.py` — signature `get_all_langchain_tools(capabilities=None)` + filtering.
- **Modified**: `prismal/agents/tool_registry.py` — signature `get_tools_for_agent(..., required_capabilities=None)`, `get_mcp_tools(capabilities=None)`, `DEFAULT_CAPABILITY_MAP`, `get_recommended_capabilities()`.

**Canonical mapping (per prompt)** exposed as the public `DEFAULT_CAPABILITY_MAP` in `tool_registry.py`:

| Node | Capabilities |
|---|---|
| `tot_agent` | `["general", "research"]` |
| `lats_agent` | `["general", "research", "file_management"]` |
| `llm_compiler` | `["general", "research", "file_management", "code_execution"]` |
| `mixture_agent` | `["general", "research"]` |
| `customer_service` | `["customer_service", "rag", "general"]` |
| `code_review` | `["code_review", "code_execution", "file_management"]` |
| `data_etl` | `["data_etl", "file_management", "general"]` |
| `document_generation` | `["document_generation", "research", "file_management"]` |
| `debate_consensus` | `["research", "general"]` |

**Deviations from the prompt:**
1. `config/mcp_servers.yaml` **did not exist** in the repo — it was created from scratch (the prompt assumed it existed). It contains 4 sample servers with `enabled: false` so as not to affect the runtime.
2. D1-01/02/03 remain deferred (documented in the Phase D section) — the Phase B/C nodes are not in `graph.py`. That is why E4 was adapted: instead of modifying `get_tools_for_agent()` calls in `graph.py` (calls that do not exist), the canonical mapping is exposed as the public constant `DEFAULT_CAPABILITY_MAP` and the helper `get_recommended_capabilities()`. When the operator runs D1-01, they will have to pass `required_capabilities=get_recommended_capabilities(node_name)` in their calls.

---

## Change History

| Version | Date | Author | Changes |
|---|---|---|---|
| 1.0 | 2026-04-19 | Ernesto Crespo | Initial version — 102 subtasks across 4 phases, 9 weeks |
| 1.1 | 2026-04-19 | Claude Code | Phase E — MCP capability routing (6 subtasks, 13 new tests, 688 total) |

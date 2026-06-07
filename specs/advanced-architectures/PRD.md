# Prismal — Advanced Architectures Expansion

## Product Requirements Document (PRD)

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `IMPLEMENTED` |
| **Version** | 1.0 |
| **Date** | 2026-04-19 |
| **Reviewers** | Tech Lead, AI Architect |
| **Last updated** | 2026-04-19 |

---

## 1. Executive Summary

Prismal v2.0.0 implements a robust AI agent framework with Supervisor/Hub-and-Spoke, CRAG, Reflection Loop, MapReduce, and a set of production pipelines. However, the agent and RAG architecture ecosystem has evolved significantly: there are production-proven patterns — Self-RAG, HyDE, RAG-Fusion, GraphRAG, Tree of Thoughts, LLM-Compiler, among others — that are not available in the system today.

This PRD defines the requirements to implement **16 new architectures** grouped into three domains: (A) advanced RAG, (B) agent patterns, and (C) domain subgraph pipelines. The implementation will be carried out in iterative phases over the existing code in `prismal/`, reusing the already-established security, observability, and provider infrastructure.

The expected result is for Prismal to offer architectural parity with the state of the art (2024-2025), allowing the teams that use it to access more precise retrieval strategies and more sophisticated reasoning patterns without changing the framework's public interface.

---

## 2. Context and Problem

### 2.1 Current Situation

Prismal-agents implements:
- **RAG**: Standard RAG (ChromaDB), CRAG (5-step pipeline), Federated RAG (multi-node), RAG+Reflection.
- **Agents**: Supervisor, ReAct, Reflection, Parallel Fan-out, HITL, Plan-Execute, CodeAct, CUA, Meta-Learning.
- **Pipelines**: dev_pipeline, ml_pipeline, financial, analysis/engineering/research orchestrators.

### 2.2 Problem

The following critical capabilities are not available:

**RAG:**
- There is no way to decide *whether* to retrieve (Self-RAG) — CRAG always retrieves.
- There is no query enhancement before retrieval (HyDE, RAG-Fusion) — the embedding of the direct query has low precision on abstract questions.
- There is no lexical search (BM25) combined with semantic search — it fails on proper nouns and technical terms without prior context.
- There is no multi-hop reasoning over knowledge graphs (GraphRAG).
- There is no hierarchical indexing (Parent-Child) — the retrieved context can be too small.

**Agent patterns:**
- There is no branching reasoning with backtracking (Tree of Thoughts, LATS) — the system only has linear reasoning.
- There is no consensus mechanism across multiple perspectives (Debate) — high-uncertainty answers have no second opinion.
- There is no systematic application of ethical/operational principles to outputs (Constitutional AI).
- There is no compilation of task DAGs with optimal parallelism (LLM-Compiler) — the current planner is sequential.
- There is no decentralized coordination without a supervisor (Swarm/Handoff).

**Subgraph Pipelines:**
- Pipelines are missing for high-value use cases: customer service, document generation, data ETL, code review, debate consensus.

### 2.3 Opportunity

Implementing these architectures positions Prismal as a reference framework for production, with capabilities that today are only available in research implementations or proprietary frameworks. The implementation cost is bounded given that the base infrastructure (providers, security, monitoring, LangGraph) already exists.

---

## 3. Target Users

### Persona 1: AI/ML Engineer
- **Description:** Engineer who integrates Prismal into AI products, builds document-processing pipelines or Q&A systems.
- **Primary need:** Access high-precision RAG strategies without implementing them from scratch; combine strategies according to query type.
- **Usage frequency:** Daily.
- **Technical level:** High.

### Persona 2: AI Solutions Architect
- **Description:** Designs the architecture of multi-agent systems for enterprises; selects patterns according to the domain (customer service, financial analysis, code generation).
- **Primary need:** Have proven reasoning patterns that can be composed into subgraphs without writing orchestration code.
- **Usage frequency:** Weekly (design) / Daily (validation).
- **Technical level:** High.

### Persona 3: Researcher / Experimenter
- **Description:** Evaluates different RAG and reasoning strategies over their own benchmarks. Needs to swap strategies easily.
- **Primary need:** A uniform API across strategies; the ability to configure and compare.
- **Usage frequency:** Daily.
- **Technical level:** Very high.

---

## 4. Objectives and Success Metrics

### 4.1 Business Objectives

| Objective | Metric | Target | Timeframe |
|---|---|---|---|
| Architectural coverage | Architectures implemented / Total identified | 16/16 | Phase C |
| RAG quality | Recall@5 on internal benchmark | ≥ +15% vs CRAG baseline | Phase A |
| Hallucination reduction | Groundedness score (Constitutional AI) | ≥ 0.90 p50 | Phase B |
| Internal adoption | Agents using new architectures | ≥ 3 production use cases | Phase C |
| Test coverage | Branch coverage of new modules | ≥ 80% | Global |

### 4.2 User Objectives

| User Objective | Indicator |
|---|---|
| Select a RAG strategy by query type | `AdaptiveRAGEngine` routes ≥ 90% of queries in the test set correctly |
| Get more precise answers in technical domains | HyDE and RAG-Fusion improve MRR vs Standard RAG |
| Reason about complex problems with backtracking | ToT and LATS solve planning problems that ReAct fails |
| Guarantee safe and ethical outputs | Constitutional AI blocks responses that violate principles |
| Execute complex tasks in optimized parallel | LLM-Compiler reduces latency by ≥ 30% vs sequential Plan-Execute |

---

## 5. Scope

### 5.1 In Scope (Included)

**Phase A — Advanced RAG:**
- [x] Self-RAG (`rag/self_rag.py`) — dynamic retrieval decision
- [x] HyDE (`rag/hyde.py`) — hypothetical document embeddings
- [x] RAG-Fusion (`rag/fusion.py`) — multi-query + Reciprocal Rank Fusion
- [x] Hybrid Search (`rag/hybrid.py`) — BM25 + embeddings with score fusion
- [x] Parent-Child RAG (`rag/hierarchical.py`) — hierarchical indexing
- [x] Adaptive RAG (`rag/adaptive.py`) — dynamic strategy selection
- [x] Multi-Vector RAG (`rag/multi_vector.py`) — multiple representations per doc

**Phase B — Agent Patterns:**
- [x] Tree of Thoughts (`agents/patterns/tree_of_thoughts.py`)
- [x] Debate / Society of Mind (`agents/patterns/debate.py`)
- [x] Constitutional AI (`agents/patterns/constitutional.py`)
- [x] LATS / Monte Carlo Tree Search (`agents/patterns/lats.py`)
- [x] LLM-Compiler (`agents/patterns/llm_compiler.py`)
- [x] Mixture of Agents (`agents/patterns/mixture_of_agents.py`)
- [x] Decentralized Swarm / Handoff (`agents/patterns/swarm.py`)

**Phase C — Subgraph Pipelines:**
- [x] Customer Service Pipeline (`agents/subgraphs/customer_service/`)
- [x] Document Generation Pipeline (`agents/subgraphs/document_generation/`)
- [x] Data ETL Pipeline (`agents/subgraphs/data_etl/`)
- [x] Code Review Pipeline (`agents/subgraphs/code_review/`)
- [x] Debate/Consensus Subgraph (`agents/subgraphs/debate_consensus/`)

**Cross-cutting:**
- [x] Integration with `agents/graph.py` (registration of new nodes)
- [x] Integration with `security/` (all patterns pass through guardrails)
- [x] Integration with `monitoring/` (OTel spans + metrics per architecture)
- [x] Unit and integration tests (≥ 80% coverage)

### 5.2 Out of Scope (Excluded)

- **Model fine-tuning (TALM)** — requires dedicated GPU infrastructure, outside the framework's scope.
- **ColBERT / PLAID** — requires a dedicated inference server (ColBERT-live); evaluated in Phase D.
- **LongRAG** — depends on LLMs with >100K token context; integrated when the provider supports it natively.
- **Neo4j in production for GraphRAG** — Phase A uses NetworkX (in-process); the Neo4j integration is Phase D.
- **UI/Dashboard** — this PRD is exclusive to the framework layer (`prismal`).
- **Public REST APIs** — the modules are Python libraries, not HTTP services.

### 5.3 Future Considerations

- GraphRAG with Neo4j in production (Phase D).
- ColBERT/PLAID as an alternative retriever.
- Self-Discover pattern.
- Structured Episodic Memory store (extension of `memory/`).
- Automated evaluation on public benchmarks (BEIR, RAGAS).

---

## 6. Functional Requirements

### RF-001: Self-RAG — Conditional Retrieval
- **Description:** The system must dynamically decide whether to retrieve external context before generating a response, using an LLM to emit control tokens (`RETRIEVE` / `NO_RETRIEVE`).
- **Actor:** `SelfRAGPipeline` invoked from `rag_agent_node`.
- **Preconditions:** Vector store initialized; LLM provider configured.
- **Main flow:**
  1. The LLM receives the query and decides whether it needs retrieval.
  2. If `RETRIEVE`: runs similarity search → Grade → Filter → Generate with context.
  3. If `NO_RETRIEVE`: generates directly from parametric knowledge.
  4. The LLM emits a token `[Supported]` / `[Unsupported]` / `[Utility:N]` for self-evaluation.
- **Alternative flow:** If the LLM fails to emit a control token → fallback to standard CRAG.
- **Postconditions:** Response generated with decision metadata (retrieved: bool, tokens emitted).
- **Priority:** `MUST`

### RF-002: HyDE — Hypothetical Document Embeddings
- **Description:** The system must generate a hypothetical document for a query and use its embedding as the search vector, improving recall on abstract questions.
- **Actor:** `HyDERetriever` called from `RAGEngine.search_hyde()`.
- **Main flow:**
  1. The LLM generates a hypothetical document that would answer the query (without real context).
  2. The hypothetical document (not the original query) is embedded.
  3. A similarity search is run with that embedding.
  4. The found chunks are returned for use in the downstream pipeline.
- **Priority:** `MUST`

### RF-003: RAG-Fusion — Multi-Query with RRF
- **Description:** The system must generate N reformulations of the query (default 4), run N searches in parallel, and fuse the results with Reciprocal Rank Fusion.
- **Actor:** `RAGFusionEngine` from `rag_agent_node`.
- **Main flow:**
  1. The LLM generates N variants of the original query.
  2. Parallel searches (`asyncio.gather`) for each variant.
  3. RRF: `score(d,q) = Σ 1/(k + rank(d,qi))` with k=60.
  4. Final rerank and return of fused top-k chunks.
- **Priority:** `MUST`

### RF-004: Hybrid Search — BM25 + Embeddings
- **Description:** The system must combine lexical search (BM25) with semantic search (embeddings) through score fusion, with a configurable weight (alpha).
- **Actor:** `HybridSearchEngine` as an extension of `RAGEngine`.
- **Main flow:**
  1. BM25 search over the indexed corpus (rank_bm25).
  2. Semantic search in ChromaDB.
  3. Score fusion: `final = alpha * semantic_score + (1-alpha) * bm25_score`.
  4. Deduplication and rerank.
- **Priority:** `MUST`

### RF-005: Parent-Child RAG — Hierarchical Indexing
- **Description:** The system must index small chunks (child, ~100 tokens) for precise retrieval, but return the parent chunk's context (~500 tokens) to the LLM for greater context.
- **Actor:** `HierarchicalRAGEngine`.
- **Main flow:**
  1. Indexing: splits documents into parent and child chunks; stores the parent_id relation.
  2. Retrieval: searches by similarity in child chunks.
  3. Expansion: retrieves the parent chunk corresponding to each found child.
  4. Generates a response with the (richer) parent context.
- **Priority:** `MUST`

### RF-006: Adaptive RAG — Dynamic Strategy Selection
- **Description:** The system must classify the incoming query and automatically select the most appropriate RAG strategy (Simple, CRAG, Self-RAG, GraphRAG, Fusion).
- **Actor:** `AdaptiveRAGEngine` as a facade over all engines.
- **Main flow:**
  1. Classify the query: simple factual / abstract / multi-hop / ambiguous.
  2. Select the engine based on the classification and configuration.
  3. Execute the selected pipeline.
  4. Return the result with metadata on the strategy used.
- **Priority:** `SHOULD`

### RF-007: Multi-Vector RAG — Multiple Representations
- **Description:** The system must index each document with multiple vectors: summary, chunks, and LLM-generated hypothetical questions, improving recall for different query types.
- **Actor:** `MultiVectorRAGEngine`.
- **Priority:** `SHOULD`

### RF-008: Tree of Thoughts — Branching Reasoning
- **Description:** The system must explore multiple "thoughts" (branches) in parallel, evaluate each branch, and prune the least promising ones, allowing backtracking on complex problems.
- **Actor:** `tree_of_thoughts()` in `agents/patterns/tree_of_thoughts.py`.
- **Main flow:**
  1. Generate N candidate thoughts for the current step (breadth-first or depth-first).
  2. Evaluate each thought with the LLM (score 0-1).
  3. Select the top-k thoughts (beam search) or prune by threshold.
  4. Expand the selected thoughts until reaching a solution or maximum depth.
  5. Return the best path found.
- **Priority:** `MUST`

### RF-009: Debate / Society of Mind
- **Description:** The system must instantiate multiple agents with distinct perspectives, have them debate a response, and synthesize consensus or majority vote.
- **Actor:** `debate_round()` in `agents/patterns/debate.py`.
- **Main flow:**
  1. Generate N initial positions (default 3: proponent, opponent, neutral).
  2. Run M debate rounds: each agent responds to the previous positions.
  3. A moderator synthesizes the consensus or applies majority vote.
  4. Return a consensus response with the agreement level (agreement_score).
- **Priority:** `MUST`

### RF-010: Constitutional AI — Constitutional Principles
- **Description:** The system must evaluate any agent output against a configurable set of constitutional principles and automatically revise responses that violate them.
- **Actor:** `ConstitutionalFilter` in `agents/patterns/constitutional.py`.
- **Main flow:**
  1. Receive the agent's draft response.
  2. For each constitutional principle: the LLM evaluates whether the draft violates it.
  3. If there are violations: the LLM generates a revised response that complies with the principle.
  4. Iterate until all principles are satisfied or max_revisions is reached.
  5. Return the final response with a log of applied revisions.
- **Priority:** `MUST`

### RF-011: LATS — Language Agent Tree Search
- **Description:** The system must apply Monte Carlo Tree Search over the agent's action space, allowing deep exploration with real backtracking when a tool path fails.
- **Actor:** `LATSAgent` in `agents/patterns/lats.py`.
- **Main flow:**
  1. Selection: select the tree node with the best UCB1 score.
  2. Expansion: expand with N candidate actions (tool calls).
  3. Simulation: execute the action and evaluate the result (reward).
  4. Backpropagation: update scores in the tree.
  5. Return the best path found upon reaching a terminal state.
- **Priority:** `SHOULD`

### RF-012: LLM-Compiler — Parallel Task DAG
- **Description:** The system must compile a high-level plan into a task DAG with explicit dependencies, execute independent tasks in parallel, and recompile the plan if any task fails or returns unexpected data.
- **Actor:** `LLMCompiler` in `agents/patterns/llm_compiler.py`.
- **Main flow:**
  1. The Planner LLM generates a list of tasks with dependencies (`{"task": ..., "depends_on": [...], "tool": ...}`).
  2. The Compiler builds the DAG and validates the absence of cycles.
  3. The Executor runs tasks in parallel according to topological sort.
  4. The Joiner synthesizes results and decides whether replanning is necessary.
  5. If replanning: return to step 1 with updated context.
- **Priority:** `MUST`

### RF-013: Mixture of Agents (MoA)
- **Description:** The system must orchestrate multiple LLMs (from different providers) in layers, where the models in layer N generate independent responses and layer N+1 synthesizes them.
- **Actor:** `MixtureOfAgents` in `agents/patterns/mixture_of_agents.py`.
- **Priority:** `SHOULD`

### RF-014: Decentralized Swarm / Handoff
- **Description:** The system must allow agents to transfer control directly between each other (without a central supervisor) through a handoff protocol with shared context.
- **Actor:** `swarm_handoff()` in `agents/patterns/swarm.py`.
- **Priority:** `SHOULD`

### RF-015 — RF-019: Domain Subgraph Pipelines
- **RF-015:** Customer Service Pipeline (Classifier → FAQ RAG → Escalation → Response → Ticket). `MUST`
- **RF-016:** Document Generation Pipeline (Planner → Researcher → Writer → Editor → Formatter). `MUST`
- **RF-017:** Data ETL Pipeline (Extractor → Validator → Transformer → Loader → Auditor). `SHOULD`
- **RF-018:** Code Review Pipeline (Linter → Security Scanner → Logic Reviewer → Suggester). `MUST`
- **RF-019:** Debate/Consensus Subgraph (Proponent → Opponent → Moderator → Consensus). `SHOULD`

---

## 7. Non-Functional Requirements

### Performance
- `HyDERetriever.search()` ≤ 3s p95 (1 LLM call + 1 vector search).
- `RAGFusionEngine.search()` ≤ 5s p95 with N=4 parallel queries.
- `HybridSearchEngine.search()` ≤ 1s p95 (BM25 is local in-process).
- `LLMCompiler` reduces latency by ≥ 30% vs sequential Plan-Execute for parallel independent tasks.
- `tree_of_thoughts()` ≤ 30s p95 with breadth=3, depth=3.

### Security
- All prompts of the new architectures must pass through `SecurePromptBuilder`.
- `ConstitutionalFilter` must record revisions in `AuditLogger`.
- No new architecture may import providers directly (only via `prismal/providers/`).
- `LLMCompiler` must validate tool names against `tool_registry` before executing.

### Availability
- All new architectures must have graceful fallback: if the LLM fails in intermediate steps, return a partial result with the flag `partial_result=True`.

### Scalability
- New RAG engines must support ChromaDB collections of ≥ 1M vectors.
- `MixtureOfAgents` must support ≥ 5 providers in parallel.

### Observability
- Each new architecture must create OTel spans with `OTelManager().start_span("architecture.operation")`.
- Minimum metrics per architecture: `{name}_requests_total`, `{name}_latency_seconds`, `{name}_errors_total`.
- Structured logs with `get_logger()` at each significant step.

### Maintainability
- Test coverage ≥ 80% per new module.
- All public classes with docstrings following the existing style in the repo.
- `ruff check` and `mypy --strict` must pass with no errors.
- `bandit` with no HIGH/CRITICAL findings.

---

## 8. Constraints and Dependencies

### Technical Constraints
- Python 3.13+, uv as the package manager.
- LangGraph `StateGraph` as the orchestration engine — do not introduce alternative orchestration frameworks.
- `prismal/` is a namespace package (no `__init__.py`); do not break this convention.
- `_MAX_TOTAL_TOOLS = 120` — the new nodes must not register excessive tools.

### External Dependencies

| Dependency | Type | Use | Status |
|---|---|---|---|
| `rank_bm25` | New PyPI | Hybrid Search BM25 | ☐ Add to pyproject.toml |
| `networkx` | New PyPI | GraphRAG (in-process graph) | ☐ Add to pyproject.toml |
| `chromadb` | Existing | Vector store (all RAG) | ✅ Already included |
| `langchain-core` | Existing | Messages, documents | ✅ Already included |
| `langgraph` | Existing | StateGraph, Send, interrupt | ✅ Already included |
| `litellm` | Existing | Provider abstraction | ✅ Already included |

---

## 9. User Stories

### Epic A: High-Precision RAG

**US-001:** As an AI Engineer, I want to use HyDE to improve recall on abstract questions, to get better answers in domains where the corpus is technical and dense.
- [ ] `RAGEngine.search_hyde(query)` generates a hypothetical document and searches by its embedding.
- [ ] The result includes metadata with `retrieval_method: "hyde"`.
- [ ] Tests demonstrate ≥ 10% better recall vs direct search on the test corpus.

**US-002:** As an AI Engineer, I want to use RAG-Fusion for ambiguous queries, so that different formulations of the same question produce complementary results.
- [ ] `RAGFusionEngine.search(query, n_queries=4)` generates variants and fuses with RRF.
- [ ] The results include the rank position of each chunk in each sub-search.

**US-003:** As an AI Engineer, I want Hybrid Search for technical terms and proper nouns, so that the system does not fail when the embedding does not capture the exact term well.
- [ ] `HybridSearchEngine.search(query, alpha=0.5)` combines BM25 and semantic.
- [ ] `alpha` is configurable at runtime to adjust the lexical/semantic balance.

**US-004:** As an AI Engineer, I want Self-RAG to avoid unnecessary retrievals, to reduce latency and cost on questions the LLM can answer directly.
- [ ] `SelfRAGPipeline.run(query)` automatically decides whether to retrieve.
- [ ] The log shows the decision made and the control tokens emitted.

### Epic B: Advanced Reasoning

**US-005:** As an AI Architect, I want Tree of Thoughts for complex planning problems, so that the agent explores multiple strategies and chooses the optimal one with backtracking.
- [ ] `tree_of_thoughts(generate_fn, eval_fn, state, breadth=3, depth=3)` works in async.
- [ ] Supports `bfs` (breadth-first) and `dfs` (depth-first) modes.

**US-006:** As an AI Architect, I want Constitutional AI to guarantee safe outputs, so that the system automatically revises responses that violate safety or accuracy principles.
- [ ] `ConstitutionalFilter` accepts a list of principles as strings.
- [ ] Each revision is recorded in `AuditLogger`.

**US-007:** As an AI Architect, I want LLM-Compiler to reduce latency on complex tasks, so that the system executes independent tasks in parallel without manual coordination.
- [ ] `LLMCompiler.compile_and_run(goal, tools)` returns results within the target time.
- [ ] The generated DAG is serializable for debugging.

### Epic C: Domain Pipelines

**US-008:** As an AI Engineer, I want the Customer Service Pipeline ready to connect to the supervisor, to handle user queries with automatic escalation.
- [ ] Full pipeline: Classifier → FAQ RAG → Escalation Gate → Response → Ticket.
- [ ] Registered in `SubgraphRegistry`.

**US-009:** As an AI Engineer, I want the Code Review Pipeline as a reusable subgraph, to integrate automated code review into CI/CD flows via an agent.
- [ ] Pipeline: Linter → Security Scanner → Logic Reviewer → Suggester.
- [ ] Returns a structured report with issues grouped by severity.

---

## 10. Risks and Mitigations

| Risk | Probability | Impact | Mitigation |
|---|---|---|---|
| LLM does not emit control tokens correctly (Self-RAG) | Medium | High | Fallback to CRAG; permissive regex parsing; extensive prompt engineering |
| In-memory BM25 does not scale for corpora >10M docs (Hybrid) | Low | High | Implement with on-disk index support (pickle); document the limit |
| GraphRAG with NetworkX slow on graphs >100K nodes | Medium | Medium | Limit to local graphs; Neo4j in Phase D as an extension |
| ToT generates too many LLM calls (cost) | High | Medium | Cap `breadth * depth` ≤ 9 by default; configurable with a warning |
| LLM-Compiler generates DAGs with cycles | Low | High | Strict topological validation; reject the plan if there is a cycle |
| Constitutional AI in an infinite loop | Low | High | `max_revisions` = 3 by default; return with a warning flag |
| LATS requires many simulations (latency) | High | Medium | Limit simulations per node; configurable timeout |

---

## 11. Estimated Timeline

| Phase | Estimated Duration | Deliverable |
|---|---|---|
| Phase A — Advanced RAG | 3 weeks | 7 functional RAG engines with tests |
| Phase B — Agent Patterns | 3 weeks | 7 agent patterns with tests |
| Phase C — Subgraph Pipelines | 2 weeks | 5 registered and tested pipelines |
| Hardening & Docs | 1 week | Coverage ≥ 80%, updated docs |
| **Total** | **9 weeks** | 19 new architectures in production |

---

## Change History

| Version | Date | Author | Changes |
|---|---|---|---|
| 1.0 | 2026-04-19 | Ernesto Crespo | Initial version — 19 architectures, 3 phases |

## Approvals

| Role | Name | Date | Status |
|---|---|---|---|
| Tech Lead | — | | ☐ Pending |
| AI Architect | — | | ☐ Pending |

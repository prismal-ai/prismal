# Prismal Advanced Architectures — Technical Design Document

## Metadata

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `IMPLEMENTED` |
| **Version** | 1.0 |
| **Date** | 2026-04-19 |
| **Related PRD** | `specs/advanced-architectures/PRD.md` |
| **Reviewers** | Tech Lead, AI Architect |

---

## 1. Context

Prismal-agents is a Python namespace package (PEP 420) that implements an AI agent framework on top of LangGraph. The existing architecture provides: Supervisor Hub-and-Spoke, CRAG, Federated RAG, Reflection Loop, Parallel Fan-out, HITL Gate, and 6 domain subgraph pipelines.

This document describes the technical design for integrating 19 new architectures while maintaining consistency with the existing conventions: namespace package, `SecurePromptBuilder` for prompts, providers via `ProviderRegistry`, OTel spans via `OTelManager`, and `get_logger()` for structured logging.

The central design principle is **composition over inheritance**: the new architectures are built on top of the already-existing primitives (`reflection_loop`, `make_parallel_dispatcher`, `CRAGPipeline`, `ChromaVectorStore`), extending their capabilities without duplicating code.

---

## 2. Technical Objectives

- **Correctness:** Each architecture faithfully implements the reference algorithm (paper or spec).
- **Performance:** No regressions in existing architectures; new ones within the PRD's SLOs.
- **Maintainability:** `ruff`, `mypy --strict`, `bandit` pass with no errors. Coverage ≥ 80%.
- **Composability:** New architectures are usable as building blocks with each other (e.g., HyDE + RAG-Fusion).
- **Operability:** OTel spans + metrics + logs in all new modules.

---

## 3. Proposed Architecture

### 3.1 High-Level Diagram — New Modules

```
prismal/
├── rag/
│   ├── [EXISTING] engine.py            ← RAGEngine (Standard RAG)
│   ├── [EXISTING] crag.py              ← CRAGPipeline
│   ├── [EXISTING] federated.py         ← FederatedRAGEngine
│   ├── [NEW] hyde.py                   ← HyDERetriever
│   ├── [NEW] fusion.py                 ← RAGFusionEngine (Multi-Query + RRF)
│   ├── [NEW] hybrid.py                 ← HybridSearchEngine (BM25 + Embeddings)
│   ├── [NEW] self_rag.py               ← SelfRAGPipeline
│   ├── [NEW] hierarchical.py           ← HierarchicalRAGEngine (Parent-Child)
│   ├── [NEW] adaptive.py               ← AdaptiveRAGEngine (facade)
│   └── [NEW] multi_vector.py           ← MultiVectorRAGEngine
│
├── agents/
│   ├── patterns/
│   │   ├── [EXISTING] reflection.py
│   │   ├── [EXISTING] parallel.py
│   │   ├── [NEW] tree_of_thoughts.py  ← tree_of_thoughts()
│   │   ├── [NEW] debate.py            ← debate_round()
│   │   ├── [NEW] constitutional.py    ← ConstitutionalFilter
│   │   ├── [NEW] lats.py              ← LATSAgent
│   │   ├── [NEW] llm_compiler.py      ← LLMCompiler
│   │   ├── [NEW] mixture_of_agents.py ← MixtureOfAgents
│   │   └── [NEW] swarm.py             ← swarm_handoff()
│   │
│   └── subgraphs/
│       ├── [EXISTING] dev_pipeline/
│       ├── [EXISTING] ml_pipeline/
│       ├── [NEW] customer_service/    ← CustomerServicePipeline
│       ├── [NEW] document_generation/ ← DocumentGenerationPipeline
│       ├── [NEW] data_etl/            ← DataETLPipeline
│       ├── [NEW] code_review/         ← CodeReviewPipeline
│       └── [NEW] debate_consensus/    ← DebateConsensusPipeline
```

### 3.2 Integration Structure with the Main Graph

```
                     ┌──────────────────────────────────┐
                     │         SUPERVISOR NODE           │
                     │  (agents/supervisor.py)           │
                     └──────┬───────────────────────────┘
                            │ routes to
       ┌────────────────────┼────────────────────────────┐
       ▼                    ▼                            ▼
 ┌──────────┐       ┌──────────────┐            ┌──────────────┐
 │ rag_agent│       │ [NEW]        │            │ [NEW]        │
 │  _node   │       │ tot_node     │            │ customer_    │
 │ (CRAG+   │       │ debate_node  │            │ service_node │
 │  Reflect)│       │ compiler_node│            │ code_review_ │
 └────┬─────┘       │ lats_node    │            │ node         │
      │             └──────┬───────┘            └──────────────┘
      ▼                    ▼
 ┌──────────────────────────────────────┐
 │         RAG ENGINE LAYER             │
 │  AdaptiveRAGEngine (facade)          │
 │  ┌──────┐ ┌───────┐ ┌──────────┐   │
 │  │ HyDE │ │Fusion │ │ Hybrid   │   │
 │  │      │ │  RRF  │ │ BM25+    │   │
 │  └──────┘ └───────┘ │ Semantic │   │
 │  ┌──────┐ ┌───────┐ └──────────┘   │
 │  │ Self │ │Parent │ ┌──────────┐   │
 │  │ RAG  │ │ Child │ │  Graph   │   │
 │  └──────┘ └───────┘ │  RAG     │   │
 │                     └──────────┘   │
 └──────────────────────────────────────┘
                     │
              ┌──────▼───────┐
              │ ChromaVectorStore│
              │ (existing)     │
              └────────────────┘
```

### 3.3 Components per Module

#### Phase A — RAG Engines

| Module | Main Class | Dependencies | Pattern |
|--------|----------------|-------------|--------|
| `rag/hyde.py` | `HyDERetriever` | `ProviderRegistry`, `ChromaVectorStore` | Generation → Embedding → Search |
| `rag/fusion.py` | `RAGFusionEngine` | `ProviderRegistry`, `RAGEngine`, `asyncio` | Multi-query fan-out + RRF |
| `rag/hybrid.py` | `HybridSearchEngine` | `rank_bm25`, `ChromaVectorStore` | Linear score fusion |
| `rag/self_rag.py` | `SelfRAGPipeline` | `ProviderRegistry`, `CRAGPipeline` | Token control + conditional retrieval |
| `rag/hierarchical.py` | `HierarchicalRAGEngine` | `ChromaVectorStore`, `DocumentProcessorFactory` | Parent-child index |
| `rag/adaptive.py` | `AdaptiveRAGEngine` | All RAG engines | Facade + query classifier |
| `rag/multi_vector.py` | `MultiVectorRAGEngine` | `ProviderRegistry`, `ChromaVectorStore` | Multi-representation index |

#### Phase B — Agent Patterns

| Module | Class/Function | Dependencies | Pattern |
|--------|--------------|-------------|--------|
| `patterns/tree_of_thoughts.py` | `tree_of_thoughts()` | `ProviderRegistry`, `parallel.py` | BFS/DFS tree + beam search |
| `patterns/debate.py` | `debate_round()` | `ProviderRegistry`, `reflection.py` | Multi-LLM + moderator synthesis |
| `patterns/constitutional.py` | `ConstitutionalFilter` | `ProviderRegistry`, `AuditLogger` | Principles check + revision loop |
| `patterns/lats.py` | `LATSAgent` | `ProviderRegistry`, `tool_registry` | MCTS: select→expand→simulate→backprop |
| `patterns/llm_compiler.py` | `LLMCompiler` | `ProviderRegistry`, `tool_registry`, DAG | Planner→Compile DAG→Execute→Join |
| `patterns/mixture_of_agents.py` | `MixtureOfAgents` | `ProviderRegistry` (multi-provider) | Layer N generate → Layer N+1 synthesize |
| `patterns/swarm.py` | `swarm_handoff()` | `AgentState`, `ProviderRegistry` | Peer handoff without supervisor |

### 3.4 Detailed Data Flows

#### Flow A1: HyDE Retrieval

```
Query ──▶ [LLM: generates hypothetical doc] ──▶ [EmbeddingsFactory.embed(doc)]
       ──▶ [ChromaVectorStore.similarity_search(hyp_embedding, k)]
       ──▶ [List[RetrievedChunk]] ──▶ downstream pipeline
```

#### Flow A2: RAG-Fusion with RRF

```
Query ──▶ [LLM: generates N variants Q1..QN]
       ──▶ asyncio.gather(
              search(Q1, k), search(Q2, k), ... search(QN, k)
           )
       ──▶ [RRF merge: score(d) = Σ 1/(60 + rank(d, Qi))]
       ──▶ [sort descending] ──▶ [top-k chunks]
```

#### Flow A3: Hybrid Search

```
Query ──┬──▶ [BM25Index.search(query)] ──▶ [(doc_id, bm25_score)]
        └──▶ [ChromaVectorStore.search(query)] ──▶ [(doc_id, sem_score)]
        ──▶ [Fusion: alpha * sem + (1-alpha) * bm25_norm]
        ──▶ [Dedup + sort] ──▶ [List[RetrievedChunk]]
```

#### Flow A4: Self-RAG

```
Query ──▶ [LLM prompt: "Do you need to retrieve information? RETRIEVE / NO_RETRIEVE"]
       ──┬── NO_RETRIEVE ──▶ [LLM generates a direct response]
         └── RETRIEVE ──▶ [CRAGPipeline.run(query)]
                       ──▶ [LLM evaluates: Supported / Unsupported / Utility:N]
                       ──▶ [SelfRAGResult with control tokens]
```

#### Flow A5: Parent-Child RAG

```
INDEXING:
  Doc ──▶ [split parent chunks ~500 tokens] ──▶ [split child chunks ~100 tokens]
       ──▶ [store child in ChromaDB with metadata.parent_id]
       ──▶ [store parent in dict/sqlite by parent_id]

RETRIEVAL:
  Query ──▶ [similarity_search on child chunks]
         ──▶ [load parent chunk for each found child]
         ──▶ [List[ParentChunk]] ──▶ LLM context
```

#### Flow B1: Tree of Thoughts

```
State ──▶ [generate N thoughts (breadth)]
       ──▶ [eval_fn(thought) → score per thought]
       ──▶ [select top-k by score (beam)]
       ──▶ [expand each selected thought → sub-thoughts]
       ──▶ [repeat until depth reached or terminal state found]
       ──▶ [return best path: List[Thought] + final_answer]
```

#### Flow B2: Debate / Society of Mind

```
Query ──▶ [Agent A generates position A]
       ──▶ [Agent B generates position B (may be opposite)]
       ──▶ [Agent C generates a neutral position]
       ──▶ [Round 2: each agent responds to the other positions]
       ──▶ [Moderator: synthesizes consensus]
       ──▶ [DebateResult: answer + agreement_score + dissenting_views]
```

#### Flow B3: LLM-Compiler

```
Goal + Tools ──▶ [Planner LLM: generates Task DAG JSON]
             ──▶ [Compiler: validates DAG, detects cycles]
             ──▶ [Topological sort → execution waves]
             ──▶ [Executor: asyncio.gather per wave]
             ──▶ [Joiner LLM: synthesizes results]
             ──▶ Replanning needed?
                 ├── NO ──▶ [CompilerResult]
                 └── YES ──▶ [return to Planner with context]
```

#### Flow B4: LATS (MCTS)

```
Root State ──▶ [Selection: UCB1 = Q/N + C*sqrt(ln(N_parent)/N)]
           ──▶ [Expansion: N candidate actions via LLM]
           ──▶ [Simulation: execute action → reward via eval_fn]
           ──▶ [Backpropagation: update Q, N along root→node path]
           ──▶ [Repeat until budget (max_simulations) or terminal]
           ──▶ [Return best path by highest Q/N]
```

---

## 4. Design Decisions

### DD-001: AdaptiveRAGEngine as a Facade, Not a Subclass

- **Decision:** `AdaptiveRAGEngine` composes instances of the specific engines (receives already-configured instances) and delegates, instead of inheriting from `RAGEngine`.
- **Context:** The engines have different configurations (BM25 requires a corpus, GraphRAG requires a graph); they do not have a perfect common interface.
- **Alternatives evaluated:**

| Option | Pros | Cons |
|---|---|---|
| **Facade (chosen)** | Flexible configuration, lazy init, composable | More delegation code |
| Multiple inheritance | Less code | Violates Liskov in some engines; complex Python MRO |
| Single Protocol/ABC | Uniform API | Overhead of implementing all methods in each engine |

- **Rationale:** The facade allows the engines to be usable independently AND as part of Adaptive, without forcing artificial inheritance.

### DD-002: In-Process BM25 (NetworkX for GraphRAG)

- **Decision:** BM25 is implemented in-process with `rank_bm25` (in-memory index). GraphRAG uses `networkx` (in-process). No external server is required in Phase A/B.
- **Context:** Adding Neo4j or ElasticSearch as a required dependency would drastically increase setup complexity.
- **Alternatives evaluated:**

| Option | Pros | Cons |
|---|---|---|
| **In-process (chosen)** | Zero-config, testable without services | Limited to corpora that fit in RAM |
| ElasticSearch | Scales to TB of data | Requires a server, Docker, ops overhead |
| Neo4j | Production-native graphs | Requires a server; complicates tests |

- **Consequences:** Document the recommended limit (~500K docs for BM25). Neo4j in Phase D.

### DD-003: Tree of Thoughts — Beam Search over Pure DFS

- **Decision:** ToT implements beam search (keep the top-k best thoughts per level) instead of pure DFS or full BFS.
- **Context:** DFS can get stuck in suboptimal paths; full BFS is exponentially costly. Beam search balances exploration and cost.
- **Alternatives evaluated:**

| Option | Pros | Cons |
|---|---|---|
| **Beam Search (chosen)** | Controlled cost; better than greedy | May miss solutions outside the beam |
| Pure DFS | Simple to implement | May be suboptimal without real backtracking |
| Full BFS | Guarantees finding the optimum | Exponential cost; infeasible with LLMs |
| MCTS | Theoretical optimum | High complexity; that is already LATS |

### DD-004: Constitutional AI — Principles as Strings, Not Hardcoded

- **Decision:** `ConstitutionalFilter` accepts `principles: list[str]` in the constructor, with no principles hardcoded in the code.
- **Context:** Different deployments need different principles (regulatory, brand, security). Hardcoding violates the configurability principle.
- **Consequences:** The default set of principles is defined in `core/config.py` as `constitutional_principles: list[str]`.

### DD-005: LLM-Compiler — DAG as Serializable JSON

- **Decision:** The task DAG is represented as `list[TaskNode]` where each `TaskNode` is a dataclass with `id`, `tool`, `args`, `depends_on: list[str]`. Serializable to JSON for debugging.
- **Consequences:** Facilitates logging, plan replay, and testing without executing real tools.

### DD-006: Swarm/Handoff — Shared State via AgentState, Not Global Memory

- **Decision:** The handoff between agents in `swarm.py` passes the complete `AgentState`; it does not use mutable global memory.
- **Context:** Global memory introduces race conditions in parallel execution with `asyncio`.
- **Consequences:** Each handoff is a new `AgentState` frame; the handoff history is recorded in `state["metadata"]["handoff_history"]`.

### DD-007: New Nodes in graph.py — Explicit Registration

- **Decision:** Each new architecture that needs a LangGraph node is registered explicitly in `agents/graph.py` following the existing pattern. There is no dynamic/automatic registration.
- **Consequences:** `graph.py` remains the single source of truth for the graph topology. The new nodes follow the naming convention `{name}_node`.

---

## 5. Code Structure

```
prismal/
│
├── rag/
│   ├── __init__.py          ← exports: HyDERetriever, RAGFusionEngine,
│   │                           HybridSearchEngine, SelfRAGPipeline,
│   │                           HierarchicalRAGEngine, AdaptiveRAGEngine,
│   │                           MultiVectorRAGEngine
│   ├── hyde.py
│   ├── fusion.py
│   ├── hybrid.py
│   ├── self_rag.py
│   ├── hierarchical.py
│   ├── adaptive.py
│   └── multi_vector.py
│
├── agents/
│   ├── patterns/
│   │   ├── __init__.py      ← exports all patterns
│   │   ├── tree_of_thoughts.py
│   │   ├── debate.py
│   │   ├── constitutional.py
│   │   ├── lats.py
│   │   ├── llm_compiler.py
│   │   ├── mixture_of_agents.py
│   │   └── swarm.py
│   │
│   └── subgraphs/
│       ├── customer_service/
│       │   ├── __init__.py
│       │   ├── classifier_node.py
│       │   ├── faq_retrieval_node.py
│       │   ├── escalation_node.py
│       │   ├── response_node.py
│       │   └── ticket_node.py
│       ├── document_generation/
│       │   ├── __init__.py
│       │   └── [nodes...]
│       ├── data_etl/
│       │   ├── __init__.py
│       │   └── [nodes...]
│       ├── code_review/
│       │   ├── __init__.py
│       │   └── [nodes...]
│       └── debate_consensus/
│           ├── __init__.py
│           └── [nodes...]
│
tests/
├── unit/
│   ├── rag/
│   │   ├── test_hyde.py
│   │   ├── test_fusion.py
│   │   ├── test_hybrid.py
│   │   ├── test_self_rag.py
│   │   ├── test_hierarchical.py
│   │   ├── test_adaptive.py
│   │   └── test_multi_vector.py
│   └── agents/patterns/
│       ├── test_tree_of_thoughts.py
│       ├── test_debate.py
│       ├── test_constitutional.py
│       ├── test_lats.py
│       ├── test_llm_compiler.py
│       ├── test_mixture_of_agents.py
│       └── test_swarm.py
└── integration/
    ├── test_rag_advanced_integration.py
    └── test_agent_patterns_integration.py
```

### Applied Patterns

| Pattern | Where | Why |
|---|---|---|
| Facade | `AdaptiveRAGEngine` | Unifies the interface over multiple engines |
| Strategy | All RAG engines | Interchangeable via the same `search()` method |
| Template Method | `BaseRAGEngine` (new ABC) | Common steps: log → span → search → return |
| Composite | `MixtureOfAgents` | Composes multiple LLM providers |
| Chain of Responsibility | `ConstitutionalFilter` | Each principle processes the output in a chain |
| Interpreter | `LLMCompiler` DAG parser | Interprets the JSON plan as an executable graph |

### Error Handling

```python
# New exception hierarchy (in core/exceptions.py)
class RAGError(PrismalError): ...           # already exists
class HyDEError(RAGError): ...                 # failure in hypothetical generation
class FusionError(RAGError): ...               # failure in multi-query
class GraphRAGError(RAGError): ...             # failure in the knowledge graph

class PatternError(PrismalError): ...       # new
class ToTError(PatternError): ...              # failure in tree of thoughts
class DebateError(PatternError): ...           # failure in debate
class ConstitutionalError(PatternError): ...   # unresolved violation
class LATSError(PatternError): ...             # MCTS failure
class CompilerError(PatternError): ...         # invalid DAG or cycle
```

---

## 6. Security

### 6.1 Attack Surface — New Architectures

| Vector | Architecture | Mitigation |
|---|---|---|
| Prompt injection in the hypothetical doc (HyDE) | HyDE | `SecurePromptBuilder` for the hypothetical generation prompt |
| Malicious query variants (RAG-Fusion) | RAG-Fusion | Variants pass through `GuardrailsEngine` before search |
| Execution of unauthorized tool calls (LLM-Compiler) | LLM-Compiler | Validate tool names against `tool_registry`; `ActionInterceptor.check()` before each execution |
| Infinite loop in Constitutional AI | Constitutional AI | `max_revisions` hardcap = 5; per-revision timeout |
| MCTS state explosion (LATS) | LATS | `max_simulations` hardcap; budget in seconds |
| Handoff to an unregistered agent (Swarm) | Swarm | Whitelist of valid agents for handoff |
| Knowledge graph with PII data (GraphRAG) | GraphRAG | Sanitize entities before storing; respect `InputSanitizer` |

### 6.2 Cross-Cutting Rules

1. **No new module imports providers directly** — always via `ProviderRegistry`.
2. **All user prompts pass through `SecurePromptBuilder`** — never f-strings with user input.
3. **`ActionInterceptor.check()` before tool execution** in `LLMCompiler` and `LATSAgent`.
4. **`AuditLogger` records** each `ConstitutionalFilter` revision and each `swarm_handoff` handoff.

---

## 7. Observability

### 7.1 OTel Spans per Architecture

| Architecture | Span Names |
|---|---|
| HyDE | `hyde.generate_hypothesis`, `hyde.embed`, `hyde.search` |
| RAG-Fusion | `fusion.generate_queries`, `fusion.search_parallel`, `fusion.rrf_merge` |
| Hybrid Search | `hybrid.bm25_search`, `hybrid.semantic_search`, `hybrid.score_fusion` |
| Self-RAG | `self_rag.decide`, `self_rag.retrieve`, `self_rag.generate`, `self_rag.evaluate` |
| Parent-Child | `hierarchical.index_doc`, `hierarchical.search_child`, `hierarchical.expand_parent` |
| ToT | `tot.generate_thoughts`, `tot.evaluate_thoughts`, `tot.beam_select` |
| Debate | `debate.generate_positions`, `debate.round`, `debate.synthesize` |
| Constitutional | `constitutional.check_principle`, `constitutional.revise` |
| LATS | `lats.select`, `lats.expand`, `lats.simulate`, `lats.backpropagate` |
| LLM-Compiler | `compiler.plan`, `compiler.compile_dag`, `compiler.execute_wave`, `compiler.join` |

### 7.2 Key Metrics

```
# Counters
rag_hyde_requests_total{status="success|error"}
rag_fusion_requests_total{n_queries="4"}
hybrid_search_requests_total{alpha="0.5"}
self_rag_retrieve_decisions_total{decision="retrieve|skip"}
tot_thoughts_generated_total
debate_rounds_total
constitutional_revisions_total{principle="..."}
lats_simulations_total
compiler_tasks_executed_total{status="success|failed"}

# Histograms
rag_hyde_latency_seconds
rag_fusion_latency_seconds
tot_latency_seconds
lats_latency_seconds
compiler_latency_seconds
```

---

## 8. Testing Strategy

| Level | Coverage | Tools | What it covers |
|---|---|---|---|
| Unit | ≥ 80% per module | pytest, unittest.mock | Algorithms (RRF, UCB1, topological sort), dataclasses, validations |
| Integration | Critical flows | pytest + AsyncMock + in-memory ChromaDB | RAG end-to-end with a real vector store, patterns with a mocked LLM |
| Markers | `@pytest.mark.unit`, `@pytest.mark.integration` | pytest | Tier separation |
| Live API | `@pytest.mark.live_api` | pytest (skipped by default) | Real validation against LLM providers |

### Mocking Strategy for LLMs

The agent pattern tests mock `ProviderRegistry.get_llm()` to return an `AsyncMock` that yields deterministic responses. This enables:
- Fast tests with no network latency.
- Reproducible tests (not dependent on LLM outputs).
- Coverage of error flows (mock raises `Exception`).

---

## 9. Rollout Plan

### 9.1 Integration Strategy in `graph.py`

The new nodes are added to the existing graph **additively**: they are added as valid supervisor destinations without removing existing nodes. The supervisor learns to route to them based on the query intent.

```python
# In agents/graph.py — add after the existing nodes:
builder.add_node("tot_agent", tot_agent_node)
builder.add_node("debate_agent", debate_agent_node)
builder.add_node("constitutional_filter", constitutional_filter_node)
builder.add_node("llm_compiler", llm_compiler_node)
# etc.

# In agents/supervisor.py — add to VALID_NEXT_NODES:
VALID_NEXT_NODES = {
    ...,  # existing
    "tot_agent", "debate_agent", "constitutional_filter",
    "llm_compiler", "lats_agent", "mixture_agent",
}
```

### 9.2 Backward Compatibility

- All new RAG engines have an interface compatible with `RAGEngine` (`search(query, k) → List[RetrievedChunk]`).
- `AdaptiveRAGEngine` can receive `None` for unconfigured engines and will use CRAG as a fallback.
- The new subgraph pipelines follow the `SubgraphFactory` pattern and are registered in `SubgraphRegistry`.

---

## 10. Open Questions

- [ ] **GraphRAG**: Use NetworkX with pickle for persistence or SQLite for the graph? — Owner: Tech Lead, Deadline: start of Phase A week 2.
- [ ] **AdaptiveRAG classifier**: An LLM call to classify (more precise but slower) or regex + heuristics (fast but less precise)? — Owner: AI Architect, Deadline: start of Phase A week 1.
- [ ] **Constitutional principles defaults**: What are the default principles in `core/config.py`? — Owner: Ernesto Crespo, Deadline: start of Phase B.
- [ ] **LATS reward function**: Is the reward defined by the caller or is there a default function? — Owner: AI Architect, Deadline: start of Phase B week 2.
- [ ] **MoA providers**: How many and which providers in the generation layer? How to handle a provider failure? — Owner: Tech Lead, Deadline: start of Phase B week 3.

---

## Change History

| Version | Date | Author | Changes |
|---|---|---|---|
| 1.0 | 2026-04-19 | Ernesto Crespo | Initial version — design of 19 architectures across 3 phases |

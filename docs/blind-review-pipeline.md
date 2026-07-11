# Blind Review Pipeline (BRP)

An opt-in subgraph in which a **spec** agent and an **implementer** agent
produce an artifact that **two independent, blind reviewers** assess — without
any visibility into the conversation — before a **deterministic synthesis** and
a **bounded correction loop** (optionally gated by human approval).

Gated by `settings.blind_review_pipeline_enabled` (default `False`); with the
flag off the compiled supervisor graph is byte-for-byte unchanged.

## Why "blind"?

Each reviewer sees **only** the specification and the implementation artifact —
never `state["messages"]`, never the other reviewer's verdict. Independence is
enforced three ways (defense in depth):

1. **Structural** — the reviewer's backend has the signature
   `(spec, artifact) -> CodeReviewReport`; the node body reads exactly two
   fields via a private `_extract_blind_context` helper.
2. **Static** — an AST guard test fails CI if `reviewer_node.py` ever references
   `state["messages"]` / `state.get("messages")`.
3. **Runtime** — `BlindnessGuard.assert_no_message_leak(...)` runs before every
   prompt is built and raises `BlindReviewBlindnessViolationError` on a
   suspected leak.

## Quick start

```python
import asyncio
from langgraph.checkpoint.memory import MemorySaver
from langchain_core.messages import HumanMessage

from prismal.agents.subgraphs.blind_review_pipeline import (
    build_blind_review_pipeline_subgraph,
)
from prismal.agents.subgraphs.factory import assemble_state_graph

definition = build_blind_review_pipeline_subgraph()  # omit fns to use real LLMs
graph = assemble_state_graph(definition).compile(checkpointer=MemorySaver())

result = asyncio.run(
    graph.ainvoke(
        {"messages": [HumanMessage(content="Write a CSV parser")], "metadata": {}},
        config={"configurable": {"thread_id": "t1"}},
    )
)
print(result["metadata"]["blind_review"]["synthesis"]["report"]["score"])
```

A runnable, fully-faked demo lives in `examples/blind_review_pipeline.py`.

Once enabled, the supervisor also routes to the pipeline automatically for
review-panel intents ("run a blind review panel", "dual review this",
"revisión ciega").

## Pipeline

```
spec_agent → implementer → reviewer_a → reviewer_b → synthesis
               ^                                        │
               └────────── score_gate (fail) ──────────┘
synthesis → score_gate (pass) → approval_seed → human_approval → hitl_gate → END
```

- **spec_agent** — the only node that reads `state["messages"]`; turns the goal
  into `spec_artifact`.
- **implementer** — reads **only** `spec_artifact` (plus the structured issue
  list on a retry); gates any file/code action through `ActionInterceptor`;
  increments `iteration_count` to bound the loop.
- **reviewer_a / reviewer_b** — blind, independent verdicts (`CodeReviewReport`).
- **synthesis** — deterministic, LLM-free merge (`synthesize_verdicts`):
  conservative `min` score, de-duplicated union of issues, `agreement` flag.
- **score_gate** — routes back to the implementer while the merged score is
  below `blind_review_approval_threshold`, force-passing after
  `blind_review_max_iterations`.
- **HITL** — the reused `seed_hitl_metadata` → `human_approval_node` →
  `hitl_gate` trio; bypassed entirely when `settings.hitl_enabled` is `False`.

> **Note — sequential reviewers.** The reviewers run sequentially rather than as
> a parallel fan-out: both write the no-reducer `metadata` channel, so a
> concurrent superstep would raise LangGraph's `InvalidUpdateError`. Blindness
> and independence are unaffected (guaranteed by the input contract + guards,
> not by concurrency); only reviewer latency is traded away.

## Per-role models and tools

Each of the four roles resolves its own LLM (`ProviderRegistry`) and its own
tool/skill scope (`ToolProviderPort`, keyed by `agent_name`). Configure via
settings:

| Setting | Default | Purpose |
|---|---|---|
| `blind_review_pipeline_enabled` | `False` | Master opt-in |
| `blind_review_spec_model` | `None` | Spec agent model (`None` = provider default) |
| `blind_review_implementer_model` | `None` | Implementer model |
| `blind_review_reviewer_a_model` | `None` | Reviewer A model |
| `blind_review_reviewer_b_model` | `None` | Reviewer B model |
| `blind_review_spec_capabilities` | `["docs", "requirements"]` | Spec tools |
| `blind_review_implementer_capabilities` | `["code", "sandbox"]` | Implementer tools |
| `blind_review_reviewer_a_capabilities` | `["code_review", "testing"]` | Reviewer A tools |
| `blind_review_reviewer_b_capabilities` | `["security", "style"]` | Reviewer B tools |
| `blind_review_approval_threshold` | `0.8` | Synthesis pass threshold (`[0, 1]`) |
| `blind_review_max_iterations` | `3` | Correction-loop bound (`>= 1`) |

A same-model reviewer pair is legal but logs a
`blind_review.reviewers_share_model` warning (a weaker panel). Out-of-range
threshold/iterations raise `BlindReviewConfigError` at settings load.

## Testing your own composition

Inject fakes for any role — no LLM backend required:

```python
async def rev(spec, artifact):
    return CodeReviewReport(score=0.9, approved=True)

definition = build_blind_review_pipeline_subgraph(
    spec_fn=..., implementer_fn=..., reviewer_a_fn=rev, reviewer_b_fn=rev,
)
```

The package never imports `prismal.mcp` / `prismal.skills` (AST-guarded); tools
reach the agents only through the injected `ToolProviderPort`.

## Observability

- Audit (hash-first, no raw content): `blind_review.spec`,
  `blind_review.implement`, `blind_review.review_a`, `blind_review.review_b`,
  `blind_review.synthesis`.
- OTel spans mirror the audit names; the `agreement=False` case is surfaced on
  the synthesis span for reviewer-disagreement monitoring.

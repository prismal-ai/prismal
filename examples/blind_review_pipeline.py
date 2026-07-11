"""End-to-end Blind Review Pipeline demo with injected fakes (Fase BRP).

Builds the blind_review_pipeline subgraph (spec → implement → two blind
reviewers → deterministic synthesis → HITL) with deterministic role backends —
no LLM, no network — and runs one goal through it. HITL is disabled so the run
completes to END; with it enabled the run would pause at the human-approval
interrupt after a passing synthesis.

Run::

    python examples/blind_review_pipeline.py
"""

from __future__ import annotations

import asyncio
import os

# Disable the human-in-the-loop gate so the demo completes without an interrupt.
# (A real deployment leaves PRISMAL_HITL_ENABLED=true and resumes the interrupt
# with Command(resume={"action": "approve"}).)
os.environ.setdefault("PRISMAL_HITL_ENABLED", "false")

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver

from prismal.agents.subgraphs.blind_review_pipeline import (
    build_blind_review_pipeline_subgraph,
)
from prismal.agents.subgraphs.code_review.types import CodeIssue, CodeReviewReport
from prismal.agents.subgraphs.factory import assemble_state_graph

GOAL = "Write a function that parses a CSV file into a list of dicts"


async def fake_spec(goal: str) -> str:
    """Deterministic stand-in for the spec agent's LLM call."""
    return (
        "SPEC: parse_csv(path) -> list[dict]. Read the file, treat the first "
        "row as headers, map each subsequent row to a dict. Raise on a missing "
        "file. Cover the empty-file case."
    )


async def fake_implement(spec: str, prior_issues: list[CodeIssue] | None) -> str:
    """Deterministic stand-in for the implementer agent's LLM call."""
    note = " (revised to address reviewer issues)" if prior_issues else ""
    return f"def parse_csv(path):{note}\n    ...  # implementation matching the spec"


async def fake_reviewer_a(spec: str, artifact: str) -> CodeReviewReport:
    """Blind reviewer A — a correctness lens. Sees only spec + artifact."""
    return CodeReviewReport(summary="looks correct", score=0.9, approved=True)


async def fake_reviewer_b(spec: str, artifact: str) -> CodeReviewReport:
    """Blind reviewer B — a robustness lens. Flags a minor edge case."""
    return CodeReviewReport(
        issues=[
            CodeIssue(
                severity="low",
                category="logic",
                description="empty-file case not explicitly handled",
                file="parse_csv.py",
                line=1,
                suggestion="return [] for an empty file",
            )
        ],
        summary="minor edge case",
        score=0.85,
        approved=True,
    )


async def main() -> None:
    """Build the subgraph with fakes, run one review, print the outcome."""
    definition = build_blind_review_pipeline_subgraph(
        spec_fn=fake_spec,
        implementer_fn=fake_implement,
        reviewer_a_fn=fake_reviewer_a,
        reviewer_b_fn=fake_reviewer_b,
    )
    graph = assemble_state_graph(definition).compile(checkpointer=MemorySaver())

    result = await graph.ainvoke(
        {"messages": [HumanMessage(content=GOAL)], "metadata": {}},
        config={"configurable": {"thread_id": "demo"}},
    )

    br = result["metadata"]["blind_review"]
    synthesis = br["synthesis"]
    print(f"Goal: {GOAL}\n")
    print(f"Spec artifact:\n  {br['spec_artifact']}\n")
    print(f"Implementation artifact:\n  {br['implementation_artifact']}\n")
    print(
        f"Reviewer A: score={br['reviewer_a_verdict'].score} "
        f"approved={br['reviewer_a_verdict'].approved}"
    )
    print(
        f"Reviewer B: score={br['reviewer_b_verdict'].score} "
        f"approved={br['reviewer_b_verdict'].approved}"
    )
    print(
        f"\nSynthesis: score={synthesis['report']['score']} "
        f"approved={synthesis['report']['approved']} "
        f"agreement={synthesis['agreement']}"
    )
    print(f"Merged issues: {[i.description for i in synthesis['report']['issues']]}")


if __name__ == "__main__":
    asyncio.run(main())

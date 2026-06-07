"""End-to-end Kokoro deliberation demo with injected fakes (Fase K).

Builds the kokoro subgraph (load_souls → deliberate → judge → act → output)
with deterministic persona/judge backends — no LLM, no network — and runs a
question through the three default souls (spirit 魂 / mind 知 / heart 情).

Run::

    python examples/kokoro_deliberation.py
"""

from __future__ import annotations

import asyncio
import json

from langchain_core.messages import HumanMessage

from prismal.agents.subgraphs.factory import assemble_state_graph
from prismal.agents.subgraphs.kokoro import build_kokoro_subgraph

QUERY = "Should we migrate the monolith to microservices this quarter?"

# Each soul answers through the same injected backend; a real deployment would
# omit generate_fn so each SoulAgent lazily wires ProviderRegistry().get_llm().
_PERSONA_REPLIES = iter(
    [
        # round 1 — spirit, mind, heart (concurrent, deterministic order)
        "Only if we can migrate without breaking our reliability promises.",
        "The migration cost is high; evidence supports a phased approach.",
        "The team is already stretched; a big-bang switch would hurt morale.",
    ]
)


async def fake_persona(messages: list[dict[str, str]]) -> str:
    """Deterministic stand-in for the per-soul LLM call."""
    return next(_PERSONA_REPLIES, "We agree on a phased migration.")


async def fake_judge(messages: list[dict[str, str]]) -> str:
    """Deterministic stand-in for the judge LLM call (returns Verdict JSON)."""
    return json.dumps(
        {
            "decision": "Adopt a phased migration starting with one bounded context.",
            "rationale": (
                "Spirit's reliability principles, Mind's cost evidence, and "
                "Heart's team-capacity signal all converge on incremental change."
            ),
            "lens_summaries": {
                "spirit": "weighed reliability promises",
                "mind": "weighed cost/evidence of phasing",
                "heart": "weighed team morale and capacity",
            },
            "dissent_retained": [],
        }
    )


async def main() -> None:
    """Build the subgraph with fakes, run one deliberation, print the verdict."""
    definition = build_kokoro_subgraph(generate_fn=fake_persona, judge_fn=fake_judge)
    graph = assemble_state_graph(definition).compile()

    result = await graph.ainvoke({"messages": [HumanMessage(content=QUERY)]})

    kokoro = result["metadata"]["kokoro"]
    deliberation = kokoro["deliberation"]
    verdict = kokoro["verdict"]

    print(f"Query: {QUERY}\n")
    print(f"Souls convened: {', '.join(kokoro['soul_ids'])}")
    print(
        f"Deliberation: {deliberation.rounds_completed} round(s), "
        f"agreement={deliberation.agreement_score:.2f}, "
        f"converged={deliberation.converged}\n"
    )
    for position in deliberation.final_positions:
        print(f"  [{position.agent_id} / {position.role}] {position.content}")
    print(f"\nVerdict: {verdict.decision}")
    print(f"Rationale: {verdict.rationale}\n")
    print("Final assistant message:")
    print(result["messages"][-1].content)


if __name__ == "__main__":
    asyncio.run(main())

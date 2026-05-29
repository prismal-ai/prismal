"""Build and run a subgraph end-to-end with PrismalStateGraphBuilder.

Run::

    python examples/extension/custom_subgraph.py
"""

from __future__ import annotations

import asyncio

from langchain_core.messages import AIMessage, HumanMessage

from prismal.agents.extension import PrismalStateGraphBuilder
from prismal.agents.state import create_initial_state


async def classify(state: dict) -> dict:
    text = state["messages"][-1].content
    return {"metadata": {"classify": {"len": len(text)}}}


async def respond(state: dict) -> dict:
    return {"messages": [AIMessage(content="Handled by the custom subgraph.")]}


async def main() -> None:
    builder = PrismalStateGraphBuilder("demo_pipeline")
    builder.add_node("classify", classify, capabilities=["general"])
    builder.add_node("respond", respond)
    builder.add_edge("classify", "respond")
    builder.add_edge("respond", "__end__")
    builder.set_entry_point("classify")

    # compile() -> SubgraphDefinition (registrable); compile_raw() -> runnable graph.
    compiled = builder.compile_raw()

    state = create_initial_state(session_id="demo")
    state["messages"] = [HumanMessage(content="Please summarise this.")]
    result = await compiled.ainvoke(state)
    print("final message:", result["messages"][-1].content)


if __name__ == "__main__":
    asyncio.run(main())

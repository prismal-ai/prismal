"""Adapt an existing LangChain Runnable into a prismal node.

Run::

    python examples/extension/langchain_migration.py
"""

from __future__ import annotations

import asyncio

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableLambda

from prismal.agents.extension import LangChainRunnableAdapter
from prismal.agents.state import create_initial_state


async def main() -> None:
    # Pretend this is your existing LangChain chain / AgentExecutor.
    legacy_chain = RunnableLambda(
        lambda msgs: AIMessage(content=f"legacy says: {msgs[-1].content}")
    )

    adapter = LangChainRunnableAdapter(legacy_chain, input_mapping="messages")
    node = adapter.as_node(name="legacy_research", capabilities=["research"])

    state = create_initial_state(session_id="demo")
    state["messages"] = [HumanMessage(content="hello from prismal")]
    update = await node(state)
    print("node output:", update["messages"][-1].content)


if __name__ == "__main__":
    asyncio.run(main())

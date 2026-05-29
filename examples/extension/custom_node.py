"""Hello-world ``@prismal_node`` with a mocked classifier.

Run::

    python examples/extension/custom_node.py
"""

from __future__ import annotations

import asyncio

from langchain_core.messages import HumanMessage

from prismal.agents.extension import prismal_node
from prismal.agents.state import create_initial_state


@prismal_node(name="sentiment_classifier", capabilities=["general"])
async def sentiment_classifier(state: dict) -> dict:
    """Classify the last user message (mocked — no LLM call)."""
    text = state["messages"][-1].content.lower()
    label = "positive" if any(w in text for w in ("great", "love", "good")) else "neutral"
    return {"metadata": {"sentiment_classifier": {"label": label}}}


async def main() -> None:
    state = create_initial_state(session_id="demo")
    state["messages"] = [HumanMessage(content="I love this framework")]

    update = await sentiment_classifier(state)
    print("state_update:", update)
    print("metadata:", sentiment_classifier.__prismal_node__)


if __name__ == "__main__":
    asyncio.run(main())

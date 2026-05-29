"""Standalone nodes contributed by the plugin (``prismal.nodes`` group)."""

from __future__ import annotations

from prismal.agents.extension import prismal_node


@prismal_node(name="example_classifier", capabilities=["general"])
async def example_classifier(state: dict) -> dict:
    """Classify the last user message (replace with your own logic)."""
    text = state["messages"][-1].content
    return {"metadata": {"example_classifier": {"length": len(text)}}}

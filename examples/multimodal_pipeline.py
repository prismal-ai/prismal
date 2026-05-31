"""End-to-end multimodal pipeline demo with mocked providers (Fase F).

Builds the multimodal subgraph, injects a fake vision agent (no VLM / network),
and runs an image through router → vision_node → fusion_node →
output_formatter_node.

Run::

    python examples/multimodal_pipeline.py
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from langchain_core.messages import HumanMessage

from prismal.agents.multimodal import VisionResult
from prismal.agents.subgraphs.multimodal_pipeline import build_multimodal_subgraph

# 1×1 transparent PNG-ish header (enough for MediaValidator magic-byte sniffing).
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def _fake_vision_agent() -> AsyncMock:
    """A VisionAgent stand-in that returns a fixed caption (no VLM call)."""
    agent = AsyncMock()
    agent.analyze = AsyncMock(
        return_value=VisionResult(
            description="A golden retriever running on a sandy beach.",
            objects=[],
            ocr_text=None,
            model_used="mock-vlm",
        )
    )
    return agent


async def main() -> None:
    definition = build_multimodal_subgraph(
        vision_agent=_fake_vision_agent(),
        fusion_strategy="concat",  # deterministic, no LLM
    )

    # Caller supplies the media under metadata.mm.media and an attachment hint.
    state: dict = {
        "messages": [
            HumanMessage(
                content="What is in this image?",
                additional_kwargs={"attachments": [{"mime_type": "image/png"}]},
            )
        ],
        "metadata": {"mm": {"media": PNG, "preferred_output": "text"}},
    }

    # Drive the nodes the way the compiled subgraph would: router → modal → fusion → output.
    state.update(await definition.nodes["router"](state))
    next_node = definition.conditional_edges["router"](state)
    print("router → ", next_node)

    state["metadata"].update((await definition.nodes[next_node](state))["metadata"])
    state["metadata"].update((await definition.nodes["fusion_node"](state))["metadata"])
    out = await definition.nodes["output_formatter_node"](state)

    print("final answer:", out["messages"][-1].content)


if __name__ == "__main__":
    asyncio.run(main())

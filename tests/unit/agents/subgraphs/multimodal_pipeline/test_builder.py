"""Tests for the multimodal_pipeline subgraph (Fase F, SPEC-MM-SUB-001)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

from langchain_core.messages import AIMessage, HumanMessage

from prismal.agents.multimodal.audio_agent import AudioResult
from prismal.agents.multimodal.video_agent import VideoResult
from prismal.agents.multimodal.vision_agent import VisionResult
from prismal.agents.subgraphs.multimodal_pipeline import (
    build_multimodal_subgraph,
    register_multimodal_pipeline,
)
from prismal.agents.subgraphs.registry import SubgraphDefinition, SubgraphRegistry

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def _vision_agent(desc: str = "a dog") -> AsyncMock:
    agent = AsyncMock()
    agent.analyze = AsyncMock(
        return_value=VisionResult(description=desc, objects=[], ocr_text=None, model_used="vlm")
    )
    return agent


def _audio_agent(text: str = "spoken reply") -> AsyncMock:
    agent = AsyncMock()
    agent.process = AsyncMock(
        return_value=AudioResult(
            transcript="hi",
            response_text=text,
            response_audio=None,
            response_mime=None,
            stt_provider_used="openai",
            tts_provider_used=None,
            duration_s=1.0,
        )
    )
    return agent


def _video_agent(summary: str = "a clip") -> AsyncMock:
    agent = AsyncMock()
    agent.summarize = AsyncMock(
        return_value=VideoResult(
            transcript="t", frame_descriptions=[], summary=summary,
            total_frames_processed=3, duration_s=3.0,
        )
    )
    return agent


class TestBuilder:
    def test_returns_subgraph_definition(self) -> None:
        definition = build_multimodal_subgraph(vision_agent=_vision_agent())
        assert isinstance(definition, SubgraphDefinition)
        assert definition.entry_point == "router"
        for node in ("router", "vision_node", "audio_node", "video_node", "fusion_node",
                     "output_formatter_node"):
            assert node in definition.nodes

    def test_router_has_conditional_edge(self) -> None:
        definition = build_multimodal_subgraph(vision_agent=_vision_agent())
        assert "router" in definition.conditional_edges

    def test_modal_nodes_flow_to_fusion(self) -> None:
        definition = build_multimodal_subgraph(vision_agent=_vision_agent())
        assert ("vision_node", "fusion_node") in definition.edges
        assert ("fusion_node", "output_formatter_node") in definition.edges


class TestRouterNode:
    async def test_router_classifies_and_routing_fn_picks_node(self) -> None:
        definition = build_multimodal_subgraph(vision_agent=_vision_agent())
        router = definition.nodes["router"]
        state = {
            "messages": [HumanMessage(content="look", additional_kwargs={
                "attachments": [{"mime_type": "image/png"}]})],
            "metadata": {"mm": {"media": PNG}},
        }
        update = await router(state)
        merged = {**state, **update}
        route = definition.conditional_edges["router"]
        assert route(merged) == "vision_node"


class TestVisionNode:
    async def test_vision_node_produces_contribution(self) -> None:
        definition = build_multimodal_subgraph(vision_agent=_vision_agent("a husky"))
        vision_node = definition.nodes["vision_node"]
        state = {"messages": [], "metadata": {"mm": {"media": PNG}}}
        update = await vision_node(state)
        contribs = update["metadata"]["mm"]["contributions"]
        assert len(contribs) == 1
        assert contribs[0]["content"] == "a husky"
        assert contribs[0]["modality"] == "image"


class TestFusionNode:
    async def test_fusion_node_combines_contributions(self) -> None:
        definition = build_multimodal_subgraph(
            vision_agent=_vision_agent(), fusion_strategy="concat"
        )
        fusion_node = definition.nodes["fusion_node"]
        state = {
            "messages": [],
            "metadata": {
                "mm": {
                    "contributions": [
                        {"modality": "image", "content": "a dog", "agent_id": "vision_agent",
                         "confidence": 0.9}
                    ]
                }
            },
        }
        update = await fusion_node(state)
        assert "a dog" in update["metadata"]["mm"]["fusion"]["answer"]


class TestOutputFormatter:
    async def test_text_output_emits_ai_message(self) -> None:
        definition = build_multimodal_subgraph(vision_agent=_vision_agent())
        formatter = definition.nodes["output_formatter_node"]
        state = {
            "messages": [],
            "metadata": {"mm": {"fusion": {"answer": "final answer"}, "preferred_output": "text"}},
        }
        update = await formatter(state)
        assert isinstance(update["messages"][-1], AIMessage)
        assert update["messages"][-1].content == "final answer"


class TestModalNodes:
    async def test_audio_node_produces_contribution(self) -> None:
        definition = build_multimodal_subgraph(
            vision_agent=_vision_agent(), audio_agent=_audio_agent("hello back")
        )
        update = await definition.nodes["audio_node"](
            {"messages": [], "metadata": {"mm": {"media": b"RIFF....WAVE"}}}
        )
        contribs = update["metadata"]["mm"]["contributions"]
        assert contribs[0]["content"] == "hello back"
        assert contribs[0]["modality"] == "audio"

    async def test_video_node_produces_contribution(self, tmp_path: Path) -> None:
        clip = tmp_path / "v.mp4"
        clip.write_bytes(b"\x00\x00\x00\x18ftypmp42")
        definition = build_multimodal_subgraph(
            vision_agent=_vision_agent(), video_agent=_video_agent("a meeting")
        )
        update = await definition.nodes["video_node"](
            {"messages": [], "metadata": {"mm": {"media": str(clip)}}}
        )
        assert update["metadata"]["mm"]["contributions"][0]["content"] == "a meeting"

    async def test_text_node_passthrough(self) -> None:
        definition = build_multimodal_subgraph(vision_agent=_vision_agent())
        update = await definition.nodes["text_node"](
            {"messages": [HumanMessage(content="just text")], "metadata": {}}
        )
        assert update["metadata"]["mm"]["contributions"][0]["content"] == "just text"

    async def test_node_without_media_yields_empty_contribution(self) -> None:
        definition = build_multimodal_subgraph(vision_agent=_vision_agent())
        update = await definition.nodes["vision_node"](
            {"messages": [], "metadata": {"mm": {}}}
        )
        assert update["metadata"]["mm"]["contributions"][0]["content"] == ""


class TestRouterEdgeCases:
    async def test_empty_messages_routes_to_text(self) -> None:
        definition = build_multimodal_subgraph(vision_agent=_vision_agent())
        update = await definition.nodes["router"]({"messages": [], "metadata": {}})
        merged = {"metadata": update["metadata"]}
        assert definition.conditional_edges["router"](merged) == "text_node"

    async def test_force_modality(self) -> None:
        definition = build_multimodal_subgraph(vision_agent=_vision_agent())
        state = {"messages": [HumanMessage(content="x")],
                 "metadata": {"mm": {"force_modality": "audio"}}}
        update = await definition.nodes["router"](state)
        assert definition.conditional_edges["router"]({"metadata": update["metadata"]}) \
            == "audio_node"


class TestOutputFormatterJson:
    async def test_json_output(self) -> None:
        definition = build_multimodal_subgraph(vision_agent=_vision_agent())
        update = await definition.nodes["output_formatter_node"](
            {"messages": [], "metadata": {"mm": {"fusion": {"answer": "A"},
                                                 "preferred_output": "json"}}}
        )
        payload = json.loads(update["messages"][-1].content)
        assert payload["answer"] == "A"


class TestDefaults:
    def test_build_with_default_agents(self) -> None:
        # No injected agents — exercises lazy default construction (no network).
        definition = build_multimodal_subgraph()
        assert isinstance(definition, SubgraphDefinition)
        assert len(definition.nodes) == 7


class TestRegister:
    def test_register_is_idempotent(self) -> None:
        registry = SubgraphRegistry()
        register_multimodal_pipeline(registry, vision_agent=_vision_agent())
        register_multimodal_pipeline(registry, vision_agent=_vision_agent())  # no raise
        assert registry.get("multimodal_pipeline") is not None
        assert "multimodal_pipeline" in registry.list()

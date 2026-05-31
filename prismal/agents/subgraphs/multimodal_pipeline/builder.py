"""Builder for the multimodal_pipeline subgraph (Fase F, SPEC-MM-SUB-001).

Pipeline shape::

    router ─┬─> vision_node ─┐
            ├─> audio_node  ─┤
            ├─> video_node  ─┼─> fusion_node -> output_formatter_node
            └─> text_node   ─┘

The ``router`` node classifies the input modality (via
:func:`classify_modality`) and stores the decision under
``state["metadata"]["mm"]["router"]``; a conditional edge then dispatches to the
matching modal node. Each modal node appends a ``ModalContribution`` to
``state["metadata"]["mm"]["contributions"]``; ``fusion_node`` combines them and
``output_formatter_node`` renders the final output (text / tts / json).

Input media is read from ``state["metadata"]["mm"]["media"]`` (bytes or path).

Note: ``MIXED``/``UNKNOWN`` modalities route to ``text_node`` in this version;
full parallel multi-modal fan-out is a planned enhancement.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from langchain_core.messages import AIMessage

from prismal.agents.multimodal.modality_router import Modality, classify_modality
from prismal.agents.multimodal.multimodal_fusion import (
    ModalContribution,
    MultimodalFusion,
)
from prismal.agents.subgraphs.registry import SubgraphDefinition, SubgraphRegistry
from prismal.core.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from prismal.agents.multimodal.audio_agent import AudioAgent
    from prismal.agents.multimodal.video_agent import VideoAgent
    from prismal.agents.multimodal.vision_agent import VisionAgent
    from prismal.core.config import Settings

logger = get_logger("prismal.subgraphs.multimodal_pipeline.builder")

_NAME = "multimodal_pipeline"
_DESCRIPTION = "Multimodal pipeline: router → [vision|audio|video|text] → fusion → output_formatter"

_MODALITY_TO_NODE: dict[str, str] = {
    Modality.IMAGE.value: "vision_node",
    Modality.AUDIO.value: "audio_node",
    Modality.VIDEO.value: "video_node",
    Modality.TEXT.value: "text_node",
    Modality.MIXED.value: "text_node",
    Modality.UNKNOWN.value: "text_node",
}


def _merge_mm(state: dict[str, Any], **updates: Any) -> dict[str, Any]:
    """Return a state update that merges *updates* into ``metadata.mm``."""
    meta = dict(state.get("metadata") or {})
    mm = dict(meta.get("mm") or {})
    mm.update(updates)
    meta["mm"] = mm
    return {"metadata": meta}


def _append_contribution(
    state: dict[str, Any], contribution: dict[str, Any], **extra: Any
) -> dict[str, Any]:
    """Merge a contribution (and extra mm keys) into ``metadata.mm``."""
    existing = list((state.get("metadata") or {}).get("mm", {}).get("contributions") or [])
    existing.append(contribution)
    return _merge_mm(state, contributions=existing, **extra)


def _media_of(state: dict[str, Any]) -> Any:
    """Resolve the primary input media from ``metadata.mm.media``.

    Supports both the ingestion-contract form (a list of descriptors → the
    ``primary_media_index`` entry's ``uri`` as a :class:`~pathlib.Path`) and the
    legacy/direct form (raw ``bytes`` or ``Path``).
    """
    mm = (state.get("metadata") or {}).get("mm", {})
    media = mm.get("media")
    if isinstance(media, list):
        if not media:
            return None
        index = mm.get("primary_media_index", 0)
        if not isinstance(index, int) or not 0 <= index < len(media):
            index = 0
        descriptor = media[index]
        uri = descriptor.get("uri") if isinstance(descriptor, dict) else descriptor
        return Path(uri) if isinstance(uri, str) else uri
    return media


def _make_router_node(
    settings: Settings | None,
) -> Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]:
    async def router_node(state: dict[str, Any]) -> dict[str, Any]:
        messages = state.get("messages") or []
        forced = (state.get("metadata") or {}).get("mm", {}).get("force_modality")
        if forced:
            modality = Modality(forced)
            confidence = 1.0
        elif messages:
            classification = classify_modality(messages[-1], settings=settings)
            modality, confidence = classification.modality, classification.confidence
        else:
            modality, confidence = Modality.TEXT, 1.0
        logger.info("mm_router", modality=modality.value)
        return _merge_mm(
            state,
            router={"modality": modality.value, "confidence": confidence},
        )

    return router_node


def _route_from_router(state: dict[str, Any]) -> str:
    """Conditional edge: pick the modal node from the stored classification."""
    modality = (state.get("metadata") or {}).get("mm", {}).get("router", {}).get("modality")
    return _MODALITY_TO_NODE.get(str(modality), "text_node")


def _make_vision_node(
    vision_agent: VisionAgent,
) -> Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]:
    async def vision_node(state: dict[str, Any]) -> dict[str, Any]:
        media = _media_of(state)
        if media is None:
            return _append_contribution(
                state,
                {"modality": "image", "content": "", "agent_id": "vision_agent", "confidence": 0.0},
            )
        result = await vision_agent.analyze(media)
        return _append_contribution(
            state,
            {
                "modality": "image",
                "content": result.description,
                "agent_id": "vision_agent",
                "confidence": 0.0 if result.used_fallback else 0.9,
            },
            vision={"description": result.description, "ocr_text": result.ocr_text},
        )

    return vision_node


def _make_audio_node(
    audio_agent: AudioAgent,
) -> Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]:
    async def audio_node(state: dict[str, Any]) -> dict[str, Any]:
        media = _media_of(state)
        if media is None:
            return _append_contribution(
                state,
                {"modality": "audio", "content": "", "agent_id": "audio_agent", "confidence": 0.0},
            )
        result = await audio_agent.process(media, state=state)
        content = result.response_text or result.transcript
        return _append_contribution(
            state,
            {"modality": "audio", "content": content, "agent_id": "audio_agent", "confidence": 0.9},
            audio={"transcript": result.transcript, "response_text": result.response_text},
        )

    return audio_node


def _make_video_node(
    video_agent: VideoAgent,
) -> Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]:
    async def video_node(state: dict[str, Any]) -> dict[str, Any]:
        media = _media_of(state)
        if not isinstance(media, (str, Path)):
            return _append_contribution(
                state,
                {"modality": "video", "content": "", "agent_id": "video_agent", "confidence": 0.0},
            )
        result = await video_agent.summarize(Path(media))
        return _append_contribution(
            state,
            {
                "modality": "video",
                "content": result.summary,
                "agent_id": "video_agent",
                "confidence": 0.9,
            },
            video={"summary": result.summary, "frames": result.total_frames_processed},
        )

    return video_node


async def _text_node(state: dict[str, Any]) -> dict[str, Any]:
    """Passthrough: treat the last message text as a contribution."""
    messages = state.get("messages") or []
    text = ""
    if messages:
        content = messages[-1].content
        text = content if isinstance(content, str) else ""
    return _append_contribution(
        state,
        {"modality": "text", "content": text, "agent_id": "text", "confidence": 1.0},
    )


def _make_fusion_node(
    fusion: MultimodalFusion,
) -> Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]:
    async def fusion_node(state: dict[str, Any]) -> dict[str, Any]:
        raw = (state.get("metadata") or {}).get("mm", {}).get("contributions") or []
        contributions = [
            ModalContribution(
                modality=Modality(c["modality"]),
                content=c["content"],
                agent_id=c["agent_id"],
                confidence=c["confidence"],
            )
            for c in raw
        ]
        result = await fusion.combine(contributions)
        return _merge_mm(
            state,
            fusion={"answer": result.answer, "strategy_used": result.strategy_used},
        )

    return fusion_node


async def _output_formatter_node(state: dict[str, Any]) -> dict[str, Any]:
    """Render the fused answer per ``metadata.mm.preferred_output``."""
    mm = (state.get("metadata") or {}).get("mm", {})
    answer = mm.get("fusion", {}).get("answer", "")
    preferred = mm.get("preferred_output", "text")
    if preferred == "json":
        payload = json.dumps(
            {"answer": answer, "mm": {k: v for k, v in mm.items() if k != "media"}}
        )
        return {"messages": [AIMessage(content=payload)]}
    # "text" and "audio" both surface the text answer here; TTS bytes (if any)
    # remain available under metadata.mm for the caller to play.
    return {"messages": [AIMessage(content=answer)]}


def build_multimodal_subgraph(
    *,
    vision_agent: VisionAgent | None = None,
    audio_agent: AudioAgent | None = None,
    video_agent: VideoAgent | None = None,
    fusion: MultimodalFusion | None = None,
    fusion_strategy: Literal["moa", "moderator", "concat"] = "moderator",
    settings: Settings | None = None,
) -> SubgraphDefinition:
    """Build the multimodal pipeline :class:`SubgraphDefinition`.

    Agents/fusion are injected for testability; defaults are constructed lazily.
    """
    if vision_agent is None:
        from prismal.agents.multimodal.vision_agent import VisionAgent

        vision_agent = VisionAgent(settings=settings)
    if audio_agent is None:
        from prismal.agents.multimodal.audio_agent import AudioAgent

        audio_agent = AudioAgent(settings=settings)
    if video_agent is None:
        from prismal.agents.multimodal.video_agent import VideoAgent

        video_agent = VideoAgent(settings=settings)
    if fusion is None:
        fusion = MultimodalFusion(strategy=fusion_strategy, settings=settings)

    definition = SubgraphDefinition(
        name=_NAME,
        description=_DESCRIPTION,
        entry_point="router",
        nodes={
            "router": _make_router_node(settings),
            "vision_node": _make_vision_node(vision_agent),
            "audio_node": _make_audio_node(audio_agent),
            "video_node": _make_video_node(video_agent),
            "text_node": _text_node,
            "fusion_node": _make_fusion_node(fusion),
            "output_formatter_node": _output_formatter_node,
        },
        edges=[
            ("vision_node", "fusion_node"),
            ("audio_node", "fusion_node"),
            ("video_node", "fusion_node"),
            ("text_node", "fusion_node"),
            ("fusion_node", "output_formatter_node"),
        ],
        conditional_edges={"router": _route_from_router},
    )
    logger.info("multimodal_subgraph_built", nodes=list(definition.nodes.keys()))
    return definition


def register_multimodal_pipeline(
    registry: SubgraphRegistry | None = None,
    *,
    settings: Settings | None = None,
    **build_kwargs: Any,
) -> None:
    """Idempotently register the multimodal pipeline (mirrors register_ml_pipeline)."""
    registry = registry or SubgraphRegistry.get_instance()
    if registry.get(_NAME) is not None:
        logger.debug("multimodal_pipeline_already_registered")
        return
    definition = build_multimodal_subgraph(settings=settings, **build_kwargs)
    registry.register_sync(_NAME, definition)


__all__ = [
    "build_multimodal_subgraph",
    "register_multimodal_pipeline",
]

"""Modality router (Fase F, SPEC-MM-AGT-004).

Deterministic, LLM-free classification of an incoming message's modality based
on attachment MIME types and content blocks, with an intent-regex fallback.
``make_modality_router_node`` adapts the classifier into a LangGraph node that
routes to the appropriate modal agent and records the decision under
``state["metadata"]["mm"]["router"]``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from prismal.core.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from langchain_core.messages import AnyMessage

    from prismal.core.config import Settings

logger = get_logger("prismal.agents.multimodal.modality_router")


class Modality(StrEnum):
    """Input modality of a message."""

    TEXT = "text"
    AUDIO = "audio"
    IMAGE = "image"
    VIDEO = "video"
    MIXED = "mixed"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ModalityClassification:
    """Result of classifying a message's modality."""

    modality: Modality
    confidence: float
    detected_attachments: list[str]
    used_fallback_llm: bool = False


# Modality of a node destination, keyed by the classified modality.
_MODALITY_TO_NODE: dict[Modality, str] = {
    Modality.IMAGE: "vision_agent",
    Modality.AUDIO: "audio_agent",
    Modality.VIDEO: "video_agent",
    Modality.MIXED: "fusion",
    Modality.TEXT: "text",
}

_MIME_PREFIX_TO_MODALITY: dict[str, Modality] = {
    "image/": Modality.IMAGE,
    "audio/": Modality.AUDIO,
    "video/": Modality.VIDEO,
}

_BLOCK_TYPE_TO_MODALITY: dict[str, Modality] = {
    "image_url": Modality.IMAGE,
    "image": Modality.IMAGE,
    "input_audio": Modality.AUDIO,
    "audio": Modality.AUDIO,
    "video": Modality.VIDEO,
    "video_url": Modality.VIDEO,
}

_INTENT_PATTERNS: tuple[tuple[Modality, re.Pattern[str]], ...] = (
    (Modality.AUDIO, re.compile(r"(?i)\b(transcrib\w*|voice|voz|audio|speech)\b")),
    (Modality.VIDEO, re.compile(r"(?i)\b(video|vídeo|clip|footage)\b")),
    (Modality.IMAGE, re.compile(r"(?i)\b(image|imagen|picture|foto|photo|screenshot)\b")),
)


def _mime_to_modality(mime: str) -> Modality | None:
    """Map a MIME type to its modality, or None if unrecognised."""
    for prefix, modality in _MIME_PREFIX_TO_MODALITY.items():
        if mime.startswith(prefix):
            return modality
    return None


def _collect_from_attachments(message: AnyMessage) -> tuple[set[Modality], list[str]]:
    """Inspect ``additional_kwargs['attachments']`` for MIME-typed attachments."""
    modalities: set[Modality] = set()
    mimes: list[str] = []
    attachments = getattr(message, "additional_kwargs", {}).get("attachments", [])
    if not isinstance(attachments, list):
        return modalities, mimes
    for item in attachments:
        mime = item.get("mime_type") if isinstance(item, dict) else None
        if not mime:
            continue
        mimes.append(mime)
        modality = _mime_to_modality(mime)
        if modality is not None:
            modalities.add(modality)
    return modalities, mimes


def _collect_from_content_blocks(message: AnyMessage) -> set[Modality]:
    """Inspect a list-style ``content`` for multimodal blocks."""
    modalities: set[Modality] = set()
    content = getattr(message, "content", None)
    if not isinstance(content, list):
        return modalities
    for block in content:
        block_type = block.get("type") if isinstance(block, dict) else None
        if block_type in _BLOCK_TYPE_TO_MODALITY:
            modalities.add(_BLOCK_TYPE_TO_MODALITY[block_type])
    return modalities


def _text_of(message: AnyMessage) -> str:
    """Best-effort plain-text extraction from a message."""
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        return " ".join(parts)
    return ""


def classify_modality(
    message: AnyMessage,
    *,
    settings: Settings | None = None,
) -> ModalityClassification:
    """Classify a message's modality without any LLM call.

    Order: attachment MIME types → content blocks → intent regex → TEXT.

    Returns:
        A :class:`ModalityClassification`. Returns ``Modality.UNKNOWN`` with
        ``confidence=0.0`` only when nothing matched and the text was empty.
    """
    del settings  # reserved for future tuning
    modalities, mimes = _collect_from_attachments(message)
    modalities |= _collect_from_content_blocks(message)

    if len(modalities) > 1:
        return ModalityClassification(Modality.MIXED, 0.9, mimes)
    if len(modalities) == 1:
        return ModalityClassification(next(iter(modalities)), 0.95, mimes)

    text = _text_of(message)
    for modality, pattern in _INTENT_PATTERNS:
        if pattern.search(text):
            return ModalityClassification(modality, 0.6, mimes)

    if text.strip():
        return ModalityClassification(Modality.TEXT, 0.7, mimes)
    return ModalityClassification(Modality.UNKNOWN, 0.0, mimes)


async def _llm_fallback_modality(text: str, settings: Settings | None) -> Modality:
    """Ask a multimodal LLM to classify when heuristics are inconclusive."""
    from prismal.providers.multimodal import get_multimodal_llm
    from prismal.security.prompt_builder import SecurePromptBuilder

    llm = get_multimodal_llm(settings=settings)
    builder = SecurePromptBuilder()
    messages = builder.build(
        system="Classify the user request modality. Answer with exactly one word: "
        "text, audio, image, or video.",
        user=text,
    )
    from langchain_core.messages import HumanMessage, SystemMessage

    response = await llm.ainvoke(
        [
            SystemMessage(content=messages[0]["content"]),
            HumanMessage(content=messages[1]["content"]),
        ]
    )
    answer = str(response.content).strip().lower()
    for modality in (Modality.AUDIO, Modality.IMAGE, Modality.VIDEO, Modality.TEXT):
        if modality.value in answer:
            return modality
    return Modality.TEXT


def make_modality_router_node(
    *,
    use_llm_fallback: bool = False,
    settings: Settings | None = None,
) -> Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]:
    """Build a LangGraph node that routes a message to the right modal agent.

    The node returns ``{"next": "<node>", "metadata": {"mm": {"router": {...}}}}``.
    ``state["metadata"]["mm"]["force_modality"]`` overrides classification.

    Args:
        use_llm_fallback: On ``Modality.UNKNOWN``, ask a multimodal LLM.
        settings: Injectable settings.
    """

    async def router_node(state: dict[str, Any]) -> dict[str, Any]:
        messages = state.get("messages") or []
        mm_meta = state.get("metadata", {}).get("mm", {})
        forced = mm_meta.get("force_modality")
        used_fallback = False

        if forced:
            modality = Modality(forced)
            classification = ModalityClassification(modality, 1.0, [])
        elif not messages:
            classification = ModalityClassification(Modality.TEXT, 1.0, [])
        else:
            classification = classify_modality(messages[-1], settings=settings)
            if classification.modality is Modality.UNKNOWN and use_llm_fallback:
                modality = await _llm_fallback_modality(_text_of(messages[-1]), settings)
                classification = ModalityClassification(modality, 0.5, [], used_fallback_llm=True)
                used_fallback = True

        modality = classification.modality
        next_node = _MODALITY_TO_NODE.get(modality, "text")
        logger.info("modality_routed", modality=modality.value, next=next_node)
        return {
            "next": next_node,
            "metadata": {
                "mm": {
                    "router": {
                        "modality": modality.value,
                        "confidence": classification.confidence,
                        "detected_attachments": classification.detected_attachments,
                        "used_fallback_llm": used_fallback,
                    }
                }
            },
        }

    return router_node


__all__ = [
    "Modality",
    "ModalityClassification",
    "classify_modality",
    "make_modality_router_node",
]

"""Image loader (Fase F, SPEC-MM-RAG-002).

Loads an image and emits a single ``Document`` whose ``page_content`` is a VLM
caption, tagged ``modality="image"`` with the source URI.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from langchain_core.documents import Document

from prismal.core.logging import get_logger

if TYPE_CHECKING:
    from pathlib import Path

    from prismal.agents.multimodal.vision_agent import VisionAgent

logger = get_logger("prismal.rag.loaders.image_loader")


class ImageLoader:
    """Loads images and generates captions via a vision agent."""

    def __init__(self, *, vision_agent: VisionAgent | None = None) -> None:
        """Store the vision agent (built lazily when first used)."""
        self._vision_agent = vision_agent

    async def load(self, path: Path) -> list[Document]:
        """Return one captioned Document for the image at *path*."""
        agent = self._resolve_agent()
        result = await agent.analyze(path)
        caption = result.description or ""
        metadata = {
            "modality": "image",
            "source_uri": str(path),
            "source": str(path),
        }
        if result.ocr_text:
            metadata["ocr_text"] = result.ocr_text
        logger.info("image_loaded", source=str(path), caption_chars=len(caption))

        # Phase H — VLM captions and OCR text are untrusted media-derived content.
        from prismal.security.taint import Provenance, mark_untrusted_active

        mark_untrusted_active(caption, Provenance.MEDIA)
        if result.ocr_text:
            mark_untrusted_active(result.ocr_text, Provenance.MEDIA)
        return [Document(page_content=caption, metadata=metadata)]

    def _resolve_agent(self) -> VisionAgent:
        if self._vision_agent is None:
            from prismal.agents.multimodal.vision_agent import VisionAgent

            self._vision_agent = VisionAgent()
        return self._vision_agent


__all__ = ["ImageLoader"]

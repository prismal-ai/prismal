"""Vision agent (Fase F, SPEC-MM-AGT-001).

Validates an image, analyses it with a vision LLM, and optionally runs an OCR
pass. Backend calls are injected (``vision_fn`` / ``ocr_fn``) so the agent is
testable without a real VLM. Degrades gracefully by default.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from prismal.core.exceptions import VisionAgentError
from prismal.core.logging import get_logger
from prismal.monitoring.otel import OTelManager
from prismal.security.media_validator import MediaKind, MediaValidator

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from prismal.core.config import Settings

logger = get_logger("prismal.agents.multimodal.vision_agent")

_DEFAULT_PROMPT = "Describe la imagen y lista los objetos visibles."
_OCR_PROMPT = "Extract all readable text from this image. Return only the text."

VisionFn = Callable[[bytes | Path, str], Awaitable[str]]
OcrFn = Callable[[bytes | Path], Awaitable[str]]


@dataclass(frozen=True)
class DetectedObject:
    """A detected object with optional normalised bounding box."""

    label: str
    confidence: float
    bbox: tuple[float, float, float, float] | None = None


@dataclass(frozen=True)
class VisionResult:
    """Result of analysing an image."""

    description: str
    objects: list[DetectedObject] = field(default_factory=list)
    ocr_text: str | None = None
    model_used: str = ""
    used_fallback: bool = False


class VisionAgent:
    """General-purpose image analysis agent.

    Args:
        vision_fn: ``async (image, prompt) -> str``. Defaults to a VLM call.
        ocr_fn: ``async (image) -> str``. Defaults to a second VLM pass.
        media_validator: Validator run before any VLM call.
        degrade_gracefully: When True (default), validation/VLM failures yield a
            fallback ``VisionResult`` instead of raising.
        settings: Injectable settings.
    """

    def __init__(
        self,
        *,
        vision_fn: Callable[[bytes | Path, str], Awaitable[str]] | None = None,
        ocr_fn: Callable[[bytes | Path], Awaitable[str]] | None = None,
        media_validator: MediaValidator | None = None,
        degrade_gracefully: bool = True,
        settings: Settings | None = None,
    ) -> None:
        """Store collaborators and resolve the default vision model label."""
        if settings is None:
            from prismal.core.config import get_settings

            settings = get_settings()
        self._settings = settings
        self._vision_fn = vision_fn or self._make_default_vision_fn()
        self._ocr_fn = ocr_fn or self._make_default_ocr_fn()
        self._validator = media_validator or MediaValidator(settings=settings)
        self._degrade = degrade_gracefully
        self._model_label = settings.vision_model or settings.cua_vision_model or "vision-llm"

    async def analyze(
        self,
        image: bytes | Path,
        *,
        prompt: str | None = None,
        with_ocr: bool | None = None,
    ) -> VisionResult:
        """Analyse an image. Raises ``VisionAgentError`` only when not degrading."""
        otel = OTelManager()
        blob = image.read_bytes() if isinstance(image, Path) else image

        with otel.start_span("mm.vision.validate"):
            validation = self._validator.validate(blob, expected_kind=MediaKind.IMAGE)
        if not validation.ok:
            return self._fail(f"invalid image: {validation.reason}")

        effective_prompt = prompt or _DEFAULT_PROMPT
        try:
            with otel.start_span("mm.vision.analyze"):
                description = await self._vision_fn(image, effective_prompt)
        except Exception as exc:
            return self._fail(f"vision analysis failed: {exc!r}")

        ocr_text: str | None = None
        do_ocr = with_ocr if with_ocr is not None else self._settings.vision_ocr_enabled
        if do_ocr:
            try:
                with otel.start_span("mm.vision.ocr"):
                    ocr_text = await self._ocr_fn(image)
            except Exception as exc:
                if not self._degrade:
                    raise VisionAgentError(f"OCR failed: {exc!r}") from exc
                logger.warning("vision_ocr_failed", error=str(exc))

        return VisionResult(
            description=str(description).strip(),
            objects=[],
            ocr_text=ocr_text,
            model_used=self._model_label,
            used_fallback=False,
        )

    def _fail(self, reason: str) -> VisionResult:
        """Either raise or return a graceful fallback, depending on policy."""
        logger.warning("vision_agent_degraded", reason=reason)
        if not self._degrade:
            raise VisionAgentError(reason)
        return VisionResult(
            description="",
            objects=[],
            ocr_text=None,
            model_used=self._model_label,
            used_fallback=True,
        )

    def _make_default_vision_fn(self) -> Callable[[bytes | Path, str], Awaitable[str]]:
        """Build a VLM-backed vision function via ``get_vision_llm``."""

        async def _vision(image: bytes | Path, prompt: str) -> str:
            from prismal.providers.vision import get_vision_llm
            from prismal.security.prompt_builder import SecurePromptBuilder

            llm = get_vision_llm(settings=self._settings)
            builder = SecurePromptBuilder()
            safe = builder.build(system="You are a vision analysis assistant.", user=prompt)
            from langchain_core.messages import HumanMessage, SystemMessage

            content: list[str | dict[Any, Any]] = [
                {"type": "text", "text": safe[1]["content"]},
                {"type": "image_url", "image_url": {"url": _to_data_url(image)}},
            ]
            response = await llm.ainvoke(
                [SystemMessage(content=safe[0]["content"]), HumanMessage(content=content)]
            )
            return str(response.content)

        return _vision

    def _make_default_ocr_fn(self) -> Callable[[bytes | Path], Awaitable[str]]:
        """Build a VLM-backed OCR function (second pass with an OCR prompt)."""

        async def _ocr(image: bytes | Path) -> str:
            return await self._vision_fn(image, _OCR_PROMPT)

        return _ocr


def _to_data_url(image: bytes | Path) -> str:
    """Encode image bytes as a base64 data URL for vision content blocks."""
    import base64

    blob = image.read_bytes() if isinstance(image, Path) else image
    encoded = base64.b64encode(blob).decode("ascii")
    return f"data:image/png;base64,{encoded}"


__all__ = [
    "DetectedObject",
    "OcrFn",
    "VisionAgent",
    "VisionFn",
    "VisionResult",
]

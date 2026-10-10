"""LLMLingua-2 passage compressor adapter for :class:`PassageCompressorPort`.

(Phase TKE — SPEC-TKE-RAG-001)

``llmlingua`` is an optional dependency (``[compression]`` extra) imported
lazily on first use. Only prose is compressed (code / JSON / short text is
returned unchanged). A missing extra logs once (``compression_extra_missing``)
and any failure passes the text through unchanged; it never raises.
"""

from __future__ import annotations

from typing import Any

from prismal.core.logging import get_logger
from prismal.rag.context_budget import is_compressible_prose

logger = get_logger(__name__)

DEFAULT_LLMLINGUA_MODEL = "microsoft/llmlingua-2-xlm-roberta-large-meetingbank"

# Module-level "log once" guard for the missing extra (reset in tests).
_EXTRA_WARNED = False
_WARN_COUNT = 0


def _warn_missing() -> None:
    global _EXTRA_WARNED, _WARN_COUNT
    if _EXTRA_WARNED:
        return
    _EXTRA_WARNED = True
    _WARN_COUNT += 1
    logger.warning("compression_extra_missing", hint="pip install 'prismal[compression]'")


class PassageCompressor:
    """Compress prose passages with LLMLingua-2 (Phase TKE — SPEC-TKE-RAG-001)."""

    def __init__(self, model_name: str = DEFAULT_LLMLINGUA_MODEL) -> None:
        self.model_name = model_name
        self._compressor: Any = None
        self._unavailable = False

    def _load(self) -> Any:
        if self._compressor is None and not self._unavailable:
            try:
                import llmlingua  # type: ignore[import-not-found, unused-ignore]
            except ImportError:
                self._unavailable = True
                _warn_missing()
                return None
            self._compressor = llmlingua.PromptCompressor(
                model_name=self.model_name, use_llmlingua2=True
            )
        return self._compressor

    def compress(self, text: str, *, target_tokens: int) -> str:
        """Return *text* compressed toward *target_tokens*, or unchanged."""
        if not is_compressible_prose(text):
            return text
        try:
            compressor = self._load()
            if compressor is None:
                return text
            result = compressor.compress_prompt(text, target_token=target_tokens)
            compressed = result["compressed_prompt"]
        except Exception as exc:
            logger.warning("passage_compression_failed", error=type(exc).__name__)
            return text
        if not isinstance(compressed, str) or not compressed:
            return text
        return compressed


__all__ = ["DEFAULT_LLMLINGUA_MODEL", "PassageCompressor"]

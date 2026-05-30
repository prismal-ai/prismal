"""Tests for the multimodal exception hierarchy (Fase F)."""

from __future__ import annotations

import pytest

from prismal.core.exceptions import (
    AudioAgentError,
    MediaValidationError,
    MissingDependencyError,
    ModalityRouterError,
    MultimodalError,
    MultimodalFusionError,
    MultimodalRAGError,
    PrismalError,
    RAGError,
    STTError,
    TTSError,
    VideoAgentError,
    VisionAgentError,
)


class TestMultimodalExceptionHierarchy:
    """Every multimodal error must be catchable via PrismalError."""

    def test_multimodal_error_is_prismal_error(self) -> None:
        assert issubclass(MultimodalError, PrismalError)

    @pytest.mark.parametrize(
        "exc_type",
        [
            STTError,
            TTSError,
            VisionAgentError,
            AudioAgentError,
            VideoAgentError,
            ModalityRouterError,
            MultimodalFusionError,
        ],
    )
    def test_subclasses_of_multimodal_error(self, exc_type: type) -> None:
        assert issubclass(exc_type, MultimodalError)

    def test_multimodal_rag_error_is_rag_error(self) -> None:
        # RAG-flavoured multimodal failures live under the RAG hierarchy so
        # callers catching RAGError keep working.
        assert issubclass(MultimodalRAGError, RAGError)

    def test_media_validation_error_is_prismal_error(self) -> None:
        assert issubclass(MediaValidationError, PrismalError)
        # It is intentionally NOT a MultimodalError: rejection happens before
        # any agent runs.
        assert not issubclass(MediaValidationError, MultimodalError)

    def test_missing_dependency_error_is_prismal_error(self) -> None:
        assert issubclass(MissingDependencyError, PrismalError)


class TestMissingDependencyError:
    def test_carries_extra_to_install(self) -> None:
        err = MissingDependencyError(
            "open_clip_torch not installed",
            extra_to_install="multimodal-embed",
        )
        assert err.extra_to_install == "multimodal-embed"
        assert "multimodal-embed" in str(err)

    def test_is_catchable_as_prismal_error(self) -> None:
        with pytest.raises(PrismalError):
            raise MissingDependencyError("nope", extra_to_install="multimodal")

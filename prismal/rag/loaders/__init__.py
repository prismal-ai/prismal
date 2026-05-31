"""RAG document loaders (Fase F refactor).

``document_loader`` holds the original ``DocumentProcessorFactory`` (moved here
from ``rag/loaders.py``); imports of ``prismal.rag.loaders`` keep working. The
multimodal loaders (image/audio/video) are exported alongside it.
"""

from __future__ import annotations

from prismal.rag.loaders.audio_loader import AudioLoader
from prismal.rag.loaders.document_loader import (
    SUPPORTED_EXTENSIONS,
    DocumentProcessorFactory,
    UnsupportedDocumentTypeError,
)
from prismal.rag.loaders.image_loader import ImageLoader
from prismal.rag.loaders.video_loader import VideoLoader

__all__ = [
    "SUPPORTED_EXTENSIONS",
    "AudioLoader",
    "DocumentProcessorFactory",
    "ImageLoader",
    "UnsupportedDocumentTypeError",
    "VideoLoader",
]

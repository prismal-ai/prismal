"""Cross-modal (CLIP-style) embeddings wrapper (Fase F, SPEC-MM-PROV-005).

Returns a LangChain ``Embeddings`` that embeds text *and* images into a shared
space. The heavy ``open_clip_torch`` backend is an optional ``[multimodal-embed]``
extra; when it is not installed, :func:`get_cross_modal_embeddings` raises a
``MissingDependencyError`` so callers can fall back to textual captions.
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING, Any

from langchain_core.embeddings import Embeddings

from prismal.core.exceptions import MissingDependencyError

if TYPE_CHECKING:
    from collections.abc import Callable

    from prismal.core.config import Settings


def _parse_model_spec(spec: str) -> tuple[str, str]:
    """Split a ``"backend:model"`` spec; bare strings default to ``open_clip``."""
    if ":" in spec:
        backend, _, model = spec.partition(":")
        return backend, model
    return "open_clip", spec


def _to_list(tensor: Any) -> list[list[float]]:
    """Convert a torch/numpy tensor (or nested list) to a list of float lists."""
    obj = tensor
    if hasattr(obj, "detach"):
        obj = obj.detach()
    if hasattr(obj, "cpu"):
        obj = obj.cpu()
    if hasattr(obj, "numpy"):
        obj = obj.numpy()
    if hasattr(obj, "tolist"):
        obj = obj.tolist()
    return [[float(x) for x in row] for row in obj]


class CLIPEmbeddings(Embeddings):
    """Embeddings backed by a CLIP-style model with text and image encoders.

    The encoders are injected so the class is unit-testable without the heavy
    ``open_clip_torch`` dependency.

    Args:
        model: Object exposing ``encode_text(tokens)`` and ``encode_image(batch)``.
        tokenizer: Callable mapping a list of strings to model tokens.
        preprocess: Callable mapping a PIL image to a model-ready tensor.
        model_name: Human-readable model id (for logging/metadata).
    """

    def __init__(
        self,
        *,
        model: Any,
        tokenizer: Callable[[list[str]], Any],
        preprocess: Callable[[Any], Any],
        model_name: str,
    ) -> None:
        """Store the injected encoders."""
        self._model = model
        self._tokenizer = tokenizer
        self._preprocess = preprocess
        self.model_name = model_name

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts into the shared space."""
        features = self._model.encode_text(self._tokenizer(list(texts)))
        return _to_list(features)

    def embed_query(self, text: str) -> list[float]:
        """Embed a single query text."""
        return self.embed_documents([text])[0]

    def embed_image(self, image: bytes | Path) -> list[float]:
        """Embed a single image into the shared space."""
        from PIL import Image

        source = image if isinstance(image, Path) else BytesIO(image)
        with Image.open(source) as img:
            rgb = img.convert("RGB")
        batch = self._preprocess(rgb)
        batch = batch.unsqueeze(0) if hasattr(batch, "unsqueeze") else batch
        features = self._model.encode_image(batch)
        return _to_list(features)[0]


def _build_open_clip(model_name: str) -> CLIPEmbeddings:
    """Construct CLIPEmbeddings from open_clip, or raise MissingDependencyError."""
    try:
        import open_clip
    except ImportError as exc:
        raise MissingDependencyError(
            "cross-modal embeddings require open_clip_torch",
            extra_to_install="multimodal-embed",
        ) from exc
    model, _, preprocess = open_clip.create_model_and_transforms(model_name)
    tokenizer = open_clip.get_tokenizer(model_name)
    model.eval()
    return CLIPEmbeddings(
        model=model,
        tokenizer=tokenizer,
        preprocess=preprocess,
        model_name=model_name,
    )


def get_cross_modal_embeddings(
    model: str | None = None,
    *,
    settings: Settings | None = None,
) -> Embeddings:
    """Resolve a cross-modal :class:`Embeddings` (CLIP-style).

    Args:
        model: ``"backend:model"`` spec; defaults to
            ``settings.cross_modal_embedding_model``.
        settings: Injectable settings.

    Returns:
        An ``Embeddings`` instance that also exposes ``embed_image``.

    Raises:
        MissingDependencyError: If the backend's extra is not installed.
        ValueError: If the backend prefix is unsupported.
    """
    if settings is None:
        from prismal.core.config import get_settings

        settings = get_settings()
    spec = model or settings.cross_modal_embedding_model
    backend, model_name = _parse_model_spec(spec)
    if backend != "open_clip":
        raise ValueError(f"unsupported cross-modal embedding backend: {backend!r}")
    return _build_open_clip(model_name)


__all__ = [
    "CLIPEmbeddings",
    "get_cross_modal_embeddings",
]

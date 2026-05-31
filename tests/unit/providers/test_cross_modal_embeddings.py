"""Tests for cross-modal embeddings wrapper (Fase F, SPEC-MM-PROV-005)."""

from __future__ import annotations

from io import BytesIO

import numpy as np
import pytest

from prismal.core.exceptions import MissingDependencyError
from prismal.providers.cross_modal_embeddings import (
    CLIPEmbeddings,
    _parse_model_spec,
    get_cross_modal_embeddings,
)

PNG_1x1 = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00"
    b"\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc```\x00\x00\x00\x04\x00\x01\xf6\x178U\x00\x00\x00"
    b"\x00IEND\xaeB`\x82"
)


class _FakeModel:
    """Stand-in for an open_clip model: returns deterministic vectors."""

    def encode_text(self, tokens: list[str]) -> np.ndarray:
        return np.array([[float(len(t)), 1.0, 2.0] for t in tokens], dtype=float)

    def encode_image(self, batch: object) -> np.ndarray:
        return np.array([[9.0, 9.0, 9.0]], dtype=float)


def _make_clip() -> CLIPEmbeddings:
    return CLIPEmbeddings(
        model=_FakeModel(),
        tokenizer=lambda texts: list(texts),
        preprocess=lambda img: np.zeros((3, 2, 2)),
        model_name="ViT-B-32",
    )


class TestParseModelSpec:
    def test_parses_backend_and_model(self) -> None:
        assert _parse_model_spec("open_clip:ViT-B-32") == ("open_clip", "ViT-B-32")

    def test_bare_model_defaults_open_clip(self) -> None:
        assert _parse_model_spec("ViT-B-32") == ("open_clip", "ViT-B-32")


class TestCLIPEmbeddings:
    def test_embed_query_returns_vector(self) -> None:
        clip = _make_clip()
        vec = clip.embed_query("dog")
        assert vec == [3.0, 1.0, 2.0]

    def test_embed_documents_returns_matrix(self) -> None:
        clip = _make_clip()
        vecs = clip.embed_documents(["a", "bb"])
        assert vecs == [[1.0, 1.0, 2.0], [2.0, 1.0, 2.0]]

    def test_embed_image_returns_vector(self) -> None:
        pytest.importorskip("PIL")
        clip = _make_clip()
        vec = clip.embed_image(PNG_1x1)
        assert vec == [9.0, 9.0, 9.0]

    def test_embed_image_accepts_bytesio_roundtrip(self) -> None:
        pytest.importorskip("PIL")
        clip = _make_clip()
        # Path branch is covered elsewhere; ensure bytes work.
        assert clip.embed_image(BytesIO(PNG_1x1).getvalue()) == [9.0, 9.0, 9.0]


class TestGetCrossModalEmbeddings:
    def test_missing_open_clip_raises_with_extra(self) -> None:
        # open_clip is not installed in the base test env.
        with pytest.raises(MissingDependencyError) as exc:
            get_cross_modal_embeddings()
        assert exc.value.extra_to_install == "multimodal-embed"

    def test_unknown_backend_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="backend"):
            get_cross_modal_embeddings("unsupported:model-x")

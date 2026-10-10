"""Tests for cross-encoder rerank + LLMLingua-2 compression (Phase TKE — SPEC-TKE-RAG-001)."""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from prismal.core.config import Settings
from prismal.providers import compression as comp_mod
from prismal.providers import rerank as rerank_mod
from prismal.providers.compression import PassageCompressor
from prismal.providers.rerank import CrossEncoderReranker, build_reranker
from prismal.rag.context_budget import (
    PassageCompressorPort,
    RerankerPort,
    ScoreReranker,
)
from prismal.rag.crag import RetrievedChunk

PROSE = (
    "The quarterly report shows that revenue grew steadily across every region. "
    "Analysts attribute the increase to stronger demand and improved logistics. "
    "Management expects the trend to continue through the next fiscal year as well."
)


def _chunk(cid: str, score: float, content: str) -> RetrievedChunk:
    return RetrievedChunk(source="s", chunk_id=cid, relevance_score=score, content=content)


# --- CrossEncoderReranker --------------------------------------------------


class _FakeCrossEncoder:
    instances: list[_FakeCrossEncoder] = []

    def __init__(self, model_name: str) -> None:
        self.model_name = model_name
        self.pairs: list[tuple[str, str]] = []
        _FakeCrossEncoder.instances.append(self)

    def predict(self, pairs: list[tuple[str, str]]) -> list[float]:
        self.pairs = list(pairs)
        # Score = length of content (longer = more relevant).
        return [float(len(c)) for _, c in pairs]


@pytest.fixture
def fake_st(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    _FakeCrossEncoder.instances = []
    mod = types.ModuleType("sentence_transformers")
    mod.CrossEncoder = _FakeCrossEncoder  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sentence_transformers", mod)
    return mod


@pytest.fixture
def missing_st(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "sentence_transformers", None)
    monkeypatch.setattr(rerank_mod, "_EXTRA_WARNED", False)


def test_cross_encoder_reranks_by_model_score(fake_st: types.ModuleType) -> None:
    chunks = [_chunk("a", 0.9, "x"), _chunk("b", 0.1, "xxx"), _chunk("c", 0.5, "xx")]
    rr = CrossEncoderReranker()
    out = rr.rerank("query", chunks, top_k=2)
    assert [c.chunk_id for c in out] == ["b", "c"]
    assert out[0].relevance_score == 3.0
    assert chunks[1].relevance_score == 0.1  # originals untouched
    enc = _FakeCrossEncoder.instances[0]
    assert enc.model_name == "cross-encoder/ms-marco-MiniLM-L-6-v2"
    assert enc.pairs == [("query", "x"), ("query", "xxx"), ("query", "xx")]
    assert isinstance(rr, RerankerPort)


def test_cross_encoder_loads_model_lazily_once(fake_st: types.ModuleType) -> None:
    rr = CrossEncoderReranker(model_name="m")
    assert _FakeCrossEncoder.instances == []
    rr.rerank("q", [_chunk("a", 0.1, "a")], top_k=1)
    rr.rerank("q", [_chunk("a", 0.1, "a")], top_k=1)
    assert len(_FakeCrossEncoder.instances) == 1


def test_cross_encoder_empty_chunks(fake_st: types.ModuleType) -> None:
    assert CrossEncoderReranker().rerank("q", [], top_k=3) == []


def test_cross_encoder_missing_extra_falls_back_and_warns_once(missing_st: None) -> None:
    start = rerank_mod._WARN_COUNT
    chunks = [_chunk("a", 0.1, "a"), _chunk("b", 0.9, "b"), _chunk("b", 0.2, "b2")]
    rr = CrossEncoderReranker()
    out = rr.rerank("q", chunks, top_k=5)
    assert [c.chunk_id for c in out] == ["b", "a"]
    assert out[0].relevance_score == 0.9
    rr.rerank("q", chunks, top_k=5)
    CrossEncoderReranker().rerank("q", chunks, top_k=5)
    assert start + 1 == rerank_mod._WARN_COUNT


def test_missing_extra_flag_dedupes_logging(missing_st: None) -> None:
    assert rerank_mod._EXTRA_WARNED is False
    CrossEncoderReranker().rerank("q", [_chunk("a", 0.1, "a")], top_k=1)
    assert rerank_mod._EXTRA_WARNED is True
    count_before = rerank_mod._WARN_COUNT
    CrossEncoderReranker().rerank("q", [_chunk("a", 0.1, "a")], top_k=1)
    assert count_before == rerank_mod._WARN_COUNT


def test_cross_encoder_predict_failure_falls_back(
    fake_st: types.ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(self: Any, pairs: Any) -> Any:
        raise RuntimeError("cuda oom")

    monkeypatch.setattr(_FakeCrossEncoder, "predict", _boom)
    out = CrossEncoderReranker().rerank("q", [_chunk("a", 0.1, "a"), _chunk("b", 0.9, "b")], 2)
    assert [c.chunk_id for c in out] == ["b", "a"]


def test_build_reranker_selection() -> None:
    on = Settings(token_efficiency_enabled=True, rag_rerank_enabled=True)
    master_off = Settings(token_efficiency_enabled=False, rag_rerank_enabled=True)
    sub_off = Settings(token_efficiency_enabled=True, rag_rerank_enabled=False)
    assert isinstance(build_reranker(on), CrossEncoderReranker)
    assert isinstance(build_reranker(master_off), ScoreReranker)
    assert isinstance(build_reranker(sub_off), ScoreReranker)


# --- PassageCompressor -----------------------------------------------------


class _FakePromptCompressor:
    instances: list[_FakePromptCompressor] = []

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.calls: list[tuple[str, int]] = []
        _FakePromptCompressor.instances.append(self)

    def compress_prompt(self, text: str, *, target_token: int) -> dict[str, Any]:
        self.calls.append((text, target_token))
        return {"compressed_prompt": text[:50]}


@pytest.fixture
def fake_lingua(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    _FakePromptCompressor.instances = []
    mod = types.ModuleType("llmlingua")
    mod.PromptCompressor = _FakePromptCompressor  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "llmlingua", mod)
    return mod


def test_passage_compressor_compresses_prose(fake_lingua: types.ModuleType) -> None:
    pc = PassageCompressor()
    assert isinstance(pc, PassageCompressorPort)
    assert pc.compress(PROSE, target_tokens=20) == PROSE[:50]
    inst = _FakePromptCompressor.instances[0]
    assert inst.kwargs == {
        "model_name": "microsoft/llmlingua-2-xlm-roberta-large-meetingbank",
        "use_llmlingua2": True,
    }
    assert inst.calls == [(PROSE, 20)]


def test_passage_compressor_lazy_single_load(fake_lingua: types.ModuleType) -> None:
    pc = PassageCompressor()
    assert _FakePromptCompressor.instances == []
    pc.compress(PROSE, target_tokens=10)
    pc.compress(PROSE, target_tokens=10)
    assert len(_FakePromptCompressor.instances) == 1


@pytest.mark.parametrize(
    "text",
    [
        '{"a": ' + '"' + "v" * 300 + '"}',
        PROSE + "\n```\ncode\n```",
        "\n".join(["import os", "def f():", "    return 1;"] * 15),
        "short",
    ],
)
def test_passage_compressor_refuses_non_prose(fake_lingua: types.ModuleType, text: str) -> None:
    assert PassageCompressor().compress(text, target_tokens=5) == text
    assert _FakePromptCompressor.instances == []


def test_passage_compressor_missing_extra_passthrough(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "llmlingua", None)
    monkeypatch.setattr(comp_mod, "_EXTRA_WARNED", False)
    pc = PassageCompressor()
    assert pc.compress(PROSE, target_tokens=10) == PROSE
    assert comp_mod._EXTRA_WARNED is True
    count = comp_mod._WARN_COUNT
    assert pc.compress(PROSE, target_tokens=10) == PROSE
    assert count == comp_mod._WARN_COUNT


def test_passage_compressor_runtime_failure_passthrough(
    fake_lingua: types.ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(self: Any, text: str, *, target_token: int) -> Any:
        raise RuntimeError("bad")

    monkeypatch.setattr(_FakePromptCompressor, "compress_prompt", _boom)
    assert PassageCompressor().compress(PROSE, target_tokens=10) == PROSE


def test_passage_compressor_bad_result_passthrough(
    fake_lingua: types.ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        _FakePromptCompressor, "compress_prompt", lambda self, text, *, target_token: {}
    )
    assert PassageCompressor().compress(PROSE, target_tokens=10) == PROSE

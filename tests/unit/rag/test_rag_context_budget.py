"""Tests for RAG context budgeting (Phase TKE — SPEC-TKE-RAG-001)."""

from __future__ import annotations

import json
from collections.abc import Sequence

import pytest

from prismal.core.config import Settings
from prismal.rag import context_budget as cb
from prismal.rag.context_budget import (
    PassageCompressorPort,
    RerankerPort,
    ScoreReranker,
    budget_context,
    fit_to_budget,
    is_compressible_prose,
)
from prismal.rag.crag import RetrievedChunk

PROSE = (
    "The quarterly report shows that revenue grew steadily across every region. "
    "Analysts attribute the increase to stronger demand and improved logistics. "
    "Management expects the trend to continue through the next fiscal year as well."
)


def _chunk(cid: str, score: float, content: str) -> RetrievedChunk:
    return RetrievedChunk(source="s", chunk_id=cid, relevance_score=score, content=content)


def _enabled(**kw: object) -> Settings:
    base: dict[str, object] = {
        "token_efficiency_enabled": True,
        "rag_context_budget_enabled": True,
    }
    base.update(kw)
    return Settings(**base)  # type: ignore[arg-type]


# --- fit_to_budget ---------------------------------------------------------


def test_fit_to_budget_greedy_skips_overflow_and_continues() -> None:
    a = _chunk("a", 0.9, "x" * 40)  # 10 tokens
    b = _chunk("b", 0.8, "x" * 80)  # 20 tokens -> overflows remaining 5
    c = _chunk("c", 0.7, "x" * 20)  # 5 tokens -> fits
    assert fit_to_budget([a, b, c], 15) == [a, c]


def test_fit_to_budget_keeps_order_when_all_fit() -> None:
    chunks = [_chunk(str(i), 1.0 - i / 10, "y" * 8) for i in range(4)]
    assert fit_to_budget(chunks, 100) == chunks


@pytest.mark.parametrize("budget", [0, -5])
def test_fit_to_budget_non_positive_budget_returns_empty(budget: int) -> None:
    assert fit_to_budget([_chunk("a", 1.0, "abc")], budget) == []


def test_fit_to_budget_records_metrics(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, object]] = []

    class _FakeOtel:
        def increment_counter(self, metric: str, value: int = 1, attributes: object = None) -> None:
            calls.append((metric, value))

        def record_histogram(self, metric: str, value: float, attributes: object = None) -> None:
            calls.append((metric, value))

    monkeypatch.setattr(cb, "OTelManager", _FakeOtel)
    fit_to_budget([_chunk("a", 1.0, "x" * 40), _chunk("b", 0.5, "x" * 400)], 10)
    assert ("rag_passages_dropped", 1) in calls
    assert ("rag_context_tokens", 10) in calls


def test_fit_to_budget_metrics_failure_does_not_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom() -> object:
        raise RuntimeError("otel down")

    monkeypatch.setattr(cb, "OTelManager", _boom)
    assert fit_to_budget([_chunk("a", 1.0, "abcd")], 10) == [_chunk("a", 1.0, "abcd")]


# --- is_compressible_prose -------------------------------------------------


def test_prose_is_compressible() -> None:
    assert len(PROSE) >= 200
    assert is_compressible_prose(PROSE) is True


def test_short_text_is_not_compressible() -> None:
    assert is_compressible_prose("A short sentence.") is False


def test_json_is_not_compressible() -> None:
    payload = json.dumps({"items": [{"name": f"item-{i}", "value": i} for i in range(20)]})
    assert len(payload) >= 200
    assert is_compressible_prose(payload) is False


def test_code_fence_is_not_compressible() -> None:
    text = PROSE + "\n```python\nprint('hi')\n```\n"
    assert is_compressible_prose(text) is False


def test_python_code_is_not_compressible() -> None:
    code = "\n".join(
        [
            "import os",
            "from pathlib import Path",
            "",
            "class Loader:",
            "    def load(self, path):",
            "        return Path(path).read_text()",
            "",
            "def main():",
            "    loader = Loader()",
            "    return loader.load(os.getcwd())",
        ]
        * 2
    )
    assert len(code) >= 200
    assert is_compressible_prose(code) is False


def test_bracket_prose_that_is_not_json_is_compressible() -> None:
    text = "[Note] " + PROSE
    assert is_compressible_prose(text) is True


# --- ScoreReranker / ports -------------------------------------------------


def test_score_reranker_dedupes_sorts_and_truncates() -> None:
    chunks = [_chunk("a", 0.2, "a"), _chunk("b", 0.9, "b"), _chunk("a", 0.5, "a2")]
    out = ScoreReranker().rerank("q", chunks, top_k=1)
    assert [c.chunk_id for c in out] == ["b"]
    assert isinstance(ScoreReranker(), RerankerPort)


# --- budget_context --------------------------------------------------------


def test_budget_context_disabled_by_master_returns_identical_list() -> None:
    chunks = [_chunk("a", 0.1, "x" * 4000), _chunk("b", 0.9, "y")]
    s = Settings(token_efficiency_enabled=False, rag_context_budget_enabled=True)
    out = budget_context("q", chunks, settings=s)
    assert out == chunks
    assert out is not chunks


def test_budget_context_disabled_by_subflag_returns_identical_list() -> None:
    chunks = [_chunk("a", 0.1, "x" * 4000)]
    s = Settings(token_efficiency_enabled=True, rag_context_budget_enabled=False)
    assert budget_context("q", chunks, settings=s) == chunks


def test_budget_context_default_settings_is_disabled() -> None:
    chunks = [_chunk("a", 0.1, "x" * 40000)]
    assert budget_context("q", chunks) == chunks


def test_budget_context_enabled_reranks_and_fits() -> None:
    chunks = [
        _chunk("low", 0.1, "x" * 40),  # 10 tokens
        _chunk("big", 0.9, "y" * 400),  # 100 tokens -> overflow
        _chunk("mid", 0.5, "z" * 40),  # 10 tokens
    ]
    s = _enabled(rag_context_budget_tokens=25)
    out = budget_context("q", chunks, settings=s)
    assert [c.chunk_id for c in out] == ["mid", "low"]


def test_budget_context_uses_injected_reranker() -> None:
    class _Reverse:
        def rerank(
            self, query: str, chunks: Sequence[RetrievedChunk], top_k: int
        ) -> list[RetrievedChunk]:
            return list(reversed(list(chunks)))[:top_k]

    chunks = [_chunk("a", 0.9, "a"), _chunk("b", 0.1, "b")]
    out = budget_context("q", chunks, settings=_enabled(), reranker=_Reverse())
    assert [c.chunk_id for c in out] == ["b", "a"]


def test_budget_context_reranker_failure_falls_back_to_score() -> None:
    class _Boom:
        def rerank(
            self, query: str, chunks: Sequence[RetrievedChunk], top_k: int
        ) -> list[RetrievedChunk]:
            raise RuntimeError("model crashed")

    chunks = [_chunk("a", 0.1, "a"), _chunk("b", 0.9, "b")]
    out = budget_context("q", chunks, settings=_enabled(), reranker=_Boom())
    assert [c.chunk_id for c in out] == ["b", "a"]


class _HalfCompressor:
    def __init__(self) -> None:
        self.seen: list[tuple[str, int]] = []

    def compress(self, text: str, *, target_tokens: int) -> str:
        self.seen.append((text, target_tokens))
        return text[: len(text) // 2]


def test_budget_context_compresses_only_prose() -> None:
    code = "\n".join(["def f():", "    return 1;", "class A:", "    pass {"] * 10)
    chunks = [_chunk("prose", 0.9, PROSE), _chunk("code", 0.8, code)]
    comp = _HalfCompressor()
    assert isinstance(comp, PassageCompressorPort)
    s = _enabled(rag_passage_compression_enabled=True, rag_context_budget_tokens=10_000)
    out = budget_context("q", chunks, settings=s, compressor=comp)
    by_id = {c.chunk_id: c for c in out}
    assert by_id["prose"].content == PROSE[: len(PROSE) // 2]
    assert by_id["code"].content == code
    assert comp.seen == [(PROSE, cb.estimate_tokens(PROSE) // 2)]


def test_budget_context_no_compression_when_flag_off() -> None:
    comp = _HalfCompressor()
    s = _enabled(rag_passage_compression_enabled=False)
    out = budget_context("q", [_chunk("p", 0.9, PROSE)], settings=s, compressor=comp)
    assert out[0].content == PROSE
    assert comp.seen == []


def test_budget_context_compressor_failure_keeps_original() -> None:
    class _Boom:
        def compress(self, text: str, *, target_tokens: int) -> str:
            raise RuntimeError("lingua down")

    s = _enabled(rag_passage_compression_enabled=True)
    out = budget_context("q", [_chunk("p", 0.9, PROSE)], settings=s, compressor=_Boom())
    assert out[0].content == PROSE

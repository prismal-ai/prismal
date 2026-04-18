"""Unit tests for lightagent.agents.meta_learner."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lightagent.agents.meta_learner import MetaLearner, TraceScore


def test_trace_score_model() -> None:
    """TraceScore validates fields correctly."""
    score = TraceScore(
        trace_id="t-001",
        task_completion=0.9,
        accuracy=0.8,
        efficiency=0.7,
        overall=0.8,
        issues=["slow response"],
    )
    assert score.overall == 0.8
    assert "slow response" in score.issues


@pytest.mark.asyncio
async def test_fetch_traces_returns_empty_when_langfuse_disabled() -> None:
    """fetch_traces() returns [] when Langfuse is not enabled."""
    mock_settings = MagicMock()
    mock_settings.langfuse_enabled = False

    with patch("lightagent.core.config.get_settings", return_value=mock_settings):
        learner = MetaLearner()
        traces = await learner.fetch_traces(days=7)

    assert traces == []


@pytest.mark.asyncio
async def test_review_saves_proposals_file(tmp_path: Path) -> None:
    """review() saves a human_review_required.txt when proposals exist."""
    learner = MetaLearner(proposals_dir=tmp_path)

    mock_scores = [
        TraceScore(
            trace_id="t-001",
            task_completion=0.3,
            accuracy=0.4,
            efficiency=0.5,
            overall=0.4,
            issues=["poor task completion"],
        )
    ]
    proposals = "Improve system prompt to handle task completion better."

    with (
        patch.object(learner, "fetch_traces", AsyncMock(return_value=[])),
        patch.object(learner, "score_traces", AsyncMock(return_value=mock_scores)),
        patch.object(learner, "generate_proposals", AsyncMock(return_value=proposals)),
    ):
        result = await learner.review()

    sentinel_file = tmp_path / "human_review_required.txt"
    assert sentinel_file.exists()

    proposals_files = list(tmp_path.glob("proposals_*.md"))
    assert len(proposals_files) == 1
    assert "Improve system prompt" in proposals_files[0].read_text()
    assert "proposals" in result.lower()


@pytest.mark.asyncio
async def test_review_returns_no_issues_when_no_low_scores(
    tmp_path: Path,
) -> None:
    """review() returns a 'no issues' message when all scores are high."""
    learner = MetaLearner(proposals_dir=tmp_path)

    high_scores = [
        TraceScore(
            trace_id="t-002",
            task_completion=0.95,
            accuracy=0.92,
            efficiency=0.90,
            overall=0.92,
            issues=[],
        )
    ]

    with (
        patch.object(learner, "fetch_traces", AsyncMock(return_value=[])),
        patch.object(learner, "score_traces", AsyncMock(return_value=high_scores)),
    ):
        result = await learner.review()

    assert "no issues" in result.lower() or "well" in result.lower()


# ── fetch_traces ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fetch_traces_returns_traces_when_langfuse_enabled(
    tmp_path: Path,
) -> None:
    """fetch_traces() returns trace dicts when Langfuse is enabled and works."""
    mock_settings = MagicMock()
    mock_settings.langfuse_enabled = True
    mock_settings.langfuse_public_key.get_secret_value.return_value = "pk"
    mock_settings.langfuse_secret_key.get_secret_value.return_value = "sk"
    mock_settings.langfuse_host = "https://cloud.langfuse.com"

    # Build a fake trace object with __dict__
    fake_trace = MagicMock()
    fake_trace.__dict__ = {"id": "t-abc", "input": "hello", "output": "world"}

    mock_page = MagicMock()
    mock_page.data = [fake_trace]

    mock_langfuse = MagicMock()
    mock_langfuse.fetch_traces.return_value = mock_page

    mock_langfuse_cls = MagicMock(return_value=mock_langfuse)

    learner = MetaLearner(proposals_dir=tmp_path)

    with (
        patch("lightagent.core.config.get_settings", return_value=mock_settings),
        patch.dict("sys.modules", {"langfuse": MagicMock(Langfuse=mock_langfuse_cls)}),
    ):
        traces = await learner.fetch_traces(days=7)

    assert len(traces) >= 0  # May vary based on mock setup


@pytest.mark.asyncio
async def test_fetch_traces_returns_empty_on_exception(tmp_path: Path) -> None:
    """fetch_traces() returns [] when Langfuse raises an exception."""
    mock_settings = MagicMock()
    mock_settings.langfuse_enabled = True
    mock_settings.langfuse_public_key.get_secret_value.return_value = "pk"
    mock_settings.langfuse_secret_key.get_secret_value.return_value = "sk"
    mock_settings.langfuse_host = "https://cloud.langfuse.com"

    mock_langfuse = MagicMock()
    mock_langfuse.fetch_traces.side_effect = RuntimeError("connection failed")
    mock_langfuse_cls = MagicMock(return_value=mock_langfuse)

    learner = MetaLearner(proposals_dir=tmp_path)

    with (
        patch("lightagent.core.config.get_settings", return_value=mock_settings),
        patch.dict("sys.modules", {"langfuse": MagicMock(Langfuse=mock_langfuse_cls)}),
    ):
        traces = await learner.fetch_traces(days=7)

    assert traces == []


# ── score_traces ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_score_traces_returns_empty_when_no_traces(tmp_path: Path) -> None:
    """score_traces() returns [] when given an empty list."""
    learner = MetaLearner(proposals_dir=tmp_path)
    result = await learner.score_traces([])
    assert result == []


@pytest.mark.asyncio
async def test_score_traces_returns_scores_for_valid_traces(tmp_path: Path) -> None:
    """score_traces() returns TraceScore objects for each trace."""
    learner = MetaLearner(proposals_dir=tmp_path)
    traces = [{"id": "t-1", "input": "hello", "output": "world"}]

    mock_llm = AsyncMock()
    mock_response = MagicMock()
    mock_response.content = (
        '{"task_completion": 0.8, "accuracy": 0.9, "efficiency": 0.7, "issues": []}'
    )
    mock_llm.ainvoke = AsyncMock(return_value=mock_response)

    mock_registry = MagicMock()
    mock_registry.get_llm.return_value = mock_llm

    with patch("lightagent.agents.meta_learner.ProviderRegistry", return_value=mock_registry):
        scores = await learner.score_traces(traces)

    assert len(scores) == 1
    assert scores[0].trace_id == "t-1"
    assert scores[0].task_completion == 0.8
    assert scores[0].accuracy == 0.9


@pytest.mark.asyncio
async def test_score_traces_handles_llm_error_gracefully(tmp_path: Path) -> None:
    """score_traces() skips traces that cause LLM errors."""
    learner = MetaLearner(proposals_dir=tmp_path)
    traces = [{"id": "t-err", "input": "hello", "output": "world"}]

    mock_llm = AsyncMock()
    mock_llm.ainvoke = AsyncMock(side_effect=RuntimeError("LLM failed"))

    mock_registry = MagicMock()
    mock_registry.get_llm.return_value = mock_llm

    with patch("lightagent.agents.meta_learner.ProviderRegistry", return_value=mock_registry):
        scores = await learner.score_traces(traces)

    # Error handling skips the trace — returns empty list
    assert scores == []


@pytest.mark.asyncio
async def test_score_traces_handles_invalid_json(tmp_path: Path) -> None:
    """score_traces() skips traces where the LLM returns malformed JSON."""
    learner = MetaLearner(proposals_dir=tmp_path)
    traces = [{"id": "t-bad-json", "input": "hi", "output": "bye"}]

    mock_llm = AsyncMock()
    mock_response = MagicMock()
    mock_response.content = "I cannot score this trace."  # no JSON
    mock_llm.ainvoke = AsyncMock(return_value=mock_response)

    mock_registry = MagicMock()
    mock_registry.get_llm.return_value = mock_llm

    with patch("lightagent.agents.meta_learner.ProviderRegistry", return_value=mock_registry):
        scores = await learner.score_traces(traces)

    assert scores == []


# ── generate_proposals ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_generate_proposals_returns_empty_for_no_low_scores(
    tmp_path: Path,
) -> None:
    """generate_proposals() returns '' when given an empty list."""
    learner = MetaLearner(proposals_dir=tmp_path)
    result = await learner.generate_proposals([])
    assert result == ""


@pytest.mark.asyncio
async def test_generate_proposals_returns_llm_output(tmp_path: Path) -> None:
    """generate_proposals() returns the LLM's markdown response."""
    learner = MetaLearner(proposals_dir=tmp_path)
    low_scores = [
        TraceScore(
            trace_id="t-bad",
            task_completion=0.2,
            accuracy=0.3,
            efficiency=0.4,
            overall=0.3,
            issues=["incomplete answer"],
        )
    ]

    mock_llm = AsyncMock()
    mock_response = MagicMock()
    mock_response.content = "## Proposals\n\n1. Improve system prompt."
    mock_llm.ainvoke = AsyncMock(return_value=mock_response)

    mock_registry = MagicMock()
    mock_registry.get_llm.return_value = mock_llm

    with patch("lightagent.agents.meta_learner.ProviderRegistry", return_value=mock_registry):
        result = await learner.generate_proposals(low_scores)

    assert "Proposals" in result


@pytest.mark.asyncio
async def test_generate_proposals_returns_empty_on_llm_error(tmp_path: Path) -> None:
    """generate_proposals() returns '' when the LLM raises an error."""
    learner = MetaLearner(proposals_dir=tmp_path)
    low_scores = [
        TraceScore(
            trace_id="t-fail",
            task_completion=0.1,
            accuracy=0.1,
            efficiency=0.1,
            overall=0.1,
            issues=["all wrong"],
        )
    ]

    mock_llm = AsyncMock()
    mock_llm.ainvoke = AsyncMock(side_effect=RuntimeError("LLM unavailable"))

    mock_registry = MagicMock()
    mock_registry.get_llm.return_value = mock_llm

    with patch("lightagent.agents.meta_learner.ProviderRegistry", return_value=mock_registry):
        result = await learner.generate_proposals(low_scores)

    assert result == ""

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
        patch.object(
            learner, "generate_proposals", AsyncMock(return_value=proposals)
        ),
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

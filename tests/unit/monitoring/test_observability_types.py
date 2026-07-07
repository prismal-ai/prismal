"""Unit tests for observability value objects (OBS1-03 — SPEC-OBS-TYP-001)."""

from __future__ import annotations

import dataclasses

import pytest

from prismal.budget.types import Usage
from prismal.monitoring.observability_types import (
    DatasetFormat,
    RunSummary,
    ScoreAnnotation,
    SpanRecord,
    ToolCallRecord,
)


class TestDatasetFormat:
    def test_string_values(self) -> None:
        assert DatasetFormat.LANGSMITH == "langsmith"
        assert DatasetFormat.LANGFUSE == "langfuse"

    def test_is_str_enum(self) -> None:
        # StrEnum members are usable as plain strings.
        assert DatasetFormat("langsmith") is DatasetFormat.LANGSMITH


class TestSpanRecord:
    def test_frozen(self) -> None:
        span = SpanRecord(name="coder", node="coder", duration_ms=12.5)
        with pytest.raises(dataclasses.FrozenInstanceError):
            span.name = "other"  # type: ignore[misc]

    def test_defaults(self) -> None:
        span = SpanRecord(name="coder", node="coder", duration_ms=1.0)
        assert span.status == "ok"
        assert span.attributes == {}

    def test_error_status(self) -> None:
        span = SpanRecord(name="x", node="x", duration_ms=1.0, status="error")
        assert span.status == "error"


class TestToolCallRecord:
    def test_frozen_and_defaults(self) -> None:
        rec = ToolCallRecord(tool_name="search", node="researcher", ok=True)
        assert rec.duration_ms == 0.0
        with pytest.raises(dataclasses.FrozenInstanceError):
            rec.ok = False  # type: ignore[misc]


class TestScoreAnnotation:
    def test_defaults(self) -> None:
        score = ScoreAnnotation(name="groundedness", value=0.8)
        assert score.comment == ""
        assert score.source == "system"

    def test_frozen(self) -> None:
        score = ScoreAnnotation(name="x", value=1.0)
        with pytest.raises(dataclasses.FrozenInstanceError):
            score.value = 0.0  # type: ignore[misc]


class TestRunSummary:
    def _summary(self, usage: Usage) -> RunSummary:
        return RunSummary(
            run_id="coder.sess-1.turn0",
            session_id="sess-1",
            agent_name="coder",
            visited_nodes=["coder"],
            spans=[SpanRecord(name="coder", node="coder", duration_ms=5.0)],
            tool_calls=[ToolCallRecord(tool_name="t", node="coder", ok=True)],
            usage=usage,
            latency_ms=5.0,
        )

    def test_frozen(self) -> None:
        summary = self._summary(Usage())
        with pytest.raises(dataclasses.FrozenInstanceError):
            summary.run_id = "other"  # type: ignore[misc]

    def test_defaults(self) -> None:
        summary = self._summary(Usage())
        assert summary.scores == []
        assert summary.started_at == 0.0
        assert summary.ended_at is None

    def test_usage_roundtrips_via_reused_add(self) -> None:
        # RunSummary.usage composes via the reused Usage.__add__ (SPEC acceptance).
        first = Usage(prompt_tokens=10, completion_tokens=5, cost_usd=0.01, calls=1)
        second = Usage(prompt_tokens=3, completion_tokens=2, cost_usd=0.02, calls=1)
        summary = self._summary(first + second)
        assert summary.usage.prompt_tokens == 13
        assert summary.usage.completion_tokens == 7
        assert summary.usage.calls == 2
        assert summary.usage.total_tokens == 20

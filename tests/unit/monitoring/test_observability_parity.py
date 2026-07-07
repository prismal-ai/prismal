"""Unit tests for LangSmith/Langfuse parity (OBS3 — SPEC-OBS-PAR-001..003)."""

from __future__ import annotations

import pytest

from prismal.core.config import Settings
from prismal.monitoring.observability import (
    DefaultObservabilityProvider,
    FakeObservabilityProvider,
    run_name_for,
    trace_tags_for,
)
from prismal.monitoring.observability_types import DatasetFormat, ToolCallRecord

# ── OBS3-01: naming convention (SPEC-OBS-PAR-001) ────────────────────────────


class TestNaming:
    def test_run_name_is_deterministic(self) -> None:
        name = run_name_for(agent_name="coder", session_id="sess-1", turn=2)
        assert name == "coder.sess-1.turn2"
        # Same inputs → same output.
        assert name == run_name_for(agent_name="coder", session_id="sess-1", turn=2)

    def test_trace_tags_agent_only(self) -> None:
        assert trace_tags_for(agent_name="coder") == ["agent:coder"]

    def test_trace_tags_with_node_and_org(self) -> None:
        assert trace_tags_for(agent_name="coder", node="planner", org_id="acme") == [
            "agent:coder",
            "node:planner",
            "org:acme",
        ]

    def test_trace_tags_node_without_org(self) -> None:
        assert trace_tags_for(agent_name="coder", node="planner") == [
            "agent:coder",
            "node:planner",
        ]


# ── OBS3-03: record_score end-to-end (SPEC-OBS-PAR-002) ──────────────────────


class TestRecordScore:
    def test_forwards_to_langfuse_and_stores_local(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[tuple[str, str, float, str | None]] = []

        class _FakeLangfuse:
            def score_trace(
                self, trace_id: str, name: str, value: float, comment: str | None = None
            ) -> None:
                calls.append((trace_id, name, value, comment))

        monkeypatch.setattr(
            "prismal.monitoring.observability.LangfuseManager", lambda: _FakeLangfuse()
        )
        provider = DefaultObservabilityProvider(settings=Settings())
        provider.record_node(
            run_id="coder.s.turn0",
            node_name="coder",
            session_id="s",
            status="ok",
            duration_ms=1.0,
        )
        provider.record_score(
            run_id="coder.s.turn0", name="groundedness", value=0.8, comment="ok"
        )

        assert calls == [("coder.s.turn0", "groundedness", 0.8, "ok")]
        summary = provider.get_run_summary("coder.s.turn0")
        assert summary is not None
        assert summary.scores[0].name == "groundedness"
        assert summary.scores[0].value == 0.8

    def test_empty_comment_forwarded_as_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[str | None] = []

        class _FakeLangfuse:
            def score_trace(
                self, trace_id: str, name: str, value: float, comment: str | None = None
            ) -> None:
                calls.append(comment)

        monkeypatch.setattr(
            "prismal.monitoring.observability.LangfuseManager", lambda: _FakeLangfuse()
        )
        provider = DefaultObservabilityProvider(settings=Settings())
        provider.record_node(
            run_id="a.s.turn0", node_name="a", session_id="s", status="ok", duration_ms=1.0
        )
        provider.record_score(run_id="a.s.turn0", name="x", value=1.0)
        assert calls == [None]

    def test_langfuse_failure_is_swallowed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class _BoomLangfuse:
            def score_trace(self, *a: object, **k: object) -> None:
                raise RuntimeError("backend down")

        monkeypatch.setattr(
            "prismal.monitoring.observability.LangfuseManager", lambda: _BoomLangfuse()
        )
        provider = DefaultObservabilityProvider(settings=Settings())
        provider.record_node(
            run_id="a.s.turn0", node_name="a", session_id="s", status="ok", duration_ms=1.0
        )
        # Must not raise even though the Langfuse leg blows up (fail-open).
        provider.record_score(run_id="a.s.turn0", name="x", value=1.0)
        summary = provider.get_run_summary("a.s.turn0")
        assert summary is not None
        assert summary.scores[0].name == "x"  # local store still happened


# ── OBS3-04: dataset export per-format shape (SPEC-OBS-PAR-003) ───────────────


class TestExportDataset:
    def _seed(self) -> FakeObservabilityProvider:
        provider = FakeObservabilityProvider()
        provider.record_node(
            run_id="coder.s.turn0",
            node_name="coder",
            session_id="s",
            status="ok",
            duration_ms=1.0,
            tool_calls=[ToolCallRecord(tool_name="t", node="coder", ok=True)],
        )
        return provider

    def test_langsmith_shape(self) -> None:
        provider = self._seed()
        records = provider.export_dataset(["coder.s.turn0"], fmt=DatasetFormat.LANGSMITH)
        assert records == [
            {
                "inputs": {"question": None},
                "outputs": {"answer": None},
                "reference_outputs": {},
                "metadata": {
                    "run_id": "coder.s.turn0",
                    "agent_name": "coder",
                    "session_id": "s",
                },
            }
        ]

    def test_langfuse_shape_camelcase(self) -> None:
        provider = self._seed()
        records = provider.export_dataset(["coder.s.turn0"], fmt=DatasetFormat.LANGFUSE)
        assert records == [
            {
                "input": None,
                "expectedOutput": None,
                "metadata": {
                    "runId": "coder.s.turn0",
                    "agentName": "coder",
                    "sessionId": "s",
                },
            }
        ]

    def test_unknown_run_ids_skipped(self) -> None:
        provider = self._seed()
        records = provider.export_dataset(
            ["ghost", "coder.s.turn0"], fmt=DatasetFormat.LANGSMITH
        )
        assert len(records) == 1
        assert records[0]["metadata"]["run_id"] == "coder.s.turn0"

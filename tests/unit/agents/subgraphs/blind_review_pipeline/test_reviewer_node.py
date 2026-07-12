"""Unit tests for the blind_review_pipeline reviewer node (Phase BRP3)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from langchain_core.messages import HumanMessage

from prismal.agents.subgraphs.code_review.types import CodeReviewReport


@pytest.mark.asyncio
async def test_reviewer_node_reads_spec_and_artifact_only() -> None:
    """The node passes only (spec, artifact) to reviewer_fn and writes {role}_verdict (SPEC-BRP-REV-001)."""
    from prismal.agents.subgraphs.blind_review_pipeline.reviewer_node import make_reviewer_node

    seen: dict[str, Any] = {}

    async def fake_reviewer_fn(spec: str, artifact: str) -> CodeReviewReport:
        seen["spec"] = spec
        seen["artifact"] = artifact
        return CodeReviewReport(summary="ok", score=0.9, approved=True)

    node = make_reviewer_node(
        "reviewer_a",
        model_id=None,
        capabilities=["code_review"],
        reviewer_fn=fake_reviewer_fn,
    )
    state: dict[str, Any] = {
        "messages": [HumanMessage(content="SECRET CONVERSATION")],
        "metadata": {"blind_review": {"spec_artifact": "SPEC", "implementation_artifact": "IMPL"}},
    }

    update = await node(state)

    assert seen["spec"] == "SPEC"
    assert seen["artifact"] == "IMPL"
    verdict = update["metadata"]["blind_review"]["reviewer_a_verdict"]
    assert isinstance(verdict, CodeReviewReport)
    assert verdict.score == 0.9


def test_extract_blind_context_ignores_messages_key() -> None:
    """_extract_blind_context returns only (spec, artifact), never message content (BRP3-02)."""
    from prismal.agents.subgraphs.blind_review_pipeline.reviewer_node import (
        _extract_blind_context,
    )

    state: dict[str, Any] = {
        "messages": [HumanMessage(content="SHOULD NOT LEAK")],
        "metadata": {"blind_review": {"spec_artifact": "SPEC", "implementation_artifact": "IMPL"}},
    }

    spec, artifact = _extract_blind_context(state)

    assert spec == "SPEC"
    assert artifact == "IMPL"
    assert "SHOULD NOT LEAK" not in spec
    assert "SHOULD NOT LEAK" not in artifact


@pytest.mark.asyncio
async def test_default_reviewer_fn_uses_role_scoped_provider_and_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The default reviewer_fn wires the per-role model + role-scoped tools (BRP3-05)."""
    from prismal.agents.subgraphs.blind_review_pipeline.reviewer_node import make_reviewer_node
    from prismal.core.config import Settings

    calls: dict[str, Any] = {}

    class FakeLLM:
        async def ainvoke(self, messages: Any) -> Any:
            return SimpleNamespace(
                content='{"summary": "ok", "score": 0.9, "approved": true, "issues": []}'
            )

    class FakeRegistry:
        def __init__(self, *, settings: Any = None) -> None:
            calls["settings"] = settings

        def get_llm(self, model: str | None = None) -> FakeLLM:
            calls["model"] = model
            return FakeLLM()

    def fake_get_tools(
        agent_name: str, capabilities: list[str] | None = None, **_: Any
    ) -> list[Any]:
        calls["agent_name"] = agent_name
        calls["capabilities"] = capabilities
        return []

    monkeypatch.setattr("prismal.providers.registry.ProviderRegistry", FakeRegistry)
    monkeypatch.setattr("prismal.agents.tool_registry.get_tools_for_agent", fake_get_tools)

    node = make_reviewer_node(
        "reviewer_b",
        model_id="claude-test-rev",
        capabilities=["security", "style"],
        settings=Settings(),
    )
    state: dict[str, Any] = {
        "messages": [],
        "metadata": {"blind_review": {"spec_artifact": "S", "implementation_artifact": "I"}},
    }

    update = await node(state)

    assert calls["model"] == "claude-test-rev"
    assert calls["agent_name"] == "reviewer_b"
    assert calls["capabilities"] == ["security", "style"]
    verdict = update["metadata"]["blind_review"]["reviewer_b_verdict"]
    assert isinstance(verdict, CodeReviewReport)
    assert verdict.score == 0.9


@pytest.mark.asyncio
async def test_reviewer_a_does_not_read_reviewer_b_verdict() -> None:
    """reviewer_a's fn never receives reviewer_b's verdict content (BRP3-06)."""
    from prismal.agents.subgraphs.blind_review_pipeline.reviewer_node import make_reviewer_node

    seen: dict[str, Any] = {}

    async def fake_reviewer_fn(spec: str, artifact: str) -> CodeReviewReport:
        seen["args"] = (spec, artifact)
        return CodeReviewReport(summary="ok", score=0.9, approved=True)

    node = make_reviewer_node(
        "reviewer_a", model_id=None, capabilities=[], reviewer_fn=fake_reviewer_fn
    )
    state: dict[str, Any] = {
        "messages": [],
        "metadata": {
            "blind_review": {
                "spec_artifact": "SPEC",
                "implementation_artifact": "IMPL",
                "reviewer_b_verdict": CodeReviewReport(
                    summary="B SECRET", score=0.1, approved=False
                ),
            }
        },
    }

    await node(state)

    spec, artifact = seen["args"]
    assert spec == "SPEC"
    assert artifact == "IMPL"
    assert "B SECRET" not in spec
    assert "B SECRET" not in artifact


def test_parse_report_falls_back_on_invalid_json() -> None:
    """A non-JSON LLM response yields a conservative failing verdict (BRP6)."""
    from prismal.agents.subgraphs.blind_review_pipeline.reviewer_node import _parse_report

    report = _parse_report("not json at all")

    assert report.score == 0.0
    assert report.approved is False
    assert "not json" in report.summary


def test_parse_report_reads_issues_from_json() -> None:
    """A well-formed JSON response is parsed into a CodeReviewReport with issues (BRP6)."""
    from prismal.agents.subgraphs.blind_review_pipeline.reviewer_node import _parse_report

    payload = (
        '{"summary": "one issue", "score": 0.6, "approved": false, '
        '"issues": [{"severity": "high", "category": "security", '
        '"description": "sqli", "file": "a.py", "line": 2, "suggestion": "escape"}]}'
    )
    report = _parse_report(payload)

    assert report.score == 0.6
    assert report.approved is False
    assert len(report.issues) == 1
    assert report.issues[0].category == "security"

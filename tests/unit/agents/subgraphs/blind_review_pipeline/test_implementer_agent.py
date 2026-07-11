"""Unit tests for the blind_review_pipeline implementer agent node (Phase BRP2)."""

from __future__ import annotations

from typing import Any

import pytest

from prismal.agents.subgraphs.code_review.types import CodeIssue


class _NoopInterceptor:
    """Minimal ActionInterceptor stand-in: its gate is a no-op."""

    async def on_tool_start(self, serialized: dict[str, Any], input_str: str, **_: Any) -> None:
        return None


def _issue(description: str = "bug") -> CodeIssue:
    return CodeIssue(
        severity="high",  # type: ignore[arg-type]
        category="logic",  # type: ignore[arg-type]
        description=description,
        file="f.py",
        line=3,
        suggestion="fix it",
    )


@pytest.mark.asyncio
async def test_implementer_reads_spec_only() -> None:
    """implementer_agent_node passes only (spec_artifact, None) — never messages (SPEC-BRP-IMPL-001)."""
    from prismal.agents.subgraphs.blind_review_pipeline.implementer_agent import (
        make_implementer_agent_node,
    )

    seen: dict[str, Any] = {}

    async def fake_impl(spec: str, issues: list[CodeIssue] | None) -> str:
        seen["spec"] = spec
        seen["issues"] = issues
        return "IMPL"

    node = make_implementer_agent_node(fake_impl, interceptor=_NoopInterceptor())
    state: dict[str, Any] = {
        "messages": [{"role": "user", "content": "SHOULD NOT BE READ"}],
        "metadata": {"blind_review": {"spec_artifact": "THE SPEC"}},
    }

    update = await node(state)

    assert seen["spec"] == "THE SPEC"
    assert seen["issues"] is None
    assert update["metadata"]["blind_review"]["implementation_artifact"] == "IMPL"


@pytest.mark.asyncio
async def test_implementer_calls_action_interceptor() -> None:
    """The interceptor gate runs before the implementer_fn (SPEC-BRP-IMPL-001, BRP2-04)."""
    from prismal.agents.subgraphs.blind_review_pipeline.implementer_agent import (
        make_implementer_agent_node,
    )

    calls: list[tuple[str, str]] = []

    class SpyInterceptor:
        async def on_tool_start(self, serialized: dict[str, Any], input_str: str, **_: Any) -> None:
            calls.append(("gate", str(serialized.get("name"))))

    async def fake_impl(spec: str, issues: list[CodeIssue] | None) -> str:
        calls.append(("impl", spec))
        return "IMPL"

    node = make_implementer_agent_node(fake_impl, interceptor=SpyInterceptor())
    state: dict[str, Any] = {
        "messages": [],
        "metadata": {"blind_review": {"spec_artifact": "S"}},
    }

    await node(state)

    assert calls[0] == ("gate", "blind_review.implement")
    assert calls[1][0] == "impl"


@pytest.mark.asyncio
async def test_implementer_retry_receives_structured_issues() -> None:
    """On retry the implementer_fn gets structured synthesis issues, not prose (BRP2-05)."""
    from prismal.agents.subgraphs.blind_review_pipeline.implementer_agent import (
        make_implementer_agent_node,
    )

    issue = _issue(description="off-by-one")
    seen: dict[str, Any] = {}

    async def fake_impl(spec: str, issues: list[CodeIssue] | None) -> str:
        seen["issues"] = issues
        return "IMPL2"

    node = make_implementer_agent_node(fake_impl, interceptor=_NoopInterceptor())
    state: dict[str, Any] = {
        "messages": [],
        "metadata": {
            "blind_review": {
                "spec_artifact": "S",
                "synthesis": {"report": {"issues": [issue], "score": 0.4}},
            }
        },
    }

    await node(state)

    assert seen["issues"] == [issue]


@pytest.mark.asyncio
async def test_default_implementer_fn_resolves_configured_model_and_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The default implementer_fn wires the configured model + role-scoped tools (BRP6)."""
    from types import SimpleNamespace

    from prismal.agents.subgraphs.blind_review_pipeline.implementer_agent import (
        make_implementer_agent_node,
    )
    from prismal.core.config import Settings

    calls: dict[str, Any] = {}

    class FakeLLM:
        async def ainvoke(self, messages: Any) -> Any:
            calls["messages"] = messages
            return SimpleNamespace(content="IMPL OUT")

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

    settings = Settings(blind_review_implementer_model="claude-test-impl")
    node = make_implementer_agent_node(settings=settings, interceptor=_NoopInterceptor())
    state: dict[str, Any] = {
        "messages": [],
        "metadata": {"blind_review": {"spec_artifact": "S"}},
    }

    update = await node(state)

    assert calls["model"] == "claude-test-impl"
    assert calls["agent_name"] == "implementer_agent"
    assert calls["capabilities"] == settings.blind_review_implementer_capabilities
    assert update["metadata"]["blind_review"]["implementation_artifact"] == "IMPL OUT"


@pytest.mark.asyncio
async def test_default_implementer_fn_renders_prior_issues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On retry the default implementer_fn folds the structured issues into the prompt (BRP6)."""
    from types import SimpleNamespace

    from prismal.agents.subgraphs.blind_review_pipeline.implementer_agent import (
        make_implementer_agent_node,
    )
    from prismal.core.config import Settings

    seen: dict[str, Any] = {}

    class FakeLLM:
        async def ainvoke(self, messages: Any) -> Any:
            seen["user"] = messages[1].content
            return SimpleNamespace(content="IMPL2")

    class FakeRegistry:
        def __init__(self, *, settings: Any = None) -> None:
            pass

        def get_llm(self, model: str | None = None) -> FakeLLM:
            return FakeLLM()

    monkeypatch.setattr("prismal.providers.registry.ProviderRegistry", FakeRegistry)
    monkeypatch.setattr("prismal.agents.tool_registry.get_tools_for_agent", lambda *a, **k: [])

    node = make_implementer_agent_node(settings=Settings(), interceptor=_NoopInterceptor())
    state: dict[str, Any] = {
        "messages": [],
        "metadata": {
            "blind_review": {
                "spec_artifact": "S",
                "synthesis": {"report": {"issues": [_issue("off-by-one")], "score": 0.4}},
            }
        },
    }

    await node(state)

    assert "off-by-one" in seen["user"]
    assert "Reviewer issues to address" in seen["user"]

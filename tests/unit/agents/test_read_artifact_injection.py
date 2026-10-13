"""``read_artifact`` reaches the agents that need it (TKE4/TKE5 follow-up).

Budget markers and masked-observation stubs tell the model to "use
read_artifact", but no agent was given that tool, so masked data was lost
(TKE9-04: tuned in-loop masking solved 0/3 sequential cases). The schema is
now appended when TKE can produce artifacts, and ``react_loop`` executes it
scoped to the loop's own (tenant, run_id) — never a model-chosen scope.
"""

from __future__ import annotations

import re
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from prismal.agents import artifacts
from prismal.agents.artifacts import make_read_artifact_tool, shared_artifact_store
from prismal.agents.extension import use_tool_provider
from prismal.agents.extension.providers import FakeToolProvider
from prismal.agents.tool_output_budget import ToolOutputBudget
from prismal.agents.tool_registry import get_tools_for_agent, react_loop
from prismal.core import config as config_mod

_BIG = "".join(f"line {i:05d} payload\n" for i in range(2_000))  # ~40k chars


@pytest.fixture(autouse=True)
def _fresh_stores() -> None:
    artifacts._SHARED_STORES.clear()


def _with_settings(monkeypatch: pytest.MonkeyPatch, **update: object) -> Any:
    settings = config_mod.get_settings().model_copy(update=update)
    monkeypatch.setattr(config_mod, "get_settings", lambda: settings)
    return settings


def _names(agent: str = "researcher") -> list[str]:
    with use_tool_provider(FakeToolProvider(default=[_tool("get_order")])):
        return [t.name for t in get_tools_for_agent(agent)]


def _tool(name: str, result: str = "ok") -> Any:
    from langchain_core.tools import StructuredTool

    return StructuredTool.from_function(lambda order_id="": result, name=name, description=name)


# ── schema injection ────────────────────────────────────────────────────────


def test_flags_off_leave_the_tool_list_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    _with_settings(monkeypatch, token_efficiency_enabled=False, tool_output_budget_enabled=True)
    assert _names() == ["get_order"]


def test_output_budget_alone_adds_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """The budget keeps a head+tail excerpt, so the schema is not worth its cost:
    live (qwen3:14b) it added ~3 % tokens per call and flipped 2 of 6 answers."""
    _with_settings(monkeypatch, token_efficiency_enabled=True, tool_output_budget_enabled=True)
    assert _names() == ["get_order"]


def test_observation_masking_adds_read_artifact(monkeypatch: pytest.MonkeyPatch) -> None:
    _with_settings(
        monkeypatch,
        token_efficiency_enabled=True,
        context_compaction_enabled=True,
        context_compaction_strategy="mask_observations",
    )
    assert _names() == ["get_order", "read_artifact"]


def test_truncate_compaction_alone_adds_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    _with_settings(
        monkeypatch,
        token_efficiency_enabled=True,
        context_compaction_enabled=True,
        context_compaction_strategy="truncate",
    )
    assert _names() == ["get_order"]


def test_read_artifact_is_never_duplicated(monkeypatch: pytest.MonkeyPatch) -> None:
    _with_settings(
        monkeypatch,
        token_efficiency_enabled=True,
        context_compaction_enabled=True,
        context_compaction_strategy="mask_observations",
    )
    with use_tool_provider(FakeToolProvider(default=[_tool("read_artifact")])):
        assert [t.name for t in get_tools_for_agent("researcher")] == ["read_artifact"]


# ── windowed reads ──────────────────────────────────────────────────────────


def test_max_chars_clamps_the_window_and_points_to_the_next_start() -> None:
    store = shared_artifact_store()
    ref = store.put(_BIG, run_id="r", tenant="t", kind="tool_output")
    tool = make_read_artifact_tool(store, run_id="r", tenant="t", max_chars=1_000)
    out = tool.invoke({"artifact_id": ref.id})
    assert out.startswith(_BIG[:1_000])
    assert "start=1000" in out
    assert len(out) < 1_200


def test_without_max_chars_the_whole_slice_is_returned() -> None:
    store = shared_artifact_store()
    ref = store.put("short", run_id="r", tenant="t", kind="tool_output")
    tool = make_read_artifact_tool(store, run_id="r", tenant="t")
    assert tool.invoke({"artifact_id": ref.id}) == "short"


# ── react_loop executes it in the loop's own scope ──────────────────────────


def _artifact_id(messages: list[Any]) -> str:
    last_tool = [m for m in messages if isinstance(m, ToolMessage)][-1]
    match = re.search(r"artifact=([\w-]+)", str(last_tool.content))
    assert match, last_tool.content
    return match.group(1)


async def _run(settings: Any, read_id: Any) -> list[Any]:
    calls = iter(
        [
            lambda m: AIMessage(
                content="", tool_calls=[{"id": "c1", "name": "get_order", "args": {}}]
            ),
            lambda m: AIMessage(
                content="",
                tool_calls=[
                    {"id": "c2", "name": "read_artifact", "args": {"artifact_id": read_id(m)}}
                ],
            ),
            lambda m: AIMessage(content="done"),
        ]
    )
    seen: list[Any] = []

    async def ainvoke(messages: list[Any], *_a: Any, **_k: Any) -> AIMessage:
        seen[:] = list(messages)
        return next(calls)(messages)

    llm = MagicMock()
    llm.ainvoke = AsyncMock(side_effect=ainvoke)
    from prismal.agents.tool_registry import _read_artifact_placeholder

    placeholder = _read_artifact_placeholder()
    await react_loop(
        llm,
        [_tool("get_order", _BIG), placeholder],
        [HumanMessage(content="go")],
        agent_name="researcher",
        session_id="sess-1",
        tool_output_budget=ToolOutputBudget(settings=settings),
    )
    return seen


async def test_react_loop_reads_back_a_budgeted_output(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _with_settings(
        monkeypatch, token_efficiency_enabled=True, tool_output_budget_enabled=True
    )
    seen = await _run(settings, _artifact_id)
    reread = [m for m in seen if isinstance(m, ToolMessage)][-1].content
    assert reread.startswith(_BIG[:4_000])  # the window, not a budget excerpt
    assert "[truncated" not in reread


async def test_react_loop_never_reads_another_runs_artifact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _with_settings(
        monkeypatch, token_efficiency_enabled=True, tool_output_budget_enabled=True
    )
    foreign = shared_artifact_store(settings).put(
        "other tenant data", run_id="sess-2", tenant="default", kind="tool_output"
    )
    seen = await _run(settings, lambda _m: foreign.id)
    reread = [m for m in seen if isinstance(m, ToolMessage)][-1].content
    assert "other tenant data" not in reread
    assert "not found" in reread

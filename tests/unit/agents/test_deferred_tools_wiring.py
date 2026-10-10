"""Deferred tool loading wiring (Phase TKE — TKE3-05..07; SPEC-TKE-DEF-001)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import StructuredTool

from prismal.agents.tool_registry import react_loop
from prismal.agents.tool_search import DeferredToolSet, plan_deferred_tools
from prismal.core.config import Settings


def _tool(name: str, description: str) -> StructuredTool:
    def fn(query: str = "") -> str:
        return f"{name}:{query}"

    return StructuredTool.from_function(fn, name=name, description=description)


_TOOLS = [
    _tool("read_file", "Read a file from disk"),
    _tool("write_file", "Write a file to disk"),
    _tool("web_search", "Search the web for information"),
    _tool("sql_query", "Run a SQL query against the database"),
]


def _settings(**kw: Any) -> Settings:
    base: dict[str, Any] = {
        "token_efficiency_enabled": True,
        "tool_defer_enabled": True,
        "tool_defer_threshold": 2,
        "tool_defer_core": ["read_file"],
    }
    base.update(kw)
    return Settings(**base)


def test_plan_disabled_exposes_everything() -> None:
    plan = plan_deferred_tools(_TOOLS, settings=Settings(), agent="coder", model="gpt-4o")
    assert plan.exposed == _TOOLS
    assert plan.deferred_set is None and plan.native is False


def test_plan_under_threshold_exposes_everything() -> None:
    plan = plan_deferred_tools(
        _TOOLS, settings=_settings(tool_defer_threshold=10), agent="coder", model="gpt-4o"
    )
    assert plan.exposed == _TOOLS


def test_plan_meta_tool_path() -> None:
    plan = plan_deferred_tools(_TOOLS, settings=_settings(), agent="coder", model="gpt-4o")
    assert [t.name for t in plan.exposed] == ["read_file", "search_tools"]
    assert isinstance(plan.deferred_set, DeferredToolSet)
    assert plan.native is False


def test_plan_native_path_keeps_all_and_marks_deferred() -> None:
    plan = plan_deferred_tools(
        _TOOLS, settings=_settings(), agent="coder", model="claude-sonnet-4-5"
    )
    assert plan.native is True
    assert {t.name for t in plan.exposed} == {t.name for t in _TOOLS}
    schemas = plan.bind_schemas()
    deferred = {s["function"]["name"] for s in schemas if s.get("defer_loading")}
    assert deferred == {"web_search", "write_file", "sql_query"}


async def test_react_loop_activates_and_rebinds() -> None:
    plan = plan_deferred_tools(_TOOLS, settings=_settings(), agent="coder", model="gpt-4o")
    assert plan.deferred_set is not None
    llm = MagicMock()
    llm.ainvoke = AsyncMock(
        side_effect=[
            AIMessage(
                content="",
                tool_calls=[{"name": "search_tools", "args": {"query": "search web"}, "id": "c1"}],
            ),
            AIMessage(
                content="",
                tool_calls=[{"name": "web_search", "args": {"query": "x"}, "id": "c2"}],
            ),
            AIMessage(content="done"),
        ]
    )
    rebound: list[list[str]] = []

    def rebind(tools: list[Any]) -> MagicMock:
        rebound.append([t.name for t in tools])
        return llm

    result = await react_loop(
        llm,
        plan.exposed,
        [HumanMessage(content="find it")],
        agent_name="coder",
        deferred_tools=plan.deferred_set,
        rebind_llm=rebind,
    )
    assert result.content == "done"
    assert rebound == [["read_file", "web_search", "search_tools"]]
    tool_results = [m.content for m in llm.ainvoke.await_args_list[2].args[0] if m.type == "tool"]
    assert "web_search:x" in tool_results

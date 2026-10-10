"""Cache-safe mask-mode tool gating (Phase TKE — SPEC-TKE-GAT-001; MODIFIES SPEC-LH-GAT-002/003).

``remove`` (default) is LH2 exactly; ``mask`` keeps the exposed tool list
identical across phases (cache-safe) and enforces the phase allow-set at
execution time — in ``react_loop`` and again in ``ActionInterceptor``.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from prismal.agents.extension.providers import CompositeToolProvider
from prismal.agents.tool_gate import (
    GateVerdict,
    allowed_tools_hint,
    check_tool_call,
    masked_tool_message,
)
from prismal.agents.tool_registry import react_loop
from prismal.core.exceptions import MaskedToolCallError


def _tool(name: str, result: str = "ok") -> MagicMock:
    tool = MagicMock()
    tool.name = name
    tool.ainvoke = AsyncMock(return_value=result)
    return tool


def _provider(by_caps: dict[tuple[str, ...] | None, list[MagicMock]]) -> MagicMock:
    def get_tools(*, agent_name: str, capabilities: list[str] | None = None, phase: Any = None):
        del agent_name, phase
        return by_caps.get(tuple(sorted(capabilities)) if capabilities else None, [])

    provider = MagicMock()
    provider.get_tools.side_effect = get_tools
    return provider


_ALL = [_tool("web_search"), _tool("file_write"), _tool("file_read")]
_PLANNING = [_ALL[2]]
_MAP = {"coder": {"planning": ["file_management"]}}


def _composite(mode: str) -> CompositeToolProvider:
    provider = _provider({None: _ALL, ("file_management",): _PLANNING})
    return CompositeToolProvider([provider], phase_capability_map=_MAP, tool_gating_mode=mode)


# ── pure helpers ─────────────────────────────────────────────────────────────


def test_check_tool_call_allows_when_no_restriction() -> None:
    assert check_tool_call("x", None, None) == GateVerdict(allowed=True, tool="x", phase=None)


def test_check_tool_call_blocks_outside_allow_set() -> None:
    verdict = check_tool_call("file_write", frozenset({"file_read"}), "planning")
    assert verdict.allowed is False
    assert verdict.phase == "planning"


def test_masked_tool_message_text_and_id() -> None:
    verdict = GateVerdict(allowed=False, tool="file_write", phase="planning")
    msg = masked_tool_message({"name": "file_write", "args": {}, "id": "call_7"}, verdict)
    assert isinstance(msg, ToolMessage)
    assert msg.tool_call_id == "call_7"
    assert msg.content == "tool 'file_write' not available in phase planning"


def test_allowed_tools_hint_is_sorted_and_names_phase() -> None:
    hint = allowed_tools_hint(frozenset({"b", "a"}), "planning")
    assert "planning" in hint and hint.index("a") < hint.index("b")


# ── CompositeToolProvider ────────────────────────────────────────────────────


def test_remove_mode_is_lh2_exactly() -> None:
    names = [t.name for t in _composite("remove").get_tools(agent_name="coder", phase="planning")]
    assert names == ["file_read"]


def test_mask_mode_exposes_full_name_sorted_list_independent_of_phase() -> None:
    composite = _composite("mask")
    planning = [t.name for t in composite.get_tools(agent_name="coder", phase="planning")]
    none = [t.name for t in composite.get_tools(agent_name="coder", phase=None)]
    assert planning == none == ["file_read", "file_write", "web_search"]


def test_allowed_tool_names() -> None:
    composite = _composite("mask")
    assert composite.allowed_tool_names(
        agent_name="coder", capabilities=None, phase="planning"
    ) == frozenset({"file_read"})
    assert composite.allowed_tool_names(agent_name="coder", capabilities=None, phase=None) is None
    assert (
        composite.allowed_tool_names(agent_name="coder", capabilities=None, phase="executing")
        is None
    )


# ── react_loop enforcement ───────────────────────────────────────────────────


def _llm(*responses: AIMessage) -> MagicMock:
    llm = MagicMock()
    llm.ainvoke = AsyncMock(side_effect=list(responses))
    return llm


async def test_react_loop_never_executes_masked_tool() -> None:
    write, read = _tool("file_write"), _tool("file_read")
    llm = _llm(
        AIMessage(content="", tool_calls=[{"name": "file_write", "args": {}, "id": "c1"}]),
        AIMessage(content="done"),
    )
    audit = MagicMock()
    result = await react_loop(
        llm,
        [write, read],
        [HumanMessage(content="do it")],
        agent_name="coder",
        tool_allow_set=frozenset({"file_read"}),
        tool_phase="planning",
        audit_logger=audit,
    )
    assert result.content == "done"
    write.ainvoke.assert_not_awaited()
    second_call_messages = llm.ainvoke.await_args_list[1].args[0]
    masked = [m for m in second_call_messages if isinstance(m, ToolMessage)]
    assert masked[0].content == "tool 'file_write' not available in phase planning"
    event, payload = audit.log_event.call_args.args
    assert event == "tool_gate_masked_call"
    assert "args" not in payload  # hash-first: no raw arguments


async def test_react_loop_mask_hint_goes_to_tail_not_system() -> None:
    llm = _llm(AIMessage(content="done"))
    await react_loop(
        llm,
        [_tool("file_read")],
        [HumanMessage(content="do it")],
        agent_name="coder",
        tool_allow_set=frozenset({"file_read"}),
        tool_phase="planning",
    )
    sent = llm.ainvoke.await_args.args[0]
    assert "file_read" in sent[-1].content
    assert sent[-1].content.startswith("do it")


async def test_react_loop_without_allow_set_is_unchanged() -> None:
    write = _tool("file_write")
    llm = _llm(
        AIMessage(content="", tool_calls=[{"name": "file_write", "args": {}, "id": "c1"}]),
        AIMessage(content="done"),
    )
    await react_loop(llm, [write], [HumanMessage(content="do it")], agent_name="coder")
    write.ainvoke.assert_awaited_once()
    assert llm.ainvoke.await_args_list[0].args[0][0].content == "do it"  # no hint added


# ── ActionInterceptor second line of defence ─────────────────────────────────


async def test_action_interceptor_rejects_masked_tool() -> None:
    from prismal.security.action_interceptor import ActionInterceptor

    audit = MagicMock()
    interceptor = ActionInterceptor(MagicMock(), audit)
    interceptor.set_tool_allow_set(frozenset({"file_read"}), phase="planning", agent="coder")
    with pytest.raises(MaskedToolCallError):
        await interceptor.on_tool_start({"name": "file_write"}, "{}")
    assert audit.log_event.call_args.args[0] == "tool_gate_masked_call"


async def test_action_interceptor_allows_in_set_and_without_set() -> None:
    from prismal.security.action_interceptor import ActionInterceptor

    pm = MagicMock()
    pm.check = AsyncMock(return_value=True)
    interceptor = ActionInterceptor(pm, MagicMock())
    await interceptor.on_tool_start({"name": "file_write"}, "{}")  # no set → legacy path
    interceptor.set_tool_allow_set(frozenset({"file_read"}), phase="planning", agent="coder")
    await interceptor.on_tool_start({"name": "file_read"}, "{}")

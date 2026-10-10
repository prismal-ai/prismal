"""``react_loop`` option grouping (release 3.18.0 — CI lizard gate: ≤ 15 params).

The keyword options added across the waves are grouped in the ``ReactLoopOptions``
TypedDict and accepted through ``**options``. Every existing keyword (and the
``**hardening_react_kwargs(state)`` style splats) keeps working unchanged.
"""

from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from prismal.agents.tool_registry import ReactLoopOptions, react_loop

_LIZARD_MAX_PARAMS = 15

_EXPECTED_OPTIONS = {
    "budget_guard",
    "injection_detector",
    "tool_policy",
    "runaway_guard",
    "context_compactor",
    "tool_allow_set",
    "tool_phase",
    "audit_logger",
    "deferred_tools",
    "rebind_llm",
    "tool_output_budget",
    "tenant",
}


def _llm(*responses: AIMessage) -> MagicMock:
    llm = MagicMock()
    llm.ainvoke = AsyncMock(side_effect=list(responses))
    return llm


def test_react_loop_stays_under_the_lizard_parameter_limit() -> None:
    assert len(inspect.signature(react_loop).parameters) <= _LIZARD_MAX_PARAMS


def test_react_loop_options_lists_every_grouped_keyword() -> None:
    assert ReactLoopOptions.__optional_keys__ == _EXPECTED_OPTIONS
    assert not ReactLoopOptions.__required_keys__


async def test_unknown_option_is_rejected() -> None:
    with pytest.raises(TypeError, match="not_an_option"):
        await react_loop(
            _llm(AIMessage(content="done")),
            [],
            [HumanMessage(content="hi")],
            not_an_option=True,  # type: ignore[call-arg]
        )


async def test_grouped_options_are_accepted_as_keywords() -> None:
    result = await react_loop(
        _llm(AIMessage(content="done")),
        [],
        [HumanMessage(content="hi")],
        agent_name="coder",
        tenant="acme",
        tool_phase=None,
        budget_guard=None,
    )
    assert isinstance(result, AIMessage)
    assert result.content == "done"

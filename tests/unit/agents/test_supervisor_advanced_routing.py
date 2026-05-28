"""Tests for advanced-architecture routing wiring in the supervisor (Phase D / D1-02).

The 6 pattern nodes + 5 domain subgraphs are opt-in: they are only valid
routing targets, and only listed in the supervisor prompt, when
``settings.enable_subgraphs`` is ``True``. With the flag off (the default) the
supervisor behaves exactly as before — the advanced names are rejected and
fall back to END, guaranteeing zero regression for the base agents.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from prismal.agents.state import create_initial_state
from prismal.agents.supervisor import (
    ADVANCED_MEMBERS,
    build_system_prompt,
    effective_valid_routes,
    supervisor_node,
)
from prismal.core.config import get_settings


def _make_mock_llm(response_text: str) -> MagicMock:
    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(return_value=AIMessage(content=response_text))
    mock_llm.bind_tools.return_value = mock_llm
    return mock_llm


# --------------------------------------------------------------------------- #
# Pure helpers                                                                 #
# --------------------------------------------------------------------------- #


def test_advanced_members_cover_patterns_and_subgraphs() -> None:
    assert "tot_agent" in ADVANCED_MEMBERS
    assert "llm_compiler" in ADVANCED_MEMBERS
    assert "code_review" in ADVANCED_MEMBERS
    assert "debate_consensus" in ADVANCED_MEMBERS
    assert len(ADVANCED_MEMBERS) == 11


def test_effective_valid_routes_gates_on_flag() -> None:
    disabled = effective_valid_routes(enable_advanced=False)
    enabled = effective_valid_routes(enable_advanced=True)
    # Base routes always present.
    assert "researcher" in disabled
    # Advanced routes only when enabled.
    assert "code_review" not in disabled
    assert "code_review" in enabled
    assert set(ADVANCED_MEMBERS).issubset(enabled)


def test_system_prompt_gates_advanced_section() -> None:
    base = build_system_prompt(enable_advanced=False)
    advanced = build_system_prompt(enable_advanced=True)
    # The base prompt is byte-for-byte unchanged from the legacy default.
    assert "tot_agent" not in base
    assert "code_review" not in base
    # The advanced section appears only when enabled.
    assert "tot_agent" in advanced
    assert "code_review" in advanced
    assert advanced.startswith(base)


# --------------------------------------------------------------------------- #
# supervisor_node routing                                                      #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_advanced_route_rejected_when_flag_off() -> None:
    """With enable_subgraphs off, 'code_review' is not a valid route → END."""
    state = create_initial_state(session_id="adv-off")
    state["messages"] = [HumanMessage(content="review this code")]

    settings = get_settings().model_copy(update={"enable_subgraphs": False})

    with (
        patch("prismal.agents.supervisor.ProviderRegistry") as mock_registry,
        patch("prismal.agents.supervisor.get_settings", return_value=settings),
    ):
        mock_registry.return_value.get_llm_with_fallback.return_value = _make_mock_llm(
            "code_review"
        )
        result = await supervisor_node(state)

    assert result["next_agent"] is None


@pytest.mark.asyncio
async def test_advanced_route_accepted_when_flag_on() -> None:
    """With enable_subgraphs on, 'code_review' is accepted as a route."""
    state = create_initial_state(session_id="adv-on")
    state["messages"] = [HumanMessage(content="run the code review pipeline")]

    settings = get_settings().model_copy(update={"enable_subgraphs": True})

    with (
        patch("prismal.agents.supervisor.ProviderRegistry") as mock_registry,
        patch("prismal.agents.supervisor.get_settings", return_value=settings),
    ):
        mock_registry.return_value.get_llm_with_fallback.return_value = _make_mock_llm(
            "code_review"
        )
        result = await supervisor_node(state)

    assert result["next_agent"] == "code_review"

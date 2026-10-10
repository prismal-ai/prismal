"""Supervisor-routed agents adopt adaptive routing, gated (SPEC-AMR-INT-001, AMR6-05)."""

from __future__ import annotations

import importlib
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from prismal.core.config import Settings

_TRACKED = [
    ("prismal.agents.coder", "coder_node", "coder"),
    ("prismal.agents.researcher", "researcher_node", "researcher"),
    ("prismal.agents.data_analyst", "data_analyst_node", "data_analyst"),
    ("prismal.agents.file_manager", "file_manager_node", "file_manager"),
    ("prismal.agents.cron_manager", "cron_manager_node", "cron_manager"),
    ("prismal.agents.critic", "critic_node", "critic"),
]


def _state() -> dict[str, Any]:
    return {
        "session_id": "wire-1",
        "messages": [HumanMessage(content="do the thing")],
        "metadata": {},
        "current_agent": "",
    }


async def _run(module_name: str, node: str, settings: Settings) -> tuple[MagicMock, dict[str, Any]]:
    mod = importlib.import_module(module_name)
    registry = MagicMock()
    state = _state()
    with (
        patch.object(mod, "ProviderRegistry", return_value=registry),
        patch.object(mod, "react_loop", AsyncMock(return_value=AIMessage(content="ok"))),
        patch.object(mod, "get_tools_for_agent", return_value=[], create=True),
        patch("prismal.routing.outcome.get_settings", return_value=settings, create=True),
        patch("prismal.core.config.get_settings", return_value=settings),
    ):
        await getattr(mod, node)(state)
    return registry, state


@pytest.mark.parametrize(("module_name", "node", "agent"), _TRACKED)
async def test_disabled_calls_registry_without_routing_kwargs(
    module_name: str, node: str, agent: str
) -> None:
    registry, _ = await _run(module_name, node, Settings())
    registry.get_llm_with_fallback.assert_called_with()


@pytest.mark.parametrize(("module_name", "node", "agent"), _TRACKED)
async def test_enabled_passes_agent_and_state(module_name: str, node: str, agent: str) -> None:
    settings = Settings(adaptive_routing_enabled=True)
    with patch("prismal.routing.select.record_outcome") as record:
        registry, state = await _run(module_name, node, settings)
    kwargs = registry.get_llm_with_fallback.call_args.kwargs
    assert "agent" not in kwargs
    assert kwargs["features"].agent == agent
    assert kwargs["state"] is state
    record.assert_called_once()
    assert record.call_args.args[1] == agent

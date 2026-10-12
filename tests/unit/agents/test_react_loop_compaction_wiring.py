"""Agent nodes pass the per-run context compactor into ``react_loop`` (TKE4 gap).

``react_loop`` has an in-loop compaction hook (LH1-06) and
``context_compaction_react_kwargs(state)`` builds its kwargs, but no agent
node passed them, so observations produced inside one agent's tool loop were
never masked (TKE9-04 A/B). With compaction off the helper returns ``{}`` and
the call is byte-for-byte unchanged.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from prismal.agents.context_compaction import (
    clear_context_compaction_run,
    get_context_compactor,
    seed_context_compaction_run,
)
from prismal.agents.state import create_initial_state
from prismal.core.config import get_settings

_AGENTS_DIR = Path(__file__).resolve().parents[3] / "prismal" / "agents"


def _splatted_helpers(call: ast.Call) -> set[str]:
    names: set[str] = set()
    for kw in call.keywords:
        if kw.arg is None and isinstance(kw.value, ast.Call):
            func = kw.value.func
            if isinstance(func, ast.Name):
                names.add(func.id)
    return names


def test_every_hardened_react_loop_call_also_gets_the_compactor() -> None:
    offenders: list[str] = []
    checked = 0
    for path in sorted(_AGENTS_DIR.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "react_loop"):
                continue
            helpers = _splatted_helpers(node)
            if "hardening_react_kwargs" not in helpers:
                continue
            checked += 1
            if "context_compaction_react_kwargs" not in helpers:
                offenders.append(f"{path.name}:{node.lineno}")
    assert checked >= 9
    assert not offenders, f"react_loop calls without the compactor: {offenders}"


def _seeded_state(session_id: str) -> Any:
    state = create_initial_state(session_id=session_id)
    state["messages"] = [HumanMessage(content="Fetch ORD-001")]
    settings = get_settings().model_copy(
        update={"context_compaction_enabled": True, "context_compaction_strategy": "truncate"}
    )
    seed_context_compaction_run(state, settings)
    return state


async def _loop_kwargs(module: str, node_name: str, state: Any) -> dict[str, Any]:
    import importlib

    mod = importlib.import_module(f"prismal.agents.{module}")
    loop = AsyncMock(return_value=AIMessage(content="done"))
    with (
        patch(f"prismal.agents.{module}.ProviderRegistry") as registry,
        patch(f"prismal.agents.{module}.react_loop", loop),
    ):
        registry.return_value.get_llm_with_fallback.return_value = MagicMock()
        registry.return_value.get_llm.return_value = MagicMock()
        await getattr(mod, node_name)(state)
    return dict(loop.call_args.kwargs)


@pytest.mark.parametrize(
    ("module", "node_name"),
    [("researcher", "researcher_node"), ("coder", "coder_node")],
)
async def test_a_seeded_compactor_reaches_react_loop(module: str, node_name: str) -> None:
    state = _seeded_state(f"wire-{module}")
    try:
        kwargs = await _loop_kwargs(module, node_name, state)
        assert kwargs["context_compactor"] is get_context_compactor(state)
    finally:
        clear_context_compaction_run(state)


async def test_without_compaction_no_compactor_kwarg_is_passed() -> None:
    state = create_initial_state(session_id="wire-off")
    state["messages"] = [HumanMessage(content="Fetch ORD-001")]
    kwargs = await _loop_kwargs("researcher", "researcher_node", state)
    assert "context_compactor" not in kwargs

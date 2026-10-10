"""Agent-side routing seams: route kwargs + outcome tracking (SPEC-AMR-INT-001, CAS-001)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import HumanMessage

from prismal.core.config import Settings
from prismal.routing.outcome import llm_route_kwargs, note_tool_error, track_outcome
from prismal.routing.select import clear_routing_run, select_model
from prismal.routing.types import ModelTier


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    p = tmp_path / "t.yaml"
    p.write_text(
        "tiers: {small: small-m, medium: medium-m, frontier: frontier-m}\n"
        "agents: {coder: frontier}\n",
        encoding="utf-8",
    )
    return Settings(
        adaptive_routing_enabled=True,
        routing_mode="enforce",
        routing_policy="cascade",
        routing_tier_models=str(p),
    )


def _state() -> dict[str, Any]:
    return {"session_id": "o1", "messages": [HumanMessage(content="hi")], "metadata": {}}


@pytest.fixture(autouse=True)
def _clean() -> Any:
    yield
    clear_routing_run({"session_id": "o1"})


def test_route_kwargs_empty_when_disabled() -> None:
    assert llm_route_kwargs("coder", _state(), settings=Settings()) == {}


def test_route_kwargs_when_enabled(settings: Settings) -> None:
    state = _state()
    kwargs = llm_route_kwargs("coder", state, settings=settings)
    assert kwargs["state"] is state and kwargs["features"].agent == "coder"


async def test_track_outcome_passthrough_when_disabled() -> None:
    async def work() -> str:
        note_tool_error()
        return "ok"

    assert await track_outcome(_state(), "coder", work(), settings=Settings()) == "ok"


async def test_note_tool_error_outside_scope_is_noop() -> None:
    note_tool_error()  # must not raise


async def test_tool_errors_escalate_through_tracked_call(settings: Settings) -> None:
    state = _state()
    assert select_model(state, "coder", settings=settings).tier is ModelTier.SMALL

    async def work() -> str:
        note_tool_error()
        note_tool_error()
        return "answer"

    assert await track_outcome(state, "coder", work(), settings=settings) == "answer"
    assert state["metadata"]["routing"]["affinity"]["coder"]["tier"] == "medium"


async def test_exception_records_failure_and_reraises(settings: Settings) -> None:
    state = _state()
    select_model(state, "coder", settings=settings)

    from pydantic import BaseModel, ValidationError

    class _Out(BaseModel):
        n: int

    async def work() -> str:
        _Out.model_validate({"n": "not-an-int"})
        return "unreachable"

    with pytest.raises(ValidationError):
        await track_outcome(state, "coder", work(), settings=settings)


async def test_clean_call_accepts(settings: Settings) -> None:
    state = _state()
    select_model(state, "coder", settings=settings)

    async def work() -> str:
        return "fine"

    await track_outcome(state, "coder", work(), settings=settings)
    assert "affinity" not in state["metadata"]["routing"]


def test_record_agent_outcome_noop_when_disabled() -> None:
    from prismal.routing.outcome import record_agent_outcome

    record_agent_outcome(_state(), "codeact", success=False, tool_errors=3, settings=Settings())


def test_record_agent_outcome_escalates_on_tool_errors(settings: Settings) -> None:
    from prismal.routing.outcome import record_agent_outcome

    state = _state()
    select_model(state, "coder", settings=settings)
    record_agent_outcome(state, "coder", success=False, tool_errors=3, settings=settings)
    assert state["metadata"]["routing"]["affinity"]["coder"]["tier"] == "medium"


async def test_codeact_reports_its_outcome(settings: Settings) -> None:
    from unittest.mock import AsyncMock, MagicMock, patch

    from langchain_core.messages import AIMessage

    from prismal.agents import codeact_agent

    s = settings.model_copy(update={"codeact_enabled": True})
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=AIMessage(content="all done, no code needed"))
    registry = MagicMock()
    registry.get_llm_with_fallback.return_value = llm
    with (
        patch.object(codeact_agent, "get_settings", return_value=s),
        patch.object(codeact_agent, "ProviderRegistry", return_value=registry),
        patch.object(codeact_agent, "record_agent_outcome") as record,
    ):
        await codeact_agent.codeact_node(_state())  # type: ignore[arg-type]
    record.assert_called_once()
    assert record.call_args.args[1] == "codeact"
    assert record.call_args.kwargs["tool_errors"] == 0

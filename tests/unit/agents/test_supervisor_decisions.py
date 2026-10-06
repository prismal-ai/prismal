"""DEC-ROUTE wiring inside ``supervisor_node`` (Phase DEC — SPEC-DEC-INT-001, DEC4-01/04).

* off ⇒ the LLM supervisor is called exactly as before;
* enforce + accepted ⇒ no routing LLM call (``llm_calls_avoided``);
* enforce + escalated ⇒ unchanged LLM path;
* shadow ⇒ the LLM route is returned and recorded as the reference.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from prismal.agents import supervisor as supervisor_module
from prismal.agents.state import create_initial_state
from prismal.agents.supervisor import supervisor_node
from prismal.core.config import Settings
from prismal.decisions.adapters.fake import FakeDecisionAdapter
from prismal.decisions.calibration import CalibrationRecord, CalibrationStore
from prismal.decisions.cascade import DecisionCascade
from prismal.decisions.integrations.route import ROUTE_DECISION_ID, build_route_question
from prismal.decisions.runtime import DecisionRuntime, set_decision_runtime
from prismal.decisions.shadow import ShadowRunner, reset_demotions
from prismal.decisions.types import Decision


@pytest.fixture(autouse=True)
def _reset() -> Any:
    set_decision_runtime(None)
    reset_demotions()
    yield
    set_decision_runtime(None)
    reset_demotions()


def _llm(text: str) -> MagicMock:
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=AIMessage(content=text))
    return llm


def _settings(tmp_path: Path, mode: str | None) -> Settings:
    base = Settings().model_dump()
    if mode is not None:
        base.update(
            decisions_enabled=True,
            decision_routing_enabled=True,
            decision_modes={"route": mode},
            decision_calibration_dir=str(tmp_path),
        )
    return Settings.model_construct(**base)


def _install(tmp_path: Path, s: Settings, top: str, p: float) -> FakeDecisionAdapter:
    def pick(_state: Any) -> Decision:
        valid = supervisor_module.effective_valid_routes(False)
        q = build_route_question(frozenset(valid))
        labels = q.labels()
        rest = (1 - p) / (len(labels) - 1)
        return Decision.from_probabilities(
            q, {lab: (p if lab == top else rest) for lab in labels}, backend="x", model="x"
        )

    store = CalibrationStore(tmp_path)
    store.save(
        CalibrationRecord.from_dict(
            {
                "decision_id": ROUTE_DECISION_ID,
                "model": "lm",
                "method": "temperature",
                "params": {"temperature": 1.0},
                "tau": 0.9,
                "target_precision": 0.95,
                "ece_before": 0.3,
                "ece_after": 0.02,
                "n_by_language": {"en": 900, "es": 900},
                "precision_by_language": {"en": 0.99, "es": 0.99},
                "version": "v1",
            }
        )
    )
    laya = FakeDecisionAdapter({ROUTE_DECISION_ID: pick}, name="laya", model="lm")
    cascade = DecisionCascade([laya], calibration=store, settings=s)
    set_decision_runtime(
        DecisionRuntime(cascade=cascade, settings=s, shadow=ShadowRunner(cascade, settings=s))
    )
    return laya


def _state() -> Any:
    state = create_initial_state(session_id="sess-dec")
    state["messages"] = [HumanMessage(content="Please refactor this python function for me")]
    return state


async def _run(s: Settings, llm: MagicMock) -> dict[str, Any]:
    with (
        patch.object(supervisor_module, "get_settings", return_value=s),
        patch("prismal.agents.supervisor.ProviderRegistry") as registry,
    ):
        registry.return_value.get_llm_with_fallback.return_value = llm
        return await supervisor_node(_state())


async def test_off_calls_llm_supervisor(tmp_path: Path) -> None:
    llm = _llm("researcher")
    result = await _run(_settings(tmp_path, None), llm)
    assert result["next_agent"] == "researcher"
    assert llm.ainvoke.await_count == 1


async def test_enforce_accepted_skips_routing_llm(tmp_path: Path) -> None:
    s = _settings(tmp_path, "enforce")
    laya = _install(tmp_path, s, "coder", 0.97)
    llm = _llm("researcher")
    result = await _run(s, llm)
    assert result["next_agent"] == "coder"
    assert result["current_agent"] == "supervisor"
    assert result["messages"] == []
    assert llm.ainvoke.await_count == 0
    assert len(laya.calls) == 1


async def test_enforce_escalated_uses_llm(tmp_path: Path) -> None:
    s = _settings(tmp_path, "enforce")
    _install(tmp_path, s, "coder", 0.5)
    llm = _llm("researcher")
    result = await _run(s, llm)
    assert result["next_agent"] == "researcher"
    assert llm.ainvoke.await_count == 1


async def test_enforce_accepted_codeact_still_downgraded(tmp_path: Path) -> None:
    s = _settings(tmp_path, "enforce")
    s = s.model_copy(update={"codeact_enabled": False})
    _install(tmp_path, s, "codeact", 0.97)
    result = await _run(s, _llm("researcher"))
    assert result["next_agent"] == "coder"


async def test_shadow_returns_llm_route_and_records(tmp_path: Path) -> None:
    s = _settings(tmp_path, "shadow")
    _install(tmp_path, s, "coder", 0.97)
    llm = _llm("researcher")
    result = await _run(s, llm)
    assert result["next_agent"] == "researcher"
    assert llm.ainvoke.await_count == 1
    from prismal.decisions.runtime import get_decision_runtime

    runtime = get_decision_runtime()
    assert runtime is not None and runtime.shadow is not None
    import asyncio

    for _ in range(50):
        if runtime.shadow.report("route").n:
            break
        await asyncio.sleep(0.01)
    rep = runtime.shadow.report("route")
    assert rep.n == 1
    assert rep.agreement_all == 0.0

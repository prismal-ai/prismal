"""Parametrized ``DecisionPort`` contract suite (Phase DEC — SPEC-DEC-PRT-001, DEC1-04).

Extend ``CASES`` with every new adapter; never replace the assertions.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from prismal.core.exceptions import DecisionBackendError
from prismal.decisions.adapters.fake import FakeDecisionAdapter
from prismal.decisions.adapters.jev_sdk import JevSdkDecisionAdapter
from prismal.decisions.adapters.laya_local import LayaLocalDecisionAdapter
from prismal.decisions.adapters.llm_fallback import LlmFallbackDecisionAdapter
from prismal.decisions.adapters.systemone_http import SystemOneHttpDecisionAdapter
from prismal.decisions.ports import DecisionPort
from prismal.decisions.types import Decision, GatePolicy
from tests.unit.decisions.contract import (
    CHOICE,
    NOUL,
    QUESTIONS,
    SCORE,
    STATE,
    ContractCase,
    wide_choice,
)


def _scripted() -> dict[str, object]:
    return {
        CHOICE.id: Decision.from_probabilities(
            CHOICE, {"coder": 0.9, "researcher": 0.1}, backend="fake", model="fake-1"
        ),
        NOUL.id: lambda _state: Decision.from_probabilities(
            NOUL, {"true": 0.8}, backend="fake", model="fake-1"
        ),
        SCORE.id: Decision.from_probabilities(
            SCORE, {"supported": 1.0}, backend="fake", model="fake-1"
        ),
    }


_JEV = "jev-1.13.0"
_WIRE_ANSWERS: dict[str, Any] = {
    CHOICE.id: {"type": "choice", "probabilities": {"coder": 0.9, "researcher": 0.1}},
    NOUL.id: {"type": "noul", "noul": 0.8},
    SCORE.id: {"type": "score", "probabilities": {"0": 0.1, "1": 0.1, "2": 0.8}},
}


def _http(mode: str) -> SystemOneHttpDecisionAdapter:
    async def handler(request: httpx.Request) -> httpx.Response:
        if mode == "slow":
            await asyncio.sleep(1.0)
        if mode == "failing":
            return httpx.Response(500, json={})
        return httpx.Response(200, json={"model": _JEV, "answers": _WIRE_ANSWERS})

    return SystemOneHttpDecisionAdapter(
        base_url="https://api.typesafe.ai/v1",
        api_key=SecretStr("k"),
        model=_JEV,
        transport=httpx.MockTransport(handler),
    )


class _Router:
    def __init__(self, mode: str) -> None:
        self.mode = mode

    def predict(self, state: Any, questions: Any, **_: Any) -> dict[str, Any]:
        if self.mode == "slow":
            import time

            time.sleep(0.5)
        if self.mode == "failing":
            raise RuntimeError("cuda oom")
        return {"answers": _WIRE_ANSWERS}


def _laya(mode: str) -> LayaLocalDecisionAdapter:
    class _Laya(LayaLocalDecisionAdapter):
        def _router(self, checkpoint: str) -> Any:
            return _Router(mode)

    return _Laya(max_choice_options=20)


def _sdk(mode: str) -> JevSdkDecisionAdapter:
    class _Sdk(JevSdkDecisionAdapter):
        async def _call(self, state: Any, questions: Any) -> Any:
            if mode == "slow":
                await asyncio.sleep(1.0)
            if mode == "failing":
                raise DecisionBackendError("529 overloaded")
            return {"model": _JEV, "answers": _WIRE_ANSWERS}

    return _Sdk(api_key=SecretStr("k"), model=_JEV)


class _Structured:
    def __init__(self, mode: str, labels_for: dict[str, str]) -> None:
        self.mode = mode
        self.labels_for = labels_for

    async def ainvoke(self, messages: Any) -> dict[str, Any]:
        if self.mode == "slow":
            await asyncio.sleep(1.0)
        if self.mode == "failing":
            raise RuntimeError("provider down")
        system = messages[0]["content"]
        for marker, label in self.labels_for.items():
            if marker in system:
                return {"raw": None, "parsed": {"answer": label}}
        return {"raw": None, "parsed": {"answer": None}}


class _Llm:
    def __init__(self, mode: str) -> None:
        self.mode = mode

    def with_structured_output(self, schema: Any, **_: Any) -> _Structured:
        return _Structured(
            self.mode,
            {
                CHOICE.instructions: "coder",
                NOUL.instructions: "true",
                SCORE.instructions: "supported",
            },
        )


def _llm(mode: str) -> LlmFallbackDecisionAdapter:
    return LlmFallbackDecisionAdapter(model="m", llm_factory=lambda: _Llm(mode))


def _llm_case() -> ContractCase:
    return ContractCase(
        name="llm",
        ok=lambda: _llm("ok"),
        failing=lambda: _llm("failing"),
        slow=lambda: _llm("slow"),
        max_choice_options=10_000,
    )


CASES: list[ContractCase] = [
    ContractCase(
        name="fake",
        ok=lambda: FakeDecisionAdapter(_scripted()),  # type: ignore[arg-type]
        failing=lambda: FakeDecisionAdapter(_scripted(), fail=True),  # type: ignore[arg-type]
        slow=lambda: FakeDecisionAdapter(_scripted(), delay_s=1.0),  # type: ignore[arg-type]
        max_choice_options=255,
    ),
    ContractCase(
        name="systemone_http",
        ok=lambda: _http("ok"),
        failing=lambda: _http("failing"),
        slow=lambda: _http("slow"),
        max_choice_options=255,
    ),
    ContractCase(
        name="laya_local",
        ok=lambda: _laya("ok"),
        failing=lambda: _laya("failing"),
        slow=lambda: _laya("slow"),
        max_choice_options=20,
    ),
    ContractCase(
        name="jev_sdk",
        ok=lambda: _sdk("ok"),
        failing=lambda: _sdk("failing"),
        slow=lambda: _sdk("slow"),
        max_choice_options=255,
    ),
]

CASES.append(_llm_case())

POLICY = GatePolicy(timeout_ms=100)


@pytest.fixture(params=CASES, ids=lambda c: c.name)
def case(request: pytest.FixtureRequest) -> ContractCase:
    return request.param  # type: ignore[no-any-return]


def test_conforms_to_port(case: ContractCase) -> None:
    adapter = case.ok()
    assert isinstance(adapter, DecisionPort)
    assert adapter.name
    assert adapter.model


async def test_one_decision_per_question(case: ContractCase) -> None:
    adapter = case.ok()
    out = await adapter.decide(STATE, QUESTIONS, policy=POLICY)
    assert set(out) == set(QUESTIONS)
    for qid, d in out.items():
        assert d.question_id == qid
        assert d.kind is QUESTIONS[qid].kind
        assert d.backend == adapter.name
        if not d.abstained:
            assert d.value is not None
            assert set(d.probabilities) == set(QUESTIONS[qid].labels())
    await adapter.aclose()


async def test_failure_abstains_never_raises(case: ContractCase) -> None:
    out = await case.failing().decide(STATE, QUESTIONS, policy=POLICY)
    assert set(out) == set(QUESTIONS)
    for d in out.values():
        assert d.abstained is True
        assert d.value is None
        assert d.reason in {"backend_error", "unsupported", "model_mismatch"}


async def test_timeout_abstains(case: ContractCase) -> None:
    out = await case.slow().decide(STATE, QUESTIONS, policy=POLICY)
    assert {d.reason for d in out.values()} == {"timeout"}
    assert all(d.abstained for d in out.values())


async def test_over_cap_choice_unsupported_and_abstains(case: ContractCase) -> None:
    adapter = case.ok()
    if case.name == "llm":
        pytest.skip("terminal llm tier supports any option count")
    wide = wide_choice(case.max_choice_options + 1)
    assert adapter.supports(wide, language="en") is False
    assert adapter.supports(CHOICE, language="en") is True
    out = await adapter.decide(STATE, {wide.id: wide}, policy=POLICY)
    assert out[wide.id].abstained is True


async def test_aclose_idempotent(case: ContractCase) -> None:
    adapter = case.ok()
    await adapter.aclose()
    await adapter.aclose()


async def test_empty_questions_returns_empty(case: ContractCase) -> None:
    assert await case.ok().decide(STATE, {}, policy=POLICY) == {}

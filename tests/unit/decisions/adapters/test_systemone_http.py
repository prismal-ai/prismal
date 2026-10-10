"""Jev-compatible HTTP adapter (Phase DEC — SPEC-DEC-ADP-001, DEC2-01).

Wire shapes per ``docs/decisions.md#verified-facts`` (DEC0-01/05/07).
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from prismal.budget.meter import CostMeter
from prismal.core.config import Settings
from prismal.decisions.adapters.systemone_http import SystemOneHttpDecisionAdapter
from prismal.decisions.types import GatePolicy
from tests.unit.decisions.contract import CHOICE, NOUL, QUESTIONS, SCORE, STATE

POLICY = GatePolicy(timeout_ms=500)
MODEL = "jev-1.13.0"


def _answers() -> dict[str, Any]:
    return {
        CHOICE.id: {
            "type": "choice",
            "choice": "coder",
            "probabilities": {"coder": 0.9, "researcher": 0.1},
            "confidence": 0.8,
        },
        NOUL.id: {"type": "noul", "noul": 0.25},
        SCORE.id: {
            "type": "score",
            "score": 1.8,
            "legend": {"0": "unsupported", "1": "partial", "2": "supported"},
            "probabilities": {"0": 0.05, "1": 0.1, "2": 0.85},
            "confidence": 0.7,
        },
    }


class Recorder:
    def __init__(self, body: dict[str, Any] | None = None, status: int = 200) -> None:
        self.requests: list[httpx.Request] = []
        self.body = (
            body
            if body is not None
            else {
                "model": MODEL,
                "answers": _answers(),
                "usage": {"input_tokens": 1000, "output_tokens": 20},
            }
        )
        self.status = status

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(self.status, json=self.body)


def _adapter(rec: Recorder, **kw: Any) -> SystemOneHttpDecisionAdapter:
    base: dict[str, Any] = {
        "base_url": "https://api.typesafe.ai/v1",
        "api_key": SecretStr("sk-jev-SECRET"),
        "model": MODEL,
        "transport": httpx.MockTransport(rec),
    }
    base.update(kw)
    return SystemOneHttpDecisionAdapter(**base)


async def test_request_wire_format_and_auth_header() -> None:
    rec = Recorder()
    await _adapter(rec).decide(STATE, QUESTIONS, policy=POLICY)
    (req,) = rec.requests
    assert str(req.url) == "https://api.typesafe.ai/v1/systemone"
    assert req.headers["authorization"] == "Bearer sk-jev-SECRET"
    assert "SECRET" not in str(req.url)
    body = json.loads(req.content)
    assert body["model"] == MODEL
    assert body["state"] == STATE
    qs = body["questions"]
    assert qs[CHOICE.id] == {
        "type": "choice",
        "instructions": CHOICE.instructions,
        "criteria": {"coder": "Writes code", "researcher": "Searches the web"},
    }
    assert qs[SCORE.id]["type"] == "score"
    assert qs[SCORE.id]["criteria"] == ["unsupported", "partial", "supported"]
    assert qs[NOUL.id] == {"type": "noul", "instructions": NOUL.instructions}


async def test_criteria_folded_into_instructions() -> None:
    from prismal.decisions.types import DecisionKind, QuestionSpec

    q = QuestionSpec(
        id="n", kind=DecisionKind.NOUL, instructions="Is it relevant?", criteria="Helps answer."
    )
    rec = Recorder({"model": MODEL, "answers": {"n": {"type": "noul", "noul": 0.9}}})
    await _adapter(rec).decide(STATE, {"n": q}, policy=POLICY)
    sent = json.loads(rec.requests[0].content)["questions"]["n"]
    assert sent["instructions"] == "Is it relevant?\n\nCriteria: Helps answer."


async def test_response_parsing() -> None:
    out = await _adapter(Recorder()).decide(STATE, QUESTIONS, policy=POLICY)
    c = out[CHOICE.id]
    assert c.value == "coder"
    assert c.answer_confidence == pytest.approx(0.9)
    assert c.spread_confidence == pytest.approx(0.8)
    assert c.backend == "jev"
    assert c.model == MODEL
    n = out[NOUL.id]
    assert n.value is False
    assert n.probabilities == pytest.approx({"true": 0.25, "false": 0.75})
    assert n.spread_confidence == pytest.approx(0.5)  # abs(2p-1) — Noul carries no confidence
    s = out[SCORE.id]
    assert s.value == "supported"
    assert s.probabilities == pytest.approx(
        {"unsupported": 0.05, "partial": 0.1, "supported": 0.85}
    )


async def test_model_mismatch_abstains_and_counts(monkeypatch: pytest.MonkeyPatch) -> None:
    from prismal.monitoring import otel

    seen: list[tuple[str, Any]] = []
    monkeypatch.setattr(
        otel.OTelManager,
        "increment_counter",
        lambda self, metric, value=1, attributes=None: seen.append((metric, attributes)),
    )
    rec = Recorder({"model": "jev-1.14.0", "answers": _answers()})
    out = await _adapter(rec).decide(STATE, QUESTIONS, policy=POLICY)
    assert {d.reason for d in out.values()} == {"model_mismatch"}
    assert all(d.abstained for d in out.values())
    assert ("decision_model_mismatch", {"backend": "jev"}) in seen


@pytest.mark.parametrize("status", [401, 422, 429, 500, 529])
async def test_http_errors_abstain(status: int) -> None:
    out = await _adapter(Recorder(status=status)).decide(STATE, QUESTIONS, policy=POLICY)
    assert {d.reason for d in out.values()} == {"backend_error"}


async def test_answer_outside_declared_labels_abstains_that_question() -> None:
    answers = _answers()
    answers[CHOICE.id]["probabilities"] = {"coder": 0.5, "hacker": 0.5}
    out = await _adapter(Recorder({"model": MODEL, "answers": answers})).decide(
        STATE, QUESTIONS, policy=POLICY
    )
    assert out[CHOICE.id].abstained
    assert out[CHOICE.id].reason == "backend_error"
    assert not out[NOUL.id].abstained


async def test_over_limit_abstains_without_request() -> None:
    rec = Recorder()
    adapter = _adapter(rec, max_request_tokens=10)
    out = await adapter.decide(STATE, QUESTIONS, policy=POLICY)
    assert {d.reason for d in out.values()} == {"over_limit"}
    assert rec.requests == []


async def test_usage_recorded_in_cost_meter() -> None:
    rec = Recorder()
    rec.body["usage"]["cost"] = 0.000042
    meter = CostMeter()
    await _adapter(rec, cost_meter=meter).decide(STATE, QUESTIONS, policy=POLICY)
    u = meter.usage
    assert u.prompt_tokens == 1000
    assert u.cost_usd == pytest.approx(0.000042)
    assert u.cost_source == "decision"
    assert u.calls == 1


async def test_usage_priced_from_table_when_not_reported() -> None:
    s = Settings(budget_pricing={MODEL: {"input": 0.000042, "output": 0.0}})
    meter = CostMeter(settings=s)
    await _adapter(Recorder(), cost_meter=meter, settings=s).decide(STATE, QUESTIONS, policy=POLICY)
    assert meter.usage.cost_usd == pytest.approx(1000 / 1000 * 0.000042)
    assert meter.usage.estimated is True


async def test_no_key_sends_no_auth_header() -> None:
    rec = Recorder()
    await _adapter(rec, api_key=None).decide(STATE, QUESTIONS, policy=POLICY)
    assert "authorization" not in rec.requests[0].headers


def test_repr_hides_key() -> None:
    adapter = _adapter(Recorder())
    assert "SECRET" not in repr(adapter)
    assert "SECRET" not in str(vars(adapter))


async def test_batch_endpoint() -> None:
    rec = Recorder(
        {
            "model": MODEL,
            "results": [
                {"model": MODEL, "answers": _answers()},
                {"model": MODEL, "answers": _answers()},
            ],
        }
    )
    adapter = _adapter(rec, base_url="http://laya:8000/v1", name="laya")
    out = await adapter.decide_batch([STATE, STATE], QUESTIONS, policy=POLICY)
    assert len(out) == 2
    assert str(rec.requests[0].url) == "http://laya:8000/v1/systemone/batch"
    assert json.loads(rec.requests[0].content)["states"] == [STATE, STATE]
    assert out[1][CHOICE.id].value == "coder"


async def test_batch_over_64_states_split() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        n = len(json.loads(request.content)["states"])
        return httpx.Response(
            200,
            json={"model": MODEL, "results": [{"model": MODEL, "answers": _answers()}] * n},
        )

    reqs: list[int] = []

    def counting(request: httpx.Request) -> httpx.Response:
        reqs.append(len(json.loads(request.content)["states"]))
        return handler(request)

    adapter = SystemOneHttpDecisionAdapter(
        base_url="http://laya:8000/v1",
        api_key=None,
        model=MODEL,
        transport=httpx.MockTransport(counting),
    )
    out = await adapter.decide_batch([STATE] * 70, QUESTIONS, policy=POLICY)
    assert len(out) == 70
    assert reqs == [64, 6]


async def test_batch_failure_abstains_every_state() -> None:
    adapter = _adapter(Recorder(status=500))
    out = await adapter.decide_batch([STATE, STATE], QUESTIONS, policy=POLICY)
    assert len(out) == 2
    assert all(d.abstained for o in out for d in o.values())


async def test_aclose_closes_owned_client() -> None:
    adapter = _adapter(Recorder())
    await adapter.decide(STATE, QUESTIONS, policy=POLICY)
    await adapter.aclose()
    await adapter.aclose()

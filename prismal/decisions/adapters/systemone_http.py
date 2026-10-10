"""Jev-compatible ``/systemone`` HTTP adapter (Phase DEC — SPEC-DEC-ADP-001, DD-DEC-004).

One adapter serves TypeSafe (``https://api.typesafe.ai/v1``), OpenRouter
(``https://openrouter.ai/api/v1``) and self-hosted ``laya-serve``
(``http://host:port/v1``), selected by ``base_url`` only; it appends
``/systemone`` (and ``/systemone/batch``). The key is sent only as
``Authorization: Bearer`` and never logged, put in a URL or mirrored to
``os.environ``. Every failure becomes an abstention (never raises).
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Any

import httpx

from prismal.budget.types import Usage
from prismal.core.exceptions import DecisionBackendError
from prismal.core.logging import get_logger
from prismal.decisions.adapters._wire import (
    count_model_mismatch,
    parse_payload,
    wire_questions,
)
from prismal.decisions.ports import abstain_all, guarded_decide
from prismal.decisions.types import DecisionKind
from prismal.utils.token_estimate import estimate_tokens

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping, Sequence

    from pydantic import SecretStr

    from prismal.budget.meter import CostMeter
    from prismal.core.config import Settings
    from prismal.decisions.types import Decision, GatePolicy, QuestionSpec

logger = get_logger(__name__)

_MAX_BATCH_STATES = 64  # laya-serve /v1/systemone/batch (DEC0-07)


class SystemOneHttpDecisionAdapter:
    """``DecisionPort`` over the Jev ``/systemone`` wire."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: SecretStr | None,
        model: str,
        name: str = "jev",
        max_choice_options: int = 255,
        max_request_tokens: int = 64_000,
        max_state_question_tokens: int = 32_000,
        languages: Collection[str] | None = None,
        settings: Settings | None = None,
        cost_meter: CostMeter | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.name = name
        self.model = model
        self._url = base_url.rstrip("/") + "/systemone"
        self._api_key = api_key
        self._max_choice_options = max_choice_options
        self._max_request_tokens = max_request_tokens
        self._max_state_question_tokens = max_state_question_tokens
        self._languages = frozenset(languages) if languages is not None else None
        self._settings = settings
        self._cost_meter = cost_meter
        self._transport = transport
        self._client: httpx.AsyncClient | None = None

    def __repr__(self) -> str:
        return f"SystemOneHttpDecisionAdapter(name={self.name!r}, model={self.model!r})"

    # ── DecisionPort ──────────────────────────────────────────────────────────

    def supports(self, question: QuestionSpec, *, language: str) -> bool:
        if self._languages is not None and language not in self._languages:
            return False
        return not (
            question.kind is DecisionKind.CHOICE
            and len(question.options) > self._max_choice_options
        )

    async def decide(
        self,
        state: Mapping[str, str],
        questions: Mapping[str, QuestionSpec],
        *,
        policy: GatePolicy,
    ) -> Mapping[str, Decision]:
        return await guarded_decide(self, questions, policy, lambda qs: self._decide(state, qs))

    async def decide_batch(
        self,
        states: Sequence[Mapping[str, str]],
        questions: Mapping[str, QuestionSpec],
        *,
        policy: GatePolicy,
    ) -> list[Mapping[str, Decision]]:
        """One result per state via ``/systemone/batch`` (chunks of ≤ 64); never raises."""
        out: list[Mapping[str, Decision]] = []
        for start in range(0, len(states), _MAX_BATCH_STATES):
            chunk = list(states[start : start + _MAX_BATCH_STATES])
            results: list[dict[str, Decision]] = []

            async def run(
                qs: Mapping[str, QuestionSpec],
                chunk: list[Mapping[str, str]] = chunk,
                results: list[dict[str, Decision]] = results,
            ) -> dict[str, Decision]:
                results.extend(await self._decide_batch(chunk, qs))
                return results[0] if results else {}

            first = await guarded_decide(self, questions, policy, run)
            if len(results) == len(chunk):
                out.extend(results)
            else:
                out.extend([first] * len(chunk))
        return out

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ── internals ─────────────────────────────────────────────────────────────

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            # The real budget is policy.timeout_ms (asyncio.wait_for); this is a backstop.
            self._client = httpx.AsyncClient(transport=self._transport, timeout=30.0)
        return self._client

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._api_key is not None and self._api_key.get_secret_value():
            headers["Authorization"] = f"Bearer {self._api_key.get_secret_value()}"
        return headers

    def _over_limit(self, body: dict[str, Any], state: Mapping[str, str]) -> bool:
        if estimate_tokens(json.dumps(body, ensure_ascii=False)) > self._max_request_tokens:
            return True
        state_tokens = estimate_tokens(json.dumps(dict(state), ensure_ascii=False))
        longest = max(
            (
                estimate_tokens(json.dumps(q, ensure_ascii=False))
                for q in body["questions"].values()
            ),
            default=0,
        )
        return state_tokens + longest > self._max_state_question_tokens

    async def _decide(
        self, state: Mapping[str, str], questions: Mapping[str, QuestionSpec]
    ) -> dict[str, Decision]:
        body = {"model": self.model, "state": dict(state), "questions": wire_questions(questions)}
        if self._over_limit(body, state):
            return abstain_all(questions, backend=self.name, model=self.model, reason="over_limit")
        start = time.perf_counter()
        response = await self._http().post(self._url, json=body, headers=self._headers())
        latency = (time.perf_counter() - start) * 1000.0
        if response.status_code >= 400:
            raise DecisionBackendError(f"{self.name} HTTP {response.status_code}")
        payload = response.json()
        return self._parse(payload, questions, latency)

    async def _decide_batch(
        self, states: list[Mapping[str, str]], questions: Mapping[str, QuestionSpec]
    ) -> list[dict[str, Decision]]:
        body = {
            "model": self.model,
            "states": [dict(s) for s in states],
            "questions": wire_questions(questions),
        }
        start = time.perf_counter()
        response = await self._http().post(f"{self._url}/batch", json=body, headers=self._headers())
        latency = (time.perf_counter() - start) * 1000.0
        if response.status_code >= 400:
            raise DecisionBackendError(f"{self.name} HTTP {response.status_code}")
        payload = response.json()
        results = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(results, list) or len(results) != len(states):
            raise DecisionBackendError(f"{self.name} batch returned a malformed result list")
        return [self._parse(item, questions, latency) for item in results]

    def _parse(
        self, payload: Any, questions: Mapping[str, QuestionSpec], latency: float
    ) -> dict[str, Decision]:
        parsed = parse_payload(
            payload, questions, backend=self.name, model=self.model, latency_ms=latency
        )
        if parsed is None:
            count_model_mismatch(self.name)
            logger.warning("decision_model_mismatch", backend=self.name, pinned=self.model)
            return abstain_all(
                questions,
                backend=self.name,
                model=self.model,
                reason="model_mismatch",
                latency_ms=latency,
            )
        self._meter(payload.get("usage"))
        return parsed

    def _meter(self, usage: Any) -> None:
        if self._cost_meter is None or not isinstance(usage, dict):
            return
        try:
            input_tokens = int(usage.get("input_tokens", 0) or 0)
            output_tokens = int(usage.get("output_tokens", 0) or 0)
            reported = usage.get("cost")
            if isinstance(reported, int | float):
                cost, estimated = float(reported), False
            else:
                from prismal.providers.cost import compute_cost_usd

                est = compute_cost_usd(self.model, input_tokens, 0, settings=self._settings)
                cost, estimated = est.cost_usd, est.estimated
            self._cost_meter.record(
                Usage(
                    prompt_tokens=input_tokens,
                    completion_tokens=output_tokens,
                    cost_usd=cost,
                    calls=1,
                    estimated=estimated,
                    cost_source="decision",
                ),
                pattern="decision",
                model=self.model,
            )
        except Exception as exc:  # metering is fail-open
            logger.warning("decision_metering_failed", error_type=type(exc).__name__)

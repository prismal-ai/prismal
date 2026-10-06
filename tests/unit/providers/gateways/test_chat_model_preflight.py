"""GatewayChatLiteLLM quota preflight (Phase GW — SPEC-GW-QUOTA-001; GW3-07, GW3-08)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from langchain_core.messages import HumanMessage

from prismal.core.exceptions import GatewayQuotaExhausted
from prismal.providers.gateways.chat_model import GatewayChatLiteLLM
from prismal.providers.gateways.quota import (
    OPENROUTER_FREE_DAILY_LIMIT,
    QuotaLedger,
    QuotaVerdict,
)

from .conftest import gw_settings
from .test_chat_model import FakeClient, _response, _stream_chunks

SENTINEL = "sk-SENTINEL-preflight"


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


def _go_ledger(clock: Clock | None = None, **kw: Any) -> QuotaLedger:
    settings = gw_settings(opencode_go_enabled=True, opencode_go_monthly_cap_usd=10.0, **kw)
    return QuotaLedger(settings=settings, org_id="acme", clock=clock or Clock())


def _llm(client: FakeClient, **kw: Any) -> GatewayChatLiteLLM:
    base: dict[str, Any] = {
        "model": "openai/glm-5.3",
        "api_base": "https://opencode.ai/zen/go/v1",
        "api_key": SENTINEL,
        "gateway_name": "opencode-go",
        "resolved_model_ref": "gw:opencode-go/glm-5.3",
        "requested_model": "glm-5.3",
        "cost_mode": "subscription",
        "max_retries": 0,
    }
    base.update(kw)
    llm = GatewayChatLiteLLM(**base)
    llm.client = client
    return llm


def test_no_ledger_calls_through() -> None:
    client = FakeClient(_response())
    _llm(client).invoke([HumanMessage(content="hi")])
    assert len(client.calls) == 1


def test_allow_calls_through_with_primary_model() -> None:
    client = FakeClient(_response())
    _llm(client, quota_ledger=_go_ledger()).invoke([HumanMessage(content="hi")])
    assert client.calls[0]["model"] == "openai/glm-5.3"


def test_exhausted_window_refuses_before_network() -> None:
    ledger = _go_ledger()
    ledger.record("opencode-go", "glm-5.3", equivalent_usd=2.0)
    client = FakeClient(_response())
    with pytest.raises(GatewayQuotaExhausted) as info:
        _llm(client, quota_ledger=ledger).invoke([HumanMessage(content="hi")])
    assert (info.value.gateway, info.value.window) == ("opencode-go", "5h")
    assert client.calls == []
    assert SENTINEL not in str(info.value)


async def test_exhausted_window_refuses_async_and_streaming() -> None:
    ledger = _go_ledger()
    ledger.record("opencode-go", "glm-5.3", equivalent_usd=2.0)
    client = FakeClient(_stream_chunks())
    llm = _llm(client, quota_ledger=ledger)
    with pytest.raises(GatewayQuotaExhausted):
        await llm.ainvoke([HumanMessage(content="hi")])
    with pytest.raises(GatewayQuotaExhausted):
        list(llm.stream([HumanMessage(content="hi")]))
    with pytest.raises(GatewayQuotaExhausted):
        async for _ in llm.astream([HumanMessage(content="hi")]):
            pass
    assert client.calls == []


def _downgrade_ledger() -> QuotaLedger:
    ledger = _go_ledger(
        opencode_go_quota_action="downgrade", opencode_go_downgrade_model="gw:opencode-go/glm-5.2"
    )
    ledger.record("opencode-go", "glm-5.3", equivalent_usd=2.0)
    return ledger


def test_downgrade_uses_downgrade_model_for_this_call() -> None:
    client = FakeClient(_response(model="glm-5.2"))
    llm = _llm(client, quota_ledger=_downgrade_ledger(), downgrade_model="openai/glm-5.2")
    llm.invoke([HumanMessage(content="hi")])
    assert client.calls[0]["model"] == "openai/glm-5.2"
    assert llm.model == "openai/glm-5.3"  # the instance is not mutated


async def test_downgrade_async_and_stream() -> None:
    client = FakeClient(_response(model="glm-5.2"))
    llm = _llm(client, quota_ledger=_downgrade_ledger(), downgrade_model="openai/glm-5.2")
    await llm.ainvoke([HumanMessage(content="hi")])
    stream_client = FakeClient(_stream_chunks())
    streaming = _llm(
        stream_client,
        quota_ledger=_downgrade_ledger(),
        downgrade_model="openai/glm-5.2",
        streaming=True,
    )
    list(streaming.stream([HumanMessage(content="hi")]))
    assert client.calls[0]["model"] == "openai/glm-5.2"
    assert stream_client.calls[0]["model"] == "openai/glm-5.2"


def test_downgrade_without_downgrade_model_refuses() -> None:
    client = FakeClient(_response())
    with pytest.raises(GatewayQuotaExhausted):
        _llm(client, quota_ledger=_downgrade_ledger()).invoke([HumanMessage(content="hi")])
    assert client.calls == []


class ExplodingLedger:
    def preflight(self, *a: Any, **kw: Any) -> QuotaVerdict:
        raise RuntimeError("boom")


def test_ledger_exception_is_fail_open(otel: Any) -> None:
    client = FakeClient(_response())
    _llm(client, quota_ledger=ExplodingLedger()).invoke([HumanMessage(content="hi")])
    assert len(client.calls) == 1
    assert {"gateway": "opencode-go", "kind": "ledger"} in otel.counts["gateway_errors"]


class RecordingLedger:
    def __init__(self) -> None:
        self.preflights: list[tuple[str, str, float]] = []
        self.records = 0

    def preflight(self, gateway: str, model: str, *, est_equivalent_usd: float) -> QuotaVerdict:
        self.preflights.append((gateway, model, est_equivalent_usd))
        return QuotaVerdict(
            allowed=True, action="allow", exhausted_window=None, downgrade_model=None
        )

    def record(self, *a: Any, **kw: Any) -> None:
        self.records += 1


def test_preflight_passes_gateway_model_and_zero_estimate_and_never_records() -> None:
    ledger = RecordingLedger()
    _llm(FakeClient(_response()), quota_ledger=ledger).invoke([HumanMessage(content="hi")])
    assert ledger.preflights == [("opencode-go", "glm-5.3", 0.0)]
    assert ledger.records == 0


# ── GW3-08: OpenRouter :free per-day window end-to-end ───────────────────────


def test_openrouter_free_daily_window_end_to_end() -> None:
    clock = Clock()
    ledger = QuotaLedger(settings=gw_settings(openrouter_enabled=True), org_id="o", clock=clock)
    model = "deepseek/deepseek-r1:free"
    for _ in range(OPENROUTER_FREE_DAILY_LIMIT):
        ledger.record("openrouter", model, equivalent_usd=0.0)
    client = FakeClient(_response(model="deepseek/deepseek-r1"))
    llm = _llm(
        client,
        model=f"openrouter/{model}",
        api_base="https://openrouter.ai/api/v1",
        gateway_name="openrouter",
        resolved_model_ref=f"gw:openrouter/{model}",
        requested_model=model,
        cost_mode="metered",
        quota_ledger=ledger,
    )
    with pytest.raises(GatewayQuotaExhausted) as info:
        llm.invoke([HumanMessage(content="hi")])
    assert info.value.window == "day"
    assert client.calls == []
    clock.now = datetime(2026, 10, 6, 0, 0, 1, tzinfo=UTC)
    llm.invoke([HumanMessage(content="hi")])
    assert len(client.calls) == 1


# ── GW5-05: the downgrade is visible in the call metadata ────────────────────


def _meta_of(msg: Any) -> Any:
    from prismal.providers.gateways.base import GatewayCallMeta

    return GatewayCallMeta.from_dict(msg.response_metadata["prismal_gateway"])


def test_downgrade_window_recorded_in_meta() -> None:
    client = FakeClient(_response(model="glm-5.2"))
    llm = _llm(client, quota_ledger=_downgrade_ledger(), downgrade_model="openai/glm-5.2")
    meta = _meta_of(llm.invoke([HumanMessage(content="hi")]))
    assert meta.quota_downgrade is not None


def test_downgrade_window_recorded_in_stream_meta() -> None:
    llm = _llm(
        FakeClient(_stream_chunks()),
        quota_ledger=_downgrade_ledger(),
        downgrade_model="openai/glm-5.2",
        streaming=True,
    )
    chunks = list(llm.stream([HumanMessage(content="hi")]))
    merged = chunks[0]
    for c in chunks[1:]:
        merged = merged + c  # type: ignore[assignment]
    assert _meta_of(merged).quota_downgrade is not None


def test_no_downgrade_meta_is_none() -> None:
    meta = _meta_of(_llm(FakeClient(_response())).invoke([HumanMessage(content="hi")]))
    assert meta.quota_downgrade is None

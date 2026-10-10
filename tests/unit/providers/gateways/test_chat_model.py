"""GatewayChatLiteLLM raw-response capture (Phase GW — SPEC-GW-REG-001, VERIFY.md GW0-03)."""

from __future__ import annotations

import contextlib
import json
from collections.abc import AsyncIterator
from typing import Any

import litellm
import pytest
from langchain_core.messages import HumanMessage
from litellm import ModelResponse
from litellm.types.utils import ModelResponseStream

from prismal.core.exceptions import GatewayPaymentRequired
from prismal.providers.gateways.base import GatewayCallMeta
from prismal.providers.gateways.chat_model import GatewayChatLiteLLM, bounded_model_label

SENTINEL = "sk-SENTINEL-chat"


def _response(
    model: str = "anthropic/claude-sonnet-4.5", cost: float | None = 0.01
) -> ModelResponse:
    usage: dict[str, Any] = {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}
    if cost is not None:
        usage["cost"] = cost
    return ModelResponse(
        id="gen-1",
        model=model,
        choices=[
            {"message": {"role": "assistant", "content": "hi"}, "finish_reason": "stop", "index": 0}
        ],
        usage=usage,
    )


def _stream_chunks(model: str = "anthropic/claude-sonnet-4.5") -> list[ModelResponseStream]:
    return [
        ModelResponseStream(
            id="gen-2",
            model=model,
            choices=[{"delta": {"role": "assistant", "content": "he"}, "index": 0}],
        ),
        ModelResponseStream(
            id="gen-2", model=model, choices=[{"delta": {"content": "llo"}, "index": 0}]
        ),
        ModelResponseStream(
            id="gen-2",
            model=model,
            choices=[],
            usage={"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5, "cost": 0.004},
        ),
    ]


class FakeClient:
    def __init__(self, result: Any = None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def completion(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        if kwargs.get("stream"):
            return iter(self.result)
        return self.result

    async def acompletion(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        if kwargs.get("stream"):

            async def gen() -> AsyncIterator[Any]:
                for c in self.result:
                    yield c

            return gen()
        return self.result


def _llm(client: FakeClient, **kw: Any) -> GatewayChatLiteLLM:
    base: dict[str, Any] = {
        "model": "openrouter/anthropic/claude-sonnet-4.5:floor",
        "api_base": "https://openrouter.ai/api/v1",
        "api_key": SENTINEL,
        "extra_headers": {"X-OpenRouter-Title": "prismal"},
        "model_kwargs": {"extra_body": {"provider": {"zdr": True}}},
        "gateway_name": "openrouter",
        "resolved_model_ref": "gw:openrouter/anthropic/claude-sonnet-4.5:floor",
        "requested_model": "anthropic/claude-sonnet-4.5:floor",
        "max_retries": 0,
    }
    base.update(kw)
    llm = GatewayChatLiteLLM(**base)
    llm.client = client
    return llm


def _meta(msg: Any) -> GatewayCallMeta:
    meta = GatewayCallMeta.from_dict(msg.response_metadata.get("prismal_gateway"))
    assert meta is not None
    return meta


def test_repr_masks_api_key() -> None:
    llm = _llm(FakeClient(_response()))
    assert SENTINEL not in repr(llm)
    assert SENTINEL not in str(llm)


def test_sync_invoke_captures_meta_and_passes_gateway_kwargs(otel: type) -> None:
    client = FakeClient(_response())
    msg = _llm(client).invoke([HumanMessage(content="hi")])
    meta = _meta(msg)
    assert meta.gateway == "openrouter"
    assert meta.requested_model == "anthropic/claude-sonnet-4.5:floor"
    assert meta.served_model == "anthropic/claude-sonnet-4.5"
    assert meta.generation_id == "gen-1"
    assert meta.reported_cost_usd == 0.01
    assert meta.cost_mode == "metered"
    assert meta.fallback_used is False
    call = client.calls[0]
    assert call["api_base"] == "https://openrouter.ai/api/v1"
    assert call["api_key"] == SENTINEL
    assert call["extra_headers"] == {"X-OpenRouter-Title": "prismal"}
    assert call["extra_body"] == {"provider": {"zdr": True}}
    assert otel.counts["gateway_requests"][0]["gateway"] == "openrouter"
    assert "gateway_fallbacks" not in otel.counts


async def test_async_invoke_captures_meta() -> None:
    msg = await _llm(FakeClient(_response())).ainvoke([HumanMessage(content="hi")])
    assert _meta(msg).generation_id == "gen-1"


def test_hidden_params_cost_used_when_usage_cost_absent() -> None:
    resp = _response(cost=None)
    resp._hidden_params["response_cost"] = 0.07
    assert _meta(_llm(FakeClient(resp)).invoke("hi")).reported_cost_usd == 0.07


def test_missing_cost_counts_cost_missing(otel: type) -> None:
    msg = _llm(FakeClient(_response(cost=None)), reports_cost=True).invoke("hi")
    assert _meta(msg).reported_cost_usd is None
    assert otel.counts["gateway_errors"] == [{"gateway": "openrouter", "kind": "cost_missing"}]


def test_served_model_differs_counts_fallback(otel: type) -> None:
    msg = _llm(FakeClient(_response(model="deepseek/deepseek-chat"))).invoke("hi")
    assert _meta(msg).fallback_used is True
    assert otel.counts["gateway_fallbacks"] == [{"gateway": "openrouter"}]


def test_dated_served_model_is_not_a_fallback() -> None:
    msg = _llm(FakeClient(_response(model="anthropic/claude-sonnet-4.5-20260101"))).invoke("hi")
    assert _meta(msg).fallback_used is False


def test_subscription_cost_mode_propagates() -> None:
    msg = _llm(FakeClient(_response()), cost_mode="subscription").invoke("hi")
    assert _meta(msg).cost_mode == "subscription"


def test_sync_stream_captures_final_meta() -> None:
    llm = _llm(FakeClient(_stream_chunks()), streaming=True)
    chunks = list(llm.stream("hi"))
    assert "".join(str(c.content) for c in chunks) == "hello"
    merged = chunks[0]
    for c in chunks[1:]:
        merged = merged + c  # type: ignore[assignment]
    meta = _meta(merged)
    assert meta.generation_id == "gen-2"
    assert meta.reported_cost_usd == 0.004
    assert meta.served_model == "anthropic/claude-sonnet-4.5"


async def test_async_stream_captures_final_meta() -> None:
    llm = _llm(FakeClient(_stream_chunks()), streaming=True)
    chunks = [c async for c in llm.astream("hi")]
    merged = chunks[0]
    for c in chunks[1:]:
        merged = merged + c  # type: ignore[assignment]
    assert _meta(merged).reported_cost_usd == 0.004


def test_invoke_with_streaming_flag_uses_stream_path() -> None:
    msg = _llm(FakeClient(_stream_chunks()), streaming=True).invoke("hi")
    assert msg.content == "hello"
    assert _meta(msg).generation_id == "gen-2"


def _err402(source: str) -> Exception:
    body = json.dumps(
        {"error": {"code": 402, "message": "no", "metadata": {"limit_source": source}}}
    )
    return litellm.exceptions.APIError(
        status_code=402, message=body, llm_provider="openrouter", model="x"
    )


@pytest.mark.parametrize(
    ("source", "reason"),
    [
        ("openrouter_credits", "account_balance"),
        ("openrouter_key_limit", "key_limit"),
        ("openrouter_in_flight_budget", "in_flight_budget"),
        ("something_else", "unknown"),
    ],
)
def test_402_maps_to_payment_required_without_retry(source: str, reason: str, otel: type) -> None:
    client = FakeClient(error=_err402(source))
    llm = _llm(client, max_retries=3)
    with pytest.raises(GatewayPaymentRequired) as exc:
        llm.invoke("hi")
    assert exc.value.reason == reason
    assert len(client.calls) == 1  # never retried
    assert otel.counts["gateway_errors"] == [{"gateway": "openrouter", "kind": "payment_required"}]


async def test_402_async_maps_too() -> None:
    client = FakeClient(error=_err402("openrouter_credits"))
    with pytest.raises(GatewayPaymentRequired):
        await _llm(client, max_retries=3).ainvoke("hi")
    assert len(client.calls) == 1


def test_429_counted_and_reraised(otel: type) -> None:
    err = litellm.exceptions.RateLimitError(
        message="slow down", llm_provider="openrouter", model="x"
    )
    with pytest.raises(litellm.exceptions.RateLimitError):
        _llm(FakeClient(error=err), max_retries=0).invoke("hi")
    assert otel.counts["gateway_errors"] == [{"gateway": "openrouter", "kind": "rate_limited"}]


def test_bounded_model_label_caps_cardinality(monkeypatch: pytest.MonkeyPatch) -> None:
    import prismal.providers.gateways.chat_model as mod

    monkeypatch.setattr(mod, "_MAX_MODEL_LABELS", 2)
    monkeypatch.setattr(mod, "_SEEN_MODEL_LABELS", set())
    assert bounded_model_label("a") == "a"
    assert bounded_model_label("b") == "b"
    assert bounded_model_label("c") == "other"
    assert bounded_model_label("a") == "a"
    assert bounded_model_label(None) == "unknown"


def test_generate_iterator_helper_is_not_exposed() -> None:
    # the private capture kwarg must never reach litellm
    client = FakeClient(_stream_chunks())
    list(_llm(client, streaming=True).stream("hi"))
    assert all("_prismal_capture" not in c for c in client.calls)


# ── SPEC-GW-OR-001: require_parameters when tools / a schema are bound ───────


def _add(a: int, b: int) -> int:
    """Add two integers."""
    return a + b


def test_bind_tools_sets_require_parameters_on_provider_object() -> None:
    client = FakeClient(_response())
    llm = _llm(client, require_parameters_on_tools=True)
    llm.bind_tools([_add]).invoke("hi")
    assert client.calls[0]["extra_body"]["provider"] == {"zdr": True, "require_parameters": True}
    llm.invoke("hi")  # the unbound model is unchanged
    assert client.calls[1]["extra_body"]["provider"] == {"zdr": True}


def test_structured_output_sets_require_parameters() -> None:
    from pydantic import BaseModel

    class Answer(BaseModel):
        text: str

    client = FakeClient(_response())
    llm = _llm(client, require_parameters_on_tools=True)
    with contextlib.suppress(Exception):  # the fake response is not valid structured output
        llm.with_structured_output(Answer).invoke("hi")
    assert client.calls[0]["extra_body"]["provider"]["require_parameters"] is True


def test_bind_tools_without_flag_or_provider_is_untouched() -> None:
    client = FakeClient(_response())
    _llm(client).bind_tools([_add]).invoke("hi")
    assert "require_parameters" not in client.calls[0]["extra_body"]["provider"]
    client2 = FakeClient(_response())
    _llm(client2, require_parameters_on_tools=True, model_kwargs={}).bind_tools([_add]).invoke("hi")
    assert "extra_body" not in client2.calls[0]

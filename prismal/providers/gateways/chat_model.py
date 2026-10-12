"""``GatewayChatLiteLLM`` — the innermost gateway chat model (Phase GW — SPEC-GW-REG-001).

A thin ``ChatLiteLLM`` subclass (ADR-WAVE-001 §2.2: innermost, owns the
raw-response seam). It captures what ``langchain_litellm`` drops — served
model, generation id and billed cost (``usage.cost`` / ``_hidden_params``) —
into ``response_metadata["prismal_gateway"]``, for sync, async and streaming
calls (VERIFY.md GW0-03: streaming never reaches ``_create_chat_result``, so
the raw chunks are observed inside ``(a)completion_with_retry``). It also maps
HTTP 402 to a non-retryable ``GatewayPaymentRequired`` inside the retried
function, and runs the quota preflight before any network call.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator, Iterator, Mapping
from typing import TYPE_CHECKING, Any, Literal

from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk, ChatResult
from langchain_litellm import ChatLiteLLM
from pydantic import Field

from prismal.core.exceptions import GatewayPaymentRequired, GatewayQuotaExhausted
from prismal.core.logging import get_logger
from prismal.providers.gateways.base import GatewayCallMeta, read_reported_cost
from prismal.providers.gateways.openai_compatible import count

if TYPE_CHECKING:
    from langchain_core.callbacks import (
        AsyncCallbackManagerForLLMRun,
        CallbackManagerForLLMRun,
    )
    from langchain_core.messages import BaseMessage

logger = get_logger("prismal.providers.gateways.chat_model")

_CAPTURE_KWARG = "_prismal_capture"
_DOWNGRADE_KEY = "prismal_quota_downgrade"
_LIMIT_SOURCES = {
    "openrouter_credits": "account_balance",
    "openrouter_key_limit": "key_limit",
    "openrouter_in_flight_budget": "in_flight_budget",
}
_LIMIT_SOURCE_RE = re.compile(r'"limit_source"\s*:\s*"([a-z_]+)"')

# Preflight estimate (GW3-07): token counts are unknown before the call, so the
# preflight only refuses windows that are *already* exhausted (or would be by
# this estimate). 0.0 never refuses a call that still fits; the real usage is
# recorded afterwards from the cost meter (GW5-03).
PREFLIGHT_ESTIMATE_USD = 0.0

# Served-model label cardinality guard (ARCHITECTURE §6): free-text model ids
# beyond the cap are reported as "other".
_MAX_MODEL_LABELS = 200
_SEEN_MODEL_LABELS: set[str] = set()


def bounded_model_label(model: str | None) -> str:
    """OTel-safe model label: the id while under the cap, else ``"other"``."""
    if not model:
        return "unknown"
    if model in _SEEN_MODEL_LABELS:
        return model
    if len(_SEEN_MODEL_LABELS) >= _MAX_MODEL_LABELS:
        return "other"
    _SEEN_MODEL_LABELS.add(model)
    return model


class _Capture:
    """Mutable holder for what the raw response/chunks reveal."""

    def __init__(self) -> None:
        self.served_model: str | None = None
        self.generation_id: str | None = None
        self.cost: float | None = None
        self.quota_downgrade: str | None = None

    def observe(self, raw: Any) -> None:
        """Record served model, generation id, reported cost and quota downgrade from *raw*."""
        getter = getattr(raw, "get", None)
        if callable(getter):
            model, gen_id = getter("model"), getter("id")
            if isinstance(model, str) and model:
                self.served_model = model
            if isinstance(gen_id, str) and gen_id:
                self.generation_id = gen_id
        reported = read_reported_cost(raw)
        if reported is not None:
            self.cost = reported.cost_usd
        hidden = getattr(raw, "_hidden_params", None)
        if isinstance(hidden, dict) and isinstance(hidden.get(_DOWNGRADE_KEY), str):
            self.quota_downgrade = hidden[_DOWNGRADE_KEY]


def _payment_required(exc: Exception, gateway: str) -> GatewayPaymentRequired | None:
    """Map an HTTP 402 to ``GatewayPaymentRequired`` (reason from ``limit_source``)."""
    if getattr(exc, "status_code", None) != 402:
        return None
    source: str | None = None
    body = getattr(exc, "body", None)
    if isinstance(body, Mapping):
        error = body.get("error", body)
        metadata = error.get("metadata") if isinstance(error, Mapping) else None
        if isinstance(metadata, Mapping):
            raw = metadata.get("limit_source")
            source = raw if isinstance(raw, str) else None
    if source is None:
        match = _LIMIT_SOURCE_RE.search(str(exc))
        source = match.group(1) if match else None
    return GatewayPaymentRequired(
        gateway=gateway, reason=_LIMIT_SOURCES.get(source or "", "unknown")
    )


class GatewayChatLiteLLM(ChatLiteLLM):
    """``ChatLiteLLM`` bound to one gateway target, with metadata capture."""

    gateway_name: str = ""
    resolved_model_ref: str = ""
    requested_model: str = ""
    cost_mode: Literal["metered", "subscription"] = "metered"
    reports_cost: bool = False
    # OpenRouter: provider.require_parameters=true once tools / a schema are bound.
    require_parameters_on_tools: bool = False
    quota_ledger: Any = Field(default=None, exclude=True)
    downgrade_model: str | None = None  # litellm model string used on a quota downgrade

    def __repr_args__(self) -> Any:
        """Never expose the key in ``repr`` / ``str`` (VERIFY.md GW0-03)."""
        for name, value in super().__repr_args__():
            if name == "api_key":
                yield name, "**********" if value else value
            else:
                yield name, value

    def bind_tools(self, tools: Any, tool_choice: Any = None, **kwargs: Any) -> Any:
        """Bind tools; with a ``provider`` object, also require parameter support."""
        return ChatLiteLLM.bind_tools(
            self._requiring_parameters(), tools, tool_choice=tool_choice, **kwargs
        )

    def with_structured_output(self, schema: Any, **kwargs: Any) -> Any:
        """Structured output; with a ``provider`` object, require parameter support."""
        return ChatLiteLLM.with_structured_output(self._requiring_parameters(), schema, **kwargs)

    def _requiring_parameters(self) -> GatewayChatLiteLLM:
        """A copy with ``provider.require_parameters=true`` (SPEC-GW-OR-001), or *self*."""
        extra_body = self.model_kwargs.get("extra_body")
        if not (self.require_parameters_on_tools and isinstance(extra_body, dict)):
            return self
        provider = extra_body.get("provider")
        if not isinstance(provider, dict):
            return self
        patched = {
            **self.model_kwargs,
            "extra_body": {**extra_body, "provider": {**provider, "require_parameters": True}},
        }
        return self.model_copy(update={"model_kwargs": patched})

    # ── raw-response seam ────────────────────────────────────────────────────

    def completion_with_retry(
        self, run_manager: CallbackManagerForLLMRun | None = None, **kwargs: Any
    ) -> Any:
        """Sync completion with quota preflight (may swap in the downgrade model).

        Streaming responses are wrapped so the capture sees every chunk.
        """
        capture: _Capture | None = kwargs.pop(_CAPTURE_KWARG, None)
        override = self._preflight()
        if override is not None:
            kwargs["model"] = override[0]
        result = super().completion_with_retry(run_manager=run_manager, **kwargs)
        if capture is not None and kwargs.get("stream"):
            capture.quota_downgrade = override[1] if override is not None else None
            return self._observe_sync(result, capture)
        return _mark_downgrade(result, override)

    async def acompletion_with_retry(
        self, run_manager: AsyncCallbackManagerForLLMRun | None = None, **kwargs: Any
    ) -> Any:
        """Async completion with quota preflight (may swap in the downgrade model).

        Streaming responses are wrapped so the capture sees every chunk.
        """
        capture: _Capture | None = kwargs.pop(_CAPTURE_KWARG, None)
        override = self._preflight()
        if override is not None:
            kwargs["model"] = override[0]
        result = await super().acompletion_with_retry(run_manager=run_manager, **kwargs)
        if capture is not None and kwargs.get("stream"):
            capture.quota_downgrade = override[1] if override is not None else None
            return self._observe_async(result, capture)
        return _mark_downgrade(result, override)

    def model_post_init(self, context: Any, /) -> None:
        """Wrap the litellm client so HTTP 402/429 map to gateway errors."""
        super().model_post_init(context)
        object.__setattr__(self, "client", _MappedClient(self))

    def __setattr__(self, name: str, value: Any) -> None:
        if name == "client" and not isinstance(value, _MappedClient):
            value = _MappedClient(self, value)
        super().__setattr__(name, value)

    @staticmethod
    def _observe_sync(stream: Any, capture: _Capture) -> Iterator[Any]:
        for chunk in stream:
            capture.observe(chunk)
            yield chunk

    @staticmethod
    async def _observe_async(stream: Any, capture: _Capture) -> AsyncIterator[Any]:
        async for chunk in stream:
            capture.observe(chunk)
            yield chunk

    def _create_chat_result(self, response: Mapping[str, Any]) -> ChatResult:
        result = super()._create_chat_result(response)
        capture = _Capture()
        capture.observe(response)
        meta = self._finish(capture)
        for gen in result.generations:
            if isinstance(gen.message, AIMessage):
                gen.message.response_metadata = {
                    **gen.message.response_metadata,
                    "prismal_gateway": meta,
                }
        return result

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        capture = _Capture()
        yield from super()._stream(
            messages, stop=stop, run_manager=run_manager, **{**kwargs, _CAPTURE_KWARG: capture}
        )
        yield self._meta_chunk(capture)

    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        capture = _Capture()
        async for chunk in super()._astream(
            messages, stop=stop, run_manager=run_manager, **{**kwargs, _CAPTURE_KWARG: capture}
        ):
            yield chunk
        yield self._meta_chunk(capture)

    def _meta_chunk(self, capture: _Capture) -> ChatGenerationChunk:
        return ChatGenerationChunk(
            message=AIMessageChunk(
                content="", response_metadata={"prismal_gateway": self._finish(capture)}
            )
        )

    def _finish(self, capture: _Capture) -> dict[str, Any]:
        """Build the call metadata and emit the per-call gateway metrics."""
        served = capture.served_model
        requested = self.requested_model
        fallback = bool(served) and not _same_model(requested, served or "")
        meta = GatewayCallMeta(
            gateway=self.gateway_name,
            requested_model=requested,
            served_model=served,
            generation_id=capture.generation_id,
            reported_cost_usd=capture.cost,
            cost_mode=self.cost_mode,
            fallback_used=fallback,
            quota_downgrade=capture.quota_downgrade,
        )
        count(
            "gateway_requests",
            gateway=self.gateway_name,
            requested_model=requested,
            served_model=bounded_model_label(served),
        )
        if fallback:
            count("gateway_fallbacks", gateway=self.gateway_name)
            logger.debug(
                "gateway.served_model_differs",
                gateway=self.gateway_name,
                requested=requested,
                served=served,
            )
        if capture.cost is None and self.reports_cost:
            count("gateway_errors", gateway=self.gateway_name, kind="cost_missing")
        return meta.to_dict()

    def _preflight(self) -> tuple[str, str] | None:
        """Quota preflight before any network call (SPEC-GW-QUOTA-001; GW3-07).

        Returns ``(override_model, exhausted_window)`` for a downgrade, else ``None``.
        Raises ``GatewayQuotaExhausted`` on ``refuse`` — or on ``downgrade`` when
        no downgrade model is configured. Ledger failures are fail-open. The
        ledger is never written here (GW5-03 records from the cost meter).
        """
        ledger = self.quota_ledger
        if ledger is None:
            return None
        try:
            verdict = ledger.preflight(
                self.gateway_name,
                self.requested_model,
                est_equivalent_usd=PREFLIGHT_ESTIMATE_USD,
            )
        except Exception as exc:
            count("gateway_errors", gateway=self.gateway_name, kind="ledger")
            logger.warning(
                "gateway.preflight_failed", gateway=self.gateway_name, error=type(exc).__name__
            )
            return None
        if verdict.action == "allow":
            return None
        window = verdict.exhausted_window or "unknown"
        if verdict.action == "downgrade" and self.downgrade_model:
            logger.info(
                "gateway.quota_downgrade",
                gateway=self.gateway_name,
                window=window,
                requested=self.requested_model,
                downgrade=self.downgrade_model,
            )
            return self.downgrade_model, window
        raise GatewayQuotaExhausted(gateway=self.gateway_name, window=window)


def _mark_downgrade(result: Any, override: tuple[str, str] | None) -> Any:
    """Tag a raw (non-streaming) response with the downgrade window, if any."""
    if override is not None:
        hidden = getattr(result, "_hidden_params", None)
        if isinstance(hidden, dict):
            hidden[_DOWNGRADE_KEY] = override[1]
    return result


def _same_model(requested: str, served: str) -> bool:
    """A served id equal to — or a dated/suffixed form of — the requested one."""
    base = requested.split(":", 1)[0]
    return served == requested or served.startswith(base)


class _MappedClient:
    """Wraps the litellm module so 402s are mapped *inside* the retried call."""

    def __init__(self, owner: GatewayChatLiteLLM, inner: Any = None) -> None:
        import litellm

        self._owner = owner
        self._inner = inner if inner is not None else litellm

    def completion(self, **kwargs: Any) -> Any:
        """Delegate to ``litellm.completion``, mapping gateway HTTP errors."""
        try:
            return self._inner.completion(**kwargs)
        except Exception as exc:
            raise self._mapped(exc) from exc

    async def acompletion(self, **kwargs: Any) -> Any:
        """Delegate to ``litellm.acompletion``, mapping gateway HTTP errors."""
        try:
            return await self._inner.acompletion(**kwargs)
        except Exception as exc:
            raise self._mapped(exc) from exc

    def _mapped(self, exc: Exception) -> Exception:
        gateway = self._owner.gateway_name
        payment = _payment_required(exc, gateway)
        if payment is not None:
            count("gateway_errors", gateway=gateway, kind="payment_required")
            return payment
        if getattr(exc, "status_code", None) == 429:
            count("gateway_errors", gateway=gateway, kind="rate_limited")
        return exc

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


__all__ = ["GatewayChatLiteLLM", "bounded_model_label"]

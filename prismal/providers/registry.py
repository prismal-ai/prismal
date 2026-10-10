"""
Provider registry — LiteLLM-backed LLM factory with usage tracking.

Provides provider-agnostic LLM access via ChatLiteLLM. All provider routing is handled
by LiteLLM; no provider-specific imports here.
"""

from __future__ import annotations

import os
from abc import ABCMeta
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import litellm

# from langchain_community.chat_models import ChatLiteLLM
from langchain_litellm import ChatLiteLLM

from prismal.core.config import Settings, get_settings
from prismal.core.exceptions import GatewayConfigError, GatewayPaymentRequired, GatewayPolicyError
from prismal.core.logging import get_logger
from prismal.monitoring.langfuse_client import LangfuseManager
from prismal.monitoring.otel import OTelManager

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel, LanguageModelInput
    from langchain_core.messages import BaseMessage
    from langchain_core.runnables.fallbacks import RunnableWithFallbacks

    from prismal.agents.state import AgentState
    from prismal.providers.gateways.base import ResolvedModel
    from prismal.providers.gateways.router import GatewayRouter
    from prismal.routing.types import TaskFeatures

logger = get_logger("prismal.providers.registry")


@dataclass
class ModelInfo:
    """Metadata for a configured LLM model."""

    id: str
    provider: str
    available: bool = True


@dataclass
class TokenUsage:
    """Cumulative token usage and cost for a session."""

    session_id: str
    total_tokens: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    estimated_cost: float = 0.0


class _SameGatewayFallbackEligible(Exception, metaclass=ABCMeta):  # noqa: N818 — marker
    """Exceptions a same-gateway fallback may handle (DD-GW-003).

    A 402 or a policy denial cannot succeed on the same gateway and key, so
    ``with_fallbacks`` must not retry them there.
    """

    @classmethod
    def __subclasshook__(cls, subclass: type) -> bool:
        return issubclass(subclass, Exception) and not issubclass(
            subclass, (GatewayPaymentRequired, GatewayPolicyError)
        )


def _without_model_fallback(target: ResolvedModel) -> ResolvedModel:
    """SPEC-AMR-GW-001: under a cascade the gateway must not fall back across models."""
    from dataclasses import replace

    body = {k: v for k, v in target.extra_body.items() if k != "models"}
    return replace(target, extra_body=body, owns_model_fallback=False)


class ProviderRegistry:
    """
    LiteLLM-backed registry for provider-agnostic LLM access.

    All provider routing (Anthropic, OpenAI, Google, Ollama, etc.) is
    handled transparently by LiteLLM. No provider-specific code lives here.

    Usage::

        registry = ProviderRegistry()
        llm = registry.get_llm()  # uses settings.default_model
        llm = registry.get_llm("gpt-4o")  # override for this call
    """

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        gateway_router: GatewayRouter | None = None,
    ) -> None:
        """
        Initialize the registry.

        Args:
            settings: Optional Settings override (useful for testing).
                      None = load from environment via get_settings().
            gateway_router: Explicit gateway router (Phase GW); ``None`` resolves
                the per-request / process router lazily (``get_gateway_router``).
        """
        self._settings = settings or get_settings()
        self._gateway_router = gateway_router
        self._usage: dict[str, TokenUsage] = {}
        self._inject_api_keys()

    def _inject_api_keys(self) -> None:
        """Bridge Pydantic Settings to LiteLLM's environment-variable API.

        LiteLLM reads provider credentials and base URLs from ``os.environ``
        directly, so values loaded through Pydantic Settings (from ``.env``
        or explicit constructor arguments) must be mirrored into the
        process environment before any LLM call.  ``setdefault`` is used so
        that values already set by the shell environment win — useful in
        container and CI deployments where secrets come from the platform
        rather than ``.env``.

        For Ollama, LiteLLM respects the ``OLLAMA_API_BASE`` variable, so
        we export ``settings.ollama_base_url`` the same way.
        """
        key_map = {
            "ANTHROPIC_API_KEY": self._settings.anthropic_api_key.get_secret_value(),
            "OPENAI_API_KEY": self._settings.openai_api_key.get_secret_value(),
            "GOOGLE_API_KEY": self._settings.google_api_key.get_secret_value(),
        }
        for env_var, value in key_map.items():
            if value:
                os.environ.setdefault(env_var, value)

        # Ollama needs no API key, but LiteLLM needs the base URL to route
        # ``ollama/*`` model strings.  Export from settings so a single
        # PRISMAL_OLLAMA_BASE_URL covers every codepath.
        if self._settings.ollama_base_url:
            os.environ.setdefault("OLLAMA_API_BASE", self._settings.ollama_base_url)

        # Allow LiteLLM to auto-fix provider-specific parameter mismatches.
        # Anthropic rejects requests containing tool-use message history without
        # `tools=`; modify_params=True makes LiteLLM add a dummy tool automatically.
        litellm.modify_params = True

    def get_llm(
        self,
        model: str | None = None,
        streaming: bool = False,
        temperature: float | None = None,
        *,
        agent: str | None = None,
        session_id: str | None = None,
        features: TaskFeatures | None = None,
        state: AgentState | None = None,
    ) -> BaseChatModel:
        """
        Return a LangChain-compatible LLM for the specified model.

        Falls back to ``settings.default_model`` when *model* is None.
        Provider routing is transparent: pass ``"gpt-4o"``, ``"claude-sonnet-4-5"``,
        ``"gemini/gemini-1.5-pro"``, or ``"ollama/llama3"`` and LiteLLM will
        route to the correct provider.

        Args:
            model: LiteLLM model string. None = use ``settings.default_model``.
            streaming: Whether to enable streaming output.
            temperature: Sampling temperature. None = use ``settings.temperature``.
            agent: Graph node name (Phase TKE). Used for the prompt-cache key and
                per-agent effort; ``None`` lets the wrapper read the node name
                from the call's ``langgraph_node`` metadata (ADR-WAVE-001 §2.1).
            session_id: Session id (Phase GW) — hashed for sticky gateway routing.
            features: Task features (Phase AMR) — with ``adaptive_routing_enabled``
                and no explicit *model*, a routing policy picks the model string.
            state: Agent state (Phase AMR) — the per-run routing registry key;
                features are extracted from it when *features* is None.

        Returns:
            A ``ChatLiteLLM`` instance (implements ``BaseChatModel``) — wrapped in
            a ``CacheAwareChatModel`` when ``token_efficiency_enabled`` and
            ``prompt_cache_enabled`` / ``llm_effort_enabled`` are on.
        """
        routed, cascade = self._route(model, agent, features, state)
        return self._build_llm(routed, streaming, temperature, agent, session_id, cascade)[0]

    # ── Phase AMR — adaptive model choice (ADR-WAVE-001 step 1) ──────────────

    def _route(
        self,
        model: str | None,
        agent: str | None,
        features: TaskFeatures | None,
        state: AgentState | None,
    ) -> tuple[str | None, bool]:
        """``(model, cascade_active)``: explicit model > policy choice > default.

        Flag off, no *features*, an explicit *model*, an agent pinned in
        ``llm_gateway_agent_map``, shadow mode or any routing failure
        (fail-open) ⇒ *model* unchanged (the 3.15.0 path).
        """
        s = self._settings
        if model is not None or not s.adaptive_routing_enabled or features is None:
            return model, False  # SPEC-AMR-INT-001: features is the routing opt-in
        name = features.agent or agent
        if not name:
            return model, False
        if s.llm_gateways_enabled and name in s.llm_gateway_agent_map:
            return model, False  # an operator-pinned gateway route beats adaptive routing
        try:
            from prismal.routing import select as routing_select
            from prismal.routing.types import ModelTier

            choice = routing_select.select_model(
                state if state is not None else {}, name, settings=s, features=features
            )
        except Exception as exc:
            logger.warning("provider.routing_failed", agent=name, error=type(exc).__name__)
            return model, False
        if s.routing_mode != "enforce" or choice.model_ref == s.default_model:
            return model, False
        cascade = s.routing_policy in {"cascade", "composite"} and choice.tier is not ModelTier.AUTO
        return choice.model_ref, cascade

    def _build_llm(
        self,
        model: str | None,
        streaming: bool,
        temperature: float | None,
        agent: str | None,
        session_id: str | None,
        cascade: bool = False,
    ) -> tuple[BaseChatModel, ResolvedModel | None]:
        """ADR-WAVE-001 pipeline; also returns the gateway target (``None`` = legacy)."""
        resolved_model = model if model is not None else self._settings.default_model
        temp = temperature if temperature is not None else self._settings.temperature
        gateway = self._resolve_gateway(model, resolved_model, agent, session_id)
        if gateway is not None:
            if cascade:
                gateway = _without_model_fallback(gateway)
            return self._gateway_llm(gateway, streaming, temp, agent), gateway

        # Ollama local models are much slower than cloud providers — use
        # the dedicated timeout override when the model is routed through
        # Ollama so the default 60 s ceiling does not break simple greetings.
        is_ollama = resolved_model.startswith(("ollama/", "ollama_chat/"))
        timeout_s = (
            self._settings.llm_ollama_timeout_seconds
            if is_ollama
            else self._settings.timeout_seconds
        )

        logger.debug(
            "creating_llm",
            model=resolved_model,
            streaming=streaming,
            temperature=temp,
            timeout_seconds=timeout_s,
        )

        langfuse = LangfuseManager()
        otel = OTelManager()

        with otel.start_span(
            "provider.get_llm",
            attributes={
                "prismal.model": resolved_model,
                "prismal.streaming": streaming,
                "prismal.temperature": temp,
            },
        ):
            otel.increment_counter("llm_requests", attributes={"model": resolved_model})
            llm = ChatLiteLLM(
                model=resolved_model,
                streaming=streaming,
                temperature=temp,
                max_tokens=self._settings.max_tokens,
                request_timeout=float(timeout_s),
                max_retries=self._settings.retry_attempts,
            )
            # Inject Langfuse callback handler if available
            handler = langfuse.get_callback_handler()
            if handler is not None:
                llm.callbacks = [handler]
            return self._maybe_wrap_token_efficient(llm, resolved_model, agent), None

    # ── Phase GW — gateway path (ADR-WAVE-001 steps 2-4) ─────────────────────

    def _router(self) -> GatewayRouter | None:
        if self._gateway_router is not None:
            return self._gateway_router
        from prismal.providers.gateways.router import get_gateway_router

        return get_gateway_router()

    def _resolve_gateway(
        self,
        model: str | None,
        resolved_model: str,
        agent: str | None,
        session_id: str | None,
    ) -> ResolvedModel | None:
        """Gateway target for this call, or ``None`` for the unchanged legacy path."""
        if not self._settings.llm_gateways_enabled:
            if resolved_model.startswith("gw:"):
                raise GatewayConfigError(
                    f"gateways_disabled: {resolved_model!r} needs PRISMAL_LLM_GATEWAYS_ENABLED=true"
                )
            return None
        router = self._router()
        if router is None:
            if resolved_model.startswith("gw:"):
                raise GatewayConfigError(
                    f"gateways_disabled: no gateway router for {resolved_model!r}"
                )
            return None
        target = router.resolve(model, agent=agent, session_id=session_id)
        if target is None and model is None and resolved_model.startswith("gw:"):
            target = router.resolve(resolved_model, agent=agent, session_id=session_id)
        return target

    def _gateway_llm(
        self, resolved: ResolvedModel, streaming: bool, temp: float, agent: str | None
    ) -> BaseChatModel:
        from prismal.providers.gateways.chat_model import GatewayChatLiteLLM

        router = self._router()
        port = router.get(resolved.gateway) if router is not None else None
        reports_cost = bool(port is not None and port.profile.capabilities.reports_cost)
        require_parameters = bool(
            port is not None
            and port.profile.capabilities.enforces_data_policy
            and port.profile.data_policy.require_parameters_for_tools
            and "provider" in resolved.extra_body
        )
        otel = OTelManager()
        with otel.start_span(
            "provider.get_llm",
            attributes={
                "prismal.model": resolved.litellm_model,
                "prismal.streaming": streaming,
                "prismal.temperature": temp,
                "prismal.gateway": resolved.gateway,
                "prismal.wire_protocol": str(resolved.wire_protocol),
            },
        ):
            otel.increment_counter("llm_requests", attributes={"model": resolved.litellm_model})
            kwargs: dict[str, Any] = {
                "model": resolved.litellm_model,
                "streaming": streaming,
                "temperature": temp,
                "max_tokens": self._settings.max_tokens,
                "request_timeout": float(self._settings.timeout_seconds),
                "max_retries": 0 if resolved.owns_model_fallback else self._settings.retry_attempts,
                "api_base": resolved.api_base,
                # The only place a gateway secret is unwrapped (SPEC conventions).
                "api_key": resolved.api_key.get_secret_value(),
                "extra_headers": dict(resolved.extra_headers),
                "model_kwargs": {"extra_body": dict(resolved.extra_body)}
                if resolved.extra_body
                else {},
                "gateway_name": resolved.gateway,
                "resolved_model_ref": f"gw:{resolved.gateway}/{resolved.upstream_model}",
                "requested_model": resolved.upstream_model,
                "cost_mode": resolved.cost_mode,
                "reports_cost": reports_cost,
                "require_parameters_on_tools": require_parameters,
                "quota_ledger": router.ledger if router is not None else None,
                "downgrade_model": resolved.downgrade_model,
            }
            llm = GatewayChatLiteLLM(**kwargs)
            handler = LangfuseManager().get_callback_handler()
            if handler is not None:
                llm.callbacks = [handler]
            return self._maybe_wrap_token_efficient(llm, resolved.litellm_model, agent)

    def _maybe_wrap_token_efficient(
        self, llm: BaseChatModel, model: str, agent: str | None
    ) -> BaseChatModel:
        """Pipeline step 6 of ADR-WAVE-001: the outer ``CacheAwareChatModel``.

        Returns *llm* untouched unless the TKE master flag and at least one of
        ``prompt_cache_enabled`` / ``llm_effort_enabled`` are on.
        """
        s = self._settings
        apply_cache = s.token_efficiency_enabled and s.prompt_cache_enabled
        apply_effort = s.token_efficiency_enabled and s.llm_effort_enabled
        if not (apply_cache or apply_effort):
            return llm
        from prismal.providers.prompt_cache import (
            CacheAwareChatModel,
            CachePolicy,
            load_cache_policy,
        )

        wrapped = CacheAwareChatModel(
            inner=llm,
            policy=load_cache_policy(s) if apply_cache else CachePolicy(),
            model_name=model,
            agent=agent,
            apply_cache=apply_cache,
            effort_by_agent=dict(s.llm_effort_by_agent) if apply_effort else {},
            effort_profile=s.llm_effort_profile if apply_effort else "none",
            max_tokens_by_agent=dict(s.llm_max_tokens_by_agent) if apply_effort else {},
        )
        if llm.callbacks:
            wrapped.callbacks = llm.callbacks
        return wrapped

    def get_llm_with_fallback(
        self,
        model: str | None = None,
        streaming: bool = False,
        temperature: float | None = None,
        *,
        agent: str | None = None,
        session_id: str | None = None,
        features: TaskFeatures | None = None,
        state: AgentState | None = None,
    ) -> BaseChatModel | RunnableWithFallbacks[LanguageModelInput, BaseMessage]:
        """
        Return a primary LLM wrapped with an optional automatic fallback.

        If ``settings.fallback_model`` is non-empty and differs from the
        primary, the returned runnable falls back to the secondary model on
        any primary failure (network, quota, timeout).  When the fallback
        is empty — typical for single-provider setups like Ollama-only —
        the primary ``BaseChatModel`` is returned directly so that the
        caller does not pay for an unreachable fallback provider.

        Args:
            model: Primary model string. None = ``settings.default_model``.
            streaming: Whether to enable streaming output.
            temperature: Sampling temperature. None = ``settings.temperature``.

        Returns:
            Either the primary ``BaseChatModel`` (when no fallback is
            configured) or a ``RunnableWithFallbacks`` wrapping both
            models.
        """
        model, cascade = self._route(model, agent, features, state)
        primary, primary_target = self._build_llm(
            model, streaming, temperature, agent, session_id, cascade
        )
        if cascade:
            # SPEC-AMR-GW-001: the cascade *is* the fallback — no settings.fallback_model.
            logger.debug("provider.fallback_skipped", reason="routing_cascade")
            return primary
        if primary_target is not None and primary_target.owns_model_fallback:
            # DD-GW-003: the gateway owns model fallback — never wrap twice.
            logger.debug(
                "provider.fallback_skipped",
                primary=primary_target.litellm_model,
                reason="gateway_owns_model_fallback",
            )
            return primary
        fallback_model = self._settings.fallback_model
        primary_model = model if model is not None else self._settings.default_model
        if not fallback_model or fallback_model == primary_model:
            logger.debug(
                "provider.fallback_skipped",
                primary=primary_model,
                reason="empty" if not fallback_model else "same_as_primary",
            )
            return primary
        fallback, fallback_target = self._build_llm(
            fallback_model, streaming, temperature, agent, session_id
        )
        if (
            primary_target is not None
            and fallback_target is not None
            and primary_target.gateway == fallback_target.gateway
        ):
            return primary.with_fallbacks(
                [fallback], exceptions_to_handle=(_SameGatewayFallbackEligible,)
            )
        return primary.with_fallbacks([fallback])

    def get_available_models(self) -> list[ModelInfo]:
        """
        Return list of models available given the configured API keys.

        A model is included only when its required API key is non-empty
        (or for Ollama, which needs no key). Does not verify liveness.

        Returns:
            List of ``ModelInfo`` for all configured providers.
        """
        models: list[ModelInfo] = []
        s = self._settings

        if s.anthropic_api_key.get_secret_value():
            models.extend(
                [
                    ModelInfo(id="claude-sonnet-4-5", provider="anthropic"),
                    ModelInfo(id="claude-opus-4-6", provider="anthropic"),
                    ModelInfo(id="claude-haiku-4-5-20251001", provider="anthropic"),
                ]
            )

        if s.openai_api_key.get_secret_value():
            models.extend(
                [
                    ModelInfo(id="gpt-4o", provider="openai"),
                    ModelInfo(id="gpt-4o-mini", provider="openai"),
                ]
            )

        if s.google_api_key.get_secret_value():
            models.extend(
                [
                    # LiteLLM requires the 'gemini/' provider prefix for routing
                    ModelInfo(id="gemini/gemini-1.5-pro", provider="google"),
                    ModelInfo(id="gemini/gemini-2.0-flash", provider="google"),
                ]
            )

        # Ollama requires no key — always listed as potentially available
        models.append(ModelInfo(id="ollama/llama3", provider="ollama"))

        return models

    def get_token_usage(self, session_id: str) -> TokenUsage:
        """
        Return cumulative token usage for a session.

        If no usage has been tracked yet for *session_id*, returns a
        ``TokenUsage`` with all fields at zero.

        Args:
            session_id: Unique session identifier.

        Returns:
            ``TokenUsage`` with accumulated totals for the session.
        """
        return self._usage.get(session_id, TokenUsage(session_id=session_id))

    def track_usage(
        self,
        session_id: str,
        prompt_tokens: int,
        completion_tokens: int,
        estimated_cost: float = 0.0,
    ) -> None:
        """
        Record token usage for a session.

        Accumulates usage across multiple calls for the same session_id.
        Thread-safety is not guaranteed — use one registry per async context.

        Args:
            session_id: Unique session identifier.
            prompt_tokens: Number of input/prompt tokens used.
            completion_tokens: Number of output/completion tokens generated.
            estimated_cost: Estimated USD cost for this call.
        """
        if session_id not in self._usage:
            self._usage[session_id] = TokenUsage(session_id=session_id)
        usage = self._usage[session_id]
        usage.prompt_tokens += prompt_tokens
        usage.completion_tokens += completion_tokens
        usage.total_tokens += prompt_tokens + completion_tokens
        usage.estimated_cost += estimated_cost
        otel = OTelManager()
        total = prompt_tokens + completion_tokens
        otel.increment_counter("llm_tokens", total, attributes={"session_id": session_id})
        logger.debug(
            "usage_tracked",
            session_id=session_id,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total=usage.total_tokens,
        )


__all__ = ["ModelInfo", "ProviderRegistry", "TokenUsage"]

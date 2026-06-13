"""Dynamic tool registry for Prismal sub-agents (Fase Y — provider injection).

Tool resolution is delegated to an injected :class:`ToolProviderPort`
(SPEC-TPI-008). The host (prismal-sdk / prismal-web) composes the providers
(MCP, Skills, stubs) and injects them once at startup; this module — and the
whole agent core — no longer imports ``prismal.mcp`` or ``prismal.skills``.

Usage::

    # Host startup (FastAPI lifespan or equivalent)
    from prismal.agents.extension import build_default_tool_provider
    from prismal.agents.tool_registry import set_tool_provider

    set_tool_provider(await build_default_tool_provider(settings))

    # Inside each agent node (unchanged API)
    tools = get_tools_for_agent("researcher")

Without an injected provider the registry degrades to the static stubs from
``tools.py`` with a structured warning (or raises ``ToolProviderNotConfigured``
when ``settings.tool_provider_strict`` is True).
"""

from __future__ import annotations

import asyncio
import json
import re
import warnings
from typing import TYPE_CHECKING

from prismal.core.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from langchain_core.runnables import RunnableConfig
    from langchain_core.tools import BaseTool

    from prismal.agents.extension.ports import ToolProviderPort
    from prismal.agents.extension.providers import StubToolProvider
    from prismal.budget.guard import BudgetGuard

logger = get_logger("prismal.agents.tool_registry")


def _resolve_model_name(llm: object) -> str:
    """Best-effort model id for cost attribution from a (possibly bound) LLM."""
    for obj in (llm, getattr(llm, "bound", None)):
        model = getattr(obj, "model", None) or getattr(obj, "model_name", None)
        if isinstance(model, str) and model:
            return model
    return "unknown"


def _meter_response(
    budget_guard: BudgetGuard | None, response: object, llm: object, agent_name: str
) -> None:
    """Record one LLM response against the per-run meter (no-op without a guard)."""
    if budget_guard is None:
        return
    from contextlib import suppress

    with suppress(Exception):
        budget_guard.meter.record_response(response, _resolve_model_name(llm), agent=agent_name)


def _budget_partial_or_none(
    budget_guard: BudgetGuard | None,
    response: object,
    *,
    agent_name: str,
    session_id: str | None,
) -> object | None:
    """Pre-call hard-cap check: return a best-effort partial AIMessage to stop the
    loop when the budget is exhausted, else None to proceed.

    The cutoff is audited via the guard; the hard-cap exception is swallowed so a
    metered turn ends with a partial answer rather than crashing.
    """
    if budget_guard is None:
        return None
    status = budget_guard.check()
    if not status.hard_exceeded:
        return None

    from contextlib import suppress

    from langchain_core.messages import AIMessage

    from prismal.core.exceptions import BudgetExceeded

    with suppress(BudgetExceeded):
        budget_guard.enforce()  # audits the cutoff; raise swallowed for graceful exit
    logger.warning(
        "react_loop.budget_exhausted",
        agent=agent_name,
        dimension=status.breached_dimension,
        session_id=session_id,
    )
    prior = str(getattr(response, "content", "") or "").strip()
    notice = "[Response truncated: budget exhausted.]"
    content = f"{prior}\n\n{notice}".strip() if prior else notice
    return AIMessage(content=content)


# ---------------------------------------------------------------------------
# Injected tool provider (variante A — SPEC-TPI-008)
# ---------------------------------------------------------------------------

_provider: ToolProviderPort | None = None

# Lazy fallback used when no provider was injected (non-strict mode).
_default_stub_provider: StubToolProvider | None = None


def set_tool_provider(provider: ToolProviderPort) -> None:
    """Inject the global tool provider.

    Idempotent — the host calls it once at startup; calling it again simply
    replaces the previous provider (last one wins).
    """
    global _provider  # module-level injection point (variante A)
    _provider = provider
    logger.info("tool_registry.provider_set", provider=type(provider).__name__)


def get_tool_provider() -> ToolProviderPort | None:
    """Return the injected global provider, or ``None`` when not configured."""
    return _provider


def _get_default_stub_provider() -> StubToolProvider:
    """Return the lazily-created stub-only fallback provider."""
    global _default_stub_provider  # lazy module-level singleton
    if _default_stub_provider is None:
        from prismal.agents.extension.providers import StubToolProvider

        _default_stub_provider = StubToolProvider()
    return _default_stub_provider


# ---------------------------------------------------------------------------
# Observability (Fase Y6 — span prismal.tools.resolve + counters)
# ---------------------------------------------------------------------------

# Short metric labels per provider class (ARCHITECTURE §7.2). Unknown
# (host-supplied) providers are labelled with their class name.
_PROVIDER_LABELS: dict[str, str] = {
    "CompositeToolProvider": "composite",
    "McpToolProvider": "mcp",
    "SkillToolProvider": "skill",
    "StubToolProvider": "stub",
    "FakeToolProvider": "fake",
}


def _provider_label(provider: ToolProviderPort) -> str:
    """Return the metric label for *provider* (``composite|mcp|skill|stub|fake``)."""
    name = type(provider).__name__
    return _PROVIDER_LABELS.get(name, name)


def _observed_get_tools(
    provider: ToolProviderPort,
    agent_name: str,
    capabilities: list[str] | None,
    *,
    fallback: bool = False,
) -> list[BaseTool]:
    """Resolve tools through *provider* inside the ``prismal.tools.resolve`` span.

    Emits the Fase Y counters: ``tool_provider_resolved{provider}``,
    ``tools_injected{agent}`` and — when *fallback* is set —
    ``tool_provider_fallback``.
    """
    from prismal.monitoring.otel import OTelManager

    otel = OTelManager()
    label = _provider_label(provider)
    with otel.start_span(
        "prismal.tools.resolve",
        attributes={"prismal.agent": agent_name},
    ) as span:
        tools = provider.get_tools(agent_name=agent_name, capabilities=capabilities)
        span.set_attribute("prismal.tool_provider", label)
        span.set_attribute("prismal.n_tools", len(tools))
        span.set_attribute("prismal.fallback", fallback)
        if capabilities:
            span.set_attribute("prismal.capabilities", ",".join(capabilities))

    otel.increment_counter("tool_provider_resolved", attributes={"provider": label})
    otel.increment_counter("tools_injected", value=len(tools), attributes={"agent": agent_name})
    if fallback:
        otel.increment_counter("tool_provider_fallback")
    return tools


# ---------------------------------------------------------------------------
# Deprecated shims (1 minor release of deprecation — DD-TPI-006)
# ---------------------------------------------------------------------------


async def init_mcp(config_path: Path | None = None) -> None:
    """DEPRECATED — build and inject a provider from the host instead.

    Delegates to ``set_tool_provider(await build_default_tool_provider(...))``
    so legacy startups keep working. No-op when a provider is already
    injected.

    Args:
        config_path: Override MCP config path (defaults to
            ``config/mcp_servers.yaml``).
    """
    warnings.warn(
        "init_mcp() is deprecated; compose and inject a provider at startup: "
        "set_tool_provider(await build_default_tool_provider(settings)).",
        DeprecationWarning,
        stacklevel=2,
    )
    if _provider is not None:
        return
    from prismal.agents.extension.providers import build_default_tool_provider

    set_tool_provider(await build_default_tool_provider(mcp_config_path=config_path))


def get_mcp_tools(
    capabilities: list[str] | None = None,
) -> list[BaseTool]:
    """DEPRECATED — MCP tools are resolved by the injected provider.

    Delegates to the ``McpToolProvider`` inside the injected provider (when
    present). Returns ``[]`` when no provider is configured or it has no MCP
    sub-provider.
    """
    warnings.warn(
        "get_mcp_tools() is deprecated; tools are resolved by the injected "
        "ToolProviderPort (see set_tool_provider).",
        DeprecationWarning,
        stacklevel=2,
    )
    from prismal.agents.extension.providers import (
        CompositeToolProvider,
        McpToolProvider,
    )

    provider = get_tool_provider()
    candidates = provider.providers if isinstance(provider, CompositeToolProvider) else (provider,)
    for candidate in candidates:
        if isinstance(candidate, McpToolProvider):
            return candidate.get_tools(agent_name="__legacy__", capabilities=capabilities)
    return []


def get_skill_tools() -> list[BaseTool]:
    """DEPRECATED — skill tools are resolved by the injected provider.

    Delegates to the ``SkillToolProvider`` inside the injected provider (when
    present). Returns ``[]`` when no provider is configured or it has no
    skill sub-provider.
    """
    warnings.warn(
        "get_skill_tools() is deprecated; tools are resolved by the injected "
        "ToolProviderPort (see set_tool_provider).",
        DeprecationWarning,
        stacklevel=2,
    )
    from prismal.agents.extension.providers import (
        CompositeToolProvider,
        SkillToolProvider,
    )

    provider = get_tool_provider()
    candidates = provider.providers if isinstance(provider, CompositeToolProvider) else (provider,)
    for candidate in candidates:
        if isinstance(candidate, SkillToolProvider):
            return candidate.get_tools(agent_name="__legacy__")
    return []


# Legacy aliases of the platform tool-policy caps.  The merge itself now
# lives in extension/providers.py (CompositeToolProvider — DD-TPI-004); these
# constants are kept here, in sync, for backward compatibility with existing
# callers and tests (a parity test asserts both sides stay equal).
#
# _MAX_MCP_TOOLS — global cap across all connected MCP servers per call.
# _MAX_TOTAL_TOOLS — hard upper bound on the merged list (OpenAI rejects
#   ``tools`` arrays longer than 128 entries).
# _FIXED_TOOL_AGENTS — agents that receive only their stub set (their prompts
#   must stay small to avoid rate-limit errors).
_MAX_MCP_TOOLS: int = 60
_MAX_TOTAL_TOOLS: int = 120
_FIXED_TOOL_AGENTS: frozenset[str] = frozenset({"cron_manager", "critic"})


# Fase E — recommended capability filter per new pattern / subgraph.
# Operators wiring D1-01/02/03 (deferred) should pass these lists to
# ``get_tools_for_agent(name, required_capabilities=DEFAULT_CAPABILITY_MAP[name])``
# when registering each node.  Pre-Fase-A agents (researcher, coder, …) are
# **absent** from this map on purpose — they continue to receive the full
# MCP pool (backward compatibility).
DEFAULT_CAPABILITY_MAP: dict[str, list[str]] = {
    # Patterns from Fase B.
    "tot_agent": ["general", "research"],
    "lats_agent": ["general", "research", "file_management"],
    "llm_compiler": ["general", "research", "file_management", "code_execution"],
    "mixture_agent": ["general", "research"],
    # Subgraphs from Fase C.
    "customer_service": ["customer_service", "rag", "general"],
    "code_review": ["code_review", "code_execution", "file_management"],
    "data_etl": ["data_etl", "file_management", "general"],
    "document_generation": ["document_generation", "research", "file_management"],
    "debate_consensus": ["research", "general"],
    # Multimodal agents from Fase F (opt-in; gated by settings.multimodal_enabled).
    "multimodal_pipeline": ["vision", "audio", "video", "general"],
    "multimodal_router": ["general"],
    "vision_agent": ["vision", "general"],
    "audio_agent": ["audio", "general"],
    "video_agent": ["vision", "audio", "video", "general"],
    # Kokoro deliberation from Fase K (opt-in; gated by settings.kokoro_enabled).
    # The judge's optional tool set resolves through the injected
    # ToolProviderPort (Fase Y) — Kokoro never imports prismal.mcp/skills.
    "kokoro": ["general", "research"],
    # Skynet swarm from Fase S (opt-in; gated by settings.skynet_enabled).
    # Each SwarmWorker resolves its tools through the injected ToolProviderPort
    # (Fase Y) under agent_name="skynet_worker" — Skynet never imports
    # prismal.mcp/skills.
    "skynet": ["general", "research"],
    "skynet_worker": ["general", "research"],
}


def get_recommended_capabilities(node_name: str) -> list[str] | None:
    """Return the recommended Fase E capability filter for *node_name*.

    Unknown names return ``None`` — legacy agents get the full MCP pool.
    """
    return DEFAULT_CAPABILITY_MAP.get(node_name)


# ---------------------------------------------------------------------------
# Per-agent tool merge
# ---------------------------------------------------------------------------


def get_tools_for_agent(
    agent_name: str,
    required_capabilities: list[str] | None = None,
) -> list[BaseTool]:
    """Return the tool list for a named agent (stable facade — SPEC-TPI-008).

    Delegates to the injected :class:`ToolProviderPort`. With the default
    composite (``build_default_tool_provider``) the result is identical to
    the historical merge: MCP → Skills → stubs, name-based dedupe, token
    caps, and the fixed-tool-agent exemption.

    Without an injected provider the call degrades to the static stubs from
    ``tools.py`` (with a ``tool_registry.no_provider`` warning), or raises
    :class:`ToolProviderNotConfigured` when ``settings.tool_provider_strict``
    is True.

    Args:
        agent_name: One of the known agent names (``"researcher"``,
            ``"coder"``, etc.).
        required_capabilities: Fase E capability filter forwarded to the
            provider as ``capabilities``. ``None`` (default) preserves the
            legacy full pool — zero regressions for pre-Fase-E callers.

    Returns:
        Deduplicated list of ``BaseTool`` instances.
    """
    provider = get_tool_provider()
    if provider is None:
        from prismal.core.config import get_settings

        if get_settings().tool_provider_strict:
            from prismal.core.exceptions import ToolProviderNotConfigured

            raise ToolProviderNotConfigured(agent_name)
        logger.warning("tool_registry.no_provider", agent=agent_name)
        return _observed_get_tools(_get_default_stub_provider(), agent_name, None, fallback=True)
    return _observed_get_tools(provider, agent_name, required_capabilities)


# ---------------------------------------------------------------------------
# Variante B — per-context provider resolution (Fase Y4, SPEC-TPI-009)
# ---------------------------------------------------------------------------


def resolve_provider(config: RunnableConfig | None = None) -> ToolProviderPort | None:
    """Resolve the tool provider for the current invocation context.

    Reads ``config["configurable"]["tool_provider"]`` (set by
    ``get_async_compiled_graph(tool_provider=...)`` in ``context`` mode) and
    falls back to the injected global provider. No lock is taken — resolution
    is a pair of dict lookups, safe under concurrent sessions.

    Args:
        config: The ``RunnableConfig`` LangGraph hands to a node, or ``None``.

    Returns:
        The session provider, the global provider, or ``None`` when neither
        is configured.
    """
    if config is not None:
        candidate = config.get("configurable", {}).get("tool_provider")
        if candidate is not None:
            return candidate  # type: ignore[no-any-return]
    return get_tool_provider()


def get_tools_for_agent_ctx(
    agent_name: str,
    config: RunnableConfig | None = None,
    required_capabilities: list[str] | None = None,
) -> list[BaseTool]:
    """Context-aware variant of :func:`get_tools_for_agent` (variante B).

    Nodes running in ``tool_provider_mode="context"`` call this with the
    ``RunnableConfig`` they receive from LangGraph so each session resolves
    its own provider. Without a session provider the behaviour is identical
    to :func:`get_tools_for_agent` (global provider → stub fallback/strict).

    Args:
        agent_name: One of the known agent names.
        config: The node's ``RunnableConfig`` (or ``None``).
        required_capabilities: Fase E capability filter.

    Returns:
        Deduplicated list of ``BaseTool`` instances.
    """
    provider: ToolProviderPort | None = None
    if config is not None:
        provider = config.get("configurable", {}).get("tool_provider")
    if provider is None:
        return get_tools_for_agent(agent_name, required_capabilities)
    return _observed_get_tools(provider, agent_name, required_capabilities)


# ---------------------------------------------------------------------------
# Shared ReAct execution loop
# ---------------------------------------------------------------------------

_MAX_REACT_ITERATIONS: int = 5

# After this many consecutive *permanent* failures a tool is considered
# permanently unavailable for the current session and subsequent LLM requests
# to use it are answered with an "unavailable" ToolMessage without calling
# the service.  Rate-limit (429) errors do NOT count toward this budget.
_MAX_TOOL_FAILURES: int = 2

# Delay (seconds) injected after a rate-limit (429) response to respect the
# API's back-off window.  Keeps the loop alive without hammering the service.
_RATE_LIMIT_BACKOFF: float = 1.2

# Pattern to detect rate-limit errors by message content.
_RATE_LIMIT_RE = re.compile(r"429|rate.?limit|too.many.requests", re.IGNORECASE)

# Pattern to detect permanent / non-retryable provider errors.  These are
# conditions that will NOT recover with a simple retry (billing, auth,
# misconfigured API key, invalid request payload) — retrying them wastes
# the remaining budget and delays the user-visible failure message.
_PERMANENT_ERROR_RE = re.compile(
    r"credit.?balance|insufficient.?(?:credit|quota|funds)|billing|"
    r"invalid.?api.?key|invalid.?request.?error|invalid.?model|"
    r"authentication|permission.?denied|unauthori[sz]ed|"
    r"account.?(?:suspended|disabled)|model.?not.?found",
    re.IGNORECASE,
)

# Maximum backoff delay (seconds) between LLM rate-limit retries.  The
# exponential formula ``base * 2**attempt`` is capped at this value so a
# badly configured ``base_delay`` cannot block the event loop for minutes.
_LLM_RATE_LIMIT_MAX_DELAY: float = 60.0


def _extract_retry_after(exc: BaseException) -> float | None:
    """Best-effort extraction of a server-advertised Retry-After delay.

    Providers like Anthropic and OpenAI include a ``retry-after`` header
    on 429 responses.  LiteLLM forwards it as ``exc.retry_after`` on its
    rate-limit exception types, and Anthropic error bodies sometimes
    include a ``retry-after`` field in the JSON payload.

    Args:
        exc: The exception raised by ``llm.ainvoke``.

    Returns:
        Number of seconds to wait before the next attempt, or ``None``
        when no hint is available.
    """
    hint = getattr(exc, "retry_after", None)
    if isinstance(hint, int | float) and hint > 0:
        return float(hint)
    headers = getattr(exc, "response_headers", None) or getattr(exc, "headers", None)
    if isinstance(headers, dict):
        raw = headers.get("retry-after") or headers.get("Retry-After")
        try:
            if raw is not None:
                parsed = float(raw)
                if parsed > 0:
                    return parsed
        except (TypeError, ValueError):
            pass
    return None


#: Keys we accept as the "final-answer" synthetic tool-call name.  Ollama
#: and other open models commonly wrap their reply in one of these shapes
#: when LiteLLM falls back to prompt-based function-call emulation.
_SYNTHETIC_FINAL_NAMES: frozenset[str] = frozenset(
    {
        "respond",
        "response",
        "answer",
        "final",
        "final_answer",
        "reply",
        "say",
    }
)


def _unwrap_synthetic_tool_call(content: str) -> str | None:
    """Extract the inner text from a synthetic function-call JSON blob.

    When LiteLLM routes a request through a model that cannot natively
    emit OpenAI-style tool calls, it falls back to a prompt template that
    instructs the model to answer with JSON of the form::

        {"function": "respond", "arguments": {"response": "Hola ..."}}

    LangChain then delivers that text as ``AIMessage.content`` without
    any ``tool_calls`` attribute, so the ReAct loop mistakes the synthetic
    wrapper for the real reply and the literal JSON reaches the user.

    This helper recognises the wrapper and returns the inner reply string.
    Accepted shapes include ``{"function": "respond", "arguments":
    {"response": "..."}}``, ``{"name": "respond", "parameters":
    {"text": "..."}}`` and the same with ``answer`` / ``final`` /
    ``reply`` as the name.  Anything else returns ``None`` so the caller
    falls back to the original content unchanged.

    Args:
        content: The raw ``AIMessage.content`` string.

    Returns:
        The extracted reply text, or ``None`` when *content* does not
        look like a synthetic final-answer JSON wrapper.
    """
    if not content:
        return None
    stripped = content.strip()
    # Tolerate code-fence decoration some models add: ```json\n{...}\n```
    if stripped.startswith("```"):
        stripped = stripped.strip("`").strip()
        if stripped.lower().startswith("json"):
            stripped = stripped[4:].lstrip()
    if not stripped.startswith("{"):
        return None
    try:
        payload = json.loads(stripped)
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    name = payload.get("function") or payload.get("name") or payload.get("tool")
    if not isinstance(name, str) or name.lower() not in _SYNTHETIC_FINAL_NAMES:
        return None
    args = payload.get("arguments") or payload.get("parameters") or payload.get("args")
    if isinstance(args, dict):
        for key in ("response", "text", "message", "content", "answer", "reply"):
            candidate = args.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate
        # Fallback: a single string value inside arguments wins.
        string_values = [v for v in args.values() if isinstance(v, str)]
        if len(string_values) == 1 and string_values[0].strip():
            return string_values[0]
    if isinstance(args, str) and args.strip():
        return args
    return None


def _sanitise_final_response(message: object) -> object:
    """Return *message* with any synthetic JSON wrapper unwrapped.

    ``react_loop`` calls this right before returning so downstream
    callers (channel routers, supervisor, cron executor) never see the
    synthetic wrapper.  Non-``AIMessage`` inputs and messages whose
    content is not a synthetic wrapper are returned unchanged.

    Args:
        message: The message produced by ``llm.ainvoke`` — usually an
            :class:`~langchain_core.messages.AIMessage`.

    Returns:
        Either the original message or a new
        :class:`~langchain_core.messages.AIMessage` whose ``content``
        has been replaced with the unwrapped reply text.
    """
    from langchain_core.messages import AIMessage

    content = getattr(message, "content", None)
    if not isinstance(content, str):
        return message
    unwrapped = _unwrap_synthetic_tool_call(content)
    if unwrapped is None:
        return message
    logger.info(
        "react_loop.synthetic_json_unwrapped",
        original_length=len(content),
        unwrapped_length=len(unwrapped),
    )
    return AIMessage(
        content=unwrapped,
        tool_calls=getattr(message, "tool_calls", None) or [],
    )


def _llm_permanent_error_message() -> str:
    """Return the canonical user-facing string for a permanent LLM failure.

    Kept as a helper so the three ``react_loop`` call sites share one
    string and tests can assert against a stable token (``"unavailable"``).

    Returns:
        A short sentence telling the user the service is unavailable
        because of a non-transient configuration or billing problem.
    """
    return (
        "The AI service is currently unavailable due to a configuration "
        "or billing problem (for example: expired credits, invalid API "
        "key, or an unauthorised model). Please contact the administrator "
        "— retrying will not fix this on its own."
    )


async def _invoke_llm_with_backoff(
    llm: object,
    messages: list[object],
    *,
    agent_name: str,
    session_id: str | None,
    iteration: int,
) -> object:
    """Call ``llm.ainvoke`` with exponential-backoff retries on 429 errors.

    Transient rate-limit errors from the LLM provider (Anthropic / OpenAI
    / Azure / …) are retried up to ``settings.llm_rate_limit_max_retries``
    times before being re-raised.  The delay between retries is
    ``base_delay * 2 ** attempt`` seconds, clamped to ``_LLM_RATE_LIMIT_MAX_DELAY``,
    and overridden by any provider-supplied ``Retry-After`` hint when
    available.

    Non rate-limit exceptions are re-raised immediately so the existing
    ``react_loop`` error handling can process them.

    Args:
        llm: A bound chat model that exposes ``ainvoke``.
        messages: The conversation slice to send.
        agent_name: Agent name used only for structured log entries.
        session_id: Optional session identifier for log correlation.
        iteration: Current ReAct iteration number (for logging).

    Returns:
        The :class:`~langchain_core.messages.AIMessage` returned by the
        LLM on the first successful attempt.

    Raises:
        BaseException: The final rate-limit exception after all retries
            are exhausted, or any non-rate-limit error unchanged.
    """
    from prismal.core.config import get_settings

    settings = get_settings()
    max_retries = settings.llm_rate_limit_max_retries
    base_delay = settings.llm_rate_limit_base_delay_seconds

    attempt = 0
    while True:
        try:
            return await llm.ainvoke(messages)  # type: ignore[attr-defined]
        except Exception as exc:
            exc_str = str(exc)
            if _PERMANENT_ERROR_RE.search(exc_str):
                # Billing / auth / invalid-request errors never recover
                # from a blind retry.  Re-raise so the caller can surface
                # the real reason to the user instead of looping.
                logger.error(
                    "react_loop.llm_permanent_error",
                    agent=agent_name,
                    iteration=iteration,
                    error=exc_str[:200],
                    session_id=session_id,
                )
                raise
            if not _RATE_LIMIT_RE.search(exc_str):
                raise
            if attempt >= max_retries:
                logger.warning(
                    "react_loop.llm_rate_limit_exhausted",
                    agent=agent_name,
                    iteration=iteration,
                    attempts=attempt + 1,
                    session_id=session_id,
                )
                raise
            hint = _extract_retry_after(exc)
            delay = hint if hint is not None else base_delay * (2**attempt)
            delay = min(delay, _LLM_RATE_LIMIT_MAX_DELAY)
            logger.warning(
                "react_loop.llm_rate_limit_retry",
                agent=agent_name,
                iteration=iteration,
                attempt=attempt + 1,
                max_attempts=max_retries,
                delay_seconds=delay,
                retry_after_hint=hint,
                session_id=session_id,
            )
            await asyncio.sleep(delay)
            attempt += 1


async def react_loop(
    llm: object,
    tools: list[BaseTool],
    messages: Sequence[object],
    *,
    agent_name: str = "agent",
    max_iterations: int = _MAX_REACT_ITERATIONS,
    session_id: str | None = None,
    budget_guard: BudgetGuard | None = None,
) -> object:
    """Execute a ReAct (Reason + Act) tool loop until the LLM returns a final answer.

    Calls the LLM, executes any requested tool calls, feeds results back as
    ``ToolMessage`` objects, and repeats until the LLM produces a response with
    no pending tool calls or *max_iterations* is reached.

    The last message in *messages* must satisfy the provider constraint of
    ending on a ``HumanMessage`` — callers are responsible for this invariant.

    **Failure resilience** — two mechanisms prevent the loop from hammering a
    permanently broken tool (e.g. Tavily 400 caused by a bad/missing API key):

    1. *Per-tool failure budget* — once a tool has failed
       ``_MAX_TOOL_FAILURES`` times consecutively it is marked unavailable for
       the remainder of the session.  Subsequent LLM requests to call it are
       answered with an "unavailable" ToolMessage without making the real call.

    2. *All-tools-failed early exit* — if every tool call in an iteration
       fails (success count == 0) the loop terminates immediately and asks
       the LLM to synthesise a best-effort answer from what it already knows,
       rather than burning additional iterations against broken services.

    Args:
        llm: A LangChain chat model already bound with tools via
            ``llm.bind_tools(tools)``.
        tools: Tool list (used to build the dispatch map by name).
        messages: Conversation slice to send on the first iteration.
            Subsequent iterations extend this list with intermediate results.
        agent_name: Agent name, used only for structured log entries.
        max_iterations: Maximum number of LLM + tool-execution cycles.
        session_id: Optional session ID for log correlation.
        budget_guard: Optional cost/budget guard (Phase C). When provided, each
            LLM response is metered and a hard cap before a call returns a
            best-effort partial answer instead of calling the model. ``None``
            (the default) leaves behaviour unchanged.

    Returns:
        The final :class:`~langchain_core.messages.AIMessage` produced by
        the LLM (either because it had no tool calls, because all tools
        failed, or because the iteration cap was reached).
    """
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    tool_map: dict[str, BaseTool] = {t.name: t for t in tools}
    loop_messages: list[object] = list(messages)
    response = AIMessage(content="")

    # Consecutive failure counter per tool name — reset to 0 on success.
    _tool_fail_counts: dict[str, int] = {}

    for iteration in range(max_iterations):
        partial = _budget_partial_or_none(
            budget_guard, response, agent_name=agent_name, session_id=session_id
        )
        if partial is not None:
            return partial
        try:
            response = await _invoke_llm_with_backoff(  # type: ignore[assignment]
                llm,
                loop_messages,
                agent_name=agent_name,
                session_id=session_id,
                iteration=iteration,
            )
        except Exception as llm_exc:
            exc_str = str(llm_exc)
            if _PERMANENT_ERROR_RE.search(exc_str):
                logger.error(
                    "react_loop.llm_unavailable",
                    agent=agent_name,
                    iteration=iteration,
                    error=exc_str[:200],
                    session_id=session_id,
                )
                return AIMessage(content=_llm_permanent_error_message())
            if _RATE_LIMIT_RE.search(exc_str):
                logger.warning(
                    "react_loop.llm_rate_limited",
                    agent=agent_name,
                    iteration=iteration,
                    error=exc_str[:200],
                    session_id=session_id,
                )
                return AIMessage(
                    content=(
                        "I'm sorry, the AI service is temporarily rate-limited "
                        "(too many tokens per minute). Please wait a moment and "
                        "try again."
                    )
                )
            raise

        _meter_response(budget_guard, response, llm, agent_name)

        tool_calls = getattr(response, "tool_calls", None) or []
        if not tool_calls:
            break  # Final answer — no more tool calls requested

        logger.debug(
            "react_loop.tool_calls",
            agent=agent_name,
            iteration=iteration,
            tools=[tc["name"] for tc in tool_calls],
            session_id=session_id,
        )

        loop_messages.append(response)
        successful_calls = 0

        # Tools rate-limited within THIS iteration (transient — do not count
        # toward the permanent failure budget; the service is just throttling).
        _rate_limited_this_iter: set[str] = set()

        for tc in tool_calls:
            tool_name: str = tc["name"]
            tool_fn = tool_map.get(tool_name)

            if tool_fn is None:
                logger.warning(
                    "react_loop.tool_not_found",
                    agent=agent_name,
                    tool_name=tool_name,
                    available_tools=sorted(tool_map.keys()),
                    session_id=session_id,
                )
                result = f"Tool '{tool_name}' not found."

            elif _tool_fail_counts.get(tool_name, 0) >= _MAX_TOOL_FAILURES:
                # Tool exhausted its permanent failure budget — skip without calling.
                logger.warning(
                    "react_loop.tool_skipped_unavailable",
                    agent=agent_name,
                    tool_name=tool_name,
                    failures=_tool_fail_counts[tool_name],
                    session_id=session_id,
                )
                result = (
                    f"Tool '{tool_name}' is currently unavailable "
                    f"(failed on {_tool_fail_counts[tool_name]} previous attempts). "
                    "Please use a different approach or inform the user that this "
                    "service cannot be reached right now."
                )

            elif tool_name in _rate_limited_this_iter:
                # Already hit the rate limit for this tool in this iteration.
                # Skip without an API call so we don't keep hammering the quota.
                logger.debug(
                    "react_loop.tool_skipped_rate_limited",
                    agent=agent_name,
                    tool_name=tool_name,
                    session_id=session_id,
                )
                result = (
                    f"Tool '{tool_name}' was rate-limited earlier in this step "
                    "and has been skipped to avoid exceeding the API quota. "
                    "Please space out search calls or use fewer concurrent queries."
                )

            else:
                try:
                    result = str(await tool_fn.ainvoke(tc.get("args", {})))
                    _tool_fail_counts[tool_name] = 0  # reset on success
                    successful_calls += 1
                except Exception as exc:
                    if _RATE_LIMIT_RE.search(str(exc)):
                        # Transient rate-limit (429): mark for this iteration only.
                        # Do NOT burn the permanent failure budget so the tool
                        # remains available in the next iteration after the
                        # back-off window resets.
                        _rate_limited_this_iter.add(tool_name)
                        logger.warning(
                            "react_loop.tool_rate_limited",
                            agent=agent_name,
                            tool_name=tool_name,
                            error=str(exc),
                            backoff_seconds=_RATE_LIMIT_BACKOFF,
                            session_id=session_id,
                        )
                        await asyncio.sleep(_RATE_LIMIT_BACKOFF)
                        result = (
                            f"Tool '{tool_name}' is temporarily rate-limited "
                            f"(HTTP 429). A {_RATE_LIMIT_BACKOFF}s back-off was "
                            "applied. Please reduce the number of concurrent "
                            "requests to this tool in your next step."
                        )
                    else:
                        # Permanent-style failure: increment the failure budget.
                        _tool_fail_counts[tool_name] = _tool_fail_counts.get(tool_name, 0) + 1
                        logger.warning(
                            "react_loop.tool_error",
                            agent=agent_name,
                            tool_name=tool_name,
                            error=str(exc),
                            total_failures=_tool_fail_counts[tool_name],
                            session_id=session_id,
                        )
                        result = f"Tool error: {exc}"

            # Cap individual tool results to avoid token explosion.
            if len(result) > 4_000:
                result = result[:4_000] + "\n…[truncated]"
            loop_messages.append(ToolMessage(content=result, tool_call_id=tc["id"]))

        # ── All-tools-failed early exit ───────────────────────────────────
        # Fire only when every call in this iteration had a *permanent* error
        # (bad API key, server down, …).  Rate-limit (429) failures are
        # transient — the service is reachable, just throttling — so they do
        # not count as a permanent failure and the loop must continue to the
        # next iteration where the back-off window has likely reset.
        if successful_calls == 0 and not _rate_limited_this_iter:
            logger.warning(
                "react_loop.all_tools_failed",
                agent=agent_name,
                iteration=iteration,
                session_id=session_id,
            )
            loop_messages.append(
                HumanMessage(
                    content=(
                        "All tool calls in this step failed — the required external "
                        "services may be temporarily unavailable or misconfigured. "
                        "Based on what you already know, please provide your best "
                        "answer and clearly state which information you could not "
                        "retrieve and why."
                    )
                )
            )
            try:
                response = await _invoke_llm_with_backoff(  # type: ignore[assignment]
                    llm,
                    loop_messages,
                    agent_name=agent_name,
                    session_id=session_id,
                    iteration=iteration,
                )
            except Exception as llm_exc:
                exc_str = str(llm_exc)
                if _PERMANENT_ERROR_RE.search(exc_str):
                    logger.error(
                        "react_loop.llm_unavailable",
                        agent=agent_name,
                        iteration=iteration,
                        error=exc_str[:200],
                        session_id=session_id,
                    )
                    return AIMessage(content=_llm_permanent_error_message())
                if _RATE_LIMIT_RE.search(exc_str):
                    logger.warning(
                        "react_loop.llm_rate_limited",
                        agent=agent_name,
                        iteration=iteration,
                        error=exc_str[:200],
                        session_id=session_id,
                    )
                    return AIMessage(
                        content=(
                            "I'm sorry, the AI service is temporarily rate-limited. "
                            "Please wait a moment and try again."
                        )
                    )
                raise
            _meter_response(budget_guard, response, llm, agent_name)
            break

    else:
        logger.warning(
            "react_loop.iteration_cap_reached",
            agent=agent_name,
            max_iterations=max_iterations,
            session_id=session_id,
        )
        # The last `response` still has pending tool_calls and no useful content.
        # Append the last tool results already in loop_messages and ask the LLM
        # to synthesise a final answer from everything gathered so far.
        loop_messages.append(response)
        loop_messages.append(
            HumanMessage(
                content=(
                    "You have reached the maximum number of tool call iterations. "
                    "Based on everything you have gathered so far, please provide "
                    "your best final answer to the user's original question. "
                    "If you were unable to retrieve the needed information, say so "
                    "clearly and explain what you found instead."
                )
            )
        )
        try:
            response = await _invoke_llm_with_backoff(  # type: ignore[assignment]
                llm,
                loop_messages,
                agent_name=agent_name,
                session_id=session_id,
                iteration=max_iterations,
            )
        except Exception as llm_exc:
            exc_str = str(llm_exc)
            if _PERMANENT_ERROR_RE.search(exc_str):
                logger.error(
                    "react_loop.llm_unavailable",
                    agent=agent_name,
                    error=exc_str[:200],
                    session_id=session_id,
                )
                return AIMessage(content=_llm_permanent_error_message())
            if _RATE_LIMIT_RE.search(exc_str):
                logger.warning(
                    "react_loop.llm_rate_limited",
                    agent=agent_name,
                    error=exc_str[:200],
                    session_id=session_id,
                )
                return AIMessage(
                    content=(
                        "I'm sorry, the AI service is temporarily rate-limited. "
                        "Please wait a moment and try again."
                    )
                )
            raise
        _meter_response(budget_guard, response, llm, agent_name)

    return _sanitise_final_response(response)


__all__ = [
    "DEFAULT_CAPABILITY_MAP",
    "get_mcp_tools",
    "get_recommended_capabilities",
    "get_skill_tools",
    "get_tool_provider",
    "get_tools_for_agent",
    "get_tools_for_agent_ctx",
    "init_mcp",
    "react_loop",
    "resolve_provider",
    "set_tool_provider",
]

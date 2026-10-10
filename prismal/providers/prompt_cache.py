"""Provider prompt-cache policy (Phase TKE — SPEC-TKE-CCH-001 / SPEC-TKE-CCH-002).

Detects the prompt-cache *family* of a model string and applies the matching
cache policy to an outgoing request:

* ``ANTHROPIC_EXPLICIT`` — explicit ``cache_control`` breakpoints on the last
  tool, the end of the system prompt and the last stable message.
* ``OPENAI_AUTO`` — automatic caching steered by a ``prompt_cache_key`` (and an
  optional ``prompt_cache_retention``) request kwarg.
* ``GEMINI_IMPLICIT`` / ``DEEPSEEK_AUTO`` / ``NONE`` — no-op.

Volatile provider facts (breakpoint cap, lookback window, minimum prefix sizes)
live in ``config/prompt_cache.yaml`` and are loaded by :func:`load_cache_policy`.

The hot path (:func:`apply_cache_policy`) is sync and fail-open: on any internal
error it returns the inputs unchanged with ``family=CacheFamily.NONE``.
"""

from __future__ import annotations

import contextlib
import copy
import hashlib
from collections.abc import Mapping  # noqa: TC003 — runtime: pydantic resolves CachePolicy
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

import yaml
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from pydantic import Field

from prismal.core.logging import get_logger
from prismal.utils.stable_json import stable_dumps
from prismal.utils.token_estimate import estimate_tokens

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator, Sequence

    from langchain_core.callbacks import (
        AsyncCallbackManagerForLLMRun,
        CallbackManagerForLLMRun,
    )
    from langchain_core.outputs import ChatGenerationChunk, ChatResult
    from langchain_core.runnables import Runnable

    from prismal.core.config import Settings

logger = get_logger(__name__)

_BREAKPOINT_COUNTER = "prompt_cache_breakpoints"
_VALID_TTLS = ("5m", "1h")
_VALID_SCOPES = ("tenant", "global")


class CacheFamily(StrEnum):
    """Prompt-cache family of a model (Phase TKE — SPEC-TKE-CCH-001)."""

    ANTHROPIC_EXPLICIT = "anthropic_explicit"
    OPENAI_AUTO = "openai_auto"
    GEMINI_IMPLICIT = "gemini_implicit"
    DEEPSEEK_AUTO = "deepseek_auto"
    NONE = "none"


_ANTHROPIC_PREFIXES = ("claude", "anthropic/", "openrouter/anthropic/")
_OPENAI_PREFIXES = ("gpt-", "openai/", "openrouter/openai/", "azure/", "o1", "o3", "o4")
_GEMINI_PREFIXES = ("gemini", "vertex_ai/gemini", "openrouter/google/")
_DEEPSEEK_PREFIXES = ("deepseek", "openrouter/deepseek/")


def detect_cache_family(model: str) -> CacheFamily:
    """Map a LiteLLM model string to its cache family (Phase TKE — SPEC-TKE-CCH-001).

    Pure and case-insensitive; unknown models map to :attr:`CacheFamily.NONE`.
    """
    m = (model or "").strip().lower()
    if not m:
        return CacheFamily.NONE
    if m.startswith(_ANTHROPIC_PREFIXES) or (
        m.startswith(("bedrock/", "vertex_ai/")) and "claude" in m
    ):
        return CacheFamily.ANTHROPIC_EXPLICIT
    if m.startswith(_GEMINI_PREFIXES):
        return CacheFamily.GEMINI_IMPLICIT
    if m.startswith(_DEEPSEEK_PREFIXES):
        return CacheFamily.DEEPSEEK_AUTO
    if m.startswith(_OPENAI_PREFIXES):
        return CacheFamily.OPENAI_AUTO
    return CacheFamily.NONE


@dataclass(frozen=True)
class CachePolicy:
    """Resolved prompt-cache policy (Phase TKE — SPEC-TKE-CCH-001)."""

    ttl: Literal["5m", "1h"] = "5m"
    max_breakpoints: int = 4
    lookback_blocks: int = 20
    min_prefix_tokens: Mapping[str, int] = field(default_factory=dict)
    openai_retention: str | None = None
    scope: Literal["tenant", "global"] = "tenant"

    def min_prefix_for(self, model: str, family: CacheFamily) -> int:
        """Minimum estimated prefix tokens for *model* (Phase TKE — SPEC-TKE-CCH-001).

        A model-specific key (substring of the model string) wins over the
        family key; absent both, ``0`` (no minimum).
        """
        m = (model or "").lower()
        family_key = str(family)
        for key, value in self.min_prefix_tokens.items():
            k = key.lower()
            if k != family_key and k in m:
                return int(value)
        return int(self.min_prefix_tokens.get(family_key, 0))


def load_cache_policy(settings: Settings) -> CachePolicy:
    """Load the cache policy YAML (Phase TKE — SPEC-TKE-CCH-001).

    Reads ``settings.prompt_cache_policy_path``. Explicitly-set
    ``settings.prompt_cache_ttl`` / ``prompt_cache_scope`` override the YAML.
    A missing or invalid file falls back to defaults with a warning; never raises.
    """
    raw: dict[str, Any] = {}
    path_str = str(getattr(settings, "prompt_cache_policy_path", "") or "")
    try:
        loaded = yaml.safe_load(Path(path_str).read_text(encoding="utf-8"))
        if loaded is None:
            loaded = {}
        if not isinstance(loaded, dict):
            raise ValueError("prompt cache policy must be a mapping")
        raw = loaded
    except Exception as exc:
        logger.warning("prompt_cache.policy_unavailable", path=path_str, error=str(exc))
        raw = {}

    defaults = CachePolicy()
    fields_set: set[str] = set(getattr(settings, "model_fields_set", set()))

    ttl: Any = raw.get("ttl", defaults.ttl)
    if "prompt_cache_ttl" in fields_set:
        ttl = settings.prompt_cache_ttl
    if ttl not in _VALID_TTLS:
        ttl = defaults.ttl

    scope: Any = raw.get("scope", getattr(settings, "prompt_cache_scope", defaults.scope))
    if "prompt_cache_scope" in fields_set:
        scope = settings.prompt_cache_scope
    if scope not in _VALID_SCOPES:
        scope = defaults.scope

    try:
        min_prefix_raw = raw.get("min_prefix_tokens") or {}
        min_prefix = {str(k): int(v) for k, v in dict(min_prefix_raw).items()}
        max_bp = int(raw.get("max_breakpoints", defaults.max_breakpoints))
        lookback = int(raw.get("lookback_blocks", defaults.lookback_blocks))
        retention_raw = raw.get("openai_retention")
        retention = None if retention_raw is None else str(retention_raw)
    except Exception as exc:
        logger.warning("prompt_cache.policy_invalid", path=path_str, error=str(exc))
        min_prefix, max_bp, lookback, retention = {}, 4, 20, None

    return CachePolicy(
        ttl=cast("Literal['5m', '1h']", ttl),
        max_breakpoints=max_bp,
        lookback_blocks=lookback,
        min_prefix_tokens=min_prefix,
        openai_retention=retention,
        scope=cast("Literal['tenant', 'global']", scope),
    )


@dataclass(frozen=True)
class CacheApplied:
    """Result of :func:`apply_cache_policy` (Phase TKE — SPEC-TKE-CCH-002)."""

    messages: list[BaseMessage]
    tools: list[dict[str, Any]]
    request_kwargs: dict[str, Any]
    breakpoints: int
    family: CacheFamily
    prefix_hash: str


def _otel() -> Any:
    from prismal.monitoring.otel import OTelManager

    return OTelManager()


def _record_breakpoints(n: int, family: CacheFamily) -> None:
    if n <= 0:
        return
    with contextlib.suppress(Exception):
        _otel().increment_counter(_BREAKPOINT_COUNTER, n, attributes={"family": str(family)})


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    parts: list[str] = []
    if isinstance(content, list):
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
    return "".join(parts)


def _system_text(messages: Sequence[BaseMessage]) -> str:
    return "".join(_content_text(m.content) for m in messages if isinstance(m, SystemMessage))


def _prefix_hash(
    tools: Sequence[dict[str, Any]], system: str, policy: CachePolicy, tenant: str | None
) -> str:
    payload = stable_dumps(list(tools)) + system
    if policy.scope == "tenant" and tenant is not None:
        payload += tenant
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _with_cache_control(msg: BaseMessage, cc: dict[str, str]) -> BaseMessage | None:
    """Return a copy of *msg* whose last text block carries *cc*; ``None`` if impossible."""
    content = msg.content
    if isinstance(content, str):
        blocks: list[Any] = [{"type": "text", "text": content, "cache_control": dict(cc)}]
    elif isinstance(content, list):
        blocks = copy.deepcopy(content)
        for i in range(len(blocks) - 1, -1, -1):
            block = blocks[i]
            if isinstance(block, str):
                blocks[i] = {"type": "text", "text": block, "cache_control": dict(cc)}
                break
            if isinstance(block, dict) and block.get("type") == "text":
                block["cache_control"] = dict(cc)
                break
        else:
            return None
    else:
        return None
    return msg.model_copy(update={"content": blocks})


def _message_breakpoint_index(messages: Sequence[BaseMessage], lookback: int) -> int | None:
    final_human = None
    for i in range(len(messages) - 1, -1, -1):
        if isinstance(messages[i], HumanMessage):
            final_human = i
            break
    if final_human is None or final_human == 0:
        return None
    candidate = final_human - 1
    target = max(candidate, len(messages) - max(lookback, 0))
    target = min(target, len(messages) - 1)
    if target == final_human:
        target = final_human + 1 if final_human + 1 < len(messages) else candidate
    if isinstance(messages[target], SystemMessage):
        return None
    return target


def _apply_anthropic(
    messages: list[BaseMessage],
    tools: list[dict[str, Any]],
    policy: CachePolicy,
) -> tuple[list[BaseMessage], list[dict[str, Any]], int]:
    cc: dict[str, str] = {"type": "ephemeral"}
    if policy.ttl == "1h":
        cc["ttl"] = "1h"
    budget = max(policy.max_breakpoints, 0)
    out_msgs = list(messages)
    out_tools = copy.deepcopy(tools)
    placed = 0

    # Priority: tool > system > message (dropped in reverse order when capped).
    if out_tools and placed < budget:
        out_tools[-1]["cache_control"] = dict(cc)
        placed += 1

    sys_idx = None
    for i in range(len(out_msgs) - 1, -1, -1):
        if isinstance(out_msgs[i], SystemMessage):
            sys_idx = i
            break
    if sys_idx is not None and placed < budget:
        updated = _with_cache_control(out_msgs[sys_idx], cc)
        if updated is not None:
            out_msgs[sys_idx] = updated
            placed += 1

    msg_idx = _message_breakpoint_index(out_msgs, policy.lookback_blocks)
    if msg_idx is not None and msg_idx != sys_idx and placed < budget:
        updated = _with_cache_control(out_msgs[msg_idx], cc)
        if updated is not None:
            out_msgs[msg_idx] = updated
            placed += 1

    return out_msgs, out_tools, placed


def apply_cache_policy(
    messages: Sequence[BaseMessage],
    tools: Sequence[dict[str, Any]],
    *,
    model: str,
    agent: str,
    policy: CachePolicy,
    tenant: str | None = None,
) -> CacheApplied:
    """Apply the provider prompt-cache policy (Phase TKE — SPEC-TKE-CCH-002).

    Sync, never raises: on any internal error the inputs are returned unchanged
    with ``family=CacheFamily.NONE``, zero breakpoints and empty request kwargs.
    Inputs are never mutated (new message objects, deep-copied tool dicts).
    """
    try:
        msgs = list(messages)
        tool_list = list(tools)
        for m in msgs:
            if not isinstance(m, BaseMessage):
                raise TypeError(f"not a BaseMessage: {type(m).__name__}")
        family = detect_cache_family(model)
        system = _system_text(msgs)
        prefix_hash = _prefix_hash(tool_list, system, policy, tenant)

        if family is CacheFamily.ANTHROPIC_EXPLICIT:
            prefix_tokens = estimate_tokens(stable_dumps(tool_list) + system)
            if prefix_tokens < policy.min_prefix_for(model, family):
                return CacheApplied(msgs, tool_list, {}, 0, family, prefix_hash)
            out_msgs, out_tools, placed = _apply_anthropic(msgs, tool_list, policy)
            _record_breakpoints(placed, family)
            return CacheApplied(out_msgs, out_tools, {}, placed, family, prefix_hash)

        if family is CacheFamily.OPENAI_AUTO:
            kwargs: dict[str, Any] = {"prompt_cache_key": f"prismal:{agent}:{prefix_hash[:16]}"}
            if policy.openai_retention:
                kwargs["prompt_cache_retention"] = policy.openai_retention
            return CacheApplied(msgs, tool_list, kwargs, 0, family, prefix_hash)

        return CacheApplied(msgs, tool_list, {}, 0, family, prefix_hash)
    except Exception as exc:
        logger.warning("prompt_cache.apply_failed", model=model, error=str(exc))
        return CacheApplied(list(messages), list(tools), {}, 0, CacheFamily.NONE, "")


# ── CacheAwareChatModel (SPEC-TKE-CCH-003, SPEC-TKE-EFF-001; ADR-WAVE-001 §2.2) ──

_RECOMMENDED_HIGH_EFFORT = frozenset({"planner", "critic", "meta_learner", "judge"})


def recommended_effort(agent: str) -> str:
    """Effort for *agent* under the ``recommended`` profile (planners/judges high)."""
    if agent in _RECOMMENDED_HIGH_EFFORT or "judge" in agent or "critic" in agent:
        return "high"
    return "low"


def supports_reasoning(model: str) -> bool:
    """``litellm.supports_reasoning`` (verified TKE0-05); False on any error."""
    try:
        import litellm

        return bool(litellm.supports_reasoning(model=model))
    except Exception:
        return False


class CacheAwareChatModel(BaseChatModel):
    """Outer wrapper applying prompt-cache policy and per-agent effort at call time.

    Composition, not inheritance (ADR-WAVE-001): ``inner`` is the concrete
    ``ChatLiteLLM`` (or GW's ``GatewayChatLiteLLM``). The wrapper only touches
    the *input* side — cache breakpoints / ``prompt_cache_key``, name-sorted
    stable tool binding, ``reasoning_effort`` / ``max_tokens`` — and returns
    the inner ``ChatResult`` unchanged, so response metadata survives.

    The agent is ``agent`` when given, else ``config["metadata"]["langgraph_node"]``
    of the call. Effort is constant per agent for the whole run (cache-safe).
    """

    inner: BaseChatModel
    policy: CachePolicy = Field(default_factory=CachePolicy)
    model_name: str = ""
    agent: str | None = None
    tenant: str | None = None
    apply_cache: bool = True
    bound_tools: list[dict[str, Any]] = []
    effort_by_agent: dict[str, str] = {}
    effort_profile: str = "none"
    max_tokens_by_agent: dict[str, int] = {}

    model_config = {"arbitrary_types_allowed": True}

    @property
    def _llm_type(self) -> str:
        return "prismal-cache-aware"

    @property
    def _identifying_params(self) -> dict[str, Any]:
        return {"model_name": self.model_name, "agent": self.agent, "inner": self.inner._llm_type}

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> Runnable[Any, Any]:
        """Bind *tools* sorted by name (stable schemas) — cache-safe tool order."""
        from langchain_core.utils.function_calling import convert_to_openai_tool

        schemas = [convert_to_openai_tool(t) for t in tools]
        schemas.sort(key=lambda s: str(s.get("function", {}).get("name", s.get("name", ""))))
        wrapped = self.model_copy(update={"bound_tools": schemas})
        return wrapped.bind(**kwargs) if kwargs else wrapped

    def effort_for(self, agent: str) -> str | None:
        """Explicit per-agent effort, else the ``recommended`` profile, else None."""
        if agent in self.effort_by_agent:
            return self.effort_by_agent[agent]
        if self.effort_profile == "recommended":
            return recommended_effort(agent)
        return None

    def _prepare(
        self, messages: list[BaseMessage], run_manager: Any, kwargs: dict[str, Any]
    ) -> tuple[list[BaseMessage], dict[str, Any]]:
        agent = self.agent or _node_from(run_manager) or ""
        call_kwargs = dict(kwargs)
        out_messages = messages
        if self.apply_cache:
            applied = apply_cache_policy(
                messages,
                self.bound_tools,
                model=self.model_name,
                agent=agent or "agent",
                policy=self.policy,
                tenant=self.tenant,
            )
            out_messages = applied.messages
            if self.bound_tools:
                call_kwargs["tools"] = applied.tools
            call_kwargs.update(applied.request_kwargs)
        elif self.bound_tools:
            call_kwargs["tools"] = self.bound_tools
        effort = self.effort_for(agent) if agent else None
        if effort and supports_reasoning(self.model_name):
            call_kwargs["reasoning_effort"] = effort
        if agent and agent in self.max_tokens_by_agent:
            call_kwargs["max_tokens"] = self.max_tokens_by_agent[agent]
        return out_messages, call_kwargs

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        msgs, call_kwargs = self._prepare(messages, run_manager, kwargs)
        return self.inner._generate(msgs, stop=stop, run_manager=run_manager, **call_kwargs)

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        msgs, call_kwargs = self._prepare(messages, run_manager, kwargs)
        return await self.inner._agenerate(msgs, stop=stop, run_manager=run_manager, **call_kwargs)

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        msgs, call_kwargs = self._prepare(messages, run_manager, kwargs)
        yield from self.inner._stream(msgs, stop=stop, run_manager=run_manager, **call_kwargs)

    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        msgs, call_kwargs = self._prepare(messages, run_manager, kwargs)
        async for chunk in self.inner._astream(
            msgs, stop=stop, run_manager=run_manager, **call_kwargs
        ):
            yield chunk


def _node_from(run_manager: Any) -> str | None:
    """The LangGraph node name carried in callback metadata, if any."""
    metadata = getattr(run_manager, "metadata", None)
    if isinstance(metadata, dict):
        node = metadata.get("langgraph_node")
        if isinstance(node, str) and node:
            return node
    return None


__all__ = [
    "CacheApplied",
    "CacheAwareChatModel",
    "CacheFamily",
    "CachePolicy",
    "apply_cache_policy",
    "detect_cache_family",
    "load_cache_policy",
    "recommended_effort",
    "supports_reasoning",
]

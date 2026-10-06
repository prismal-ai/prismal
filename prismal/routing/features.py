"""Deterministic, LLM-free feature extraction (Phase AMR — SPEC-AMR-INT-001).

Untrusted text never chooses a model directly: it only feeds a regex intent
label, a length estimate, a language guess and a taint flag. Callers can pin
any feature through ``state["metadata"]["routing"]`` hints (``intent``,
``risk_tier``, ``needs_tools``, ``needs_structured_output``, ``tainted``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from prismal.core.config import get_settings
from prismal.decisions.state import detect_language
from prismal.decisions.types import RiskTier
from prismal.routing.types import TaskFeatures
from prismal.security.taint import TaintRegistry, get_active_taint_registry
from prismal.utils.token_estimate import estimate_tokens

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from prismal.core.config import Settings

#: Agents that execute code, drive a computer or write files (DEC tier map style).
_HIGH_RISK_AGENTS = frozenset({"codeact", "cua", "file_manager"})
#: Agents with side effects that are reviewed / reversible.
_MEDIUM_RISK_AGENTS = frozenset({"coder", "cron_manager", "skill_manager", "dev_pipeline"})
#: Agents that never bind tools.
_TOOL_FREE_AGENTS = frozenset(
    {
        "supervisor",
        "critic",
        "tot_agent",
        "debate_agent",
        "constitutional_filter",
        "mixture_agent",
        "kokoro",
    }
)
#: Agents whose output is parsed as structured data.
_STRUCTURED_AGENTS = frozenset({"supervisor", "planner", "llm_compiler", "domain_supervisor"})


def match_intent(text: str | None) -> str | None:
    """Lazy proxy to ``agents.intent_router.match_intent`` (avoids an import cycle)."""
    from prismal.agents.intent_router import match_intent as _match

    return _match(text)


def _text(message: Any) -> str:
    content = getattr(message, "content", message)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            str(part.get("text", "")) if isinstance(part, dict) else str(part) for part in content
        )
    return str(content)


def _messages(state: Mapping[str, Any]) -> Sequence[Any]:
    messages = state.get("messages")
    return messages if isinstance(messages, list) else []


def _current_turn(messages: Sequence[Any]) -> tuple[str, Sequence[Any]]:
    """(last human text, messages since and including it)."""
    for idx in range(len(messages) - 1, -1, -1):
        if getattr(messages[idx], "type", None) == "human":
            return _text(messages[idx]), messages[idx:]
    return "", messages


def _taint_registry(metadata: Mapping[str, Any]) -> TaintRegistry | None:
    active = get_active_taint_registry()
    if active is not None:
        return active
    hardening = metadata.get("hardening")
    raw = hardening.get("taint") if isinstance(hardening, dict) else None
    return TaintRegistry.from_dict(raw) if isinstance(raw, dict) and raw else None


def _bool_hint(hints: Mapping[str, Any], key: str, default: bool) -> bool:
    value = hints.get(key)
    return value if isinstance(value, bool) else default


def extract_features(
    state: Mapping[str, Any], agent: str, *, settings: Settings | None = None
) -> TaskFeatures:
    """Build :class:`TaskFeatures` for *agent* from *state* (pure, deterministic)."""
    _ = settings or get_settings()  # reserved for per-tenant feature tuning
    metadata = state.get("metadata") if isinstance(state.get("metadata"), dict) else {}
    assert isinstance(metadata, dict)
    routing = metadata.get("routing")
    hints: Mapping[str, Any] = routing if isinstance(routing, dict) else {}
    messages = _messages(state)
    human, turn = _current_turn(messages)

    intent = hints.get("intent") if isinstance(hints.get("intent"), str) else None
    if intent is None:
        intent = match_intent(human or None)

    if agent in _HIGH_RISK_AGENTS:
        risk = RiskTier.HIGH
    elif agent in _MEDIUM_RISK_AGENTS:
        risk = RiskTier.MEDIUM
    else:
        risk = RiskTier.LOW
    risk_hint = hints.get("risk_tier")
    if isinstance(risk_hint, str) and risk_hint in {r.value for r in RiskTier}:
        hinted = RiskTier(risk_hint)
        risk = hinted if hinted.at_least(risk) else risk  # hints may only raise risk

    tainted = _bool_hint(hints, "tainted", False)
    if not tainted:
        registry = _taint_registry(metadata)
        if registry is not None:
            tainted = any(registry.is_untrusted(_text(m)) for m in turn)

    return TaskFeatures(
        agent=agent,
        intent=intent,
        est_input_tokens=sum(estimate_tokens(_text(m)) for m in messages),
        needs_tools=_bool_hint(hints, "needs_tools", agent not in _TOOL_FREE_AGENTS),
        needs_structured_output=_bool_hint(
            hints, "needs_structured_output", agent in _STRUCTURED_AGENTS
        ),
        risk_tier=risk,
        language=detect_language(human) if human else "en",
        tainted=tainted,
    )

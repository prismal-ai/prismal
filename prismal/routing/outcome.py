"""Agent-side routing seams (Phase AMR — SPEC-AMR-INT-001 / CAS-001).

Two one-liners for supervisor-routed agents, both no-ops with routing off so
the disabled call is byte-identical to 3.15.0:

- ``registry.get_llm_with_fallback(**llm_route_kwargs("coder", state))`` — the
  policy picks the model string (ADR-WAVE-001 step 1);
- ``await track_outcome(state, "coder", react_loop(...))`` — counts tool errors
  noted by ``react_loop`` (``note_tool_error``) and feeds a ``RoutingOutcome``
  back, so an escalation sticks for the next call via cache affinity.

This module is import-light on purpose: ``agents/tool_registry.py`` imports
``note_tool_error`` from here.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Awaitable

    from prismal.core.config import Settings

_TOOL_ERRORS: ContextVar[list[int] | None] = ContextVar("prismal_routing_tool_errors", default=None)


def _enabled(settings: Settings | None) -> bool:
    from prismal.core.config import get_settings

    return bool((settings or get_settings()).adaptive_routing_enabled)


def note_tool_error() -> None:
    """Count one failed tool call in the active tracked call (no-op outside one)."""
    stats = _TOOL_ERRORS.get()
    if stats is not None:
        stats[0] += 1


def llm_route_kwargs(agent: str, state: Any, *, settings: Settings | None = None) -> dict[str, Any]:
    """``get_llm`` kwargs for adaptive routing; ``{}`` when routing is disabled.

    Carries ``features`` + ``state`` but never ``agent=``: passing the agent
    would newly activate the gateway agent map / per-agent effort for these
    call sites, which 3.15.0 never did (shadow must equal 3.15.0).
    """
    if not _enabled(settings):
        return {}
    from prismal.routing.features import extract_features

    return {"features": extract_features(state, agent, settings=settings), "state": state}


def record_agent_outcome(
    state: Any,
    agent: str,
    *,
    success: bool,
    tool_errors: int = 0,
    settings: Settings | None = None,
) -> None:
    """Report an agent's outcome when its loop is not a single awaitable (e.g. CodeAct)."""
    if not _enabled(settings):
        return
    from prismal.routing.select import record_outcome
    from prismal.routing.types import RoutingOutcome

    record_outcome(state, agent, RoutingOutcome(success=success, tool_errors=tool_errors))


def _is_structural_failure(exc: BaseException) -> bool:
    """Parse/validation failures escalate; transport or policy errors never do."""
    import json

    from pydantic import ValidationError

    try:
        from langchain_core.exceptions import OutputParserException
    except ImportError:  # pragma: no cover - langchain_core is a hard dependency
        return isinstance(exc, ValidationError | json.JSONDecodeError)
    return isinstance(exc, ValidationError | json.JSONDecodeError | OutputParserException)


async def track_outcome[T](
    state: Any, agent: str, call: Awaitable[T], *, settings: Settings | None = None
) -> T:
    """Await *call*; when routing is on, record its outcome for *agent*.

    An exception is recorded as a failed, structurally invalid outcome (the
    typical cause is a parse/validation error) and re-raised unchanged.
    """
    if not _enabled(settings):
        return await call
    from prismal.routing.select import record_outcome
    from prismal.routing.types import RoutingOutcome

    stats = [0]
    token = _TOOL_ERRORS.set(stats)
    try:
        result = await call
    except Exception as exc:
        if _is_structural_failure(exc):
            record_outcome(state, agent, RoutingOutcome(success=False, structured_valid=False))
        raise
    finally:
        _TOOL_ERRORS.reset(token)
    record_outcome(
        state,
        agent,
        RoutingOutcome(
            success=True,
            tool_errors=stats[0],
            usage=_usage_of(result),
            **(await _adequacy(state, agent, result)),
        ),
    )
    return result


def _usage_of(result: Any) -> Any:
    from prismal.budget.types import Usage
    from prismal.budget.usage import extract_token_usage

    counts = extract_token_usage(result)
    if counts.prompt_tokens == 0 and counts.completion_tokens == 0:
        return None
    return Usage(
        prompt_tokens=counts.prompt_tokens, completion_tokens=counts.completion_tokens, calls=1
    )


async def _adequacy(state: Any, agent: str, result: Any) -> dict[str, Any]:
    """``adequacy`` / ``adequacy_checked`` from the run's checker (empty when none)."""
    from prismal.routing.select import get_adequacy_checker, get_routing_features

    checker = get_adequacy_checker(state)
    features = get_routing_features(state, agent)
    content = getattr(result, "content", None)
    if checker is None or features is None or not isinstance(content, str) or not content:
        return {}
    request = ""
    messages = state.get("messages") if isinstance(state, dict) else None
    for message in reversed(messages if isinstance(messages, list) else []):
        if getattr(message, "type", None) == "human":
            request = str(getattr(message, "content", ""))
            break
    try:
        p = await checker.check(request, content, risk_tier=features.risk_tier)
    except Exception:
        return {"adequacy_checked": True}
    return {"adequacy": p, "adequacy_checked": True}

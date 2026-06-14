"""Per-run hardening engine registry (Phase H — H5-01/H5-02).

Mirrors ``budget/resolve.py``: when ``hardening_enabled`` it builds the per-run
**live** engines (injection detector, runaway guard, tool-policy enforcer, taint
registry) and stores them in an in-process registry keyed by ``session_id`` —
never in checkpointed state, so a live engine never reaches the serializer. State
carries only a small serializable marker under ``state['metadata']['hardening']``.

Seeding is idempotent per user-turn signature, so the engines persist across the
supervisor's many hops within a turn (the runaway step counter accumulates) and
reset between turns.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from prismal.core.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Mapping

    from prismal.agents.state import AgentState
    from prismal.core.config import Settings
    from prismal.security.audit import AuditLogger
    from prismal.security.indirect_injection import IndirectInjectionDetector
    from prismal.security.runaway import RunawayGuard
    from prismal.security.taint import TaintRegistry
    from prismal.security.tool_policy import RunToolPolicy

logger = get_logger("prismal.security.hardening_run")

# session_id -> {detector, runaway, tool_policy, taint, turn}. In-process only.
_RUN_ENGINES: dict[str, dict[str, Any]] = {}

_DEFAULT_KEY = "_default"


def _run_key(state: Mapping[str, Any]) -> str:
    if isinstance(state, dict):
        sid = state.get("session_id")
        if sid:
            return str(sid)
    return _DEFAULT_KEY


def _turn_signature(state: Mapping[str, Any]) -> int:
    messages = state.get("messages") if isinstance(state, dict) else None
    if not isinstance(messages, list):
        return 0
    return sum(1 for m in messages if getattr(m, "type", None) == "human")


def seed_hardening_run(
    state: AgentState,
    settings: Settings,
    *,
    session_id: str | None = None,
    audit: AuditLogger | None = None,
) -> None:
    """Install the per-run hardening engines in the registry.

    No-op when ``settings.hardening_enabled`` is False, so the disabled path
    carries zero state and is byte-for-byte unchanged.
    """
    if not settings.hardening_enabled:
        return

    from prismal.security.indirect_injection import IndirectInjectionDetector
    from prismal.security.runaway import RunawayGuard
    from prismal.security.taint import TaintRegistry
    from prismal.security.tool_policy import RunToolPolicy, ToolPolicyEngine, load_tool_policies

    key = session_id or _run_key(state)

    classifier_fn = None
    if settings.hardening_injection_classifier:
        from contextlib import suppress

        with suppress(Exception):
            from prismal.providers.injection_classifier import build_injection_classifier

            classifier_fn = build_injection_classifier(settings=settings)

    detector = IndirectInjectionDetector(
        classifier_fn=classifier_fn, audit=audit, settings=settings
    )
    runaway = RunawayGuard(settings=settings)
    policies = load_tool_policies(settings.tool_policy_path)
    tool_policy = RunToolPolicy(ToolPolicyEngine(policies, settings=settings), audit=audit)
    taint = TaintRegistry() if settings.taint_tracking_enabled else None

    _RUN_ENGINES[key] = {
        "detector": detector,
        "runaway": runaway,
        "tool_policy": tool_policy,
        "taint": taint,
        "turn": _turn_signature(state),
    }
    metadata = state.setdefault("metadata", {})
    metadata["hardening"] = {"enabled": True, "mode": settings.hardening_mode}


def maybe_seed_hardening_run(
    state: AgentState,
    settings: Settings,
    *,
    audit: AuditLogger | None = None,
) -> None:
    """Seed once per turn — idempotent across the supervisor's hops. No-op when off."""
    if not settings.hardening_enabled:
        return
    existing = _RUN_ENGINES.get(_run_key(state))
    if existing is not None and existing.get("turn") == _turn_signature(state):
        return
    seed_hardening_run(state, settings, audit=audit)


def _bucket(state: AgentState) -> dict[str, Any] | None:
    return _RUN_ENGINES.get(_run_key(state))


def get_injection_detector(state: AgentState) -> IndirectInjectionDetector | None:
    """Return the per-run injection detector, or None when disabled/unseeded."""
    bucket = _bucket(state)
    return bucket.get("detector") if bucket else None


def get_runaway_guard(state: AgentState) -> RunawayGuard | None:
    """Return the per-run runaway guard, or None when disabled/unseeded."""
    bucket = _bucket(state)
    return bucket.get("runaway") if bucket else None


def get_tool_policy(state: AgentState) -> RunToolPolicy | None:
    """Return the per-run tool-policy enforcer, or None when disabled/unseeded."""
    bucket = _bucket(state)
    return bucket.get("tool_policy") if bucket else None


def get_taint_registry(state: AgentState) -> TaintRegistry | None:
    """Return the per-run taint registry, or None when disabled or tracking off."""
    bucket = _bucket(state)
    return bucket.get("taint") if bucket else None


def hardening_react_kwargs(state: AgentState) -> dict[str, Any]:
    """Return the ``react_loop`` hardening kwargs for *state*.

    Returns an **empty dict** when hardening is disabled/unseeded, so splatting
    this into ``react_loop(**hardening_react_kwargs(state))`` passes no extra
    kwargs at all — the disabled call is byte-for-byte identical to before.
    """
    bucket = _bucket(state)
    if not bucket:
        return {}
    return {
        "injection_detector": bucket.get("detector"),
        "tool_policy": bucket.get("tool_policy"),
        "runaway_guard": bucket.get("runaway"),
    }


def clear_hardening_run(state: AgentState) -> None:
    """Release the per-run engines for *state* (idempotent)."""
    _RUN_ENGINES.pop(_run_key(state), None)


__all__ = [
    "clear_hardening_run",
    "get_injection_detector",
    "get_runaway_guard",
    "get_taint_registry",
    "get_tool_policy",
    "hardening_react_kwargs",
    "maybe_seed_hardening_run",
    "seed_hardening_run",
]

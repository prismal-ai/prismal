"""Mask-mode tool gating (Phase TKE — SPEC-TKE-GAT-001; MODIFIES SPEC-LH-GAT-002/003).

In ``tool_gating_mode="mask"`` the model always sees the agent's full,
name-sorted tool list (so the provider prompt-cache prefix — tools first —
never changes between phases); the phase-narrowed set becomes an *execution*
allow-set. A call outside it is never executed: it is answered with a
synthetic :class:`ToolMessage`, audited hash-first and counted. Enforcement is
fail-closed and duplicated in ``react_loop`` and ``ActionInterceptor``.
"""

from __future__ import annotations

import hashlib
from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from langchain_core.messages import ToolMessage

if TYPE_CHECKING:
    from collections.abc import Mapping


@dataclass(frozen=True)
class GateVerdict:
    """Whether *tool* may run in *phase*."""

    allowed: bool
    tool: str
    phase: str | None
    reason: str = ""


def check_tool_call(
    tool_name: str, allow_set: frozenset[str] | None, phase: str | None
) -> GateVerdict:
    """Verdict for one tool call; ``allow_set=None`` means no restriction."""
    if allow_set is None or tool_name in allow_set:
        return GateVerdict(allowed=True, tool=tool_name, phase=phase)
    return GateVerdict(allowed=False, tool=tool_name, phase=phase, reason="not in phase allow-set")


def masked_tool_message(call: Mapping[str, Any], verdict: GateVerdict) -> ToolMessage:
    """The synthetic answer returned instead of executing a masked call."""
    return ToolMessage(
        content=f"tool '{verdict.tool}' not available in phase {verdict.phase}",
        tool_call_id=str(call.get("id", "")),
    )


def allowed_tools_hint(allow_set: frozenset[str], phase: str) -> str:
    """Tail hint naming the tools usable in *phase* (never put in the system prompt)."""
    return f"<allowed_tools phase={phase}>{', '.join(sorted(allow_set))}</allowed_tools>"


def record_masked_call(
    verdict: GateVerdict, *, agent: str, audit_logger: Any | None = None
) -> None:
    """Audit (hash-first) and count a masked call. Never raises."""
    with suppress(Exception):
        from prismal.monitoring.otel import OTelManager

        OTelManager().increment_counter(
            "tool_gate_masked_calls",
            attributes={"agent": agent, "phase": verdict.phase or ""},
        )
    with suppress(Exception):
        if audit_logger is None:
            from prismal.security.audit import AuditLogger

            audit_logger = AuditLogger()
        audit_logger.log_event(
            "tool_gate_masked_call",
            {
                "agent": agent,
                "tool": verdict.tool,
                "phase": verdict.phase or "",
                "tool_hash": hashlib.sha256(verdict.tool.encode()).hexdigest()[:16],
            },
        )


__all__ = [
    "GateVerdict",
    "allowed_tools_hint",
    "check_tool_call",
    "masked_tool_message",
    "record_masked_call",
]

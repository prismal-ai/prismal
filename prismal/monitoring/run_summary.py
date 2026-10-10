"""Run-summary accessor for hosts (Phase PRT — SPEC-PRT-USG-001, PRT5-06).

``get_run_summary(session_id)`` returns the :class:`RunSummary` of the session's
current turn (or of every retained turn, summed, with ``turn="all"``) from the
per-run observability provider; its ``to_usage_wire()`` is the 12-key usage
contract shared with ``prismal-server`` and ``prismal-sdk``. Never raises.
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import TYPE_CHECKING, Literal

from prismal.budget.types import Usage
from prismal.core.logging import get_logger
from prismal.monitoring.observability_resolve import _RUNS, get_observability_provider

if TYPE_CHECKING:
    from prismal.monitoring.observability_types import RunSummary

logger = get_logger(__name__)

_TURN_RE = re.compile(r"\.turn(\d+)$")


def _turn_of(run_id: str) -> int:
    match = _TURN_RE.search(run_id)
    return int(match.group(1)) if match else 0


def _merge(session_id: str, label: str, summaries: list[RunSummary]) -> RunSummary | None:
    if not summaries:
        return None
    if len(summaries) == 1:
        return summaries[0]
    usage = Usage()
    for s in summaries:
        usage = usage + s.usage
    last = summaries[-1]
    return replace(
        last,
        run_id=f"{session_id}.{label}",
        agent_name=",".join(dict.fromkeys(s.agent_name for s in summaries)),
        visited_nodes=[n for s in summaries for n in s.visited_nodes],
        spans=[sp for s in summaries for sp in s.spans],
        tool_calls=[t for s in summaries for t in s.tool_calls],
        usage=usage,
        latency_ms=sum(s.latency_ms for s in summaries),
        cache_read_tokens=usage.cache_read_tokens,
        cache_write_tokens=usage.cache_write_tokens,
        reasoning_tokens=usage.reasoning_tokens,
        cache_hit_ratio=usage.cache_hit_ratio,
        gateway=usage.gateway,
        served_model=usage.served_model,
        equivalent_usd=usage.equivalent_usd,
    )


def get_run_summary(session_id: str, *, turn: Literal["last", "all"] = "last") -> RunSummary | None:
    """The session's run summary for the last turn (default) or all turns; None if unknown."""
    try:
        provider = get_observability_provider(session_id)
        if provider is None:
            return None
        lister = getattr(provider, "session_summaries", None)
        summaries: list[RunSummary] = list(lister(session_id)) if callable(lister) else []
        if not summaries:
            entry = _RUNS.get(session_id) or {}
            run_id = str(entry.get("run_id", ""))
            return provider.get_run_summary(run_id) if run_id else None
        if turn == "all":
            return _merge(session_id, "all", summaries)
        last_turn = max(_turn_of(s.run_id) for s in summaries)
        return _merge(
            session_id,
            f"turn{last_turn}",
            [s for s in summaries if _turn_of(s.run_id) == last_turn],
        )
    except Exception as exc:
        logger.warning("run_summary.unavailable", error=type(exc).__name__)
        return None


__all__ = ["get_run_summary"]

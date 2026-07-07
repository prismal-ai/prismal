"""Value objects for the observability layer (Phase OBS — SPEC-OBS-TYP-001).

Frozen, immutable snapshots of one run's telemetry — deliberately
*shape-parallel* to (but independent of) :class:`prismal.eval.types.Trajectory`
(see ``ARCHITECTURE.md`` DD-OBS-001 for why the two are not unified). These types
carry **structural** data only (node/tool names, durations, token counts, cost) —
never raw prompt/response content — matching the hash-first convention the
``AuditLogger`` and Budget/Hardening layers already follow.

:class:`RunSummary.usage` reuses :class:`prismal.budget.types.Usage` (and its
``__add__``) for the cost/latency portion rather than reinventing it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from prismal.budget.types import Usage


class DatasetFormat(StrEnum):
    """Evaluation-dataset export target (SPEC-OBS-PAR-003)."""

    LANGSMITH = "langsmith"
    LANGFUSE = "langfuse"


@dataclass(frozen=True)
class SpanRecord:
    """One recorded unit of work within a run (a node visit or an LLM call)."""

    name: str  # e.g. "coder" (node) or "coder.llm_call"
    node: str
    duration_ms: float
    status: Literal["ok", "error"] = "ok"
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolCallRecord:
    """One recorded tool invocation within a run."""

    tool_name: str
    node: str
    ok: bool
    duration_ms: float = 0.0


@dataclass(frozen=True)
class ScoreAnnotation:
    """A feedback/score attached to a run (SPEC-OBS-PAR-002)."""

    name: str
    value: float
    comment: str = ""
    source: Literal["human", "llm_judge", "system"] = "system"


@dataclass(frozen=True)
class RunSummary:
    """Queryable snapshot of one run's telemetry (SPEC-OBS-PRT-001).

    Deliberately shape-parallel to (but independent of)
    :class:`prismal.eval.types.Trajectory` — see ARCHITECTURE.md DD-OBS-001 for
    why the two are not unified in this phase.
    """

    run_id: str
    session_id: str
    agent_name: str
    visited_nodes: list[str]
    spans: list[SpanRecord]
    tool_calls: list[ToolCallRecord]
    usage: Usage  # reused from prismal.budget.types
    latency_ms: float
    scores: list[ScoreAnnotation] = field(default_factory=list)
    started_at: float = 0.0
    ended_at: float | None = None


__all__ = [
    "DatasetFormat",
    "RunSummary",
    "ScoreAnnotation",
    "SpanRecord",
    "ToolCallRecord",
]

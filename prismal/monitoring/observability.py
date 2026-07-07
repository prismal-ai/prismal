"""Observability adapters and helpers (Phase OBS — SPEC-OBS-PRT/PAR-001..003).

``DefaultObservabilityProvider`` is **glue, not a new backend** (DD-OBS-003): it
forwards to the existing ``OTelManager``/``LangfuseManager`` singletons and keeps
a small *bounded, in-memory* ring buffer per run so ``get_run_summary`` returns
something even when no OTel collector or Langfuse project is reachable. Process
restart loses the buffer — exactly like an unflushed OTel batch (best-effort by
contract, DD-OBS-006). ``FakeObservabilityProvider`` is the deterministic,
I/O-free test double.

Both share the accumulation logic in :class:`_RunRegistry`; only the default
adapter adds the OTel/Langfuse side effects and the capacity caps.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from prismal.budget.types import Usage
from prismal.core.logging import get_logger
from prismal.monitoring.langfuse_client import LangfuseManager
from prismal.monitoring.observability_types import (
    DatasetFormat,
    RunSummary,
    ScoreAnnotation,
    SpanRecord,
    ToolCallRecord,
)
from prismal.monitoring.otel import OTelManager

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence

    from prismal.core.config import Settings

logger = get_logger("prismal.observability")


# ── Naming convention (single source of truth — SPEC-OBS-PAR-001, DD-OBS-004) ──


def run_name_for(*, agent_name: str, session_id: str, turn: int) -> str:
    """Return the canonical run/trace name ``f"{agent_name}.{session_id}.turn{turn}"``.

    Single source of truth consumed by ``DefaultObservabilityProvider``,
    ``LangfuseManager.create_trace(name=...)``, and any LangSmith-side integration
    a host wires. Both dashboards group/filter by this string in their default
    views — it is stable, deterministic, and free of PII (``session_id`` is
    already an opaque identifier elsewhere in the codebase).
    """
    return f"{agent_name}.{session_id}.turn{turn}"


def trace_tags_for(
    *, agent_name: str, node: str | None = None, org_id: str | None = None
) -> list[str]:
    """Return the canonical tag set for a run/trace (SPEC-OBS-PAR-001).

    ``["agent:<agent_name>"]`` plus optional ``"node:<node>"`` / ``"org:<org_id>"``.
    Used for Langfuse ``tags`` and as LangSmith run metadata/tags.
    """
    tags = [f"agent:{agent_name}"]
    if node is not None:
        tags.append(f"node:{node}")
    if org_id is not None:
        tags.append(f"org:{org_id}")
    return tags


def _agent_from_run_id(run_id: str) -> str:
    """Derive the agent name from a canonical ``run_id`` (SPEC-OBS-PAR-001).

    ``run_name_for`` names a run ``f"{agent_name}.{session_id}.turn{turn}"`` — the
    agent name is the first dotted segment. Falls back to the whole id for
    non-canonical run ids (best-effort; never raises).
    """
    return run_id.split(".", 1)[0] if "." in run_id else run_id


@dataclass
class _RunAccumulator:
    """Mutable per-run telemetry accumulator (internal)."""

    session_id: str
    agent_name: str
    visited_nodes: list[str] = field(default_factory=list)
    spans: list[SpanRecord] = field(default_factory=list)
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    latency_ms: float = 0.0
    scores: list[ScoreAnnotation] = field(default_factory=list)

    def to_summary(self, run_id: str) -> RunSummary:
        """Build the immutable :class:`RunSummary` snapshot for *run_id*."""
        return RunSummary(
            run_id=run_id,
            session_id=self.session_id,
            agent_name=self.agent_name,
            visited_nodes=list(self.visited_nodes),
            spans=list(self.spans),
            tool_calls=list(self.tool_calls),
            usage=self.usage,
            latency_ms=self.latency_ms,
            scores=list(self.scores),
        )


class _RunRegistry:
    """In-memory per-run accumulation shared by the default and fake adapters.

    ``buffer_size`` caps the spans/tool-calls retained per run (0 = unbounded);
    ``max_runs`` caps the number of concurrent runs before LRU eviction
    (0 = unbounded). The default adapter passes the settings-derived caps; the
    fake leaves both unbounded for deterministic tests.
    """

    def __init__(
        self,
        *,
        buffer_size: int = 0,
        max_runs: int = 0,
        on_evict: Callable[[str], None] | None = None,
    ) -> None:
        self._buffer_size = buffer_size
        self._max_runs = max_runs
        self._on_evict = on_evict
        self._runs: OrderedDict[str, _RunAccumulator] = OrderedDict()

    def _touch(self, run_id: str, session_id: str) -> _RunAccumulator:
        acc = self._runs.get(run_id)
        if acc is None:
            acc = _RunAccumulator(session_id=session_id, agent_name=_agent_from_run_id(run_id))
            self._runs[run_id] = acc
            self._evict_if_needed()
        self._runs.move_to_end(run_id)
        return acc

    def _evict_if_needed(self) -> None:
        if self._max_runs <= 0:
            return
        while len(self._runs) > self._max_runs:
            evicted, _ = self._runs.popitem(last=False)
            logger.debug("observability.run_evicted", run_id=evicted)
            if self._on_evict is not None:
                self._on_evict(evicted)

    def _cap(self, items: list[Any]) -> None:
        if self._buffer_size > 0 and len(items) > self._buffer_size:
            del items[: len(items) - self._buffer_size]

    def add_node(
        self,
        *,
        run_id: str,
        node_name: str,
        session_id: str,
        status: Literal["ok", "error"],
        duration_ms: float,
        tool_calls: Sequence[ToolCallRecord],
        usage: Usage | None,
    ) -> None:
        """Accumulate one node visit into the run's buffer (bounded)."""
        acc = self._touch(run_id, session_id)
        acc.visited_nodes.append(node_name)
        acc.spans.append(
            SpanRecord(name=node_name, node=node_name, duration_ms=duration_ms, status=status)
        )
        acc.tool_calls.extend(tool_calls)
        acc.latency_ms += duration_ms
        if usage is not None:
            acc.usage = acc.usage + usage
        self._cap(acc.spans)
        self._cap(acc.tool_calls)

    def add_score(self, *, run_id: str, annotation: ScoreAnnotation) -> bool:
        """Append a score to a known run; return False for an unknown run."""
        acc = self._runs.get(run_id)
        if acc is None:
            logger.debug("observability.score_unknown_run", run_id=run_id)
            return False
        acc.scores.append(annotation)
        self._runs.move_to_end(run_id)
        return True

    def summary(self, run_id: str) -> RunSummary | None:
        """Return the snapshot for *run_id*, or ``None`` if unknown/evicted."""
        acc = self._runs.get(run_id)
        return acc.to_summary(run_id) if acc is not None else None


def _export_records(summaries: Iterable[RunSummary], fmt: DatasetFormat) -> list[dict[str, Any]]:
    """Serialize *summaries* into per-format evaluation-dataset records (SPEC-OBS-PAR-003).

    The default/fake adapters carry structural data only (no prompt/response
    content), so ``inputs``/``outputs`` are ``None`` unless a richer adapter
    attaches them. Never raises.
    """
    records: list[dict[str, Any]] = []
    for summary in summaries:
        if fmt is DatasetFormat.LANGFUSE:
            records.append(
                {
                    "input": None,
                    "expectedOutput": None,
                    "metadata": {
                        "runId": summary.run_id,
                        "agentName": summary.agent_name,
                        "sessionId": summary.session_id,
                    },
                }
            )
        else:  # DatasetFormat.LANGSMITH (default)
            records.append(
                {
                    "inputs": {"question": None},
                    "outputs": {"answer": None},
                    "reference_outputs": {},
                    "metadata": {
                        "run_id": summary.run_id,
                        "agent_name": summary.agent_name,
                        "session_id": summary.session_id,
                    },
                }
            )
    return records


class DefaultObservabilityProvider:
    """Thin ``ObservabilityPort`` over the existing OTel/Langfuse singletons (DD-OBS-003).

    Adds a bounded, in-memory ring buffer per run (``observability_run_buffer_size``
    spans/tool-calls, ``observability_max_runs`` runs with LRU eviction) so
    ``get_run_summary`` has something to return even with no collector/project
    reachable. All hot-path methods are fail-open: a backend error is logged,
    never raised into the graph (SPEC conventions).
    """

    def __init__(self, settings: Settings | None = None) -> None:
        if settings is None:
            from prismal.core.config import get_settings

            settings = get_settings()
        self._settings = settings
        self._otel = OTelManager()
        self._completed: set[str] = set()
        self._registry = _RunRegistry(
            buffer_size=settings.observability_run_buffer_size,
            max_runs=settings.observability_max_runs,
            on_evict=self._on_evict,
        )

    def _on_evict(self, run_id: str) -> None:
        """Count an LRU eviction (observability-of-observability, best-effort)."""
        self._completed.discard(run_id)
        self._otel.increment_counter("observability_runs", 1, {"result": "evicted"})

    def record_node(
        self,
        *,
        run_id: str,
        node_name: str,
        session_id: str,
        status: Literal["ok", "error"],
        duration_ms: float,
        tool_calls: Sequence[ToolCallRecord] = (),
        usage: Usage | None = None,
    ) -> None:
        """Accumulate a node visit into the bounded buffer (fail-open)."""
        try:
            self._registry.add_node(
                run_id=run_id,
                node_name=node_name,
                session_id=session_id,
                status=status,
                duration_ms=duration_ms,
                tool_calls=tool_calls,
                usage=usage,
            )
        except Exception as exc:  # pragma: no cover - defensive fail-open
            logger.warning("observability.record_node_failed", error=str(exc))

    def record_score(
        self,
        *,
        run_id: str,
        name: str,
        value: float,
        comment: str = "",
        source: Literal["human", "llm_judge", "system"] = "system",
    ) -> None:
        """Store a score annotation locally and forward it to Langfuse (fail-open).

        Stores a :class:`ScoreAnnotation` in the run's local ``RunSummary`` **and**
        forwards to ``LangfuseManager.score_trace`` (SPEC-OBS-PAR-002), keyed by the
        canonical ``run_id`` as the Langfuse trace id. Both legs are best-effort: a
        backend error is logged, never raised (fail-open contract).
        """
        try:
            self._registry.add_score(
                run_id=run_id,
                annotation=ScoreAnnotation(name=name, value=value, comment=comment, source=source),
            )
        except Exception as exc:  # pragma: no cover - defensive fail-open
            logger.warning("observability.record_score_failed", error=str(exc))
        self._otel.increment_counter("observability_scores", 1, {"name": name})
        try:
            LangfuseManager().score_trace(run_id, name, value, comment or None)
        except Exception as exc:  # pragma: no cover - defensive fail-open
            logger.warning("observability.langfuse_score_failed", error=str(exc))

    def get_run_summary(self, run_id: str) -> RunSummary | None:
        """Return the run snapshot, or ``None`` if unknown/evicted (best-effort).

        Counts a run as ``completed`` the first time its summary is successfully
        retrieved (the data-flow's run-end signal) — deduped so repeat queries
        don't double-count.
        """
        summary = self._registry.summary(run_id)
        if summary is not None and run_id not in self._completed:
            self._completed.add(run_id)
            self._otel.increment_counter("observability_runs", 1, {"result": "completed"})
        return summary

    def export_dataset(self, run_ids: Sequence[str], *, fmt: DatasetFormat) -> list[dict[str, Any]]:
        """Export known runs as per-format dataset records (unknown ids skipped)."""
        summaries = [s for rid in run_ids if (s := self._registry.summary(rid)) is not None]
        self._otel.increment_counter("observability_dataset_exports", 1, {"fmt": str(fmt)})
        return _export_records(summaries, fmt)


class FakeObservabilityProvider:
    """Deterministic, I/O-free ``ObservabilityPort`` double for tests (SPEC-OBS-PRT-001).

    No OTel/Langfuse calls, no capacity caps — a pure in-memory round-trip of
    ``record_node``/``record_score``/``get_run_summary``/``export_dataset``.
    """

    def __init__(self) -> None:
        self._registry = _RunRegistry()

    def record_node(
        self,
        *,
        run_id: str,
        node_name: str,
        session_id: str,
        status: Literal["ok", "error"],
        duration_ms: float,
        tool_calls: Sequence[ToolCallRecord] = (),
        usage: Usage | None = None,
    ) -> None:
        """Accumulate a node visit (never raises)."""
        self._registry.add_node(
            run_id=run_id,
            node_name=node_name,
            session_id=session_id,
            status=status,
            duration_ms=duration_ms,
            tool_calls=tool_calls,
            usage=usage,
        )

    def record_score(
        self,
        *,
        run_id: str,
        name: str,
        value: float,
        comment: str = "",
        source: Literal["human", "llm_judge", "system"] = "system",
    ) -> None:
        """Attach a score to a known run (unknown run = no-op)."""
        self._registry.add_score(
            run_id=run_id,
            annotation=ScoreAnnotation(name=name, value=value, comment=comment, source=source),
        )

    def get_run_summary(self, run_id: str) -> RunSummary | None:
        """Return the run snapshot, or ``None`` if unknown."""
        return self._registry.summary(run_id)

    def export_dataset(self, run_ids: Sequence[str], *, fmt: DatasetFormat) -> list[dict[str, Any]]:
        """Export known runs as per-format dataset records (unknown ids skipped)."""
        summaries = [s for rid in run_ids if (s := self._registry.summary(rid)) is not None]
        return _export_records(summaries, fmt)


__all__ = [
    "DefaultObservabilityProvider",
    "FakeObservabilityProvider",
    "run_name_for",
    "trace_tags_for",
]

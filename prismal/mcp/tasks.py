"""MCP tasks extension — poll a task-augmented ``tools/call`` to completion (SPEC-PRT-MTK-001).

:class:`McpTaskPoller` drives ``tasks/get`` with exponential backoff
(``mcp_task_poll_interval_s`` → ``mcp_task_poll_max_interval_s``), never polling faster than the
server's ``pollIntervalMs``. A task that outlives ``mcp_task_timeout_s`` is cancelled
(``tasks/cancel``) and raises :class:`McpTaskTimeout`; a set ``cancel_event`` (run cancellation)
cancels best-effort and raises :class:`McpTaskError`. The wall-clock spent waiting is reported to
an optional budget callback (e.g. a ``BudgetGuard`` wall-clock meter) and to the
``prismal.mcp_task_wait_s`` histogram.

Fail closed (ADR-PRT-001 §2.2): task results are accepted only when the client declared the tasks
extension (``mcp_tasks_enabled``).
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import TYPE_CHECKING, Any, Protocol

from prismal.core.exceptions import McpTaskError, McpTaskTimeout
from prismal.core.logging import get_logger
from prismal.mcp import constants as c
from prismal.mcp.protocol import McpTaskHandle, ToolCallOutcome

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from prismal.core.config import Settings
    from prismal.mcp.protocol import McpProtocolAdapter

logger = get_logger("prismal.mcp.tasks")


class TaskMetricsSink(Protocol):
    """Counter + histogram slice of :class:`~prismal.monitoring.otel.OTelManager`."""

    def increment_counter(
        self, metric: str, value: int = 1, attributes: dict[str, Any] | None = None
    ) -> None: ...

    def record_histogram(
        self, metric: str, value: float, attributes: dict[str, Any] | None = None
    ) -> None: ...


class McpTaskPoller:
    """Wait for one MCP task through an adapter's ``get_task`` / ``cancel_task``."""

    def __init__(
        self,
        adapter: McpProtocolAdapter,
        *,
        settings: Settings | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
        metrics: TaskMetricsSink | None = None,
        wall_clock_reporter: Callable[[float], None] | None = None,
    ) -> None:
        if settings is None:
            from prismal.core.config import get_settings

            settings = get_settings()
        if metrics is None:
            from prismal.monitoring.otel import OTelManager

            metrics = OTelManager()
        self._adapter = adapter
        self._tasks_enabled = bool(settings.mcp_tasks_enabled)
        self._interval = float(settings.mcp_task_poll_interval_s)
        self._max_interval = float(settings.mcp_task_poll_max_interval_s)
        self._timeout = float(settings.mcp_task_timeout_s)
        self._clock = clock
        self._sleep = sleep
        self._metrics = metrics
        self._reporter = wall_clock_reporter

    async def wait(
        self, handle: McpTaskHandle, *, cancel_event: asyncio.Event | None = None
    ) -> ToolCallOutcome:
        """Poll until terminal; return the final result as a :class:`ToolCallOutcome`."""
        if not self._tasks_enabled:
            msg = f"task {handle.task_id!r}: tasks extension not declared (mcp_tasks_enabled=False)"
            raise McpTaskError(msg)
        start = self._clock()
        label = ["error"]  # final status for the metric, set by _poll
        try:
            return await self._poll(handle, start, cancel_event, label)
        finally:
            elapsed = max(self._clock() - start, 0.0)
            self._metrics.increment_counter("mcp_tasks", attributes={"status": label[0]})
            self._metrics.record_histogram("mcp_task_wait_s", elapsed)
            if self._reporter is not None:
                try:
                    self._reporter(elapsed)
                except Exception as exc:  # budget reporting must not mask the outcome
                    logger.warning("mcp.task.wall_clock_report_failed", error=str(exc))

    async def _poll(
        self,
        handle: McpTaskHandle,
        start: float,
        cancel_event: asyncio.Event | None,
        label: list[str],
    ) -> ToolCallOutcome:
        interval = self._interval
        while True:
            if cancel_event is not None and cancel_event.is_set():
                label[0] = "cancelled"
                await self._cancel(handle)
                raise McpTaskError(f"task {handle.task_id!r} cancelled by run")
            elapsed = self._clock() - start
            if elapsed >= self._timeout:
                label[0] = "timeout"
                await self._cancel(handle)
                msg = f"task {handle.task_id!r} exceeded mcp_task_timeout_s={self._timeout}"
                raise McpTaskTimeout(msg)

            status = await self._adapter.get_task(handle.task_id)
            if status.status == c.TASK_STATUS_COMPLETED:
                label[0] = c.TASK_STATUS_COMPLETED
                return ToolCallOutcome(result=status.result or {})
            if status.status in (c.TASK_STATUS_FAILED, c.TASK_STATUS_CANCELLED):
                label[0] = status.status
                detail = f": {status.status_message}" if status.status_message else ""
                msg = f"task {handle.task_id!r} {status.status}{detail}"
                raise McpTaskError(msg)

            hint = (status.poll_interval_ms or 0) / 1000
            remaining = self._timeout - (self._clock() - start)
            delay = min(max(interval, hint), self._max_interval, max(remaining, 0.0))
            await self._sleep(delay)
            interval = min(interval * 2, self._max_interval)

    async def _cancel(self, handle: McpTaskHandle) -> None:
        with contextlib.suppress(Exception):  # best-effort: the task may already be gone
            await self._adapter.cancel_task(handle.task_id)


__all__ = ["McpTaskHandle", "McpTaskPoller", "TaskMetricsSink"]

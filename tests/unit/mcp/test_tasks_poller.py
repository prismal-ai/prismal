"""Tests for the MCP tasks-extension poller (SPEC-PRT-MTK-001, PRT4-01..03)."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

import pytest

from prismal.core.config import Settings
from prismal.core.exceptions import McpTaskError, McpTaskTimeout
from prismal.mcp.protocol import McpTaskStatus, ServerDiscovery, ToolCallOutcome, ToolListPage
from prismal.mcp.tasks import McpTaskHandle, McpTaskPoller

pytestmark = pytest.mark.unit


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, delay: float) -> None:
        self.sleeps.append(delay)
        self.now += delay


class _Metrics:
    def __init__(self) -> None:
        self.counters: list[tuple[str, dict[str, Any]]] = []
        self.histograms: list[tuple[str, float]] = []

    def increment_counter(
        self, metric: str, value: int = 1, attributes: dict[str, Any] | None = None
    ) -> None:
        self.counters.append((metric, dict(attributes or {})))

    def record_histogram(
        self, metric: str, value: float, attributes: dict[str, Any] | None = None
    ) -> None:
        self.histograms.append((metric, value))


class _FakeAdapter:
    def __init__(self, statuses: list[McpTaskStatus], *, cancel_raises: bool = False) -> None:
        self._statuses = statuses
        self.gets = 0
        self.cancelled: list[str] = []
        self._cancel_raises = cancel_raises

    @property
    def discovery(self) -> ServerDiscovery:
        return ServerDiscovery(protocol_version="2026-07-28", stateless=True)

    async def list_tools(self) -> ToolListPage:
        return ToolListPage(tools=())

    async def call_tool(
        self, name: str, arguments: Mapping[str, Any], *, allow_task: bool = False
    ) -> ToolCallOutcome:
        return ToolCallOutcome(result={})

    async def get_task(self, task_id: str) -> McpTaskStatus:
        self.gets += 1
        idx = min(self.gets - 1, len(self._statuses) - 1)
        return self._statuses[idx]

    async def cancel_task(self, task_id: str) -> None:
        self.cancelled.append(task_id)
        if self._cancel_raises:
            raise RuntimeError("gone")

    async def aclose(self) -> None:
        return None


def _settings(**kw: Any) -> Settings:
    base: dict[str, Any] = {
        "mcp_tasks_enabled": True,
        "mcp_task_poll_interval_s": 1.0,
        "mcp_task_poll_max_interval_s": 4.0,
        "mcp_task_timeout_s": 30.0,
    }
    base.update(kw)
    return Settings(**base)


def _working(hint: int | None = None) -> McpTaskStatus:
    return McpTaskStatus(task_id="t1", status="working", poll_interval_ms=hint)


def _done() -> McpTaskStatus:
    return McpTaskStatus(task_id="t1", status="completed", result={"content": [{"text": "ok"}]})


def _handle(hint: int | None = None) -> McpTaskHandle:
    return McpTaskHandle(server="srv", task_id="t1", created_at=0.0, poll_hint_ms=hint)


def _poller(adapter: _FakeAdapter, clock: _Clock, metrics: _Metrics, **kw: Any) -> McpTaskPoller:
    reporter = kw.pop("reporter", None)
    return McpTaskPoller(
        adapter,
        settings=_settings(**kw),
        clock=clock,
        sleep=clock.sleep,
        metrics=metrics,
        wall_clock_reporter=reporter,
    )


class TestWait:
    async def test_completes_via_polling_with_backoff(self) -> None:
        clock, m = _Clock(), _Metrics()
        adapter = _FakeAdapter([_working(), _working(), _working(), _working(), _done()])
        out = await _poller(adapter, clock, m).wait(_handle())
        assert out.result == {"content": [{"text": "ok"}]}
        assert out.task is None
        assert clock.sleeps == [1.0, 2.0, 4.0, 4.0]
        assert ("mcp_tasks", {"status": "completed"}) in m.counters
        assert m.histograms and m.histograms[0][0] == "mcp_task_wait_s"

    async def test_honours_poll_hint(self) -> None:
        clock, m = _Clock(), _Metrics()
        adapter = _FakeAdapter([_working(hint=3000), _done()])
        await _poller(adapter, clock, m).wait(_handle(hint=2500))
        assert clock.sleeps == [3.0]

    async def test_timeout_cancels_and_raises(self) -> None:
        clock, m = _Clock(), _Metrics()
        adapter = _FakeAdapter([_working()])
        with pytest.raises(McpTaskTimeout):
            await _poller(adapter, clock, m, mcp_task_timeout_s=5.0).wait(_handle())
        assert adapter.cancelled == ["t1"]
        assert ("mcp_tasks", {"status": "timeout"}) in m.counters
        assert sum(clock.sleeps) <= 5.0 + 1e-9

    async def test_cancel_event_cancels_best_effort(self) -> None:
        clock, m = _Clock(), _Metrics()
        adapter = _FakeAdapter([_working()], cancel_raises=True)
        event = asyncio.Event()
        event.set()
        with pytest.raises(McpTaskError) as info:
            await _poller(adapter, clock, m).wait(_handle(), cancel_event=event)
        assert not isinstance(info.value, McpTaskTimeout)
        assert adapter.cancelled == ["t1"]
        assert ("mcp_tasks", {"status": "cancelled"}) in m.counters

    async def test_failed_task_raises(self) -> None:
        clock, m = _Clock(), _Metrics()
        adapter = _FakeAdapter(
            [McpTaskStatus(task_id="t1", status="failed", status_message="boom")]
        )
        with pytest.raises(McpTaskError, match="failed"):
            await _poller(adapter, clock, m).wait(_handle())
        assert ("mcp_tasks", {"status": "failed"}) in m.counters

    async def test_server_cancelled_task_raises(self) -> None:
        clock, m = _Clock(), _Metrics()
        adapter = _FakeAdapter([McpTaskStatus(task_id="t1", status="cancelled")])
        with pytest.raises(McpTaskError):
            await _poller(adapter, clock, m).wait(_handle())

    async def test_wall_clock_reported(self) -> None:
        clock, m = _Clock(), _Metrics()
        seen: list[float] = []
        adapter = _FakeAdapter([_working(), _done()])
        await _poller(adapter, clock, m, reporter=seen.append).wait(_handle())
        assert seen == [1.0]

    async def test_reporter_failure_is_swallowed(self) -> None:
        clock, m = _Clock(), _Metrics()

        def _boom(_: float) -> None:
            raise RuntimeError("x")

        adapter = _FakeAdapter([_done()])
        out = await _poller(adapter, clock, m, reporter=_boom).wait(_handle())
        assert out.result is not None

    async def test_fail_closed_when_tasks_extension_not_declared(self) -> None:
        clock, m = _Clock(), _Metrics()
        adapter = _FakeAdapter([_done()])
        with pytest.raises(McpTaskError, match="not declared"):
            await _poller(adapter, clock, m, mcp_tasks_enabled=False).wait(_handle())
        assert adapter.gets == 0


class TestDefaults:
    def test_reexports_handle_and_default_wiring(self) -> None:
        from prismal.mcp import protocol

        assert McpTaskHandle is protocol.McpTaskHandle
        poller = McpTaskPoller(_FakeAdapter([_done()]), settings=_settings())
        assert poller is not None

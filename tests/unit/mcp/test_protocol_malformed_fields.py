"""Malformed server-supplied fields never escape as non-``McpProtocolError`` (review finding 11).

A stateless MCP server is untrusted: a non-integer JSON-RPC ``error.code``, a non-list
``supportedVersions`` or an absurd ``ttlMs`` must map to :class:`McpProtocolError` (or a
safe default) so the ``auto`` probe can still fall back to the legacy transport.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from prismal.core.config import Settings
from prismal.core.exceptions import McpProtocolError
from prismal.mcp import constants as c
from prismal.mcp.connection import MCPServerConfig
from prismal.mcp.protocol import (
    MAX_HINT_MS,
    LegacySessionAdapter,
    McpJsonRpcError,
    StatelessHttpAdapter,
    clear_protocol_memo,
    select_adapter,
)

pytestmark = pytest.mark.unit

_HUGE = "1" + "0" * 400  # a valid JSON integer whose /1000 overflows a float


class _Metrics:
    def increment_counter(
        self, metric: str, value: int = 1, attributes: dict[str, Any] | None = None
    ) -> None:
        del metric, value, attributes


class _FakeConnection:
    def __init__(self, config: MCPServerConfig) -> None:
        self._config = config

    async def connect(self) -> None:
        return None


@pytest.fixture(autouse=True)
def _clean_memo() -> Any:
    clear_protocol_memo()
    yield
    clear_protocol_memo()


def _cfg() -> MCPServerConfig:
    return MCPServerConfig.model_validate(
        {"name": "remote", "type": "streamable-http", "url": "http://mcp.test/mcp"}
    )


def _raw(body_for: Callable[[str, int], str]) -> httpx.MockTransport:
    """A transport answering each request with the raw JSON text ``body_for(method, id)``."""

    def handler(request: httpx.Request) -> httpx.Response:
        req = json.loads(request.content)
        return httpx.Response(
            200,
            text=body_for(req["method"], req["id"]),
            headers={"content-type": "application/json"},
        )

    return httpx.MockTransport(handler)


def _result(req_id: int, result: str) -> str:
    return f'{{"jsonrpc": "2.0", "id": {req_id}, "result": {result}}}'


def _adapter(transport: httpx.MockTransport, **kw: Any) -> StatelessHttpAdapter:
    return StatelessHttpAdapter(
        _cfg(), settings=Settings(**kw), transport=transport, metrics=_Metrics()
    )


_BAD_CODES = ['"x"', "null", "{}", "[]", "Infinity", "NaN", "1.5e400"]


class TestErrorCode:
    @pytest.mark.parametrize("code", _BAD_CODES)
    async def test_non_integer_code_maps_to_jsonrpc_error(self, code: str) -> None:
        transport = _raw(
            lambda m, i: (
                f'{{"jsonrpc": "2.0", "id": {i}, "error": {{"code": {code}, "message": "m"}}}}'
            )
        )
        with pytest.raises(McpJsonRpcError) as info:
            await _adapter(transport).discover()
        assert info.value.code == 0

    @pytest.mark.parametrize("code", _BAD_CODES)
    async def test_auto_probe_falls_back_to_legacy(self, code: str) -> None:
        transport = _raw(
            lambda m, i: (
                f'{{"jsonrpc": "2.0", "id": {i}, "error": {{"code": {code}, "message": "m"}}}}'
            )
        )
        adapter = await select_adapter(
            _cfg(),
            settings=Settings(mcp_protocol_mode="auto"),
            transport=transport,
            metrics=_Metrics(),
            connection_factory=_FakeConnection,
        )
        assert isinstance(adapter, LegacySessionAdapter)

    async def test_integer_string_code_still_understood(self) -> None:
        transport = _raw(
            lambda m, i: (
                f'{{"jsonrpc": "2.0", "id": {i}, "error": {{"code": "-32601", "message": "m"}}}}'
            )
        )
        with pytest.raises(McpJsonRpcError) as info:
            await _adapter(transport).discover()
        assert info.value.code == c.ERROR_METHOD_NOT_FOUND


class TestDiscoverShapes:
    @pytest.mark.parametrize(
        "result",
        [
            '{"supportedVersions": 5}',
            '{"supportedVersions": [], "serverInfo": "x"}',
            '{"supportedVersions": [], "capabilities": {"extensions": 3}, "serverInfo": []}',
        ],
    )
    async def test_malformed_discovery_is_not_stateless(self, result: str) -> None:
        discovery = await _adapter(_raw(lambda m, i: _result(i, result))).discover()
        assert discovery.stateless is False

    async def test_auto_probe_with_malformed_discovery_is_legacy(self) -> None:
        adapter = await select_adapter(
            _cfg(),
            settings=Settings(mcp_protocol_mode="auto"),
            transport=_raw(lambda m, i: _result(i, '{"supportedVersions": 5}')),
            metrics=_Metrics(),
            connection_factory=_FakeConnection,
        )
        assert isinstance(adapter, LegacySessionAdapter)


class TestToolsListShapes:
    @pytest.mark.parametrize(
        "result",
        ['{"tools": 5}', '{"tools": [], "nextCursor": {"a": 1}}', '{"tools": [], "nextCursor": 7}'],
    )
    async def test_malformed_page_is_protocol_error(self, result: str) -> None:
        with pytest.raises(McpProtocolError):
            await _adapter(_raw(lambda m, i: _result(i, result))).list_tools()

    @pytest.mark.parametrize(
        ("ttl", "expected"),
        [(_HUGE, MAX_HINT_MS), ("true", None), ("-5", 0), ("1.5", None), ("2500", 2500)],
    )
    async def test_ttl_hint_sanitized(self, ttl: str, expected: int | None) -> None:
        result = f'{{"tools": [], "ttlMs": {ttl}}}'
        page = await _adapter(_raw(lambda m, i: _result(i, result))).list_tools()
        assert page.ttl_ms == expected
        if page.ttl_ms is not None:
            assert page.ttl_ms / 1000 >= 0  # never overflows downstream


class TestTaskHints:
    async def test_huge_poll_hint_on_task_result_is_clamped(self) -> None:
        result = (
            f'{{"resultType": "{c.RESULT_TYPE_TASK}", "taskId": "t1", "pollIntervalMs": {_HUGE}}}'
        )
        adapter = _adapter(_raw(lambda m, i: _result(i, result)), mcp_tasks_enabled=True)
        outcome = await adapter.call_tool("slow", {}, allow_task=True)
        assert outcome.task is not None
        assert outcome.task.poll_hint_ms == MAX_HINT_MS

    async def test_huge_hints_on_get_task_are_clamped(self) -> None:
        result = f'{{"status": "working", "pollIntervalMs": {_HUGE}, "ttlMs": true}}'
        adapter = _adapter(_raw(lambda m, i: _result(i, result)), mcp_tasks_enabled=True)
        status = await adapter.get_task("t1")
        assert status.poll_interval_ms == MAX_HINT_MS
        assert status.ttl_ms is None

"""In-process fake MCP servers for ``httpx.MockTransport`` (PRT3/PRT4/PRT6-04 tests)."""

from __future__ import annotations

import json
from typing import Any

import httpx

STATELESS = "2026-07-28"
TASKS_EXT = "io.modelcontextprotocol/tasks"


def _rpc_result(req_id: Any, result: dict[str, Any]) -> httpx.Response:
    return httpx.Response(200, json={"jsonrpc": "2.0", "id": req_id, "result": result})


def _rpc_error(req_id: Any, code: int, message: str, status: int = 200) -> httpx.Response:
    return httpx.Response(
        status, json={"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}
    )


class FakeStatelessServer:
    """A 2026-07-28 server: ``server/discover``, paginated tools, MRTR, tasks extension."""

    def __init__(
        self,
        *,
        tools: list[dict[str, Any]] | None = None,
        page_size: int = 2,
        ttl_ms: int | None = 60_000,
        cache_scope: str | None = "public",
        task_working_polls: int = 2,
        supported_versions: tuple[str, ...] = (STATELESS, "2025-11-25"),
        sse: bool = False,
    ) -> None:
        self.tools = tools or [
            {"name": f"tool_{i}", "description": f"d{i}", "inputSchema": {"type": "object"}}
            for i in range(5)
        ]
        self.page_size = page_size
        self.ttl_ms = ttl_ms
        self.cache_scope = cache_scope
        self.task_working_polls = task_working_polls
        self.supported_versions = supported_versions
        self.sse = sse
        self.requests: list[dict[str, Any]] = []
        self.headers: list[httpx.Headers] = []
        self.task_gets = 0
        self.cancelled: list[str] = []
        self.updates: list[dict[str, Any]] = []

    @property
    def methods(self) -> list[str]:
        return [r["method"] for r in self.requests]

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def _respond(self, req_id: Any, result: dict[str, Any]) -> httpx.Response:
        if not self.sse:
            return _rpc_result(req_id, result)
        body = json.dumps({"jsonrpc": "2.0", "id": req_id, "result": result})
        text = f"event: message\ndata: {json.dumps({'jsonrpc': '2.0', 'method': 'x'})}\n\n"
        text += f"event: message\ndata: {body}\n\n"
        return httpx.Response(200, text=text, headers={"content-type": "text/event-stream"})

    def handle(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.requests.append(body)
        self.headers.append(request.headers)
        method = body["method"]
        params = body.get("params") or {}
        req_id = body.get("id")
        if method == "initialize":
            return _rpc_error(req_id, -32600, "stateless server: no initialize")
        if method == "server/discover":
            return self._respond(
                req_id,
                {
                    "supportedVersions": list(self.supported_versions),
                    "capabilities": {"tools": {}, "extensions": {TASKS_EXT: {}}},
                    "serverInfo": {"name": "fake-stateless", "version": "9.9"},
                    "instructions": "be nice",
                },
            )
        if method == "tools/list":
            start = int(params.get("cursor") or 0)
            page = self.tools[start : start + self.page_size]
            result: dict[str, Any] = {"tools": page}
            if self.ttl_ms is not None:
                result["ttlMs"] = self.ttl_ms
            if self.cache_scope is not None:
                result["cacheScope"] = self.cache_scope
            if start + self.page_size < len(self.tools):
                result["nextCursor"] = str(start + self.page_size)
            return self._respond(req_id, result)
        if method == "tools/call":
            return self._call(req_id, params)
        if method == "tasks/get":
            self.task_gets += 1
            if self.task_gets <= self.task_working_polls:
                return self._respond(
                    req_id, {"taskId": params["taskId"], "status": "working", "pollIntervalMs": 10}
                )
            return self._respond(
                req_id,
                {
                    "taskId": params["taskId"],
                    "status": "completed",
                    "result": {"content": [{"type": "text", "text": "long done"}]},
                },
            )
        if method == "tasks/cancel":
            self.cancelled.append(params["taskId"])
            return self._respond(req_id, {})
        if method == "tasks/update":
            self.updates.append(params)
            return self._respond(req_id, {})
        return _rpc_error(req_id, -32601, f"unknown method {method}")

    def _call(self, req_id: Any, params: dict[str, Any]) -> httpx.Response:
        name = params.get("name")
        if name == "ask":
            if "inputResponses" not in params:
                return self._respond(
                    req_id,
                    {
                        "resultType": "input_required",
                        "inputRequests": {
                            "q": {
                                "method": "elicitation/create",
                                "params": {
                                    "mode": "form",
                                    "message": "Enter your API key",
                                    "requestedSchema": {"type": "object"},
                                },
                            }
                        },
                        "requestState": "opaque-1",
                    },
                )
            action = params["inputResponses"]["q"]["action"]
            return self._respond(
                req_id,
                {
                    "resultType": "complete",
                    "content": [{"type": "text", "text": f"{action}:{params.get('requestState')}"}],
                },
            )
        if name == "always_ask":
            return self._respond(
                req_id,
                {
                    "resultType": "input_required",
                    "inputRequests": {"r": {"method": "roots/list"}},
                },
            )
        if name == "long":
            return self._respond(
                req_id,
                {
                    "resultType": "task",
                    "taskId": "task-1",
                    "status": "working",
                    "ttlMs": 60_000,
                    "pollIntervalMs": 5,
                },
            )
        if name == "fail":
            return self._respond(
                req_id, {"content": [{"type": "text", "text": "bad"}], "isError": True}
            )
        args = params.get("arguments") or {}
        return self._respond(
            req_id, {"content": [{"type": "text", "text": json.dumps(args, sort_keys=True)}]}
        )


class FakeLegacyHttpServer:
    """A pre-2026 HTTP server: ``server/discover`` is unknown (-32601) or a 404/405."""

    def __init__(self, *, mode: str = "rpc", status: int = 404) -> None:
        self.mode = mode
        self.status = status
        self.methods: list[str] = []

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.methods.append(body["method"])
        if self.mode == "http":
            return httpx.Response(self.status, text="nope")
        return _rpc_error(body.get("id"), -32601, "Method not found")

"""Unscoped (``headers=None``) legacy path must not reach a tenant's task.

Regression for the M5 review finding: ``_in_scope(..., None)`` returned
``True``, so a pre-PRT unscoped ``tasks/get`` / ``tasks/cancel`` could read or
cancel a task that a scoped (tenant) request recorded in the shared store.
"""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.messages import AIMessage

from prismal.a2a.server import A2AServerHandler, AuthContext
from prismal.core.config import Settings

pytestmark = pytest.mark.unit

_NOT_FOUND = -32004


class _Graph:
    async def ainvoke(self, state: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        return {"messages": [AIMessage(content="TENANT-A SECRET ANSWER")]}


class _Audit:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def log_event(self, event: str, data: dict[str, Any]) -> None:
        self.events.append((event, data))


def _handler() -> A2AServerHandler:
    return A2AServerHandler(_Graph(), settings=Settings(a2a_strict=True), audit=_Audit())


def _send(task_id: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "message/send",
        "params": {
            "taskId": task_id,
            "message": {
                "role": "user",
                "parts": [{"kind": "text", "text": "hi"}],
                "messageId": "m",
            },
        },
    }


def _rpc(method: str, task_id: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": 2, "method": method, "params": {"id": task_id}}


ALICE = AuthContext(authenticated=True, subject="alice")
MALLORY = AuthContext(authenticated=True, subject="mallory")


async def _scoped_task(h: A2AServerHandler, task_id: str = "t-123") -> None:
    resp = await h.handle_rpc(_send(task_id), auth_ctx=ALICE, headers={}, org_id="orgA")
    assert "error" not in resp


class TestUnscopedCannotReachScopedTasks:
    async def test_unscoped_get_of_scoped_task_is_not_found(self) -> None:
        h = _handler()
        await _scoped_task(h)
        resp = await h.handle_rpc(_rpc("tasks/get", "t-123"), auth_ctx=MALLORY)
        assert resp["error"]["code"] == _NOT_FOUND
        assert "result" not in resp

    async def test_unscoped_cancel_of_scoped_task_is_not_found_and_unchanged(self) -> None:
        h = _handler()
        await _scoped_task(h)
        before = h._tasks["t-123"].model_copy()
        record_before = h._task_store.peek("t-123")
        resp = await h.handle_rpc(_rpc("tasks/cancel", "t-123"), auth_ctx=MALLORY)
        assert resp["error"]["code"] == _NOT_FOUND
        assert h._tasks["t-123"] == before
        assert h._tasks["t-123"].status == "completed"
        assert h._task_store.peek("t-123") == record_before
        owner = await h.handle_rpc(
            _rpc("tasks/get", "t-123"), auth_ctx=ALICE, headers={}, org_id="orgA"
        )
        assert owner["result"]["status"] == "completed"


class TestUnscopedPathUnchanged:
    async def test_unscoped_created_task_get_and_cancel(self) -> None:
        h = _handler()
        sent = await h.handle_rpc(_send("t-legacy"), auth_ctx=ALICE)
        assert sent["result"]["id"] == "t-legacy"
        got = await h.handle_rpc(_rpc("tasks/get", "t-legacy"), auth_ctx=MALLORY)
        assert got["result"]["status"] == "completed"
        canceled = await h.handle_rpc(_rpc("tasks/cancel", "t-legacy"), auth_ctx=MALLORY)
        assert canceled["result"]["status"] == "canceled"

    async def test_unscoped_unknown_task_not_found(self) -> None:
        h = _handler()
        resp = await h.handle_rpc(_rpc("tasks/get", "nope"), auth_ctx=ALICE)
        assert resp["error"]["code"] == _NOT_FOUND

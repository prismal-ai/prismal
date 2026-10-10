"""Dual-stack inbound handler (SPEC-PRT-A2A-004/005, ADR-PRT-001 §2.1)."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from langchain_core.messages import AIMessage

from prismal.a2a import server as server_mod
from prismal.a2a.server import A2AServerHandler, AuthContext
from prismal.a2a.task_store import InMemoryA2ATaskStore, TaskRecord
from prismal.a2a.types import A2ATask
from prismal.a2a.v1.types import (
    ListTasksResult,
    SendMessageResponse,
    StreamResponse,
    TaskState,
    TaskV1,
)
from prismal.core.config import Settings
from prismal.monitoring.host_stats import current_org

pytestmark = pytest.mark.unit

V1 = {"A2A-Version": "1.0"}


class _FakeGraph:
    def __init__(self) -> None:
        self.calls = 0

    async def ainvoke(self, state: dict[str, Any], config: Any = None) -> dict[str, Any]:
        self.calls += 1
        text = state["messages"][-1].content
        return {"messages": [*state["messages"], AIMessage(content=f"echo: {text}")]}


class _SpyAudit:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def log_event(self, event_type: str, payload: dict[str, Any]) -> None:
        self.events.append((event_type, payload))


class _Counter:
    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    def __call__(self, **labels: str) -> None:
        self.calls.append(labels)


@pytest.fixture(autouse=True)
def counter(monkeypatch: pytest.MonkeyPatch) -> _Counter:
    spy = _Counter()
    monkeypatch.setattr(server_mod, "count_a2a_request", spy)
    return spy


class _Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 6, tzinfo=UTC)

    def __call__(self) -> datetime:
        self.now += timedelta(seconds=1)
        return self.now


def _handler(
    *,
    audit: _SpyAudit | None = None,
    graph: Any = None,
    store: Any = None,
    **settings_kw: Any,
) -> A2AServerHandler:
    settings_kw.setdefault("a2a_strict", False)
    settings = Settings(a2a_enabled=True, a2a_inbound_enabled=True, **settings_kw)
    return A2AServerHandler(
        graph or _FakeGraph(),
        settings=settings,
        audit=audit or _SpyAudit(),
        task_store=store,
        clock=_Clock(),
    )


def _v1_send(text: str = "hi", *, tenant: str | None = None, rid: Any = "r1") -> dict[str, Any]:
    params: dict[str, Any] = {
        "message": {
            "messageId": "m1",
            "role": "ROLE_USER",
            "parts": [{"text": text}],
            "contextId": "ctx-1",
        }
    }
    if tenant is not None:
        params["tenant"] = tenant
    return {"jsonrpc": "2.0", "id": rid, "method": "SendMessage", "params": params}


def _legacy_send(text: str = "hi") -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": "l1",
        "method": "message/send",
        "params": {
            "message": {"role": "user", "parts": [{"kind": "text", "text": text}], "messageId": "m"}
        },
    }


def _rpc(method: str, params: dict[str, Any], rid: Any = "x") -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": rid, "method": method, "params": params}


class TestLegacyUnchanged:
    async def test_none_headers_keeps_v03_dispatch(self, counter: _Counter) -> None:
        h = _handler()
        resp = await h.handle_rpc(_v1_send())
        assert resp["error"]["code"] == -32601  # pre-PRT: unknown method
        assert counter.calls == []

    async def test_negotiated_legacy_is_served(self, counter: _Counter) -> None:
        h = _handler()
        resp = await h.handle_rpc(_legacy_send("x"), headers={}, org_id="acme")
        assert resp["result"]["status"] == "completed"
        assert counter.calls[-1] == {
            "protocol_version": "0.3",
            "direction": "inbound",
            "method": "message/send",
            "outcome": "ok",
        }
        task_id = resp["result"]["id"]
        get = await h.handle_rpc(_rpc("tasks/get", {"id": task_id}), headers={}, org_id="acme")
        assert get["result"]["id"] == task_id
        other = await h.handle_rpc(_rpc("tasks/get", {"id": task_id}), headers={}, org_id="beta")
        assert other["error"]["code"] == -32004
        cancel_other = await h.handle_rpc(
            _rpc("tasks/cancel", {"id": task_id}), headers={}, org_id="beta"
        )
        assert cancel_other["error"]["code"] == -32004
        canceled = await h.handle_rpc(
            _rpc("tasks/cancel", {"id": task_id}), headers={}, org_id="acme"
        )
        assert canceled["result"]["status"] == "canceled"

    async def test_legacy_refused_with_32009(self, counter: _Counter) -> None:
        h = _handler(a2a_protocol_version="1.0", a2a_accept_legacy=False)
        resp = await h.handle_rpc(_legacy_send(), headers={})
        assert resp["error"]["code"] == -32009
        assert counter.calls[-1]["outcome"] == "rejected"

    async def test_unsupported_header_refused(self, counter: _Counter) -> None:
        resp = await _handler().handle_rpc(_v1_send(), headers={"A2A-Version": "9.0"})
        assert resp["error"]["code"] == -32009

    async def test_legacy_error_outcome_counted(self, counter: _Counter) -> None:
        resp = await _handler().handle_rpc(_rpc("tasks/get", {"id": "nope"}), headers={})
        assert resp["error"]["code"] == -32004
        assert counter.calls[-1]["outcome"] == "error"

    async def test_strict_unauthorized_on_new_path(self, counter: _Counter) -> None:
        h = _handler(a2a_strict=True)
        resp = await h.handle_rpc(_v1_send(), headers=V1, auth_ctx=None)
        assert resp["error"]["code"] == -32001
        assert counter.calls[-1]["outcome"] == "unauthorized"


class TestV1Send:
    async def test_v1_without_header_is_routed_v1(self, counter: _Counter) -> None:
        resp = await _handler().handle_rpc(_v1_send("bill me"), headers={})
        result = SendMessageResponse.model_validate(resp["result"])
        assert result.task is not None
        assert result.task.status.state is TaskState.COMPLETED
        assert result.task.context_id == "ctx-1"
        assert result.task.artifacts is not None
        assert result.task.artifacts[0].parts[0].text == "echo: bill me"
        assert "kind" not in json.dumps(resp)
        assert counter.calls[-1] == {
            "protocol_version": "1.0",
            "direction": "inbound",
            "method": "SendMessage",
            "outcome": "ok",
        }

    async def test_v1_task_reuses_run_task_audit(self) -> None:
        audit = _SpyAudit()
        await _handler(audit=audit).handle_rpc(_v1_send(), headers=V1, org_id="acme")
        assert any(e == "a2a.inbound" and p.get("status") == "completed" for e, p in audit.events)

    async def test_matching_tenant_is_accepted(self) -> None:
        resp = await _handler().handle_rpc(_v1_send(tenant="acme"), headers=V1, org_id="acme")
        assert "result" in resp

    async def test_history_length_from_configuration(self) -> None:
        req = _v1_send()
        req["params"]["configuration"] = {"historyLength": 0}
        resp = await _handler().handle_rpc(req, headers=V1)
        assert "history" not in resp["result"]["task"]

    async def test_invalid_params_32602(self) -> None:
        resp = await _handler().handle_rpc(_rpc("SendMessage", {"message": {}}), headers=V1)
        assert resp["error"]["code"] == -32602


class TestTenantMismatch:
    @pytest.mark.parametrize("org_id", ["beta", None])
    async def test_mismatch_rejected_and_audited(
        self, org_id: str | None, counter: _Counter
    ) -> None:
        audit = _SpyAudit()
        graph = _FakeGraph()
        h = _handler(audit=audit, graph=graph)
        resp = await h.handle_rpc(_v1_send(tenant="acme"), headers=V1, org_id=org_id)
        assert resp["error"]["code"] == -32001
        assert graph.calls == 0
        events = [p for e, p in audit.events if e == "a2a.inbound.tenant_mismatch"]
        assert len(events) == 1
        dumped = json.dumps(events[0])
        assert "acme" not in dumped  # hash-first: no raw tenant
        assert len(events[0]["tenant_hash"]) == 16
        assert counter.calls[-1]["outcome"] == "tenant_mismatch"

    async def test_mismatch_on_stream(self) -> None:
        audit = _SpyAudit()
        h = _handler(audit=audit)
        req = _v1_send(tenant="acme")
        req["method"] = "SendStreamingMessage"
        lines = [line async for line in h.stream_rpc(req, headers=V1, org_id="beta")]
        assert json.loads(lines[0][len("data: ") :])["error"]["code"] == -32001
        assert any(e == "a2a.inbound.tenant_mismatch" for e, _ in audit.events)


class TestV1Tasks:
    async def _send(self, h: A2AServerHandler, org: str | None = "acme", **kw: Any) -> str:
        resp = await h.handle_rpc(_v1_send(), headers=V1, org_id=org, **kw)
        return str(resp["result"]["task"]["id"])

    async def test_get_task_scoped_by_tenant(self) -> None:
        h = _handler()
        tid = await self._send(h)
        ok = await h.handle_rpc(_rpc("GetTask", {"id": tid}), headers=V1, org_id="acme")
        task = TaskV1.model_validate(ok["result"])
        assert task.id == tid
        other = await h.handle_rpc(_rpc("GetTask", {"id": tid}), headers=V1, org_id="beta")
        assert other["error"]["code"] == -32001

    async def test_get_task_history_length(self) -> None:
        h = _handler()
        tid = await self._send(h)
        resp = await h.handle_rpc(
            _rpc("GetTask", {"id": tid, "historyLength": 0}), headers=V1, org_id="acme"
        )
        assert "history" not in resp["result"]

    async def test_cancel_completed_task_not_cancelable(self) -> None:
        h = _handler()
        tid = await self._send(h)
        resp = await h.handle_rpc(_rpc("CancelTask", {"id": tid}), headers=V1, org_id="acme")
        assert resp["error"]["code"] == -32002
        missing = await h.handle_rpc(_rpc("CancelTask", {"id": "zz"}), headers=V1, org_id="acme")
        assert missing["error"]["code"] == -32001

    async def test_cancel_working_task(self) -> None:
        registry: dict[str, A2ATask] = {}
        store = InMemoryA2ATaskStore(registry)
        h = _handler(store=store)
        registry["w"] = A2ATask(id="w", status="working")
        now = datetime(2026, 1, 1, tzinfo=UTC)
        store.put(
            TaskRecord(
                task_id="w",
                context_id="c",
                org_id="acme",
                principal=None,
                state=TaskState.WORKING,
                created_at=now,
                updated_at=now,
            )
        )
        resp = await h.handle_rpc(_rpc("CancelTask", {"id": "w"}), headers=V1, org_id="acme")
        assert resp["result"]["status"]["state"] == "TASK_STATE_CANCELED"
        rec = store.get("w", org_id="acme")
        assert rec is not None and rec.state is TaskState.CANCELED

    async def test_list_tasks_paginates_and_scopes(self) -> None:
        h = _handler()
        for _ in range(3):
            await self._send(h, "acme")
        await self._send(h, "beta")
        resp = await h.handle_rpc(_rpc("ListTasks", {"pageSize": 2}), headers=V1, org_id="acme")
        page = ListTasksResult.model_validate(resp["result"])
        assert len(page.tasks) == 2
        assert page.total_size == 3
        assert page.page_size == 2
        assert page.next_page_token
        assert all(t.artifacts is None for t in page.tasks)  # includeArtifacts default false
        resp2 = await h.handle_rpc(
            _rpc(
                "ListTasks",
                {"pageSize": 2, "pageToken": page.next_page_token, "includeArtifacts": True},
            ),
            headers=V1,
            org_id="acme",
        )
        page2 = ListTasksResult.model_validate(resp2["result"])
        assert len(page2.tasks) == 1
        assert page2.next_page_token == ""
        assert page2.tasks[0].artifacts

    async def test_list_page_size_clamped_by_setting(self) -> None:
        h = _handler(a2a_tasks_list_max_page_size=2)
        for _ in range(3):
            await self._send(h)
        resp = await h.handle_rpc(_rpc("ListTasks", {"pageSize": 50}), headers=V1, org_id="acme")
        assert resp["result"]["pageSize"] == 2
        assert len(resp["result"]["tasks"]) == 2

    async def test_list_principal_scoped_under_strict(self) -> None:
        h = _handler(a2a_strict=True)
        alice = AuthContext(authenticated=True, subject="alice")
        bob = AuthContext(authenticated=True, subject="bob")
        await self._send(h, auth_ctx=alice)
        await self._send(h, auth_ctx=bob)
        resp = await h.handle_rpc(_rpc("ListTasks", {}), headers=V1, org_id="acme", auth_ctx=alice)
        assert resp["result"]["totalSize"] == 1
        tid = resp["result"]["tasks"][0]["id"]
        # bob cannot read alice's task under strict
        other = await h.handle_rpc(
            _rpc("GetTask", {"id": tid}), headers=V1, org_id="acme", auth_ctx=bob
        )
        assert other["error"]["code"] == -32001

    async def test_list_bad_token_32602(self) -> None:
        resp = await _handler().handle_rpc(
            _rpc("ListTasks", {"pageToken": "@@"}), headers=V1, org_id="acme"
        )
        assert resp["error"]["code"] == -32602

    async def test_list_body_missing_falls_back_to_record(self) -> None:
        store = InMemoryA2ATaskStore({})
        now = datetime(2026, 1, 1, tzinfo=UTC)
        store.put(
            TaskRecord(
                task_id="ghost",
                context_id="c",
                org_id="acme",
                principal=None,
                state=TaskState.WORKING,
                created_at=now,
                updated_at=now,
            )
        )
        h = _handler(store=store)
        resp = await h.handle_rpc(_rpc("ListTasks", {}), headers=V1, org_id="acme")
        assert resp["result"]["tasks"][0]["status"]["state"] == "TASK_STATE_WORKING"

    async def test_unsupported_and_unknown_methods(self) -> None:
        h = _handler()
        sub = await h.handle_rpc(_rpc("SubscribeToTask", {"id": "x"}), headers=V1)
        assert sub["error"]["code"] == -32004
        unknown = await h.handle_rpc(_rpc("Nope", {}), headers=V1)
        assert unknown["error"]["code"] == -32601

    async def test_non_dict_params_rejected(self) -> None:
        resp = await _handler().handle_rpc(
            {"jsonrpc": "2.0", "id": 1, "method": "GetTask", "params": [1]}, headers=V1
        )
        assert resp["error"]["code"] == -32602


class TestV1Stream:
    async def test_streaming_events(self, counter: _Counter) -> None:
        h = _handler()
        req = _v1_send("s")
        req["method"] = "SendStreamingMessage"
        lines = [line async for line in h.stream_rpc(req, headers=V1, org_id="acme")]
        events = [
            StreamResponse.model_validate(json.loads(line[len("data: ") :])["result"])
            for line in lines
        ]
        assert events[0].status_update is not None
        assert events[0].status_update.status.state is TaskState.WORKING
        assert events[1].artifact_update is not None
        assert events[1].artifact_update.artifact.parts[0].text == "echo: s"
        assert events[-1].status_update is not None
        assert events[-1].status_update.status.state is TaskState.COMPLETED
        assert counter.calls[-1]["method"] == "SendStreamingMessage"
        tid = events[-1].status_update.task_id
        get = await h.handle_rpc(_rpc("GetTask", {"id": tid}), headers=V1, org_id="acme")
        assert get["result"]["id"] == tid

    async def test_stream_wrong_method(self) -> None:
        lines = [
            line async for line in _handler().stream_rpc(_rpc("GetTask", {"id": "x"}), headers=V1)
        ]
        assert json.loads(lines[0][6:])["error"]["code"] == -32004

    async def test_stream_invalid_params(self) -> None:
        lines = [
            line
            async for line in _handler().stream_rpc(
                _rpc("SendStreamingMessage", {"message": {}}), headers=V1
            )
        ]
        assert json.loads(lines[0][6:])["error"]["code"] == -32602

    async def test_stream_refused_legacy(self) -> None:
        h = _handler(a2a_protocol_version="1.0", a2a_accept_legacy=False)
        lines = [line async for line in h.stream_rpc(_legacy_send(), headers={})]
        assert json.loads(lines[0][6:])["error"]["code"] == -32009

    async def test_stream_negotiated_legacy_records_tenant(self) -> None:
        h = _handler()
        lines = [line async for line in h.stream_rpc(_legacy_send(), headers={}, org_id="acme")]
        assert json.loads(lines[-1][6:])["result"]["status"] == "completed"
        resp = await h.handle_rpc(_rpc("ListTasks", {}), headers=V1, org_id="acme")
        assert resp["result"]["totalSize"] == 1

    async def test_stream_unauthorized_new_path(self) -> None:
        h = _handler(a2a_strict=True)
        lines = [line async for line in h.stream_rpc(_v1_send(), headers=V1)]
        assert json.loads(lines[0][6:])["error"]["code"] == -32001

    async def test_stream_none_headers_unchanged(self) -> None:
        req = _v1_send()
        req["method"] = "SendStreamingMessage"
        lines = [line async for line in _handler().stream_rpc(req)]
        assert json.loads(lines[0][6:])["error"]["code"] == -32601


class TestTaskIdOwnership:
    """Security regression: a caller-supplied task id never overwrites another owner's task."""

    def _working(
        self, store: InMemoryA2ATaskStore, registry: dict[str, A2ATask], **kw: Any
    ) -> None:
        registry["w"] = A2ATask(id="w", status="working")
        now = datetime(2026, 1, 1, tzinfo=UTC)
        store.put(
            TaskRecord(
                task_id="w",
                context_id="c",
                org_id=kw.get("org", "acme"),
                principal=kw.get("principal"),
                state=TaskState.WORKING,
                created_at=now,
                updated_at=now,
            )
        )

    async def _a_task(self, h: A2AServerHandler) -> str:
        resp = await h.handle_rpc(_v1_send(), headers=V1, org_id="acme")
        return str(resp["result"]["task"]["id"])

    @staticmethod
    def _hijack_v1(task_id: str, method: str = "SendMessage") -> dict[str, Any]:
        req = _v1_send("pwn")
        req["method"] = method
        req["params"]["message"]["taskId"] = task_id
        return req

    @staticmethod
    def _hijack_legacy(task_id: str) -> dict[str, Any]:
        req = _legacy_send("pwn")
        req["params"]["taskId"] = task_id
        return req

    def _assert_untouched(
        self, h: A2AServerHandler, tid: str, before: Any, body_before: A2ATask, audit: _SpyAudit
    ) -> None:
        assert h._task_store.get(tid, org_id="acme") == before
        assert h._tasks[tid] == body_before
        assert any(e == "a2a.inbound.tenant_mismatch" for e, _ in audit.events)

    async def test_v1_cross_tenant_send_rejected(self, counter: _Counter) -> None:
        audit = _SpyAudit()
        graph = _FakeGraph()
        h = _handler(audit=audit, graph=graph)
        tid = await self._a_task(h)
        before, body = h._task_store.get(tid, org_id="acme"), h._tasks[tid]
        resp = await h.handle_rpc(self._hijack_v1(tid), headers=V1, org_id="beta")
        assert resp["error"]["code"] == -32001
        assert graph.calls == 1
        assert counter.calls[-1]["outcome"] == "tenant_mismatch"
        self._assert_untouched(h, tid, before, body, audit)

    async def test_v1_cross_tenant_stream_rejected(self) -> None:
        audit = _SpyAudit()
        h = _handler(audit=audit)
        tid = await self._a_task(h)
        before, body = h._task_store.get(tid, org_id="acme"), h._tasks[tid]
        req = self._hijack_v1(tid, "SendStreamingMessage")
        lines = [line async for line in h.stream_rpc(req, headers=V1, org_id="beta")]
        assert len(lines) == 1
        assert json.loads(lines[0][6:])["error"]["code"] == -32001
        self._assert_untouched(h, tid, before, body, audit)

    async def test_legacy_scoped_cross_tenant_send_and_stream_rejected(self) -> None:
        audit = _SpyAudit()
        h = _handler(audit=audit)
        tid = await self._a_task(h)
        before, body = h._task_store.get(tid, org_id="acme"), h._tasks[tid]
        resp = await h.handle_rpc(self._hijack_legacy(tid), headers={}, org_id="beta")
        assert resp["error"]["code"] == -32001
        lines = [
            line async for line in h.stream_rpc(self._hijack_legacy(tid), headers={}, org_id="beta")
        ]
        assert len(lines) == 1
        assert json.loads(lines[0][6:])["error"]["code"] == -32001
        self._assert_untouched(h, tid, before, body, audit)

    async def test_unscoped_legacy_cannot_overwrite_scoped_task(self) -> None:
        audit = _SpyAudit()
        h = _handler(audit=audit)
        tid = await self._a_task(h)
        before, body = h._task_store.get(tid, org_id="acme"), h._tasks[tid]
        resp = await h.handle_rpc(self._hijack_legacy(tid))
        assert resp["error"]["code"] == -32001
        lines = [line async for line in h.stream_rpc(self._hijack_legacy(tid))]
        assert json.loads(lines[0][6:])["error"]["code"] == -32001
        self._assert_untouched(h, tid, before, body, audit)

    async def test_scoped_caller_cannot_claim_unscoped_task(self) -> None:
        h = _handler()
        resp = await h.handle_rpc(_legacy_send("x"))  # pre-PRT host, no record
        tid = resp["result"]["id"]
        hijack = await h.handle_rpc(self._hijack_v1(tid), headers=V1, org_id="beta")
        assert hijack["error"]["code"] == -32001
        assert h._tasks[tid].history[0].parts[0].text == "x"

    async def test_other_principal_same_tenant_rejected(self) -> None:
        registry: dict[str, A2ATask] = {}
        store = InMemoryA2ATaskStore(registry)
        h = _handler(store=store)
        h._tasks = registry
        self._working(store, registry, principal="alice")
        bob = AuthContext(authenticated=True, subject="bob")
        resp = await h.handle_rpc(self._hijack_v1("w"), headers=V1, org_id="acme", auth_ctx=bob)
        assert resp["error"]["code"] == -32001
        rec = store.get("w", org_id="acme")
        assert rec is not None and rec.principal == "alice" and rec.state is TaskState.WORKING

    async def test_same_owner_continues_non_terminal_task_v1(self) -> None:
        registry: dict[str, A2ATask] = {}
        store = InMemoryA2ATaskStore(registry)
        h = _handler(store=store)
        h._tasks = registry
        self._working(store, registry)
        resp = await h.handle_rpc(self._hijack_v1("w"), headers=V1, org_id="acme")
        assert resp["result"]["task"]["id"] == "w"
        rec = store.get("w", org_id="acme")
        assert rec is not None and rec.state is TaskState.COMPLETED

    async def test_same_owner_continues_non_terminal_task_legacy(self) -> None:
        registry: dict[str, A2ATask] = {}
        store = InMemoryA2ATaskStore(registry)
        h = _handler(store=store)
        h._tasks = registry
        self._working(store, registry)
        resp = await h.handle_rpc(self._hijack_legacy("w"), headers={}, org_id="acme")
        assert resp["result"]["id"] == "w"

    async def test_same_owner_terminal_task_v1_unsupported(self) -> None:
        h = _handler()
        tid = await self._a_task(h)
        resp = await h.handle_rpc(self._hijack_v1(tid), headers=V1, org_id="acme")
        assert resp["error"]["code"] == -32004


# ── Review fixes (M5): ownership reservation, thread scoping, strict v0.3 ────


class _GatedGraph:
    """Records thread ids + bound org; the first call blocks until ``gate`` is set."""

    def __init__(self) -> None:
        self.threads: list[str] = []
        self.orgs: list[str | None] = []
        self.gate = asyncio.Event()
        self.started = asyncio.Event()

    async def ainvoke(self, state: dict[str, Any], config: Any = None) -> dict[str, Any]:
        self.threads.append(config["configurable"]["thread_id"])
        self.orgs.append(current_org.get())
        if len(self.threads) == 1:
            self.started.set()
            await self.gate.wait()
        text = state["messages"][-1].content
        return {"messages": [*state["messages"], AIMessage(content=f"echo: {text}")]}


def _with_task_v1(task_id: str, text: str = "hi", method: str = "SendMessage") -> dict[str, Any]:
    req = _v1_send(text)
    req["method"] = method
    req["params"]["message"]["taskId"] = task_id
    return req


def _with_task_legacy(task_id: str, text: str = "hi") -> dict[str, Any]:
    req = _legacy_send(text)
    req["params"]["taskId"] = task_id
    return req


async def _send_via(
    h: A2AServerHandler, path: str, task_id: str, text: str, org: str
) -> dict[str, Any]:
    """Send on *path*; return the (first) JSON-RPC envelope that carries a result/error."""
    if path == "v1":
        return await h.handle_rpc(_with_task_v1(task_id, text), headers=V1, org_id=org)
    if path == "legacy":
        return await h.handle_rpc(_with_task_legacy(task_id, text), headers={}, org_id=org)
    if path == "v1_stream":
        req = _with_task_v1(task_id, text, "SendStreamingMessage")
        lines = [ln async for ln in h.stream_rpc(req, headers=V1, org_id=org)]
    else:  # legacy_stream
        req = _with_task_legacy(task_id, text)
        lines = [ln async for ln in h.stream_rpc(req, headers={}, org_id=org)]
    return dict(json.loads(lines[-1][6:]))


class TestConcurrentReservation:
    @pytest.mark.parametrize("path", ["v1", "legacy", "v1_stream", "legacy_stream"])
    async def test_concurrent_cross_tenant_same_id_rejected(self, path: str) -> None:
        graph = _GatedGraph()
        audit = _SpyAudit()
        h = _handler(graph=graph, audit=audit)
        first = asyncio.create_task(_send_via(h, path, "t1", "A", "acme"))
        await graph.started.wait()
        second = await _send_via(h, path, "t1", "B", "beta")
        assert second["error"]["code"] == -32001
        graph.gate.set()
        done = await first
        assert "error" not in done
        assert len(graph.threads) == 1  # beta never ran on any thread
        rec = h._task_store.get("t1", org_id="acme")
        assert rec is not None and rec.org_id == "acme"
        assert h._task_store.get("t1", org_id="beta") is None
        assert any(e == "a2a.inbound.tenant_mismatch" for e, _ in audit.events)

    async def test_record_reserved_working_before_run(self) -> None:
        graph = _GatedGraph()
        h = _handler(graph=graph)
        first = asyncio.create_task(_send_via(h, "v1", "t1", "A", "acme"))
        await graph.started.wait()
        rec = h._task_store.get("t1", org_id="acme")
        assert rec is not None and rec.state is TaskState.WORKING
        graph.gate.set()
        await first
        rec = h._task_store.get("t1", org_id="acme")
        assert rec is not None and rec.state is TaskState.COMPLETED

    async def test_failed_run_marks_record_failed(self) -> None:
        class _Boom:
            async def ainvoke(self, state: dict[str, Any], config: Any = None) -> Any:
                raise RuntimeError("boom")

        h = _handler(graph=_Boom())
        with pytest.raises(RuntimeError):
            await _send_via(h, "v1", "t1", "A", "acme")
        rec = h._task_store.get("t1", org_id="acme")
        assert rec is not None and rec.state is TaskState.FAILED


class TestThreadScoping:
    async def test_thread_id_differs_per_tenant_for_same_task_id(self) -> None:
        graph = _GatedGraph()
        graph.gate.set()
        # two handlers sharing one graph/checkpointer ≈ restart with persistent checkpoints
        h1, h2 = _handler(graph=graph), _handler(graph=graph)
        await _send_via(h1, "v1", "t1", "A", "acme")
        await _send_via(h2, "v1", "t1", "B", "beta")
        await _send_via(h2, "legacy", "t2", "C", "acme")
        a, b, c_ = graph.threads
        assert a != b
        assert "t1" not in (a, b)
        assert c_ != "t2"

    async def test_thread_id_includes_principal(self) -> None:
        graph = _GatedGraph()
        graph.gate.set()
        h1, h2 = _handler(graph=graph), _handler(graph=graph)
        alice = AuthContext(authenticated=True, subject="alice")
        bob = AuthContext(authenticated=True, subject="bob")
        await h1.handle_rpc(_with_task_v1("t1"), headers=V1, org_id="acme", auth_ctx=alice)
        await h2.handle_rpc(_with_task_v1("t1"), headers=V1, org_id="acme", auth_ctx=bob)
        assert graph.threads[0] != graph.threads[1]

    async def test_default_path_keeps_plain_task_id(self) -> None:
        graph = _GatedGraph()
        graph.gate.set()
        h = _handler(graph=graph)
        await h.handle_rpc(_with_task_legacy("t9"))
        lines = [ln async for ln in h.stream_rpc(_with_task_legacy("t8"))]
        assert lines
        assert graph.threads == ["t9", "t8"]


class TestHostStatsOrgBinding:
    async def test_scoped_run_binds_org(self) -> None:
        graph = _GatedGraph()
        graph.gate.set()
        h = _handler(graph=graph)
        await _send_via(h, "v1", "t1", "A", "acme")
        await _send_via(h, "legacy_stream", "t2", "B", "beta")
        assert graph.orgs == ["acme", "beta"]
        assert current_org.get() is None

    async def test_default_path_binds_nothing(self) -> None:
        graph = _GatedGraph()
        graph.gate.set()
        h = _handler(graph=graph)
        await h.handle_rpc(_legacy_send("x"))
        assert graph.orgs == [None]


class TestStrictLegacyPrincipal:
    async def test_bob_cannot_get_or_cancel_alices_task(self) -> None:
        h = _handler(a2a_strict=True)
        alice = AuthContext(authenticated=True, subject="alice")
        bob = AuthContext(authenticated=True, subject="bob")
        sent = await h.handle_rpc(_legacy_send("x"), headers={}, org_id="acme", auth_ctx=alice)
        tid = sent["result"]["id"]
        get = await h.handle_rpc(
            _rpc("tasks/get", {"id": tid}), headers={}, org_id="acme", auth_ctx=bob
        )
        assert get["error"]["code"] == -32004  # same answer as an unknown task (no leak)
        cancel = await h.handle_rpc(
            _rpc("tasks/cancel", {"id": tid}), headers={}, org_id="acme", auth_ctx=bob
        )
        assert cancel["error"]["code"] == -32004
        assert h._tasks[tid].status == "completed"
        mine = await h.handle_rpc(
            _rpc("tasks/get", {"id": tid}), headers={}, org_id="acme", auth_ctx=alice
        )
        assert mine["result"]["id"] == tid


class TestStreamUnauthorizedAudit:
    async def test_stream_unauthorized_is_audited(self) -> None:
        audit = _SpyAudit()
        h = _handler(audit=audit, a2a_strict=True)
        req = _with_task_v1("t1", method="SendStreamingMessage")
        lines = [ln async for ln in h.stream_rpc(req, headers=V1, org_id="acme")]
        assert json.loads(lines[0][6:])["error"]["code"] == -32001
        assert (
            "a2a.inbound",
            {"status": "unauthorized", "method": "SendStreamingMessage"},
        ) in audit.events


class TestListTimestamps:
    async def test_naive_status_timestamp_after_treated_as_utc(self) -> None:
        h = _handler()
        await _send_via(h, "v1", "t1", "A", "acme")
        resp = await h.handle_rpc(
            _rpc("ListTasks", {"statusTimestampAfter": "2026-01-01T00:00:00"}),
            headers=V1,
            org_id="acme",
        )
        assert resp["result"]["totalSize"] == 1

    async def test_forged_naive_page_token_does_not_crash(self) -> None:
        import base64

        token = base64.urlsafe_b64encode(b'{"u": "2099-01-01T00:00:00", "id": "z"}').decode()
        h = _handler()
        await _send_via(h, "v1", "t1", "A", "acme")
        resp = await h.handle_rpc(
            _rpc("ListTasks", {"pageToken": token}), headers=V1, org_id="acme"
        )
        assert len(resp["result"]["tasks"]) == 1

"""Tests for the @prismal_node middleware chain (X2, SPEC-EXT-007)."""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from prismal.agents.extension import RetryPolicy, prismal_node
from prismal.agents.state import create_initial_state
from prismal.core.exceptions import NodeExecutionError, NodeTimeoutError


def _state(text: str = "hello"):
    state = create_initial_state(session_id="sess-mw")
    state["messages"] = [HumanMessage(content=text)]
    return state


class TestErrorMapping:
    async def test_error_captured_as_state_update_by_default(self) -> None:
        @prismal_node(name="boom_soft", security="off", audit=False)
        async def boom_soft(state):
            raise ValueError("kaboom")

        out = await boom_soft(_state())
        assert out["metadata"]["error"]["node"] == "boom_soft"
        assert "kaboom" in out["metadata"]["error"]["message"]

    async def test_error_raised_when_raise_on_error_true(self) -> None:
        @prismal_node(name="boom_hard", security="off", audit=False, raise_on_error=True)
        async def boom_hard(state):
            raise ValueError("kaboom")

        with pytest.raises(NodeExecutionError) as ei:
            await boom_hard(_state())
        assert ei.value.node_name == "boom_hard"
        assert isinstance(ei.value.cause, ValueError)


class TestTimeout:
    async def test_timeout_raises_node_timeout_error(self) -> None:
        import asyncio

        @prismal_node(
            name="slow_hard",
            security="off",
            audit=False,
            timeout_s=0.05,
            raise_on_error=True,
        )
        async def slow_hard(state):
            await asyncio.sleep(1.0)
            return {}

        with pytest.raises(NodeTimeoutError) as ei:
            await slow_hard(_state())
        assert ei.value.timeout_s == 0.05

    async def test_timeout_soft_returns_error_update(self) -> None:
        import asyncio

        @prismal_node(name="slow_soft", security="off", audit=False, timeout_s=0.05)
        async def slow_soft(state):
            await asyncio.sleep(1.0)
            return {}

        out = await slow_soft(_state())
        assert out["metadata"]["error"]["timeout"] is True


class TestRetry:
    async def test_retries_until_success(self) -> None:
        attempts = {"n": 0}

        @prismal_node(
            name="flaky_node",
            security="off",
            audit=False,
            retry=RetryPolicy(max_attempts=3, backoff_s=(0.0, 0.0), retry_on=(ValueError,)),
        )
        async def flaky_node(state):
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise ValueError("transient")
            return {"current_agent": "recovered"}

        out = await flaky_node(_state())
        assert out["current_agent"] == "recovered"
        assert attempts["n"] == 3

    async def test_retry_exhausted_surfaces_error(self) -> None:
        attempts = {"n": 0}

        @prismal_node(
            name="always_fails",
            security="off",
            audit=False,
            retry=RetryPolicy(max_attempts=2, backoff_s=(0.0,), retry_on=(ValueError,)),
        )
        async def always_fails(state):
            attempts["n"] += 1
            raise ValueError("permanent")

        out = await always_fails(_state())
        assert out["metadata"]["error"]["node"] == "always_fails"
        assert attempts["n"] == 2

    async def test_non_retryable_exception_not_retried(self) -> None:
        attempts = {"n": 0}

        @prismal_node(
            name="type_error_node",
            security="off",
            audit=False,
            retry=RetryPolicy(max_attempts=3, backoff_s=(0.0, 0.0), retry_on=(ValueError,)),
        )
        async def type_error_node(state):
            attempts["n"] += 1
            raise TypeError("not retryable")

        out = await type_error_node(_state())
        assert out["metadata"]["error"]["node"] == "type_error_node"
        assert attempts["n"] == 1


class TestAudit:
    async def test_audit_logs_node_when_enabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[dict] = []
        from prismal.security.audit import AuditLogger

        def fake_log_node(self, **kwargs):  # noqa: ANN001
            calls.append(kwargs)

        monkeypatch.setattr(AuditLogger, "log_node", fake_log_node, raising=False)

        @prismal_node(name="audited_node", security="off", audit=True)
        async def audited_node(state):
            return {"current_agent": "ok"}

        await audited_node(_state())
        assert len(calls) == 1
        assert calls[0]["node_name"] == "audited_node"
        assert calls[0]["status"] == "ok"

    async def test_audit_logs_error_status_on_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[dict] = []
        from prismal.security.audit import AuditLogger

        monkeypatch.setattr(
            AuditLogger, "log_node", lambda self, **kw: calls.append(kw), raising=False
        )

        @prismal_node(name="audited_fail", security="off", audit=True, raise_on_error=True)
        async def audited_fail(state):
            raise ValueError("nope")

        with pytest.raises(NodeExecutionError):
            await audited_fail(_state())
        assert calls and calls[0]["status"] == "error"

    async def test_no_audit_when_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[dict] = []
        from prismal.security.audit import AuditLogger

        monkeypatch.setattr(
            AuditLogger, "log_node", lambda self, **kw: calls.append(kw), raising=False
        )

        @prismal_node(name="silent_node", security="off", audit=False)
        async def silent_node(state):
            return {}

        await silent_node(_state())
        assert calls == []


class TestSecurity:
    async def test_standard_sanitizes_user_input(self) -> None:
        @prismal_node(name="sani_node", security="standard", audit=False)
        async def sani_node(state):
            return {"current_agent": state["messages"][-1].content}

        # Control char \x00 must be stripped by InputSanitizer before the node sees it.
        out = await sani_node(_state("clean\x00text"))
        assert "\x00" not in out["current_agent"]
        assert "cleantext" in out["current_agent"].replace(" ", "")

    async def test_off_does_not_invoke_tool_checker(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from prismal.agents.extension import _middleware

        invoked = {"n": 0}

        async def fake_checker(update, metadata):  # noqa: ANN001
            invoked["n"] += 1

        monkeypatch.setattr(_middleware, "_tool_call_checker", fake_checker)

        @prismal_node(name="off_sec_node", security="off", audit=False)
        async def off_sec_node(state):
            return {"messages": [AIMessage(content="x")]}

        await off_sec_node(_state())
        assert invoked["n"] == 0

    async def test_strict_invokes_tool_checker(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from prismal.agents.extension import _middleware

        invoked = {"n": 0}

        async def fake_checker(update, metadata):  # noqa: ANN001
            invoked["n"] += 1

        monkeypatch.setattr(_middleware, "_tool_call_checker", fake_checker)

        @prismal_node(name="strict_sec_node", security="strict", audit=False)
        async def strict_sec_node(state):
            return {"messages": [AIMessage(content="x")]}

        await strict_sec_node(_state())
        assert invoked["n"] == 1


class TestDefaultToolChecker:
    async def test_unmapped_tool_passes_through(self) -> None:
        from prismal.agents.extension._middleware import _check_tool_calls
        from prismal.agents.extension.decorators import NodeMetadata

        meta = NodeMetadata(
            name="n",
            capabilities=(),
            security="strict",
            audit=False,
            retry=None,
            timeout_s=None,
            raise_on_error=False,
            registered_at="t",
            source_module="m",
        )
        update = {
            "messages": [
                AIMessage(content="", tool_calls=[{"name": "unmapped_tool", "args": {}, "id": "1"}])
            ]
        }
        # Must not raise for a tool that is not in the permission map.
        await _check_tool_calls(update, meta)

    async def test_mapped_tool_without_grant_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from prismal.agents.extension._middleware import _check_tool_calls
        from prismal.agents.extension.decorators import NodeMetadata
        from prismal.core.exceptions import PermissionDeniedError
        from prismal.security.permissions import PermissionManager

        async def deny(self, perm, resource):  # noqa: ANN001
            return False

        monkeypatch.setattr(PermissionManager, "check", deny)

        meta = NodeMetadata(
            name="n",
            capabilities=(),
            security="strict",
            audit=False,
            retry=None,
            timeout_s=None,
            raise_on_error=False,
            registered_at="t",
            source_module="m",
        )
        update = {
            "messages": [
                AIMessage(content="", tool_calls=[{"name": "write_file", "args": {}, "id": "1"}])
            ]
        }
        with pytest.raises(PermissionDeniedError):
            await _check_tool_calls(update, meta)

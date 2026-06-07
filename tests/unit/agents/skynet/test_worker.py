"""Unit tests for ``prismal.agents.skynet.worker`` (SPEC-SKY-WRK-001).

Covers RF-SKY-05/14 and the S3 "done when" criteria: a worker's tool action
passes through the ActionInterceptor gate (spy-verified); failures are
captured as ``WorkerResult(success=False)`` and never raised out.
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.tools import tool

from prismal.agents.extension.providers import FakeToolProvider
from prismal.agents.skynet.types import SwarmOrder
from prismal.agents.skynet.worker import SwarmWorker
from prismal.core.config import Settings
from prismal.core.exceptions import PermissionDeniedError

# ── fixtures / fakes ─────────────────────────────────────────────────────────


@tool
def echo_tool(text: str) -> str:
    """Echo the given text back."""
    return f"echo:{text}"


class FakeWorkerFn:
    """Deterministic worker_fn capturing the messages it receives."""

    def __init__(self, reply: str = "plain answer") -> None:
        self.reply = reply
        self.received: list[list[dict[str, str]]] = []

    async def __call__(self, messages: list[dict[str, str]]) -> str:
        self.received.append(messages)
        return self.reply


class SpyInterceptor:
    """ActionInterceptor stand-in that records every gate call and allows all."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def on_tool_start(
        self, serialized: dict[str, Any], input_str: str, **kwargs: Any
    ) -> None:
        self.calls.append((str(serialized.get("name")), input_str))


class DenyInterceptor:
    """ActionInterceptor stand-in that denies every action."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def on_tool_start(
        self, serialized: dict[str, Any], input_str: str, **kwargs: Any
    ) -> None:
        name = str(serialized.get("name"))
        self.calls.append(name)
        raise PermissionDeniedError(name, "tool_execution")


class SpyToolProvider:
    """ToolProviderPort spy recording resolution arguments."""

    def __init__(self, tools: list[Any] | None = None) -> None:
        self.tools = tools or []
        self.calls: list[tuple[str, list[str] | None]] = []

    def get_tools(self, *, agent_name: str, capabilities: list[str] | None = None) -> list[Any]:
        self.calls.append((agent_name, capabilities))
        return self.tools


def _order(instruction: str = "do the thing", **kwargs: Any) -> SwarmOrder:
    return SwarmOrder(order_id="ord-1", instruction=instruction, **kwargs)


def _worker(
    worker_fn: FakeWorkerFn | None = None,
    *,
    tool_provider: Any = None,
    interceptor: Any = None,
    **settings_kwargs: Any,
) -> SwarmWorker:
    return SwarmWorker(
        worker_fn=worker_fn or FakeWorkerFn(),
        tool_provider=tool_provider,
        interceptor=interceptor,
        settings=Settings(**settings_kwargs),
    )


def _action_reply(*actions: dict[str, Any], output: str = "did it") -> str:
    return json.dumps({"output": output, "actions": list(actions)})


# ── execute(): happy path (RF-SKY-05) ────────────────────────────────────────


async def test_execute_returns_successful_result() -> None:
    """A plain text reply becomes a successful WorkerResult."""
    result = await _worker(FakeWorkerFn("the answer")).execute(_order())
    assert result.order_id == "ord-1"
    assert result.success is True
    assert result.output == "the answer"
    assert result.error is None
    assert result.tool_calls == 0


async def test_execute_instruction_travels_secure_channel() -> None:
    """order.instruction is user-derived → sanitized <user_input> (RF-SKY-14)."""
    fn = FakeWorkerFn()
    await _worker(fn).execute(_order("research competitor X"))
    messages = fn.received[0]
    assert messages[0]["role"] == "system"
    assert "canary:" in messages[0]["content"]
    assert "<user_input>" in messages[1]["content"]
    assert "research competitor X" in messages[1]["content"]


async def test_execute_includes_order_context_in_user_channel() -> None:
    """The small context snapshot rides in the sanitized user channel too."""
    fn = FakeWorkerFn()
    await _worker(fn).execute(_order(context={"region": "EU"}))
    assert "region" in fn.received[0][1]["content"]


async def test_execute_json_output_extracted() -> None:
    """A JSON reply's 'output' field becomes the result output."""
    result = await _worker(FakeWorkerFn(_action_reply(output="structured"))).execute(_order())
    assert result.output == "structured"


# ── execute(): tool resolution via ToolProviderPort (RF-SKY-05) ──────────────


async def test_execute_resolves_tools_for_role_via_injected_provider() -> None:
    """Tools come from the injected ToolProviderPort, keyed by the order role."""
    provider = SpyToolProvider()
    await _worker(tool_provider=provider).execute(_order(role="researcher"))
    assert provider.calls == [("skynet_worker", ["researcher"])]


async def test_execute_without_provider_resolves_no_tools() -> None:
    """No provider → no tools, plain execution still works."""
    result = await _worker(tool_provider=None).execute(_order())
    assert result.success is True


async def test_execute_advertises_tool_names_in_system_prompt() -> None:
    """Available tool names are listed in the trusted system template."""
    fn = FakeWorkerFn()
    provider = FakeToolProvider(default=[echo_tool])
    await _worker(fn, tool_provider=provider).execute(_order())
    assert "echo_tool" in fn.received[0][0]["content"]


# ── execute(): gated tool actions (RF-SKY-14) ────────────────────────────────


async def test_tool_action_passes_through_interceptor_gate() -> None:
    """Every requested tool action hits ActionInterceptor first (spy)."""
    interceptor = SpyInterceptor()
    provider = FakeToolProvider(default=[echo_tool])
    fn = FakeWorkerFn(_action_reply({"tool_name": "echo_tool", "args": {"text": "hi"}}))
    result = await _worker(fn, tool_provider=provider, interceptor=interceptor).execute(_order())
    assert interceptor.calls and interceptor.calls[0][0] == "echo_tool"
    assert result.tool_calls == 1
    assert "echo:hi" in result.output


async def test_denied_tool_action_is_skipped_not_fatal() -> None:
    """A denied action is recorded as blocked; the worker still succeeds."""
    interceptor = DenyInterceptor()
    provider = FakeToolProvider(default=[echo_tool])
    fn = FakeWorkerFn(_action_reply({"tool_name": "echo_tool", "args": {"text": "hi"}}))
    result = await _worker(fn, tool_provider=provider, interceptor=interceptor).execute(_order())
    assert interceptor.calls == ["echo_tool"]
    assert result.success is True
    assert result.tool_calls == 0
    assert "blocked" in result.output


async def test_unknown_tool_request_is_skipped_gracefully() -> None:
    """A requested tool that was not resolved is skipped, not executed."""
    interceptor = SpyInterceptor()
    provider = FakeToolProvider(default=[echo_tool])
    fn = FakeWorkerFn(_action_reply({"tool_name": "rm_rf", "args": {}}))
    result = await _worker(fn, tool_provider=provider, interceptor=interceptor).execute(_order())
    assert result.success is True
    assert result.tool_calls == 0
    assert interceptor.calls == []  # never reaches the gate for unresolved tools


async def test_failing_tool_does_not_abort_the_worker() -> None:
    """A tool that raises is captured in the output; the worker succeeds."""

    @tool
    def broken_tool(text: str) -> str:
        """Always fails."""
        raise RuntimeError("tool exploded")

    provider = FakeToolProvider(default=[broken_tool])
    fn = FakeWorkerFn(_action_reply({"tool_name": "broken_tool", "args": {"text": "x"}}))
    result = await _worker(fn, tool_provider=provider, interceptor=SpyInterceptor()).execute(
        _order()
    )
    assert result.success is True
    assert result.tool_calls == 0
    assert "tool exploded" in result.output


# ── execute(): failure isolation (S3-02) ─────────────────────────────────────


async def test_worker_fn_failure_captured_never_raised() -> None:
    """A failing worker backend yields WorkerResult(success=False)."""

    async def boom(messages: list[dict[str, str]]) -> str:
        raise RuntimeError("llm down")

    result = await SwarmWorker(worker_fn=boom, settings=Settings()).execute(_order())
    assert result.success is False
    assert result.error is not None and "llm down" in result.error
    assert result.output == ""


async def test_failed_result_keeps_order_id_for_replanning() -> None:
    """The failed WorkerResult carries the order id so the loop can re-plan it."""

    async def boom(messages: list[dict[str, str]]) -> str:
        raise RuntimeError("nope")

    result = await SwarmWorker(worker_fn=boom, settings=Settings()).execute(_order())
    assert result.order_id == "ord-1"

"""Unit tests for MCPToolAdapter.

All MCP SDK I/O and ActionInterceptor calls are mocked so that tests do
not require a live server or security infrastructure.

Tests focus on *behaviour* — correct argument parsing, correct content
extraction, interceptor ordering, and error propagation — rather than
just verifying mock invocations.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import mcp.types
import pytest

from lightagent.core.exceptions import MCPToolError, PermissionDeniedError
from lightagent.mcp.adapter import MCPToolAdapter
from lightagent.mcp.connection import MCPServerConfig, MCPServerConnection

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _make_stdio_config(name: str = "test-server") -> MCPServerConfig:
    """Return a minimal valid stdio MCPServerConfig."""
    return MCPServerConfig.model_validate(
        {
            "name": name,
            "type": "stdio",
            "command": ["python", "-m", "testserver"],
            "timeout_seconds": 5,
            "retry_attempts": 1,
        }
    )


def _make_mock_connection(server_name: str = "test-server") -> MagicMock:
    """Return a mock MCPServerConnection with a pre-set config name."""
    conn = MagicMock(spec=MCPServerConnection)
    conn.connected = True
    # MCPToolAdapter accesses conn._config.name for error messages.
    conn._config = _make_stdio_config(server_name)
    return conn


def _make_mcp_tool(
    name: str = "my_tool",
    description: str | None = "A test tool",
) -> mcp.types.Tool:
    """Return a minimal mcp.types.Tool object."""
    return mcp.types.Tool(
        name=name,
        description=description,
        inputSchema={"type": "object"},
    )


def _make_call_tool_result(
    text: str = "ok",
    is_error: bool = False,
    extra_blocks: list[mcp.types.ContentBlock] | None = None,
) -> mcp.types.CallToolResult:
    """Return a CallToolResult with one TextContent block."""
    content: list[mcp.types.ContentBlock] = [
        mcp.types.TextContent(type="text", text=text)
    ]
    if extra_blocks:
        content.extend(extra_blocks)
    return mcp.types.CallToolResult(content=content, isError=is_error)


def _make_mock_interceptor(
    deny: bool = False,
) -> MagicMock:
    """Return a mock ActionInterceptor.

    If ``deny=True`` the ``on_tool_start`` side effect is set to raise
    ``PermissionDeniedError``.
    """
    interceptor = MagicMock()
    if deny:
        interceptor.on_tool_start = AsyncMock(
            side_effect=PermissionDeniedError("my_tool", "filesystem_write")
        )
    else:
        interceptor.on_tool_start = AsyncMock(return_value=None)
    interceptor.on_tool_end = AsyncMock(return_value=None)
    interceptor.on_tool_error = AsyncMock(return_value=None)
    return interceptor


# ---------------------------------------------------------------------------
# Creation / field mapping
# ---------------------------------------------------------------------------


class TestMCPToolAdapterCreation:
    """Tests that MCPToolAdapter is created with correct name/description."""

    def test_name_taken_from_mcp_tool(self) -> None:
        """Adapter name must match the MCP tool name."""
        conn = _make_mock_connection()
        tool = _make_mcp_tool(name="file_reader")
        adapter = MCPToolAdapter(conn, tool)
        assert adapter.name == "file_reader"

    def test_description_taken_from_mcp_tool(self) -> None:
        """Adapter description must match the MCP tool description."""
        conn = _make_mock_connection()
        tool = _make_mcp_tool(description="Reads a file from disk")
        adapter = MCPToolAdapter(conn, tool)
        assert adapter.description == "Reads a file from disk"

    def test_description_defaults_to_empty_string_when_none(self) -> None:
        """When the MCP tool description is None the adapter must use ''."""
        conn = _make_mock_connection()
        tool = _make_mcp_tool(description=None)
        adapter = MCPToolAdapter(conn, tool)
        assert adapter.description == ""

    def test_private_attrs_stored(self) -> None:
        """Private connection and mcp_tool attributes must be stored."""
        conn = _make_mock_connection()
        mcp_tool = _make_mcp_tool()
        adapter = MCPToolAdapter(conn, mcp_tool)
        assert adapter._connection is conn
        assert adapter._mcp_tool is mcp_tool
        assert adapter._interceptor is None

    def test_interceptor_stored_when_provided(self) -> None:
        """Interceptor must be stored in _interceptor when passed."""
        conn = _make_mock_connection()
        mcp_tool = _make_mcp_tool()
        interceptor = _make_mock_interceptor()
        adapter = MCPToolAdapter(conn, mcp_tool, interceptor=interceptor)
        assert adapter._interceptor is interceptor

    def test_is_langchain_basetool(self) -> None:
        """MCPToolAdapter must be a subclass of LangChain BaseTool."""
        from langchain_core.tools import BaseTool

        conn = _make_mock_connection()
        adapter = MCPToolAdapter(conn, _make_mcp_tool())
        assert isinstance(adapter, BaseTool)


# ---------------------------------------------------------------------------
# from_mcp_tool classmethod
# ---------------------------------------------------------------------------


class TestFromMcpTool:
    """Tests for the from_mcp_tool() convenience factory classmethod."""

    def test_creates_adapter_with_correct_name(self) -> None:
        """from_mcp_tool must return an adapter with the tool's name."""
        conn = _make_mock_connection()
        tool = _make_mcp_tool(name="web_search")
        adapter = MCPToolAdapter.from_mcp_tool(conn, tool)
        assert adapter.name == "web_search"

    def test_creates_adapter_with_interceptor(self) -> None:
        """from_mcp_tool must pass the interceptor through to the adapter."""
        conn = _make_mock_connection()
        tool = _make_mcp_tool()
        interceptor = _make_mock_interceptor()
        adapter = MCPToolAdapter.from_mcp_tool(conn, tool, interceptor=interceptor)
        assert adapter._interceptor is interceptor

    def test_creates_adapter_without_interceptor_by_default(self) -> None:
        """from_mcp_tool without interceptor arg must leave _interceptor as None."""
        conn = _make_mock_connection()
        adapter = MCPToolAdapter.from_mcp_tool(conn, _make_mcp_tool())
        assert adapter._interceptor is None


# ---------------------------------------------------------------------------
# _arun — argument parsing
# ---------------------------------------------------------------------------


class TestArunArgumentParsing:
    """Tests that _arun correctly parses JSON and plain-string inputs."""

    @pytest.mark.asyncio
    async def test_json_string_parsed_as_dict(self) -> None:
        """Valid JSON dict input must be parsed and passed as kwargs."""
        conn = _make_mock_connection()
        conn.call_tool = AsyncMock(return_value=_make_call_tool_result("hello"))
        adapter = MCPToolAdapter(conn, _make_mcp_tool())

        await adapter._arun('{"path": "/some/file.txt"}')

        conn.call_tool.assert_awaited_once_with(
            name="my_tool",
            arguments={"path": "/some/file.txt"},
        )

    @pytest.mark.asyncio
    async def test_plain_string_wrapped_in_input_key(self) -> None:
        """A non-JSON plain string must be wrapped in {"input": ...}."""
        conn = _make_mock_connection()
        conn.call_tool = AsyncMock(return_value=_make_call_tool_result("hello"))
        adapter = MCPToolAdapter(conn, _make_mcp_tool())

        await adapter._arun("search query text")

        conn.call_tool.assert_awaited_once_with(
            name="my_tool",
            arguments={"input": "search query text"},
        )

    @pytest.mark.asyncio
    async def test_json_non_dict_wrapped_in_input_key(self) -> None:
        """A JSON value that is not a dict (e.g. a list) must be wrapped in
        {"input": ...}."""
        conn = _make_mock_connection()
        conn.call_tool = AsyncMock(return_value=_make_call_tool_result("hello"))
        adapter = MCPToolAdapter(conn, _make_mcp_tool())

        # JSON array — not a dict, so must be treated as a plain string.
        await adapter._arun('["a", "b"]')

        conn.call_tool.assert_awaited_once_with(
            name="my_tool",
            arguments={"input": '["a", "b"]'},
        )

    @pytest.mark.asyncio
    async def test_empty_json_object_passed_as_empty_dict(self) -> None:
        """An empty JSON object '{}' must be passed as an empty arguments dict."""
        conn = _make_mock_connection()
        conn.call_tool = AsyncMock(return_value=_make_call_tool_result("ok"))
        adapter = MCPToolAdapter(conn, _make_mcp_tool())

        await adapter._arun("{}")

        conn.call_tool.assert_awaited_once_with(
            name="my_tool",
            arguments={},
        )


# ---------------------------------------------------------------------------
# _arun — content extraction
# ---------------------------------------------------------------------------


class TestArunContentExtraction:
    """Tests that _arun correctly extracts text from CallToolResult content."""

    @pytest.mark.asyncio
    async def test_single_text_block_returned(self) -> None:
        """A single TextContent block must be returned as-is."""
        conn = _make_mock_connection()
        conn.call_tool = AsyncMock(
            return_value=_make_call_tool_result("result text")
        )
        adapter = MCPToolAdapter(conn, _make_mcp_tool())

        output = await adapter._arun("{}")

        assert output == "result text"

    @pytest.mark.asyncio
    async def test_multiple_text_blocks_joined_with_newline(self) -> None:
        """Multiple content blocks must be joined with a newline character."""
        extra = mcp.types.TextContent(type="text", text="second block")
        conn = _make_mock_connection()
        conn.call_tool = AsyncMock(
            return_value=_make_call_tool_result(
                "first block", extra_blocks=[extra]
            )
        )
        adapter = MCPToolAdapter(conn, _make_mcp_tool())

        output = await adapter._arun("{}")

        assert output == "first block\nsecond block"

    @pytest.mark.asyncio
    async def test_empty_content_returns_empty_string(self) -> None:
        """A result with an empty content list must return an empty string."""
        conn = _make_mock_connection()
        conn.call_tool = AsyncMock(
            return_value=mcp.types.CallToolResult(content=[], isError=False)
        )
        adapter = MCPToolAdapter(conn, _make_mcp_tool())

        output = await adapter._arun("{}")

        assert output == ""

    @pytest.mark.asyncio
    async def test_non_text_block_stringified(self) -> None:
        """Non-TextContent blocks must be included via str() fallback."""
        # Use ImageContent as a non-text block example.
        img_block = mcp.types.ImageContent(
            type="image", data="base64data", mimeType="image/png"
        )
        conn = _make_mock_connection()
        conn.call_tool = AsyncMock(
            return_value=mcp.types.CallToolResult(
                content=[
                    mcp.types.TextContent(type="text", text="text part"),
                    img_block,
                ],
                isError=False,
            )
        )
        adapter = MCPToolAdapter(conn, _make_mcp_tool())

        output = await adapter._arun("{}")

        # First line is the text block; second is the str() of the image block.
        lines = output.split("\n")
        assert lines[0] == "text part"
        assert len(lines) == 2
        # The image block stringified form should contain something identifiable.
        assert any(
            marker in lines[1]
            for marker in ["image", "ImageContent", "base64data"]
        )


# ---------------------------------------------------------------------------
# _arun — error handling
# ---------------------------------------------------------------------------


class TestArunErrorHandling:
    """Tests that _arun raises MCPToolError on isError=True results."""

    @pytest.mark.asyncio
    async def test_is_error_true_raises_mcp_tool_error(self) -> None:
        """When isError=True the adapter must raise MCPToolError."""
        conn = _make_mock_connection()
        conn.call_tool = AsyncMock(
            return_value=_make_call_tool_result(
                "something went wrong", is_error=True
            )
        )
        adapter = MCPToolAdapter(conn, _make_mcp_tool())

        with pytest.raises(MCPToolError) as exc_info:
            await adapter._arun("{}")

        assert exc_info.value.tool_name == "my_tool"
        assert exc_info.value.server_name == "test-server"

    @pytest.mark.asyncio
    async def test_is_error_true_includes_error_text_in_reason(self) -> None:
        """The MCPToolError reason must contain the error text from the result."""
        conn = _make_mock_connection()
        conn.call_tool = AsyncMock(
            return_value=_make_call_tool_result("tool crashed", is_error=True)
        )
        adapter = MCPToolAdapter(conn, _make_mcp_tool())

        with pytest.raises(MCPToolError) as exc_info:
            await adapter._arun("{}")

        assert "tool crashed" in exc_info.value.reason

    @pytest.mark.asyncio
    async def test_connection_error_propagates(self) -> None:
        """Exceptions from call_tool must propagate after on_tool_error is called."""
        conn = _make_mock_connection()
        original_exc = MCPToolError("my_tool", "test-server", "timeout")
        conn.call_tool = AsyncMock(side_effect=original_exc)
        adapter = MCPToolAdapter(conn, _make_mcp_tool())

        with pytest.raises(MCPToolError, match="timeout"):
            await adapter._arun("{}")


# ---------------------------------------------------------------------------
# _arun — interceptor integration (AC-004-4)
# ---------------------------------------------------------------------------


class TestArunInterceptorIntegration:
    """Tests that the ActionInterceptor is called correctly (AC-004-4)."""

    @pytest.mark.asyncio
    async def test_on_tool_start_called_before_call_tool(self) -> None:
        """on_tool_start must be awaited before call_tool is invoked."""
        call_order: list[str] = []

        conn = _make_mock_connection()

        async def record_call_tool(
            name: str, arguments: dict[str, Any]
        ) -> mcp.types.CallToolResult:
            call_order.append("call_tool")
            return _make_call_tool_result("ok")

        conn.call_tool = record_call_tool

        interceptor = _make_mock_interceptor()

        async def record_on_tool_start(
            serialized: dict[str, Any], input_str: str, **kwargs: Any
        ) -> None:
            call_order.append("on_tool_start")

        interceptor.on_tool_start = AsyncMock(side_effect=record_on_tool_start)

        adapter = MCPToolAdapter(conn, _make_mcp_tool(), interceptor=interceptor)
        await adapter._arun('{"x": 1}')

        assert call_order == ["on_tool_start", "call_tool"]

    @pytest.mark.asyncio
    async def test_on_tool_end_called_after_successful_call(self) -> None:
        """on_tool_end must be awaited after a successful call_tool."""
        conn = _make_mock_connection()
        conn.call_tool = AsyncMock(return_value=_make_call_tool_result("done"))
        interceptor = _make_mock_interceptor()

        adapter = MCPToolAdapter(conn, _make_mcp_tool(), interceptor=interceptor)
        result = await adapter._arun("{}")

        interceptor.on_tool_end.assert_awaited_once_with(output="done")
        assert result == "done"

    @pytest.mark.asyncio
    async def test_on_tool_start_receives_correct_serialized(self) -> None:
        """on_tool_start must receive a serialized dict with name and
        description."""
        conn = _make_mock_connection()
        conn.call_tool = AsyncMock(return_value=_make_call_tool_result("ok"))
        interceptor = _make_mock_interceptor()
        captured: dict[str, Any] = {}

        async def capture_start(
            serialized: dict[str, Any], input_str: str, **kwargs: Any
        ) -> None:
            captured.update(serialized)

        interceptor.on_tool_start = AsyncMock(side_effect=capture_start)
        tool = _make_mcp_tool(name="file_reader", description="Reads files")
        adapter = MCPToolAdapter(conn, tool, interceptor=interceptor)

        await adapter._arun("{}")

        assert captured["name"] == "file_reader"
        assert captured["description"] == "Reads files"

    @pytest.mark.asyncio
    async def test_on_tool_start_receives_raw_input_str(self) -> None:
        """on_tool_start must receive the raw (unparsed) tool_input string."""
        conn = _make_mock_connection()
        conn.call_tool = AsyncMock(return_value=_make_call_tool_result("ok"))
        interceptor = _make_mock_interceptor()
        captured_input: list[str] = []

        async def capture_start(
            serialized: dict[str, Any], input_str: str, **kwargs: Any
        ) -> None:
            captured_input.append(input_str)

        interceptor.on_tool_start = AsyncMock(side_effect=capture_start)
        adapter = MCPToolAdapter(conn, _make_mcp_tool(), interceptor=interceptor)

        raw = '{"key": "value"}'
        await adapter._arun(raw)

        assert captured_input == [raw]

    @pytest.mark.asyncio
    async def test_permission_denied_error_propagates(self) -> None:
        """A PermissionDeniedError from on_tool_start must not be swallowed."""
        conn = _make_mock_connection()
        conn.call_tool = AsyncMock(return_value=_make_call_tool_result("ok"))
        interceptor = _make_mock_interceptor(deny=True)

        adapter = MCPToolAdapter(conn, _make_mcp_tool(), interceptor=interceptor)

        with pytest.raises(PermissionDeniedError):
            await adapter._arun("{}")

        # call_tool must NOT have been called when interceptor denies.
        conn.call_tool.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_on_tool_error_called_when_call_tool_raises(self) -> None:
        """on_tool_error must be awaited when call_tool raises an exception."""
        conn = _make_mock_connection()
        exc = MCPToolError("my_tool", "test-server", "boom")
        conn.call_tool = AsyncMock(side_effect=exc)
        interceptor = _make_mock_interceptor()

        adapter = MCPToolAdapter(conn, _make_mcp_tool(), interceptor=interceptor)

        with pytest.raises(MCPToolError):
            await adapter._arun("{}")

        interceptor.on_tool_error.assert_awaited_once()
        interceptor.on_tool_end.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_on_tool_error_called_when_is_error_true(self) -> None:
        """on_tool_error must be awaited when isError=True (tool-level error)."""
        conn = _make_mock_connection()
        conn.call_tool = AsyncMock(
            return_value=_make_call_tool_result("failure", is_error=True)
        )
        interceptor = _make_mock_interceptor()

        adapter = MCPToolAdapter(conn, _make_mcp_tool(), interceptor=interceptor)

        with pytest.raises(MCPToolError):
            await adapter._arun("{}")

        interceptor.on_tool_error.assert_awaited_once()
        interceptor.on_tool_end.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_interceptor_call_tool_still_works(self) -> None:
        """Without an interceptor the adapter must still call call_tool correctly."""
        conn = _make_mock_connection()
        conn.call_tool = AsyncMock(return_value=_make_call_tool_result("result"))
        adapter = MCPToolAdapter(conn, _make_mcp_tool())  # no interceptor

        output = await adapter._arun('{"q": "hello"}')

        assert output == "result"
        conn.call_tool.assert_awaited_once_with(
            name="my_tool",
            arguments={"q": "hello"},
        )


# ---------------------------------------------------------------------------
# _run (sync wrapper)
# ---------------------------------------------------------------------------


class TestRun:
    """Tests for the synchronous _run method."""

    def test_run_delegates_to_arun_via_asyncio(self) -> None:
        """_run must call asyncio.run() with the async version and return a
        string."""
        import asyncio
        import inspect

        conn = _make_mock_connection()
        conn.call_tool = AsyncMock(return_value=_make_call_tool_result("sync result"))
        adapter = MCPToolAdapter(conn, _make_mcp_tool())

        received_coro: list[Any] = []

        def capture_and_close(coro: Any) -> str:
            """Capture the coroutine, close it to avoid leaks, return mock result."""
            received_coro.append(coro)
            if inspect.iscoroutine(coro):
                coro.close()  # prevent ResourceWarning
            return "sync result"

        # Patch asyncio.run to avoid nested event-loop issues in test runner.
        with patch(
            "lightagent.mcp.adapter.asyncio.run",
            side_effect=capture_and_close,
        ):
            result = adapter._run("{}")

        assert len(received_coro) == 1
        # The coroutine passed to asyncio.run must be _arun's coroutine object.
        assert asyncio.iscoroutine(received_coro[0]) or callable(received_coro[0])
        assert result == "sync result"

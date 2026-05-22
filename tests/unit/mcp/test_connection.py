"""Unit tests for MCPServerConnection and Pydantic config models.

All MCP SDK I/O calls are mocked so that tests do not require a live server.
Tests focus on *behaviour* — state transitions, error handling, and correct
delegation to the MCP SDK — rather than just verifying mock invocations.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import mcp.types
import pytest
from pydantic import ValidationError

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

from prismal.core.exceptions import MCPConnectionError, MCPToolError
from prismal.mcp.connection import (
    MCPAuthConfig,
    MCPServerConfig,
    MCPServerConnection,
    MCPServerStatus,
)

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _make_stdio_config(**kwargs: object) -> MCPServerConfig:
    """Return a minimal valid stdio MCPServerConfig."""
    defaults: dict[str, object] = {
        "name": "test-stdio",
        "type": "stdio",
        "command": ["python", "-m", "testserver"],
        "timeout_seconds": 5,
        "retry_attempts": 1,
    }
    defaults.update(kwargs)
    return MCPServerConfig.model_validate(defaults)


def _make_sse_config(**kwargs: object) -> MCPServerConfig:
    """Return a minimal valid SSE MCPServerConfig."""
    defaults: dict[str, object] = {
        "name": "test-sse",
        "type": "sse",
        "url": "http://localhost:8080/sse",
        "timeout_seconds": 5,
        "retry_attempts": 1,
    }
    defaults.update(kwargs)
    return MCPServerConfig.model_validate(defaults)


def _make_tool(name: str = "my_tool") -> mcp.types.Tool:
    """Return a minimal mcp.types.Tool object."""
    return mcp.types.Tool(name=name, inputSchema={"type": "object"})


def _make_call_tool_result(is_error: bool = False) -> mcp.types.CallToolResult:
    """Return a minimal mcp.types.CallToolResult."""
    return mcp.types.CallToolResult(
        content=[mcp.types.TextContent(type="text", text="ok")],
        isError=is_error,
    )


@asynccontextmanager
async def _fake_transport(read: object, write: object) -> AsyncGenerator[tuple[object, object]]:
    """Async context manager that yields (read, write) — simulates stdio/sse client."""
    yield read, write


# ---------------------------------------------------------------------------
# MCPAuthConfig tests
# ---------------------------------------------------------------------------


class TestMCPAuthConfig:
    """Tests for MCPAuthConfig model validation."""

    def test_bearer_type_accepted(self) -> None:
        """MCPAuthConfig with type='bearer' must be valid."""
        cfg = MCPAuthConfig(type="bearer", token_env="MY_TOKEN")  # noqa: S106
        assert cfg.type == "bearer"
        assert cfg.token_env == "MY_TOKEN"  # noqa: S105

    def test_token_env_required(self) -> None:
        """MCPAuthConfig without token_env must raise ValidationError."""
        with pytest.raises(ValidationError):
            MCPAuthConfig.model_validate({"type": "bearer"})

    def test_default_type_is_bearer(self) -> None:
        """MCPAuthConfig.type must default to 'bearer'."""
        cfg = MCPAuthConfig(token_env="MY_TOKEN")  # noqa: S106
        assert cfg.type == "bearer"


# ---------------------------------------------------------------------------
# MCPServerConfig tests
# ---------------------------------------------------------------------------


class TestMCPServerConfig:
    """Tests for MCPServerConfig model validation."""

    def test_stdio_config_valid(self) -> None:
        """A complete stdio config must validate without error."""
        cfg = _make_stdio_config()
        assert cfg.name == "test-stdio"
        assert cfg.type == "stdio"
        assert cfg.command == ["python", "-m", "testserver"]
        assert cfg.timeout_seconds == 5
        assert cfg.retry_attempts == 1

    def test_sse_config_valid(self) -> None:
        """A complete SSE config must validate without error."""
        cfg = _make_sse_config()
        assert cfg.type == "sse"
        assert cfg.url == "http://localhost:8080/sse"

    def test_defaults_applied(self) -> None:
        """timeout_seconds=30 and retry_attempts=3 must be the defaults."""
        cfg = MCPServerConfig(
            name="x",
            type="stdio",
            command=["echo"],
        )
        assert cfg.timeout_seconds == 30
        assert cfg.retry_attempts == 3
        assert cfg.enabled is True

    def test_stdio_missing_command_raises(self) -> None:
        """stdio config without command must raise ValidationError."""
        with pytest.raises(ValidationError, match="command"):
            MCPServerConfig(name="bad", type="stdio")

    def test_sse_missing_url_raises(self) -> None:
        """SSE config without url must raise ValidationError."""
        with pytest.raises(ValidationError, match="url"):
            MCPServerConfig(name="bad", type="sse")

    def test_optional_auth_field(self) -> None:
        """MCPServerConfig.auth must be None by default."""
        cfg = _make_sse_config()
        assert cfg.auth is None

    def test_auth_config_stored(self) -> None:
        """Auth config embedded in SSE config must be parsed correctly."""
        cfg = _make_sse_config(auth={"type": "bearer", "token_env": "SSE_TOKEN"})
        assert cfg.auth is not None
        assert cfg.auth.token_env == "SSE_TOKEN"  # noqa: S105

    def test_env_field_accepted(self) -> None:
        """env dict must be stored on stdio config."""
        cfg = _make_stdio_config(env={"FOO": "bar"})
        assert cfg.env == {"FOO": "bar"}

    def test_description_optional(self) -> None:
        """description must default to None."""
        cfg = _make_stdio_config()
        assert cfg.description is None

    def test_description_stored(self) -> None:
        """description must be stored when provided."""
        cfg = _make_stdio_config(description="A helpful server")
        assert cfg.description == "A helpful server"


# ---------------------------------------------------------------------------
# MCPServerStatus tests
# ---------------------------------------------------------------------------


class TestMCPServerStatus:
    """Tests for MCPServerStatus model."""

    def test_status_fields(self) -> None:
        """All MCPServerStatus fields must be stored correctly."""
        st = MCPServerStatus(
            name="srv",
            connected=True,
            tool_count=3,
            error=None,
            server_type="stdio",
        )
        assert st.name == "srv"
        assert st.connected is True
        assert st.tool_count == 3
        assert st.error is None
        assert st.server_type == "stdio"

    def test_default_tool_count(self) -> None:
        """tool_count must default to 0."""
        st = MCPServerStatus(name="srv", connected=False, server_type="sse")
        assert st.tool_count == 0

    def test_error_stored(self) -> None:
        """error message must be stored."""
        st = MCPServerStatus(
            name="srv",
            connected=False,
            error="Connection refused",
            server_type="stdio",
        )
        assert st.error == "Connection refused"


# ---------------------------------------------------------------------------
# MCPServerConnection — initial state
# ---------------------------------------------------------------------------


class TestMCPServerConnectionInit:
    """Tests for MCPServerConnection initial state before connect()."""

    def test_not_connected_initially(self) -> None:
        """A fresh MCPServerConnection must not be connected."""
        conn = MCPServerConnection(_make_stdio_config())
        assert conn.connected is False

    def test_status_reflects_not_connected(self) -> None:
        """status property must reflect disconnected state initially."""
        conn = MCPServerConnection(_make_stdio_config())
        st = conn.status
        assert st.connected is False
        assert st.tool_count == 0
        assert st.error is None
        assert st.name == "test-stdio"
        assert st.server_type == "stdio"


# ---------------------------------------------------------------------------
# MCPServerConnection — stdio transport
# ---------------------------------------------------------------------------


class TestStdioConnect:
    """Tests for stdio transport connect/disconnect."""

    @pytest.fixture
    def mock_session(self) -> AsyncMock:
        """Return a pre-configured mock ClientSession."""
        session = AsyncMock(spec=mcp.ClientSession)
        session.initialize = AsyncMock(return_value=None)
        list_result = MagicMock()
        list_result.tools = [_make_tool("tool_a"), _make_tool("tool_b")]
        session.list_tools = AsyncMock(return_value=list_result)
        return session

    @pytest.mark.asyncio
    async def test_connect_sets_connected_true(self, mock_session: AsyncMock) -> None:
        """connect() must mark the connection as connected on success."""
        cfg = _make_stdio_config()
        conn = MCPServerConnection(cfg)

        read_mock, write_mock = MagicMock(), MagicMock()

        with (
            patch(
                "prismal.mcp.connection.stdio_client",
                return_value=_fake_transport(read_mock, write_mock),
            ),
            patch("prismal.mcp.connection.ClientSession") as mock_cls,
        ):
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            await conn.connect()

        assert conn.connected is True

    @pytest.mark.asyncio
    async def test_connect_caches_tools(self, mock_session: AsyncMock) -> None:
        """connect() must populate the cached tool list."""
        cfg = _make_stdio_config()
        conn = MCPServerConnection(cfg)

        read_mock, write_mock = MagicMock(), MagicMock()

        with (
            patch(
                "prismal.mcp.connection.stdio_client",
                return_value=_fake_transport(read_mock, write_mock),
            ),
            patch(
                "prismal.mcp.connection.ClientSession",
            ) as mock_cls,
        ):
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            await conn.connect()

        assert conn.status.tool_count == 2

    @pytest.mark.asyncio
    async def test_disconnect_sets_connected_false(self, mock_session: AsyncMock) -> None:
        """disconnect() must set connected to False."""
        cfg = _make_stdio_config()
        conn = MCPServerConnection(cfg)
        read_mock, write_mock = MagicMock(), MagicMock()

        with (
            patch(
                "prismal.mcp.connection.stdio_client",
                return_value=_fake_transport(read_mock, write_mock),
            ),
            patch("prismal.mcp.connection.ClientSession") as mock_cls,
        ):
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)
            await conn.connect()

        assert conn.connected is True
        await conn.disconnect()
        assert conn.connected is False
        assert conn.status.tool_count == 0

    @pytest.mark.asyncio
    async def test_list_tools_returns_cached(self, mock_session: AsyncMock) -> None:
        """list_tools() must return the cached list after connect()."""
        cfg = _make_stdio_config()
        conn = MCPServerConnection(cfg)
        read_mock, write_mock = MagicMock(), MagicMock()

        with (
            patch(
                "prismal.mcp.connection.stdio_client",
                return_value=_fake_transport(read_mock, write_mock),
            ),
            patch("prismal.mcp.connection.ClientSession") as mock_cls,
        ):
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)
            await conn.connect()

        tools = await conn.list_tools()
        assert len(tools) == 2
        assert tools[0].name == "tool_a"
        assert tools[1].name == "tool_b"

    @pytest.mark.asyncio
    async def test_call_tool_returns_result(self, mock_session: AsyncMock) -> None:
        """call_tool() must return the result from the session."""
        cfg = _make_stdio_config()
        conn = MCPServerConnection(cfg)
        expected = _make_call_tool_result()
        mock_session.call_tool = AsyncMock(return_value=expected)
        read_mock, write_mock = MagicMock(), MagicMock()

        with (
            patch(
                "prismal.mcp.connection.stdio_client",
                return_value=_fake_transport(read_mock, write_mock),
            ),
            patch("prismal.mcp.connection.ClientSession") as mock_cls,
        ):
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)
            await conn.connect()

        result = await conn.call_tool("tool_a", {"x": 1})
        assert result is expected
        assert not result.isError


# ---------------------------------------------------------------------------
# MCPServerConnection — SSE transport
# ---------------------------------------------------------------------------


class TestSSEConnect:
    """Tests for SSE transport connect with bearer auth (AC-004-9)."""

    @pytest.mark.asyncio
    async def test_sse_connect_without_auth(self) -> None:
        """SSE connect without auth must not include Authorization header."""
        cfg = _make_sse_config()
        conn = MCPServerConnection(cfg)

        session = AsyncMock(spec=mcp.ClientSession)
        session.initialize = AsyncMock(return_value=None)
        list_result = MagicMock()
        list_result.tools = []
        session.list_tools = AsyncMock(return_value=list_result)

        read_mock, write_mock = MagicMock(), MagicMock()
        captured_headers: dict[str, str] = {}

        @asynccontextmanager
        async def fake_sse(
            url: str,
            headers: dict[str, str],
            timeout: int,
        ) -> AsyncGenerator[tuple[object, object]]:
            captured_headers.update(headers)
            yield read_mock, write_mock

        with (
            patch("prismal.mcp.connection.sse_client", fake_sse),
            patch("prismal.mcp.connection.ClientSession") as mock_cls,
        ):
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=session)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)
            await conn.connect()

        assert "Authorization" not in captured_headers
        assert conn.connected is True

    @pytest.mark.asyncio
    async def test_sse_connect_with_bearer_auth(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """SSE connect with bearer auth must pass Authorization header (AC-004-9)."""
        monkeypatch.setenv("SSE_TOKEN", "super-secret-token")
        cfg = _make_sse_config(auth={"type": "bearer", "token_env": "SSE_TOKEN"})
        conn = MCPServerConnection(cfg)

        session = AsyncMock(spec=mcp.ClientSession)
        session.initialize = AsyncMock(return_value=None)
        list_result = MagicMock()
        list_result.tools = []
        session.list_tools = AsyncMock(return_value=list_result)

        read_mock, write_mock = MagicMock(), MagicMock()
        captured_headers: dict[str, str] = {}

        @asynccontextmanager
        async def fake_sse(
            url: str,
            headers: dict[str, str],
            timeout: int,
        ) -> AsyncGenerator[tuple[object, object]]:
            captured_headers.update(headers)
            yield read_mock, write_mock

        with (
            patch("prismal.mcp.connection.sse_client", fake_sse),
            patch("prismal.mcp.connection.ClientSession") as mock_cls,
        ):
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=session)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)
            await conn.connect()

        assert captured_headers.get("Authorization") == "Bearer super-secret-token"
        assert conn.connected is True

    @pytest.mark.asyncio
    async def test_sse_bearer_token_env_not_set_warns_but_connects(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Missing bearer token env var must log a warning but still attempt connect."""
        # Ensure the env var is absent
        monkeypatch.delenv("MISSING_TOKEN_ENV", raising=False)
        cfg = _make_sse_config(auth={"type": "bearer", "token_env": "MISSING_TOKEN_ENV"})
        conn = MCPServerConnection(cfg)

        session = AsyncMock(spec=mcp.ClientSession)
        session.initialize = AsyncMock(return_value=None)
        list_result = MagicMock()
        list_result.tools = []
        session.list_tools = AsyncMock(return_value=list_result)

        read_mock, write_mock = MagicMock(), MagicMock()

        @asynccontextmanager
        async def fake_sse(
            url: str,
            headers: dict[str, str],
            timeout: int,
        ) -> AsyncGenerator[tuple[object, object]]:
            yield read_mock, write_mock

        with (
            patch("prismal.mcp.connection.sse_client", fake_sse),
            patch("prismal.mcp.connection.ClientSession") as mock_cls,
        ):
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=session)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)
            # Should not raise; logs a warning internally
            await conn.connect()

        # The connection still proceeds even with an empty token
        assert conn.connected is True


# ---------------------------------------------------------------------------
# Error handling — AC-004-5
# ---------------------------------------------------------------------------


class TestConnectionErrorHandling:
    """Tests that connection errors are contained, not re-raised (AC-004-5)."""

    @pytest.mark.asyncio
    async def test_connect_failure_marks_unavailable(self) -> None:
        """connect() failure must set connected=False and record error."""
        cfg = _make_stdio_config(retry_attempts=1)
        conn = MCPServerConnection(cfg)

        with patch(
            "prismal.mcp.connection.stdio_client",
            side_effect=OSError("Process failed to start"),
        ):
            await conn.connect()  # must NOT raise

        assert conn.connected is False
        assert conn.status.error is not None
        assert "Process failed to start" in conn.status.error

    @pytest.mark.asyncio
    async def test_connect_failure_does_not_crash(self) -> None:
        """connect() must never propagate exceptions to the caller."""
        cfg = _make_stdio_config(retry_attempts=1)
        conn = MCPServerConnection(cfg)

        with patch(
            "prismal.mcp.connection.stdio_client",
            side_effect=RuntimeError("unexpected error"),
        ):
            # Must complete without raising
            await asyncio.wait_for(conn.connect(), timeout=5)

        assert conn.connected is False

    @pytest.mark.asyncio
    async def test_list_tools_raises_when_not_connected(self) -> None:
        """list_tools() on a disconnected connection must raise MCPConnectionError."""
        conn = MCPServerConnection(_make_stdio_config())
        with pytest.raises(MCPConnectionError, match="Not connected"):
            await conn.list_tools()

    @pytest.mark.asyncio
    async def test_call_tool_raises_when_not_connected(self) -> None:
        """call_tool() on a disconnected connection must raise MCPConnectionError."""
        conn = MCPServerConnection(_make_stdio_config())
        with pytest.raises(MCPConnectionError, match="Not connected"):
            await conn.call_tool("tool_a", {})

    @pytest.mark.asyncio
    async def test_session_error_during_call_tool_raises_mcp_tool_error(
        self,
    ) -> None:
        """call_tool() must wrap unexpected session errors in MCPToolError."""
        cfg = _make_stdio_config()
        conn = MCPServerConnection(cfg)

        session = AsyncMock(spec=mcp.ClientSession)
        session.initialize = AsyncMock(return_value=None)
        list_result = MagicMock()
        list_result.tools = []
        session.list_tools = AsyncMock(return_value=list_result)
        session.call_tool = AsyncMock(side_effect=ValueError("bad arg"))

        read_mock, write_mock = MagicMock(), MagicMock()

        with (
            patch(
                "prismal.mcp.connection.stdio_client",
                return_value=_fake_transport(read_mock, write_mock),
            ),
            patch("prismal.mcp.connection.ClientSession") as mock_cls,
        ):
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=session)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)
            await conn.connect()

        with pytest.raises(MCPToolError, match="bad arg"):
            await conn.call_tool("tool_a", {})

    @pytest.mark.asyncio
    async def test_disconnect_still_marks_disconnected_when_aclose_raises(
        self,
    ) -> None:
        """disconnect() must set connected=False even if _exit_stack.aclose() raises."""
        cfg = _make_stdio_config()
        conn = MCPServerConnection(cfg)

        session = AsyncMock(spec=mcp.ClientSession)
        session.initialize = AsyncMock(return_value=None)
        list_result = MagicMock()
        list_result.tools = []
        session.list_tools = AsyncMock(return_value=list_result)

        read_mock, write_mock = MagicMock(), MagicMock()

        with (
            patch(
                "prismal.mcp.connection.stdio_client",
                return_value=_fake_transport(read_mock, write_mock),
            ),
            patch("prismal.mcp.connection.ClientSession") as mock_cls,
        ):
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=session)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)
            await conn.connect()

        assert conn.connected is True

        # Patch aclose on the live exit stack to simulate a cleanup failure.
        assert conn._exit_stack is not None
        conn._exit_stack.aclose = AsyncMock(  # type: ignore[method-assign]
            side_effect=RuntimeError("cleanup failed")
        )

        # disconnect() must not propagate the exception.
        await conn.disconnect()

        assert conn.connected is False


# ---------------------------------------------------------------------------
# Timeout enforcement — AC-004-6
# ---------------------------------------------------------------------------


class TestTimeoutEnforcement:
    """Tests that per-server timeouts are enforced (AC-004-6)."""

    @pytest.mark.asyncio
    async def test_call_tool_timeout_raises_mcp_tool_error(self) -> None:
        """A call_tool() that exceeds timeout_seconds must raise MCPToolError."""
        cfg = _make_stdio_config(timeout_seconds=1)
        conn = MCPServerConnection(cfg)

        async def slow_call(*_args: object, **_kwargs: object) -> mcp.types.CallToolResult:
            await asyncio.sleep(10)  # deliberate timeout
            return _make_call_tool_result()

        session = AsyncMock(spec=mcp.ClientSession)
        session.initialize = AsyncMock(return_value=None)
        list_result = MagicMock()
        list_result.tools = []
        session.list_tools = AsyncMock(return_value=list_result)
        session.call_tool = slow_call

        read_mock, write_mock = MagicMock(), MagicMock()

        with (
            patch(
                "prismal.mcp.connection.stdio_client",
                return_value=_fake_transport(read_mock, write_mock),
            ),
            patch("prismal.mcp.connection.ClientSession") as mock_cls,
        ):
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=session)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)
            await conn.connect()

        with pytest.raises(MCPToolError, match="timed out"):
            await conn.call_tool("slow_tool", {})


# ---------------------------------------------------------------------------
# Retry behaviour
# ---------------------------------------------------------------------------


class TestRetryBehaviour:
    """Tests for retry logic on transient failures."""

    @pytest.mark.asyncio
    async def test_retries_on_failure(self) -> None:
        """connect() must retry up to retry_attempts times before giving up."""
        cfg = _make_stdio_config(retry_attempts=3)
        conn = MCPServerConnection(cfg)
        call_count = 0

        @asynccontextmanager
        async def failing_transport(
            *_args: object, **_kwargs: object
        ) -> AsyncGenerator[tuple[object, object]]:
            nonlocal call_count
            call_count += 1
            raise OSError("Transient error")
            yield MagicMock(), MagicMock()  # unreachable; needed for generator

        with patch("prismal.mcp.connection.stdio_client", failing_transport):
            await conn.connect()

        assert call_count == 3
        assert conn.connected is False

    @pytest.mark.asyncio
    async def test_succeeds_on_second_attempt(self) -> None:
        """connect() must succeed on the first successful attempt."""
        cfg = _make_stdio_config(retry_attempts=3)
        conn = MCPServerConnection(cfg)
        call_count = 0

        session = AsyncMock(spec=mcp.ClientSession)
        session.initialize = AsyncMock(return_value=None)
        list_result = MagicMock()
        list_result.tools = []
        session.list_tools = AsyncMock(return_value=list_result)

        @asynccontextmanager
        async def flaky_transport(
            *_args: object, **_kwargs: object
        ) -> AsyncGenerator[tuple[object, object]]:
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise OSError("Transient error")
            yield MagicMock(), MagicMock()

        with (
            patch("prismal.mcp.connection.stdio_client", flaky_transport),
            patch("prismal.mcp.connection.ClientSession") as mock_cls,
        ):
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=session)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)
            await conn.connect()

        assert call_count == 2
        assert conn.connected is True


# ---------------------------------------------------------------------------
# Status property
# ---------------------------------------------------------------------------


class TestStatusProperty:
    """Tests that the status property returns correct MCPServerStatus."""

    def test_status_name_matches_config(self) -> None:
        """status.name must match the config name."""
        cfg = _make_stdio_config(name="my-server")
        conn = MCPServerConnection(cfg)
        assert conn.status.name == "my-server"

    def test_status_server_type_matches_config(self) -> None:
        """status.server_type must reflect the transport type."""
        cfg = _make_stdio_config()
        conn = MCPServerConnection(cfg)
        assert conn.status.server_type == "stdio"

    def test_status_sse_type(self) -> None:
        """status.server_type must be 'sse' for SSE configs."""
        cfg = _make_sse_config()
        conn = MCPServerConnection(cfg)
        assert conn.status.server_type == "sse"

    @pytest.mark.asyncio
    async def test_status_after_failed_connect(self) -> None:
        """status must reflect the last error after a failed connect()."""
        cfg = _make_stdio_config(retry_attempts=1)
        conn = MCPServerConnection(cfg)

        with patch(
            "prismal.mcp.connection.stdio_client",
            side_effect=OSError("connection refused"),
        ):
            await conn.connect()

        st = conn.status
        assert st.connected is False
        assert st.error is not None
        assert "connection refused" in st.error
        assert st.tool_count == 0

    @pytest.mark.asyncio
    async def test_status_connected_after_successful_connect(self) -> None:
        """status.connected must be True after a successful connect()."""
        cfg = _make_stdio_config()
        conn = MCPServerConnection(cfg)

        session = AsyncMock(spec=mcp.ClientSession)
        session.initialize = AsyncMock(return_value=None)
        list_result = MagicMock()
        list_result.tools = [_make_tool("t1")]
        session.list_tools = AsyncMock(return_value=list_result)

        read_mock, write_mock = MagicMock(), MagicMock()

        with (
            patch(
                "prismal.mcp.connection.stdio_client",
                return_value=_fake_transport(read_mock, write_mock),
            ),
            patch("prismal.mcp.connection.ClientSession") as mock_cls,
        ):
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=session)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)
            await conn.connect()

        st = conn.status
        assert st.connected is True
        assert st.error is None
        assert st.tool_count == 1


# ---------------------------------------------------------------------------
# Graceful shutdown — CancelledError suppression (regression: Ctrl+C fix)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cleanup_stack_suppresses_cancelled_error() -> None:
    """_cleanup_stack must swallow CancelledError so Ctrl+C does not crash uvicorn."""
    conn = MCPServerConnection(_make_stdio_config())

    mock_stack = AsyncMock()
    mock_stack.aclose = AsyncMock(side_effect=asyncio.CancelledError("test cancel"))
    conn._exit_stack = mock_stack
    conn._connected = True

    # Must NOT raise — the CancelledError should be caught and logged.
    await conn._cleanup_stack()

    assert conn._connected is False
    assert conn._exit_stack is None
    assert conn._session is None


@pytest.mark.asyncio
async def test_cleanup_stack_suppresses_base_exception() -> None:
    """_cleanup_stack must swallow any BaseException during teardown."""
    conn = MCPServerConnection(_make_stdio_config())

    mock_stack = AsyncMock()
    mock_stack.aclose = AsyncMock(side_effect=BaseException("unexpected"))
    conn._exit_stack = mock_stack
    conn._connected = True

    await conn._cleanup_stack()  # must not raise

    assert conn._connected is False


@pytest.mark.asyncio
async def test_disconnect_suppresses_cancelled_error() -> None:
    """disconnect() must complete cleanly even when the transport raises CancelledError."""
    conn = MCPServerConnection(_make_stdio_config())

    mock_stack = AsyncMock()
    mock_stack.aclose = AsyncMock(side_effect=asyncio.CancelledError("cancel on stop"))
    conn._exit_stack = mock_stack
    conn._connected = True
    conn._cached_tools = [_make_tool("t1")]

    await conn.disconnect()  # must not raise

    assert conn._connected is False
    assert conn._cached_tools == []


@pytest.mark.asyncio
async def test_cleanup_stack_suppresses_anyio_cross_task_runtime_error() -> None:
    """_cleanup_stack swallows anyio cross-task cancel scope RuntimeError.

    Reproduces the OpenBB scenario: killing the subprocess before aclose()
    used to leave anyio background tasks in a broken-pipe state, causing
    RuntimeError: 'Attempted to exit cancel scope in a different task'.
    The new order (aclose first, kill after) avoids the root cause, but
    the suppression guard must still handle it if it occurs.
    """
    conn = MCPServerConnection(_make_stdio_config())

    mock_stack = AsyncMock()
    mock_stack.aclose = AsyncMock(
        side_effect=RuntimeError(
            "Attempted to exit cancel scope in a different task than it was entered in"
        )
    )
    conn._exit_stack = mock_stack
    conn._connected = True

    await conn._cleanup_stack()  # must not raise

    assert conn._connected is False
    assert conn._exit_stack is None


@pytest.mark.asyncio
async def test_cleanup_stack_kills_processes_after_aclose() -> None:
    """_cleanup_stack calls _kill_process_tree AFTER aclose(), not before.

    Ensures the order is: aclose() → kill, so anyio task groups can exit
    cleanly before their subprocess is terminated.
    """
    from unittest.mock import patch

    conn = MCPServerConnection(_make_stdio_config())
    conn._subprocess_pids = [1234, 5678]

    call_order: list[str] = []

    async def _tracked_aclose() -> None:
        call_order.append("aclose")

    mock_stack = AsyncMock()
    mock_stack.aclose = _tracked_aclose
    conn._exit_stack = mock_stack
    conn._connected = True

    with patch(
        "prismal.mcp.connection._kill_process_tree",
        side_effect=lambda _pids: call_order.append("kill"),
    ):
        await conn._cleanup_stack()

    assert call_order == ["aclose", "kill"], f"Expected aclose() before kill, got: {call_order}"
    assert conn._connected is False


@pytest.mark.asyncio
async def test_do_connect_timeout_with_process_lookup_error_on_cleanup() -> None:
    """_do_connect suppresses ProcessLookupError from stack.aclose() on timeout.

    Reproduces the context7 scenario: session.initialize() times out, then
    the MCP SDK's stdio_client cleanup calls terminate_posix_process_tree on
    an already-dead process, raising ProcessLookupError.  Only MCPConnectionError
    should be raised to the caller — the chained ProcessLookupError must be
    suppressed.
    """
    from contextlib import asynccontextmanager

    cfg = _make_stdio_config(timeout_seconds=1, retry_attempts=1)
    conn = MCPServerConnection(cfg)

    @asynccontextmanager
    async def _timeout_client(_params: object):  # type: ignore[override]
        """Simulate a stdio_client that raises TimeoutError on initialize, then
        ProcessLookupError during cleanup (process already dead)."""
        mock_stack = AsyncMock()
        mock_stack.aclose = AsyncMock(side_effect=ProcessLookupError(3, "No such process"))
        yield (AsyncMock(), AsyncMock())

    timeout_session = AsyncMock()
    timeout_session.initialize = AsyncMock(side_effect=TimeoutError("timed out"))

    with (
        patch("prismal.mcp.connection.stdio_client", _timeout_client),
        patch(
            "prismal.mcp.connection.ClientSession",
            return_value=AsyncMock(
                __aenter__=AsyncMock(return_value=timeout_session),
                __aexit__=AsyncMock(side_effect=ProcessLookupError(3, "No such process")),
            ),
        ),
    ):
        # connect() must NOT raise — errors are contained (AC-004-5)
        await conn.connect()

    assert conn.connected is False
    assert conn.status.error is not None

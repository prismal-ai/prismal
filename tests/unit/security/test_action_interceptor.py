"""Unit tests for ActionInterceptor."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from lightagent.core.exceptions import PermissionDeniedError
from lightagent.security.action_interceptor import (
    _TOOL_PERMISSION_MAP,
    ActionInterceptor,
)
from lightagent.security.permissions import PermissionType


@pytest.fixture
def mock_pm() -> AsyncMock:
    """Mock PermissionManager that grants all permissions by default."""
    pm = AsyncMock()
    pm.check = AsyncMock(return_value=True)
    return pm


@pytest.fixture
def mock_audit() -> MagicMock:
    """Mock AuditLogger."""
    return MagicMock()


@pytest.fixture
def interceptor(mock_pm: AsyncMock, mock_audit: MagicMock) -> ActionInterceptor:
    """ActionInterceptor with mocked dependencies."""
    return ActionInterceptor(permission_manager=mock_pm, audit_logger=mock_audit)


def test_tool_permission_map_contains_shell() -> None:
    """_TOOL_PERMISSION_MAP must map 'bash' to SHELL."""
    assert "bash" in _TOOL_PERMISSION_MAP
    assert _TOOL_PERMISSION_MAP["bash"] == PermissionType.SHELL


def test_tool_permission_map_contains_filesystem_write() -> None:
    """_TOOL_PERMISSION_MAP must map 'write_file' to FILESYSTEM_WRITE."""
    assert "write_file" in _TOOL_PERMISSION_MAP
    assert _TOOL_PERMISSION_MAP["write_file"] == PermissionType.FILESYSTEM_WRITE


async def test_tool_start_passes_with_permission(
    interceptor: ActionInterceptor, mock_pm: AsyncMock
) -> None:
    """on_tool_start must call permission check when tool is in the map."""
    mock_pm.check.return_value = True
    await interceptor.on_tool_start({"name": "bash"}, "ls -la")
    mock_pm.check.assert_awaited_once()


async def test_tool_start_raises_without_permission(
    interceptor: ActionInterceptor, mock_pm: AsyncMock
) -> None:
    """on_tool_start must raise PermissionDeniedError when check returns False."""
    mock_pm.check.return_value = False
    with pytest.raises(PermissionDeniedError):
        await interceptor.on_tool_start({"name": "bash"}, "rm -rf /")


async def test_tool_start_unknown_tool_passes_through(
    interceptor: ActionInterceptor, mock_pm: AsyncMock
) -> None:
    """Tools not in the permission map must pass through without a check."""
    await interceptor.on_tool_start({"name": "calculator"}, "2+2")
    mock_pm.check.assert_not_awaited()


async def test_tool_start_missing_name_passes_through(
    interceptor: ActionInterceptor, mock_pm: AsyncMock
) -> None:
    """Tools with missing name must pass through without a check."""
    await interceptor.on_tool_start({}, "input")
    mock_pm.check.assert_not_awaited()


async def test_tool_end_logs_to_audit(
    interceptor: ActionInterceptor, mock_audit: MagicMock
) -> None:
    """on_tool_end must call audit_logger.log_tool_call."""
    await interceptor.on_tool_end("result text")
    mock_audit.log_tool_call.assert_called_once()


async def test_tool_error_logs_to_audit(
    interceptor: ActionInterceptor, mock_audit: MagicMock
) -> None:
    """on_tool_error must call audit_logger.log_tool_call."""
    await interceptor.on_tool_error(ValueError("test error"))
    mock_audit.log_tool_call.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(("tool_name", "expected_perm"), list(_TOOL_PERMISSION_MAP.items()))
async def test_all_mapped_tools_check_permission(
    interceptor: ActionInterceptor,
    mock_pm: AsyncMock,
    tool_name: str,
    expected_perm: PermissionType,
) -> None:
    """Each mapped tool must trigger a permission check for its required permission."""
    mock_pm.check.return_value = True
    await interceptor.on_tool_start({"name": tool_name}, "arg")
    mock_pm.check.assert_awaited_once_with(expected_perm, "*")

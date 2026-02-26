"""Action interceptor — Security Layer L4: pre-tool permission checks.

Extends LangChain's BaseCallbackHandler. Checks PermissionManager before
allowing tool execution and logs all tool events to AuditLogger.

Any is required for LangChain callback signatures (LangChain uses untyped
serialized dicts and Any outputs) — unavoidable, documented per CLAUDE.md.
"""

from __future__ import annotations

import time
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler

from lightagent.core.exceptions import PermissionDeniedError
from lightagent.core.logging import get_logger
from lightagent.security.audit import AuditLogger
from lightagent.security.permissions import PermissionManager, PermissionType

logger = get_logger("lightagent.security.action_interceptor")

# Maps tool names to required PermissionType. Extend when new tools are added.
_TOOL_PERMISSION_MAP: dict[str, PermissionType] = {
    "bash": PermissionType.SHELL,
    "shell": PermissionType.SHELL,
    "terminal": PermissionType.SHELL,
    "write_file": PermissionType.FILESYSTEM_WRITE,
    "create_file": PermissionType.FILESYSTEM_WRITE,
    "delete_file": PermissionType.FILESYSTEM_WRITE,
    "read_file": PermissionType.FILESYSTEM_READ,
    "http_request": PermissionType.NETWORK,
    "web_fetch": PermissionType.NETWORK,
    "database_write": PermissionType.DATABASE_WRITE,
}


class ActionInterceptor(BaseCallbackHandler):
    """LangChain callback that enforces permissions before tool execution.

    Inject via the ``callbacks`` parameter of an AgentExecutor::

        interceptor = ActionInterceptor(permission_manager=pm, audit_logger=audit)
        executor = AgentExecutor(..., callbacks=[interceptor])
    """

    def __init__(
        self,
        permission_manager: PermissionManager,
        audit_logger: AuditLogger | None = None,
    ) -> None:
        """Initialize the interceptor.

        Args:
            permission_manager: Used to check grants before tool execution.
            audit_logger: Optional audit logger. Created with defaults if None.
        """
        super().__init__()
        self._pm = permission_manager
        self._audit = audit_logger or AuditLogger()
        self._tool_start_time: float = 0.0
        self._last_tool_name: str = "unknown"

    async def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,  # noqa: ARG002 — required by LangChain BaseCallbackHandler interface
        **kwargs: Any,  # noqa: ANN401, ARG002 — required by LangChain interface
    ) -> None:
        """Check permissions before a tool executes.

        Args:
            serialized: LangChain tool metadata dict (contains 'name').
            input_str: Tool input string.

        Raises:
            PermissionDeniedError: If a required permission is not granted.
        """
        tool_name = str(serialized.get("name") or "unknown")
        self._last_tool_name = tool_name
        self._tool_start_time = time.monotonic()

        required_perm = _TOOL_PERMISSION_MAP.get(tool_name)
        if required_perm is None:
            return  # not in map — pass through

        has_permission = await self._pm.check(required_perm, "*")
        if not has_permission:
            logger.warning(
                "tool_blocked",
                tool=tool_name,
                required_permission=required_perm.value,
            )
            raise PermissionDeniedError(tool_name, required_perm.value)

        logger.info("tool_allowed", tool=tool_name, permission=required_perm.value)

    async def on_tool_end(
        self,
        output: Any,  # noqa: ANN401 — required by LangChain interface
        **kwargs: Any,  # noqa: ANN401, ARG002 — required by LangChain interface
    ) -> None:
        """Log successful tool execution to audit log.

        Args:
            output: Tool output (any type; stored as string preview).
        """
        duration_ms = int((time.monotonic() - self._tool_start_time) * 1000)
        self._audit.log_tool_call(
            name=self._last_tool_name,
            params={},
            result=str(output),
            duration_ms=duration_ms,
        )

    async def on_tool_error(
        self,
        error: BaseException,
        **kwargs: Any,  # noqa: ANN401, ARG002 — required by LangChain interface
    ) -> None:
        """Log tool errors to audit log.

        Args:
            error: The exception raised during tool execution.
        """
        duration_ms = int((time.monotonic() - self._tool_start_time) * 1000)
        self._audit.log_tool_call(
            name=self._last_tool_name,
            params={},
            result=f"ERROR: {error!r}",
            duration_ms=duration_ms,
        )


__all__ = ["_TOOL_PERMISSION_MAP", "ActionInterceptor"]

"""Action interceptor — Security Layer L4: pre-tool permission checks.

Extends LangChain's BaseCallbackHandler. Checks PermissionManager before
allowing tool execution and logs all tool events to AuditLogger.

Any is required for LangChain callback signatures (LangChain uses untyped
serialized dicts and Any outputs) — unavoidable, documented per CLAUDE.md.
"""

from __future__ import annotations

import time
import uuid
from typing import TYPE_CHECKING, Any

from langchain_core.callbacks import BaseCallbackHandler

from prismal.core.exceptions import PermissionDeniedError
from prismal.core.logging import get_logger
from prismal.security.audit import AuditLogger
from prismal.security.filesystem_guard import FilesystemGuard, PathViolation
from prismal.security.permissions import PermissionManager, PermissionType

if TYPE_CHECKING:
    from pathlib import Path

logger = get_logger("prismal.security.action_interceptor")

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
        # key: run_id str → (tool_name, input_str, start_time)
        self._runs: dict[str, tuple[str, str, float]] = {}

    async def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        **kwargs: Any,
    ) -> None:
        """Check permissions before a tool executes.

        Args:
            serialized: LangChain tool metadata dict (contains 'name').
            input_str: Tool input string.

        Raises:
            PermissionDeniedError: If a required permission is not granted.
        """
        tool_name = str(serialized.get("name") or "unknown")
        run_id_val = str(kwargs.get("run_id") or uuid.uuid4())
        self._runs[run_id_val] = (tool_name, input_str, time.monotonic())

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
        output: Any,
        **kwargs: Any,
    ) -> None:
        """Log successful tool execution to audit log.

        Args:
            output: Tool output (any type; stored as string preview).
        """
        run_id_val = str(kwargs.get("run_id") or "")
        tool_name, input_str, start_time = self._runs.pop(run_id_val, ("unknown", "", 0.0))
        duration_ms = int((time.monotonic() - start_time) * 1000)
        self._audit.log_tool_call(
            name=tool_name,
            params={"input": input_str},
            result=str(output),
            duration_ms=duration_ms,
        )

    async def on_tool_error(
        self,
        error: BaseException,
        **kwargs: Any,
    ) -> None:
        """Log tool errors to audit log.

        Args:
            error: The exception raised during tool execution.
        """
        run_id_val = str(kwargs.get("run_id") or "")
        tool_name, input_str, start_time = self._runs.pop(run_id_val, ("unknown", "", 0.0))
        duration_ms = int((time.monotonic() - start_time) * 1000)
        self._audit.log_tool_call(
            name=tool_name,
            params={"input": input_str},
            result=f"ERROR: {error!r}",
            duration_ms=duration_ms,
        )

    def check_tool_policy(
        self,
        *,
        agent: str,
        tool: str,
        args: dict[str, Any],
        call_count: int,
        policy_engine: Any,
    ) -> Any:
        """Consult the declarative :class:`ToolPolicyEngine` (Phase H — H4-03).

        Returns the :class:`PolicyDecision`. A ``DENY`` is audited and raised as
        :class:`ToolPolicyDenied`; ``ALLOW`` / ``REQUIRE_HITL`` are returned for
        the caller to honour (REQUIRE_HITL routes through ``hitl_gate`` at the
        graph-node level).
        """
        from prismal.core.exceptions import ToolPolicyDenied
        from prismal.security.tool_policy import PolicyEffect

        decision = policy_engine.evaluate(agent=agent, tool=tool, args=args, call_count=call_count)
        if decision.effect is PolicyEffect.DENY:
            self._audit.log_event(
                "tool_policy_denied",
                {"agent": agent, "tool": tool, "rule": decision.rule, "reason": decision.reason},
            )
            logger.warning(
                "tool_policy_denied",
                agent=agent,
                tool=tool,
                rule=decision.rule,
                reason=decision.reason,
            )
            raise ToolPolicyDenied(
                f"tool '{tool}' denied for agent '{agent}' by rule "
                f"{decision.rule}: {decision.reason}"
            )
        return decision

    @staticmethod
    def check_shell(cmd: list[str]) -> bool:
        """Return True if shell execution is permitted by current settings.

        Checks ``settings.shell_enabled`` (``PRISMAL_SHELL_ENABLED`` env var).
        Always call this before spawning any subprocess from application code.

        Args:
            cmd: The command list that would be passed to
                ``asyncio.create_subprocess_exec``.  Used only for logging.

        Returns:
            ``True`` when shell execution is enabled; ``False`` otherwise.
        """
        from prismal.core.config import get_settings

        if not get_settings().shell_enabled:
            logger.warning(
                "shell_execution_blocked",
                cmd=cmd[0] if cmd else "",
            )
            return False
        return True

    @staticmethod
    def check_media_op(
        op: str,
        path: str | Path,
        *,
        workspace_root: str | None = None,
    ) -> bool:
        """Return True if a media read/write at *path* is permitted (Fase F).

        Confines media filesystem operations using :class:`FilesystemGuard`:
        blocked system prefixes are always rejected, and when *workspace_root*
        is given the path must resolve inside it. Always call this before
        reading or writing media to disk from a multimodal agent or loader.

        Args:
            op: ``"read"`` or ``"write"``.
            path: Target media path.
            workspace_root: Optional confinement root. ``None`` only enforces
                the blocked-prefix policy.

        Returns:
            ``True`` when the operation is allowed, ``False`` when blocked.

        Raises:
            ValueError: If *op* is not ``"read"`` or ``"write"``.
        """
        if op not in ("read", "write"):
            raise ValueError(f"unknown media op: {op!r} (expected 'read' or 'write')")
        guard = FilesystemGuard(workspace_root=workspace_root)
        try:
            guard.validate(str(path), write=(op == "write"))
        except PathViolation:
            logger.warning("media_op_blocked", op=op, path=str(path))
            return False
        return True


__all__ = ["_TOOL_PERMISSION_MAP", "ActionInterceptor"]

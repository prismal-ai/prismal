"""MCP → LangChain tool adapter.

Implements ``MCPToolAdapter`` — a LangChain ``BaseTool`` subclass that wraps a
single MCP tool definition, routing ``_arun`` calls through
``MCPServerConnection.call_tool()`` and optionally passing each call through
the ``ActionInterceptor`` before execution.

References:
    - SPEC-004 (AC-004-3, AC-004-4)
    - T-052
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any

import mcp.types
from langchain_core.tools import BaseTool
from pydantic import ConfigDict, PrivateAttr

from lightagent.core.exceptions import MCPToolError
from lightagent.core.logging import get_logger

if TYPE_CHECKING:
    from langchain_core.callbacks import (
        AsyncCallbackManagerForToolRun,
        CallbackManagerForToolRun,
    )

    from lightagent.mcp.connection import MCPServerConnection
    from lightagent.security.action_interceptor import ActionInterceptor

logger = get_logger("lightagent.mcp.adapter")


class MCPToolAdapter(BaseTool):
    """Wraps a single MCP tool as a LangChain ``BaseTool``.

    Routes ``_arun`` to ``MCPServerConnection.call_tool()``.  If an
    ``ActionInterceptor`` is provided every call passes through
    ``on_tool_start`` before execution and ``on_tool_end`` / ``on_tool_error``
    after — satisfying AC-004-4.

    A ``PermissionDeniedError`` raised by the interceptor propagates
    immediately so that the tool is blocked at the security layer.

    Usage::

        adapter = MCPToolAdapter.from_mcp_tool(connection, mcp_tool)
        result = await adapter.arun('{"path": "/tmp/file.txt"}')
    """

    # LangChain BaseTool requires these as class-level Pydantic fields.
    name: str  # MCP tool name
    description: str  # MCP tool description (empty string when None)

    # Allow non-serialisable types stored in PrivateAttr fields.
    model_config = ConfigDict(arbitrary_types_allowed=True)

    # Private, non-Pydantic attributes — excluded from model serialisation.
    _connection: MCPServerConnection = PrivateAttr()
    _mcp_tool: mcp.types.Tool = PrivateAttr()
    _interceptor: ActionInterceptor | None = PrivateAttr(default=None)

    def __init__(
        self,
        connection: MCPServerConnection,
        mcp_tool: mcp.types.Tool,
        interceptor: ActionInterceptor | None = None,
    ) -> None:
        """Initialise the adapter from a live MCP connection and tool definition.

        Args:
            connection: An active ``MCPServerConnection`` used to route calls.
            mcp_tool: The ``mcp.types.Tool`` whose calls this adapter handles.
            interceptor: Optional ``ActionInterceptor`` for permission checks
                and audit logging.  When ``None`` calls are passed through
                without interception.
        """
        super().__init__(
            name=mcp_tool.name,
            description=mcp_tool.description or "",
        )
        self._connection = connection
        self._mcp_tool = mcp_tool
        self._interceptor = interceptor

    # ------------------------------------------------------------------
    # LangChain BaseTool interface
    # ------------------------------------------------------------------

    def _run(
        self,
        tool_input: str,
        run_manager: CallbackManagerForToolRun | None = None,  # noqa: ARG002
    ) -> str:
        """Execute the tool synchronously by running the async version.

        Args:
            tool_input: JSON string or plain string passed by LangChain.
            run_manager: Unused sync callback manager.

        Returns:
            Tool result as a plain string.
        """
        return asyncio.run(self._arun(tool_input))

    async def _arun(
        self,
        tool_input: str,
        run_manager: AsyncCallbackManagerForToolRun | None = None,  # noqa: ARG002
    ) -> str:
        """Execute the MCP tool asynchronously.

        Parses ``tool_input`` as JSON to extract an arguments dict.  If the
        input is not valid JSON (or is not a dict) it is wrapped in
        ``{"input": tool_input}`` so that downstream tools receive a
        consistent mapping.

        If an interceptor is configured its ``on_tool_start`` hook is called
        before the MCP call.  A ``PermissionDeniedError`` from the interceptor
        propagates immediately — the tool is blocked.  ``on_tool_end`` is
        called on success; ``on_tool_error`` on failure.

        Args:
            tool_input: JSON string (or plain string) containing the tool
                arguments.
            run_manager: Unused async callback manager.

        Returns:
            The tool result joined from all ``TextContent`` blocks.

        Raises:
            MCPToolError: If the server returns ``isError=True`` or if the
                underlying call raises.
            PermissionDeniedError: If the interceptor blocks the call.
        """
        # ── Parse arguments ──────────────────────────────────────────────
        args: dict[str, Any]  # Any: MCP JSON-compatible values — unavoidable
        try:
            parsed = json.loads(tool_input)
            args = parsed if isinstance(parsed, dict) else {"input": tool_input}
        except json.JSONDecodeError:
            args = {"input": tool_input}

        # ── Interceptor: pre-call hook ───────────────────────────────────
        if self._interceptor is not None:
            # Any: LangChain callback interface requires dict[str, Any] — unavoidable
            serialized: dict[str, Any] = {
                "name": self.name,
                "description": self.description,
            }
            # May raise PermissionDeniedError — intentional: let it propagate.
            await self._interceptor.on_tool_start(
                serialized=serialized,
                input_str=tool_input,
            )

        # ── MCP call ─────────────────────────────────────────────────────
        try:
            result = await self._connection.call_tool(
                name=self.name,
                arguments=args,
            )
        except Exception as exc:
            logger.error(
                "mcp_adapter_call_error",
                tool=self.name,
                error=str(exc),
            )
            if self._interceptor is not None:
                await self._interceptor.on_tool_error(error=exc)
            raise

        # ── Extract text content ─────────────────────────────────────────
        texts: list[str] = []
        for block in result.content:
            if isinstance(block, mcp.types.TextContent):
                texts.append(block.text)
            else:
                texts.append(str(block))
        output = "\n".join(texts)

        # ── Check for tool-level error flag ──────────────────────────────
        if result.isError:
            error_reason = output or "server reported isError=True"
            logger.warning(
                "mcp_adapter_tool_error",
                tool=self.name,
                reason=error_reason,
            )
            exc_err = MCPToolError(
                tool_name=self.name,
                server_name=self._connection._config.name,
                reason=error_reason,
            )
            if self._interceptor is not None:
                await self._interceptor.on_tool_error(error=exc_err)
            raise exc_err

        # ── Interceptor: post-call hook ──────────────────────────────────
        if self._interceptor is not None:
            await self._interceptor.on_tool_end(output=output)

        logger.debug(
            "mcp_adapter_call_success",
            tool=self.name,
            output_chars=len(output),
        )
        return output

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def from_mcp_tool(
        cls,
        connection: MCPServerConnection,
        tool: mcp.types.Tool,
        interceptor: ActionInterceptor | None = None,
    ) -> MCPToolAdapter:
        """Convenience factory to create an adapter from an MCP tool definition.

        Args:
            connection: The active ``MCPServerConnection`` for the server that
                hosts ``tool``.
            tool: The ``mcp.types.Tool`` to wrap.
            interceptor: Optional ``ActionInterceptor`` for pre-call checks.

        Returns:
            A new ``MCPToolAdapter`` ready to use as a LangChain tool.
        """
        return cls(connection=connection, mcp_tool=tool, interceptor=interceptor)


__all__ = ["MCPToolAdapter"]

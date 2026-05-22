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

import json
import uuid
import warnings
from typing import TYPE_CHECKING, Any

from langchain_core.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, create_model

import mcp.types
from prismal.core.exceptions import MCPToolError
from prismal.core.logging import get_logger
from prismal.monitoring.otel import OTelManager

if TYPE_CHECKING:
    from prismal.mcp.connection import MCPServerConnection
    from prismal.security.action_interceptor import ActionInterceptor

logger = get_logger("prismal.mcp.adapter")
_otel = OTelManager()

# Maps JSON Schema primitive types to Python types used in create_model.
_JSON_TYPE_MAP: dict[str, type] = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "array": list,
    "object": dict,
}


def _build_args_schema(mcp_tool: mcp.types.Tool) -> type[BaseModel] | None:
    """Build a Pydantic ``args_schema`` from an MCP tool's ``inputSchema``.

    Exposes the real MCP parameter names (e.g. ``path``, ``query``) to
    LangChain so the LLM generates correctly-named arguments.  Without this,
    LangChain falls back to a generic ``tool_input: str`` parameter and the
    LLM produces ``{"tool_input": "value"}`` which the adapter can't reliably
    map to the MCP tool's expected fields.

    Args:
        mcp_tool: The MCP tool whose ``inputSchema`` is used.

    Returns:
        A dynamically-created Pydantic ``BaseModel`` subclass, or ``None``
        when the schema has no properties (zero-argument tools).
    """
    schema: dict[str, Any] = mcp_tool.inputSchema or {}  # Any: MCP JSON schema — unavoidable
    properties: dict[str, Any] = schema.get("properties") or {}
    required: list[str] = schema.get("required") or []

    if not properties:
        return None

    fields: dict[str, Any] = {}
    for field_name, prop in properties.items():
        if not isinstance(prop, dict):
            continue
        raw_type = prop.get("type", "string")
        py_type: type = _JSON_TYPE_MAP.get(raw_type if isinstance(raw_type, str) else "string", str)
        description: str = prop.get("description") or ""
        if field_name in required:
            fields[field_name] = (py_type, Field(..., description=description))
        else:
            fields[field_name] = (
                py_type | None,
                Field(default=None, description=description),
            )

    if not fields:
        return None

    try:
        # Suppress "Field name X shadows an attribute in parent BaseModel" warnings
        # that arise when an MCP tool parameter uses a reserved Pydantic name (e.g.
        # "json", "dict", "schema").  The model still functions correctly; the
        # warning is cosmetic and originates from the external tool schema.
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message=r"Field name .* shadows an attribute in parent",
                category=UserWarning,
            )
            return create_model(f"MCPInput_{mcp_tool.name}", **fields)
    except Exception as exc:  # broad: pydantic create_model can raise for exotic schemas
        logger.debug(
            "mcp_adapter_schema_build_failed",
            tool=mcp_tool.name,
            error=str(exc),
        )
        return None


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
            args_schema=_build_args_schema(mcp_tool),
        )
        self._connection = connection
        # Retained for future use (e.g. inputSchema validation).
        self._mcp_tool = mcp_tool
        self._interceptor = interceptor

    # ------------------------------------------------------------------
    # LangChain BaseTool interface
    # ------------------------------------------------------------------

    def _run(
        self,
        tool_input: str,
        run_manager: Any | None = None,
    ) -> str:
        """Synchronous execution is not supported for MCP tools.

        MCPToolAdapter requires async execution. Use arun() or ainvoke() instead.

        Raises:
            NotImplementedError: Always raised. Use _arun instead.
        """
        raise NotImplementedError(
            "MCPToolAdapter does not support synchronous execution. "
            "Use arun() or ainvoke() instead."
        )

    async def _arun(
        self,
        tool_input: str = "",
        run_manager: Any | None = None,  # noqa: ARG002 — LangChain interface
        **kwargs: Any,
    ) -> str:
        """Execute the MCP tool asynchronously.

        When ``args_schema`` is set (the normal case), LangChain validates the
        LLM's argument dict against the schema and dispatches the fields as
        ``**kwargs`` — these are passed directly to the MCP server.

        Falls back to parsing ``tool_input`` as JSON for legacy callers that
        pass a raw string (e.g. direct ``arun("...")`` calls).

        If an interceptor is configured its ``on_tool_start`` hook is called
        before the MCP call.  A ``PermissionDeniedError`` from the interceptor
        propagates immediately — the tool is blocked.  ``on_tool_end`` is
        called on success; ``on_tool_error`` on failure.

        Args:
            tool_input: Legacy JSON string path.  Empty when LangChain
                dispatches via ``**kwargs`` (the normal path with args_schema).
            run_manager: Unused async callback manager.
            **kwargs: MCP tool arguments dispatched by LangChain when
                ``args_schema`` is set.

        Returns:
            The tool result joined from all ``TextContent`` blocks.

        Raises:
            MCPToolError: If the server returns ``isError=True`` or if the
                underlying call raises.
            PermissionDeniedError: If the interceptor blocks the call.
        """
        # ── Correlation ID for audit logging ─────────────────────────────
        run_id = str(uuid.uuid4())

        # ── Parse arguments ──────────────────────────────────────────────
        args: dict[str, Any]  # Any: MCP JSON-compatible values — unavoidable
        if kwargs:
            # Primary path: LangChain dispatched validated args_schema fields as **kwargs.
            # Strip None values — optional fields not provided by the LLM are set to None
            # by Pydantic defaults but must be omitted entirely from the MCP call (sending
            # null for a number/string field fails MCP server schema validation).
            args = {k: v for k, v in kwargs.items() if v is not None}
        elif tool_input:
            # Legacy path: raw JSON string (e.g. direct arun("...") calls)
            try:
                parsed = json.loads(tool_input)
                args = parsed if isinstance(parsed, dict) else {"input": tool_input}
            except json.JSONDecodeError:
                args = {"input": tool_input}
        else:
            args = {}

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
                run_id=run_id,
            )

        # ── MCP call (wrapped in OTEL span) ──────────────────────────────
        server_name: str = self._connection._config.name
        with _otel.start_span("mcp.tool_call") as span:
            span.set_attribute("prismal.mcp.server_name", server_name)
            span.set_attribute("prismal.mcp.tool_name", self.name)
            span.set_attribute("prismal.mcp.input_len", len(tool_input))

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
                    await self._interceptor.on_tool_error(error=exc, run_id=run_id)
                raise

            # ── Extract text content ─────────────────────────────────────
            texts: list[str] = []
            for block in result.content:
                if isinstance(block, mcp.types.TextContent):
                    texts.append(block.text)
                else:
                    texts.append(str(block))
            output = "\n".join(texts)

            # ── Check for tool-level error flag ──────────────────────────
            if result.isError:
                error_reason = output or "server reported isError=True"
                logger.warning(
                    "mcp_adapter_tool_error",
                    tool=self.name,
                    reason=error_reason,
                )
                exc_err = MCPToolError(
                    tool_name=self.name,
                    server_name=server_name,
                    reason=error_reason,
                )
                if self._interceptor is not None:
                    await self._interceptor.on_tool_error(error=exc_err, run_id=run_id)
                raise exc_err

            _otel.increment_counter(
                "mcp_tool_calls",
                attributes={"server": server_name, "tool": self.name},
            )

        # ── Interceptor: post-call hook ──────────────────────────────────
        if self._interceptor is not None:
            await self._interceptor.on_tool_end(output=output, run_id=run_id)

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

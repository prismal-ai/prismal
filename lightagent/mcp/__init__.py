"""MCP (Model Context Protocol) client — public API.

Provides connection management, multi-server orchestration, and LangChain
tool adapters for MCP servers configured in config/mcp_servers.yaml.

Quick start::

    from lightagent.mcp import MCPClientManager
    from pathlib import Path

    manager = MCPClientManager()
    await manager.load_from_config(Path("config/mcp_servers.yaml"))
    tools = manager.get_all_langchain_tools()
"""

from lightagent.mcp.adapter import MCPToolAdapter
from lightagent.mcp.client import MCPClientManager
from lightagent.mcp.connection import (
    MCPAuthConfig,
    MCPServerConfig,
    MCPServerConnection,
    MCPServerStatus,
)

__all__ = [
    "MCPAuthConfig",
    "MCPClientManager",
    "MCPServerConfig",
    "MCPServerConnection",
    "MCPServerStatus",
    "MCPToolAdapter",
]

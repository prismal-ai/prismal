"""Embedded MCP servers shipped with LightAgent.

Each server in this package is a standalone FastMCP application that can be
launched as a subprocess via stdio transport::

    python -m lightagent.mcp.servers.datetime_server

Servers in this package intentionally import **no** ``lightagent`` internals
so that they remain lightweight and self-contained when run as subprocesses.
"""

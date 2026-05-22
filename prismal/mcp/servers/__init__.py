"""Embedded MCP servers shipped with Prismal.

Each server in this package is a standalone FastMCP application that can be
launched as a subprocess via stdio transport::

    python -m prismal.mcp.servers.datetime_server

Servers in this package intentionally import **no** ``prismal`` internals
so that they remain lightweight and self-contained when run as subprocesses.
"""

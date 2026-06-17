"""Expose prismal as an A2A agent — inbound handler demo (Phase I).

Drives the inbound side without a web server or any LLM:

1. Build the Agent Card from the capability registry (served at
   ``/.well-known/agent-card.json``).
2. Dispatch an A2A ``message/send`` JSON-RPC request through ``A2AServerHandler``
   onto a tiny echo graph (stands in for the real compiled supervisor graph).
3. Inspect the returned task + artifacts, then stream the same task as SSE.

In production the host (``prismal-server``) mounts the HTTP routes, validates the
caller's auth into an ``AuthContext``, and passes ``await get_async_compiled_graph()``
to the handler.

Run::

    python examples/a2a_server.py
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from langchain_core.messages import AIMessage

from prismal.a2a import A2AServerHandler, build_agent_card
from prismal.a2a.server import AuthContext
from prismal.a2a.types import A2AMessage, A2APart
from prismal.core.config import Settings

REGISTRY = {"research": ["general", "research"], "coding": ["general", "code_execution"]}


class EchoGraph:
    """Stand-in for the compiled supervisor graph."""

    async def ainvoke(self, state: dict[str, Any], config: Any = None) -> dict[str, Any]:
        text = state["messages"][-1].content
        return {"messages": [*state["messages"], AIMessage(content=f"prismal handled: {text}")]}


def _request(text: str) -> dict[str, Any]:
    msg = A2AMessage(role="user", parts=[A2APart(kind="text", text=text)], message_id="m1")
    return {
        "jsonrpc": "2.0",
        "id": "req-1",
        "method": "message/send",
        "params": {"message": msg.model_dump(by_alias=True), "skillId": "research"},
    }


async def main() -> None:
    settings = Settings(
        a2a_enabled=True,
        a2a_inbound_enabled=True,
        a2a_base_url="https://prismal.example.com/a2a",
        a2a_strict=True,
    )

    card = build_agent_card(settings, REGISTRY)
    print("Agent Card (/.well-known/agent-card.json):")
    print(json.dumps(card.model_dump(by_alias=True, exclude_none=True), indent=2))

    handler = A2AServerHandler(EchoGraph(), settings=settings)
    auth = AuthContext(authenticated=True, subject="acme-orchestrator")

    response = await handler.handle_rpc(_request("summarize Q3 revenue"), auth_ctx=auth)
    print("\nmessage/send →", json.dumps(response["result"], indent=2))

    print("\nstreaming the same task as SSE:")
    async for line in handler.stream_rpc(_request("stream this"), auth_ctx=auth):
        print("  ", line.strip())

    denied = await handler.handle_rpc(_request("no auth"), auth_ctx=None)
    print("\nunauthenticated (strict) →", denied["error"])


if __name__ == "__main__":
    asyncio.run(main())

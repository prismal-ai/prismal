"""Node I/O type-safety demo (Phase NTS).

Annotates a toy node with narrow ``input_model``/``output_model`` contracts and
shows the three modes end to end:

1. ``off`` / disabled — the decorated node behaves like an undecorated one.
2. ``warn`` — a malformed return is logged + counted, then passed through.
3. ``enforce`` — a malformed return raises ``NodeValidationError``, mapped by
   ``error_mapping_middleware`` to a ``metadata.error`` update.

Run::

    python examples/node_typesafety.py
"""

from __future__ import annotations

import asyncio

from pydantic import BaseModel

from prismal.agents.extension import _middleware, prismal_node
from prismal.core.config import Settings


class GreeterInput(BaseModel):
    """The toy node reads a session id and a message list."""

    session_id: str
    messages: list


class GreeterOutput(BaseModel):
    """The toy node must return which agent ran and the new messages."""

    current_agent: str
    messages: list


def _use_settings(**kwargs: object) -> None:
    """Point the middleware at a fresh Settings (module-level test seam)."""
    settings = Settings(**kwargs)  # type: ignore[arg-type]
    _middleware.get_settings = lambda: settings  # type: ignore[assignment]


@prismal_node(
    name="greeter",
    security="off",
    audit=False,
    input_model=GreeterInput,
    output_model=GreeterOutput,
)
async def greeter_node(state):  # type: ignore[no-untyped-def]
    # Deliberately BUGGY: forgets ``current_agent`` — a silent routing break
    # in production, caught by the output contract here.
    return {"messages": [f"hello {state['session_id']}"]}


@prismal_node(
    name="greeter_ok",
    security="off",
    audit=False,
    output_model=GreeterOutput,
)
async def greeter_ok_node(state):  # type: ignore[no-untyped-def]
    return {"current_agent": "greeter_ok", "messages": ["hi"]}


async def main() -> None:
    state = {"session_id": "demo", "messages": []}

    print("── disabled (default) — buggy node passes silently ──")
    _use_settings(node_typesafety_enabled=False)
    print("  ", await greeter_node(state))

    print("\n── warn — buggy output logged + counted, still passes through ──")
    _use_settings(node_typesafety_enabled=True, node_typesafety_mode="warn")
    print("  ", await greeter_node(state))

    print("\n── enforce — buggy output mapped to metadata.error ──")
    _use_settings(node_typesafety_enabled=True, node_typesafety_mode="enforce")
    out = await greeter_node(state)
    print("  ", out.get("metadata", {}).get("error", out))

    print("\n── enforce — a correct node passes cleanly ──")
    print("  ", await greeter_ok_node(state))


if __name__ == "__main__":
    asyncio.run(main())

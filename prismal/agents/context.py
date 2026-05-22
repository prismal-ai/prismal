"""Ambient context for agent invocations.

Exposes :data:`channel_context_var`, a :class:`~contextvars.ContextVar` used
to propagate the originating channel metadata (Telegram, Slack, Discord, ...)
from :class:`~lightagent.channels.router.ChannelRouter` all the way down to
tools such as ``cron_add`` / ``cron_once``.

Rationale
---------
LangGraph nodes receive the full :class:`~lightagent.agents.state.AgentState`,
but our custom :func:`~lightagent.agents.tool_registry.react_loop` invokes
tools via ``tool_fn.ainvoke(args)`` without the LangGraph ``ToolNode`` that
normally resolves ``Annotated[..., InjectedState]`` parameters.  Passing
``state`` through every call site would bloat signatures and couple every
tool to LangGraph internals.

A :class:`~contextvars.ContextVar` is the idiomatic Python answer: it is
copied lazily on each asyncio task so the whole graph execution (and all
nested tool calls) inherit the same per-request context without any
plumbing.  Callers use the :func:`use_channel_context` helper to set and
automatically reset the value on exit.

Example::

    from prismal.agents.context import use_channel_context

    async with use_channel_context({"channel": "telegram", ...}):
        await graph.ainvoke(state)
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator


#: Per-task channel context populated by :class:`ChannelRouter` and consumed
#: by cron / messaging tools to auto-route their output back to the
#: originating chat.
#:
#: ``None`` is the correct default for requests that did not enter through a
#: channel gateway (API, dashboard, CLI, cron tick, …).  Tools must tolerate
#: ``None`` gracefully and fall through to their explicit routing arguments.
channel_context_var: ContextVar[dict[str, str] | None] = ContextVar(
    "lightagent_channel_context",
    default=None,
)


@contextmanager
def use_channel_context(ctx: dict[str, str] | None) -> Iterator[None]:
    """Set :data:`channel_context_var` for the duration of a ``with`` block.

    The previous value is restored on exit, making this safe to nest and
    safe to call from multiple concurrent asyncio tasks sharing a thread.

    Args:
        ctx: Channel context to expose, or ``None`` to leave unset.  A
            non-``None`` dict should carry at least ``channel`` and
            ``chat_id`` keys; other keys (``user_id``, …) are optional.

    Yields:
        ``None`` — the context is available through
        :func:`channel_context_var.get` while inside the block.
    """
    token = channel_context_var.set(ctx)
    try:
        yield
    finally:
        channel_context_var.reset(token)


__all__ = ["channel_context_var", "use_channel_context"]

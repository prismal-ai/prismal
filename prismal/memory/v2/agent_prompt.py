"""Agent adoption of recalled memory (Phase MEM follow-up).

Hosts bind the tenant's :class:`MemoryService` for a graph run with
:func:`use_memory_service` (``context`` mode) or :func:`set_memory_service`
(``global`` mode); agent nodes call :func:`memory_turn_message` to get one
trailing message carrying the recalled memory.

The message body is :func:`~prismal.memory.v2.render.memory_block_for_prompt`
inside a ``<turn_context>`` tail, appended *after* the conversation, so the
cache-stable prefix (system prompt + history) is never touched — the MEM
invariant. It is a no-op (``None``) unless ``memory_v2_enabled``,
``memory_mode == "enforce"``, the cache-stable layout is active
(``token_efficiency_enabled`` and ``prompt_layout == "cache_stable"``) and a
service is bound. It never raises: a recall failure is logged and skipped.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

from langchain_core.messages import HumanMessage

from prismal.core.logging import get_logger
from prismal.memory.v2.render import memory_block_for_prompt
from prismal.memory.v2.types import MemoryContext, MemoryScope, RecallQuery
from prismal.security.prompt_builder import PromptLayout, SecurePromptBuilder

if TYPE_CHECKING:
    from prismal.core.config import Settings
    from prismal.memory.v2.service import MemoryService

logger = get_logger(__name__)

_SERVICE: MemoryService | None = None
_BOUND: ContextVar[MemoryService | None] = ContextVar("prismal_memory_service", default=None)


def set_memory_service(service: MemoryService | None) -> None:
    """Install (or clear) the process memory service (``global`` runtime mode)."""
    global _SERVICE
    _SERVICE = service


def get_memory_service() -> MemoryService | None:
    """The service bound for this run (context first, then the process one)."""
    return _BOUND.get() or _SERVICE


@contextmanager
def use_memory_service(service: MemoryService | None) -> Iterator[MemoryService | None]:
    """Bind *service* for the duration of the block (``context`` runtime mode)."""
    token = _BOUND.set(service)
    try:
        yield service
    finally:
        _BOUND.reset(token)


def _enabled(settings: Settings) -> bool:
    return (
        settings.memory_v2_enabled
        and settings.memory_mode == "enforce"
        and settings.token_efficiency_enabled
        and settings.prompt_layout == "cache_stable"
    )


def _last_human_text(state: Mapping[str, Any]) -> str:
    for message in reversed(list(state.get("messages") or [])):
        if getattr(message, "type", "") == "human":
            content = message.content
            return content if isinstance(content, str) else ""
    return ""


def _user_id(state: Mapping[str, Any]) -> str | None:
    channel = state.get("channel_context")
    if isinstance(channel, Mapping):
        user = channel.get("user_id")
        return str(user) if user else None
    return None


async def memory_turn_message(
    state: Mapping[str, Any], *, agent: str, settings: Settings | None = None
) -> HumanMessage | None:
    """One trailing ``<turn_context>`` message with recalled memory, or ``None``."""
    if settings is None:
        from prismal.core.config import get_settings

        settings = get_settings()
    if not _enabled(settings):
        return None
    service = get_memory_service()
    query = _last_human_text(state)
    if service is None or not query.strip():
        return None
    try:
        ctx = MemoryContext(
            scope=MemoryScope(org_id=service.org_id, user_id=_user_id(state)),
            session_id=str(state.get("session_id") or ""),
            actor=agent,
        )
        result = await service.recall(
            RecallQuery(text=query), ctx=ctx, budget_tokens=settings.memory_recall_budget_tokens
        )
        # Populate the builder's canaries (incl. the tenant prefix canary) so any
        # canary echoed in recalled text is redacted from the block.
        builder = SecurePromptBuilder(settings=settings)
        builder.build("", "", layout=PromptLayout.CACHE_STABLE, tenant=service.org_id)
        block = memory_block_for_prompt(result, builder=builder)
    except Exception as exc:
        logger.warning("memory_turn_recall_failed", agent=agent, error=type(exc).__name__)
        return None
    if not block:
        return None
    return HumanMessage(content=f"<turn_context>memory:{block}</turn_context>")


__all__ = [
    "get_memory_service",
    "memory_turn_message",
    "set_memory_service",
    "use_memory_service",
]

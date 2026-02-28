"""Short-term (in-session) message memory for LightAgent.

Holds a bounded, thread-safe list of LangChain ``BaseMessage`` objects
for the duration of one agent session.  When the buffer is full, the
oldest message is evicted (FIFO).

Example::

    from lightagent.memory.short_term import ShortTermMemory
    from langchain_core.messages import HumanMessage, AIMessage

    mem = ShortTermMemory(max_messages=50)
    mem.add(HumanMessage(content="Hello"))
    mem.add(AIMessage(content="Hi there!"))
    for msg in mem.get_all():
        print(msg.content)

AC-011-1: Within a session, the agent remembers all prior messages.
AC-011-3: Memory is session-scoped by default.
"""

from __future__ import annotations

import threading
from collections import deque
from typing import TYPE_CHECKING

from lightagent.core.logging import get_logger

if TYPE_CHECKING:
    from langchain_core.messages import BaseMessage

logger = get_logger("lightagent.memory.short_term")

_DEFAULT_MAX = 100


class ShortTermMemory:
    """Thread-safe, bounded in-session message buffer.

    Stores LangChain ``BaseMessage`` objects in insertion order.  When the
    buffer reaches ``max_messages``, the oldest message is automatically
    dropped to make room for the new one (circular-buffer semantics).

    Args:
        max_messages: Maximum number of messages to keep in memory.
            Defaults to ``100``.

    Example::

        mem = ShortTermMemory(max_messages=20)
        mem.add(HumanMessage(content="Hello"))
        recent = mem.get_recent(5)
    """

    def __init__(self, max_messages: int = _DEFAULT_MAX) -> None:
        """Initialise an empty ShortTermMemory."""
        self.max_messages = max_messages
        self._messages: deque[BaseMessage] = deque(maxlen=max_messages)
        self._lock = threading.Lock()

    # ── write ─────────────────────────────────────────────────────────────────

    def add(self, message: BaseMessage) -> None:
        """Append a message to the buffer.

        If the buffer is full, the oldest message is evicted automatically
        (``deque`` with ``maxlen`` semantics).

        Args:
            message: Any LangChain ``BaseMessage`` subclass.
        """
        with self._lock:
            self._messages.append(message)
        logger.debug(
            "short_term_memory_add",
            msg_type=type(message).__name__,
            total=len(self._messages),
        )

    # ── read ──────────────────────────────────────────────────────────────────

    def get_all(self) -> list[BaseMessage]:
        """Return a snapshot of all messages in chronological order.

        Returns:
            A new list containing all buffered messages.  Modifying the
            returned list does not affect the internal buffer.
        """
        with self._lock:
            return list(self._messages)

    def get_recent(self, n: int) -> list[BaseMessage]:
        """Return the *n* most recent messages.

        Args:
            n: Number of messages to return.  If ``n`` exceeds the buffer
                size, all messages are returned.

        Returns:
            List of the most recent ``n`` messages in chronological order.
        """
        if n <= 0:
            return []
        with self._lock:
            msgs = list(self._messages)
        return msgs[-n:]

    # ── reset ─────────────────────────────────────────────────────────────────

    def clear(self) -> None:
        """Remove all messages from the buffer."""
        with self._lock:
            self._messages.clear()
        logger.debug("short_term_memory_cleared")

    # ── dunder ────────────────────────────────────────────────────────────────

    def __len__(self) -> int:
        """Return the current number of messages in the buffer."""
        with self._lock:
            return len(self._messages)


__all__ = ["ShortTermMemory"]

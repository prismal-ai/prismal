"""
Memory system — short-term session buffer and long-term persistent store.

Public API::

    from prismal.memory import ShortTermMemory, LongTermMemory, MemoryEntry
    from prismal.memory import MongoDBMemoryStore  # requires [mongodb] extra
    from prismal.memory import (
        PreferenceExtractor,
        PreferenceFacts,
        PreferencesManager,
    )
"""

from __future__ import annotations

from prismal.memory.long_term import LongTermMemory, MemoryEntry
from prismal.memory.mongodb_store import MongoDBMemoryStore
from prismal.memory.preferences import (
    PreferenceExtractor,
    PreferenceFacts,
    PreferencesManager,
)
from prismal.memory.profile import ProfileManager
from prismal.memory.short_term import ShortTermMemory

__all__ = [
    "LongTermMemory",
    "MemoryEntry",
    "MongoDBMemoryStore",
    "PreferenceExtractor",
    "PreferenceFacts",
    "PreferencesManager",
    "ProfileManager",
    "ShortTermMemory",
]

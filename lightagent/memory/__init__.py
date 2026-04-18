"""
Memory system — short-term session buffer and long-term persistent store.

Public API::

    from lightagent.memory import ShortTermMemory, LongTermMemory, MemoryEntry
    from lightagent.memory import MongoDBMemoryStore  # requires [mongodb] extra
    from lightagent.memory import (
        PreferenceExtractor,
        PreferenceFacts,
        PreferencesManager,
    )
"""

from __future__ import annotations

from lightagent.memory.long_term import LongTermMemory, MemoryEntry
from lightagent.memory.mongodb_store import MongoDBMemoryStore
from lightagent.memory.preferences import (
    PreferenceExtractor,
    PreferenceFacts,
    PreferencesManager,
)
from lightagent.memory.profile import ProfileManager
from lightagent.memory.short_term import ShortTermMemory

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

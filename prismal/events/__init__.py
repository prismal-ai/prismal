"""Events package — file-system event watching and dispatching.

Public API::

    from prismal.events import FileWatcher, create_default_watcher
"""

from __future__ import annotations

from prismal.events.file_watcher import (
    EventCallback,
    FileWatcher,
    create_default_watcher,
)

__all__ = [
    "EventCallback",
    "FileWatcher",
    "create_default_watcher",
]

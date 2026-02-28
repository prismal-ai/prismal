"""Events package — file-system event watching and dispatching.

Public API::

    from lightagent.events import FileWatcher, create_default_watcher
"""

from __future__ import annotations

from lightagent.events.file_watcher import (
    EventCallback,
    FileWatcher,
    create_default_watcher,
)

__all__ = [
    "EventCallback",
    "FileWatcher",
    "create_default_watcher",
]

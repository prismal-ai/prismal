"""File-system event watcher using Watchdog.

Monitors configured directories and dispatches named events to registered
callbacks.  Four paths are pre-wired by :func:`create_default_watcher`:

* ``data/documents/``         → ``index_document_event``   (AC-007-5)
* ``data/workspace/``         → ``workspace_update_event`` (AC-007-5)
* ``lightagent/skills/available/`` → ``skill_discovery_event``  (AC-007-6)
* ``config/``                 → ``config_reload_event``    (AC-007-7)

Example::

    from lightagent.events.file_watcher import create_default_watcher


    def on_doc(event_type: str, path: str) -> None:
        print(f"New document: {path}")


    watcher = create_default_watcher(callbacks={"index_document_event": on_doc})
    watcher.start()
    # ... do work ...
    watcher.stop()

AC-007-5: New files in ``data/documents/`` trigger RAG indexing.
AC-007-6: New skill files in ``skills/available/`` trigger skill discovery.
AC-007-7: Changes to ``config/`` trigger config hot-reload.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from lightagent.core.logging import get_logger

logger = get_logger("lightagent.events.file_watcher")

# ── Types ─────────────────────────────────────────────────────────────────────

EventCallback = Callable[[str, str], None]  # (event_name, file_path)

# ── Default watch paths (relative to project root) ───────────────────────────

_PROJECT_ROOT = Path(__file__).parent.parent.parent

_DEFAULT_WATCHES: list[tuple[str, str]] = [
    ("data/documents", "index_document_event"),
    ("data/workspace", "workspace_update_event"),
    ("lightagent/skills/available", "skill_discovery_event"),
    ("config", "config_reload_event"),
]


# ── Event handler ─────────────────────────────────────────────────────────────


class _LightAgentEventHandler(FileSystemEventHandler):
    """Watchdog handler that converts FS events into named LightAgent events.

    Args:
        callback: Callable receiving ``(event_name, file_path)`` on each
            file creation or modification.
        event_name: The logical event name to pass to ``callback``.
    """

    def __init__(self, callback: EventCallback, event_name: str) -> None:
        """Initialise the handler with a callback and event name."""
        super().__init__()
        self._callback = callback
        self._event_name = event_name

    def on_created(self, event: FileSystemEvent) -> None:
        """Fire the callback when a file is created.

        Args:
            event: The Watchdog file-system event.
        """
        if event.is_directory:
            return
        logger.debug(
            "file_watcher_created",
            watch_event=self._event_name,
            path=event.src_path,
        )
        self._callback(self._event_name, str(event.src_path))

    def on_modified(self, event: FileSystemEvent) -> None:
        """Fire the callback when a file is modified.

        Args:
            event: The Watchdog file-system event.
        """
        if event.is_directory:
            return
        logger.debug(
            "file_watcher_modified",
            watch_event=self._event_name,
            path=event.src_path,
        )
        self._callback(self._event_name, str(event.src_path))


# ── Watch registry entry ──────────────────────────────────────────────────────


class _WatchEntry:
    """Internal entry mapping a path + event name to a callback."""

    __slots__ = ("callback", "event_name", "path")

    def __init__(self, path: Path, event_name: str, callback: EventCallback) -> None:
        """Initialise a watch entry."""
        self.path = path
        self.event_name = event_name
        self.callback = callback


# ── FileWatcher ───────────────────────────────────────────────────────────────


class FileWatcher:
    """Watchdog-based file-system monitor with a callback registry.

    Paths are registered before calling :meth:`start`.  Once started,
    any file creation or modification under a registered path fires the
    corresponding callback.

    Args:
        observer: A Watchdog ``Observer`` instance.  Defaults to a new
            :class:`~watchdog.observers.Observer`.  Inject a mock for tests.

    Example::

        watcher = FileWatcher()
        watcher.register(Path("data/documents"), "doc_event", my_callback)
        watcher.start()
    """

    def __init__(self, observer: Observer | None = None) -> None:
        """Initialise FileWatcher with an optional custom observer."""
        self._observer: Observer = observer or Observer()
        self._watches: list[_WatchEntry] = []

    def register(
        self,
        path: Path,
        event_name: str,
        callback: EventCallback,
    ) -> None:
        """Register a directory to watch.

        Args:
            path: Directory path to monitor (need not exist at registration
                time; non-existent paths are silently skipped at start).
            event_name: Logical event name forwarded to ``callback``.
            callback: Callable receiving ``(event_name, file_path)`` on each
                file-system event.
        """
        self._watches.append(_WatchEntry(path, event_name, callback))

    def start(self) -> None:
        """Start the observer and schedule all registered watches.

        Non-existent directories are skipped with a warning so that the
        watcher starts cleanly even in development environments.
        """
        for entry in self._watches:
            if not entry.path.exists():
                logger.warning(
                    "file_watcher_path_missing",
                    path=str(entry.path),
                    watch_event=entry.event_name,
                )
                continue
            handler = _LightAgentEventHandler(entry.callback, entry.event_name)
            self._observer.schedule(handler, str(entry.path), recursive=True)
            logger.info(
                "file_watcher_scheduled",
                path=str(entry.path),
                watch_event=entry.event_name,
            )

        self._observer.start()
        logger.info("file_watcher_started", watch_count=len(self._watches))

    def stop(self) -> None:
        """Stop the observer and wait for its thread to finish."""
        self._observer.stop()
        self._observer.join()
        logger.info("file_watcher_stopped")

    def is_running(self) -> bool:
        """Return True if the underlying observer thread is alive.

        Returns:
            Boolean indicating whether the observer is running.
        """
        return bool(self._observer.is_alive())


# ── Factory ───────────────────────────────────────────────────────────────────


def create_default_watcher(
    callbacks: dict[str, EventCallback] | None = None,
) -> FileWatcher:
    """Create a :class:`FileWatcher` pre-wired with the four standard paths.

    The four standard watch paths are:

    * ``data/documents/``         → ``index_document_event``
    * ``data/workspace/``         → ``workspace_update_event``
    * ``lightagent/skills/available/`` → ``skill_discovery_event``
    * ``config/``                 → ``config_reload_event``

    Args:
        callbacks: Optional mapping of event-name → callable.  Events
            without a registered callback use a no-op default.

    Returns:
        A configured (but not yet started) :class:`FileWatcher`.

    AC-007-5 / AC-007-6 / AC-007-7: Standard LightAgent event bindings.
    """
    cbs: dict[str, EventCallback] = callbacks or {}

    def _noop(event_name: str, path: str) -> None:
        logger.info("file_watcher_event", watch_event=event_name, path=path)

    watcher = FileWatcher()
    for rel_path, event_name in _DEFAULT_WATCHES:
        cb = cbs.get(event_name, _noop)
        watcher.register(_PROJECT_ROOT / rel_path, event_name, cb)

    return watcher


__all__ = [
    "EventCallback",
    "FileWatcher",
    "_LightAgentEventHandler",
    "create_default_watcher",
]

"""Unit tests for lightagent.events.file_watcher (T-082)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

# ── FileWatcher construction ──────────────────────────────────────────────────


def test_file_watcher_default_not_running() -> None:
    """A new FileWatcher is not running by default."""
    from lightagent.events.file_watcher import FileWatcher

    watcher = FileWatcher()
    assert not watcher.is_running()


def test_file_watcher_accepts_custom_observer() -> None:
    """FileWatcher accepts a custom Observer via constructor injection."""
    from lightagent.events.file_watcher import FileWatcher

    mock_observer = MagicMock()
    mock_observer.is_alive.return_value = False
    watcher = FileWatcher(observer=mock_observer)
    assert not watcher.is_running()


# ── register ──────────────────────────────────────────────────────────────────


def test_register_stores_watch(tmp_path: Path) -> None:
    """register() stores a watch entry for the given path."""
    from lightagent.events.file_watcher import FileWatcher

    watcher = FileWatcher()
    watcher.register(tmp_path, "test_event", lambda event_type, path: None)
    assert len(watcher._watches) == 1


def test_register_multiple_paths(tmp_path: Path) -> None:
    """register() can store watches for multiple paths."""
    from lightagent.events.file_watcher import FileWatcher

    docs = tmp_path / "docs"
    workspace = tmp_path / "workspace"
    docs.mkdir()
    workspace.mkdir()

    watcher = FileWatcher()
    watcher.register(docs, "doc_event", lambda e, p: None)
    watcher.register(workspace, "ws_event", lambda e, p: None)
    assert len(watcher._watches) == 2


# ── start / stop ─────────────────────────────────────────────────────────────


def test_start_calls_observer_start(tmp_path: Path) -> None:
    """start() calls observer.start()."""
    from lightagent.events.file_watcher import FileWatcher

    mock_observer = MagicMock()
    mock_observer.is_alive.return_value = True
    watcher = FileWatcher(observer=mock_observer)
    watcher.register(tmp_path, "evt", lambda e, p: None)
    watcher.start()
    mock_observer.start.assert_called_once()


def test_stop_calls_observer_stop() -> None:
    """stop() calls observer.stop() and observer.join()."""
    from lightagent.events.file_watcher import FileWatcher

    mock_observer = MagicMock()
    mock_observer.is_alive.return_value = False
    watcher = FileWatcher(observer=mock_observer)
    watcher.stop()
    mock_observer.stop.assert_called_once()
    mock_observer.join.assert_called_once()


def test_is_running_reflects_observer_state() -> None:
    """is_running() delegates to observer.is_alive()."""
    from lightagent.events.file_watcher import FileWatcher

    mock_observer = MagicMock()
    mock_observer.is_alive.return_value = True
    watcher = FileWatcher(observer=mock_observer)
    assert watcher.is_running() is True


def test_start_skips_nonexistent_path(tmp_path: Path) -> None:
    """start() skips non-existent directories with a warning."""
    from lightagent.events.file_watcher import FileWatcher

    missing = tmp_path / "does_not_exist"
    mock_observer = MagicMock()
    mock_observer.is_alive.return_value = True

    watcher = FileWatcher(observer=mock_observer)
    watcher.register(missing, "evt", lambda e, p: None)
    watcher.start()  # should not raise

    mock_observer.schedule.assert_not_called()
    mock_observer.start.assert_called_once()


def test_start_schedules_existing_paths(tmp_path: Path) -> None:
    """start() schedules existing directories with the observer."""
    from lightagent.events.file_watcher import FileWatcher

    existing = tmp_path / "docs"
    existing.mkdir()

    mock_observer = MagicMock()
    mock_observer.is_alive.return_value = True

    watcher = FileWatcher(observer=mock_observer)
    watcher.register(existing, "doc_event", lambda e, p: None)
    watcher.start()

    mock_observer.schedule.assert_called_once()


# ── callback invocation ──────────────────────────────────────────────────────


def test_handler_calls_callback_on_created() -> None:
    """_LightAgentEventHandler calls callback on file creation."""
    from watchdog.events import FileCreatedEvent

    from lightagent.events.file_watcher import _LightAgentEventHandler

    received: list[tuple[str, str]] = []

    def cb(event_type: str, path: str) -> None:
        received.append((event_type, path))

    handler = _LightAgentEventHandler(callback=cb, event_name="doc_event")
    handler.on_created(FileCreatedEvent("/data/docs/new_file.pdf"))

    assert len(received) == 1
    assert received[0][0] == "doc_event"
    assert "new_file.pdf" in received[0][1]


def test_handler_calls_callback_on_modified() -> None:
    """_LightAgentEventHandler calls callback on file modification."""
    from watchdog.events import FileModifiedEvent

    from lightagent.events.file_watcher import _LightAgentEventHandler

    received: list[tuple[str, str]] = []

    def cb(event_type: str, path: str) -> None:
        received.append((event_type, path))

    handler = _LightAgentEventHandler(callback=cb, event_name="ws_event")
    handler.on_modified(FileModifiedEvent("/data/workspace/file.txt"))

    assert len(received) == 1
    assert received[0][0] == "ws_event"


def test_handler_ignores_directory_created() -> None:
    """_LightAgentEventHandler ignores directory creation events."""
    from watchdog.events import DirCreatedEvent

    from lightagent.events.file_watcher import _LightAgentEventHandler

    received: list[tuple[str, str]] = []
    handler = _LightAgentEventHandler(
        callback=lambda e, p: received.append((e, p)),
        event_name="doc_event",
    )
    handler.on_created(DirCreatedEvent("/data/docs/subdir/"))
    assert len(received) == 0


def test_handler_ignores_directory_modified() -> None:
    """_LightAgentEventHandler ignores directory modification events."""
    from watchdog.events import DirModifiedEvent

    from lightagent.events.file_watcher import _LightAgentEventHandler

    received: list[tuple[str, str]] = []
    handler = _LightAgentEventHandler(
        callback=lambda e, p: received.append((e, p)),
        event_name="doc_event",
    )
    handler.on_modified(DirModifiedEvent("/data/docs/"))
    assert len(received) == 0


# ── create_default_watcher ───────────────────────────────────────────────────


def test_create_default_watcher_returns_file_watcher() -> None:
    """create_default_watcher() returns a FileWatcher instance."""
    from lightagent.events.file_watcher import FileWatcher, create_default_watcher

    watcher = create_default_watcher()
    assert isinstance(watcher, FileWatcher)


def test_create_default_watcher_has_four_watches() -> None:
    """create_default_watcher() registers the four standard watch paths."""
    from lightagent.events.file_watcher import create_default_watcher

    watcher = create_default_watcher()
    assert len(watcher._watches) == 4


def test_create_default_watcher_uses_provided_callbacks() -> None:
    """create_default_watcher() uses provided callbacks over no-op default."""
    from lightagent.events.file_watcher import create_default_watcher

    called: list[str] = []

    def my_cb(event_type: str, path: str) -> None:
        called.append(event_type)

    watcher = create_default_watcher(callbacks={"index_document_event": my_cb})
    # Verify our callback was registered (by checking the watch entries)
    doc_watch = next(w for w in watcher._watches if w.event_name == "index_document_event")
    doc_watch.callback("index_document_event", "/some/doc.pdf")
    assert "index_document_event" in called


def test_create_default_watcher_event_names() -> None:
    """create_default_watcher() uses the four standard event names."""
    from lightagent.events.file_watcher import create_default_watcher

    watcher = create_default_watcher()
    event_names = {w.event_name for w in watcher._watches}
    assert "index_document_event" in event_names
    assert "workspace_update_event" in event_names
    assert "skill_discovery_event" in event_names
    assert "config_reload_event" in event_names

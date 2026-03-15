"""Unit tests for FilesystemGuard."""
from __future__ import annotations

from pathlib import Path

import pytest

from lightagent.security.filesystem_guard import FilesystemGuard, PathViolation


def test_blocked_system_path_raises() -> None:
    """Paths under /etc/ must raise PathViolation."""
    guard = FilesystemGuard(workspace_root=None)
    with pytest.raises(PathViolation, match="blocked"):
        guard.validate("/etc/passwd", write=False)


def test_blocked_ssh_path_raises() -> None:
    """Paths under ~/.ssh/ must raise PathViolation."""
    guard = FilesystemGuard(workspace_root=None)
    with pytest.raises(PathViolation, match="blocked"):
        guard.validate(str(Path.home() / ".ssh" / "id_rsa"), write=False)


def test_blocked_aws_path_raises() -> None:
    """Paths under ~/.aws/ must raise PathViolation."""
    guard = FilesystemGuard(workspace_root=None)
    with pytest.raises(PathViolation, match="blocked"):
        guard.validate(str(Path.home() / ".aws" / "credentials"), write=False)


def test_safe_path_passes_without_workspace(tmp_path: Path) -> None:
    """A safe path with no workspace restriction must pass and return resolved Path."""
    guard = FilesystemGuard(workspace_root=None)
    resolved = guard.validate(str(tmp_path / "file.txt"), write=False)
    assert resolved == (tmp_path / "file.txt").resolve()


def test_path_inside_workspace_passes(tmp_path: Path) -> None:
    """A path inside the workspace must pass validation."""
    guard = FilesystemGuard(workspace_root=str(tmp_path))
    resolved = guard.validate(str(tmp_path / "subdir" / "file.txt"), write=False)
    assert resolved == (tmp_path / "subdir" / "file.txt").resolve()


def test_path_outside_workspace_raises(tmp_path: Path) -> None:
    """A path outside the workspace must raise PathViolation."""
    guard = FilesystemGuard(workspace_root=str(tmp_path / "workspace"))
    with pytest.raises(PathViolation, match="outside workspace"):
        guard.validate(str(tmp_path / "other" / "file.txt"), write=False)


def test_path_traversal_blocked(tmp_path: Path) -> None:
    """Path traversal sequences that escape the workspace must be blocked."""
    guard = FilesystemGuard(workspace_root=str(tmp_path))
    with pytest.raises(PathViolation, match="outside workspace"):
        guard.validate(str(tmp_path / ".." / "escape.txt"), write=False)


def test_write_blocked_for_readonly_path(tmp_path: Path) -> None:
    """Write operations to a readonly path must raise PathViolation."""
    guard = FilesystemGuard(workspace_root=None, readonly_paths=[str(tmp_path)])
    with pytest.raises(PathViolation, match="read-only"):
        guard.validate(str(tmp_path / "file.txt"), write=True)


def test_read_allowed_for_readonly_path(tmp_path: Path) -> None:
    """Read operations on readonly paths must be allowed."""
    guard = FilesystemGuard(workspace_root=None, readonly_paths=[str(tmp_path)])
    result = guard.validate(str(tmp_path / "file.txt"), write=False)
    assert result == (tmp_path / "file.txt").resolve()


def test_workspace_and_readonly_combined(tmp_path: Path) -> None:
    """workspace_root and readonly_paths restrictions work together."""
    ro_dir = tmp_path / "readonly"
    ro_dir.mkdir()
    guard = FilesystemGuard(workspace_root=str(tmp_path), readonly_paths=[str(ro_dir)])
    # Read inside workspace is fine
    guard.validate(str(tmp_path / "file.txt"), write=False)
    # Write to non-readonly inside workspace is fine
    guard.validate(str(tmp_path / "writable" / "file.txt"), write=True)
    # Write to readonly inside workspace is blocked
    with pytest.raises(PathViolation, match="read-only"):
        guard.validate(str(ro_dir / "file.txt"), write=True)

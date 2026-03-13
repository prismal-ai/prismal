"""Unit tests for FilesystemGuard."""
from __future__ import annotations

import pytest
from pathlib import Path
from lightagent.security.filesystem_guard import FilesystemGuard, PathViolation


def test_blocked_system_path_raises():
    guard = FilesystemGuard(workspace_root=None)
    with pytest.raises(PathViolation, match="blocked"):
        guard.validate("/etc/passwd", write=False)


def test_blocked_ssh_path_raises():
    guard = FilesystemGuard(workspace_root=None)
    with pytest.raises(PathViolation, match="blocked"):
        guard.validate(str(Path.home() / ".ssh" / "id_rsa"), write=False)


def test_blocked_aws_path_raises():
    guard = FilesystemGuard(workspace_root=None)
    with pytest.raises(PathViolation, match="blocked"):
        guard.validate(str(Path.home() / ".aws" / "credentials"), write=False)


def test_safe_path_passes_without_workspace(tmp_path: Path):
    guard = FilesystemGuard(workspace_root=None)
    resolved = guard.validate(str(tmp_path / "file.txt"), write=False)
    assert resolved == (tmp_path / "file.txt").resolve()


def test_path_inside_workspace_passes(tmp_path: Path):
    guard = FilesystemGuard(workspace_root=str(tmp_path))
    resolved = guard.validate(str(tmp_path / "subdir" / "file.txt"), write=False)
    assert resolved == (tmp_path / "subdir" / "file.txt").resolve()


def test_path_outside_workspace_raises(tmp_path: Path):
    guard = FilesystemGuard(workspace_root=str(tmp_path / "workspace"))
    with pytest.raises(PathViolation, match="outside workspace"):
        guard.validate(str(tmp_path / "other" / "file.txt"), write=False)


def test_path_traversal_blocked(tmp_path: Path):
    guard = FilesystemGuard(workspace_root=str(tmp_path))
    with pytest.raises(PathViolation, match="outside workspace"):
        guard.validate(str(tmp_path / ".." / "escape.txt"), write=False)


def test_write_blocked_for_readonly_path(tmp_path: Path):
    guard = FilesystemGuard(workspace_root=None, readonly_paths=[str(tmp_path)])
    with pytest.raises(PathViolation, match="read-only"):
        guard.validate(str(tmp_path / "file.txt"), write=True)

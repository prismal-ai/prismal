"""Centralized filesystem path validation for agent tools."""
from __future__ import annotations

from pathlib import Path

import structlog

logger = structlog.get_logger("lightagent.security.filesystem_guard")

_BLOCKED_PREFIXES: tuple[str, ...] = (
    "/etc/",
    "/boot/",
    "/proc/",
    "/sys/",
    "/dev/",
    "/root/",
    str(Path.home() / ".ssh"),
    str(Path.home() / ".aws"),
    str(Path.home() / ".gnupg"),
    str(Path.home() / ".config/gcloud"),
)


class PathViolation(ValueError):  # noqa: N818
    """Raised when a requested path violates security policy."""


class FilesystemGuard:
    """Validates filesystem paths against security policy.

    Args:
        workspace_root: If set, all paths must be inside this directory.
            Pass ``None`` to allow any non-blocked path.
        readonly_paths: List of path prefixes that reject write operations.
    """

    def __init__(
        self,
        workspace_root: str | None = None,
        readonly_paths: list[str] | None = None,
    ) -> None:
        """Initialise the guard."""
        self._workspace: Path | None = (
            Path(workspace_root).resolve() if workspace_root else None
        )
        self._readonly: list[Path] = [
            Path(p).resolve() for p in (readonly_paths or [])
        ]

    def validate(self, path: str, *, write: bool) -> Path:
        """Validate *path* against security policy and return the resolved Path.

        Args:
            path: The filesystem path to validate (str or path-like).
            write: ``True`` if the operation will modify the filesystem.

        Returns:
            The resolved :class:`~pathlib.Path` when validation passes.

        Raises:
            PathViolation: If the path violates any security rule.
        """
        resolved = Path(path).resolve()
        resolved_str = str(resolved)

        for prefix in _BLOCKED_PREFIXES:
            if resolved_str.startswith(prefix):
                logger.warning(
                    "filesystem_guard.blocked_prefix",
                    path=resolved_str,
                    prefix=prefix,
                )
                raise PathViolation(f"Path is blocked: {resolved_str}")

        if self._workspace is not None:
            try:
                resolved.relative_to(self._workspace)
            except ValueError:
                logger.warning(
                    "filesystem_guard.outside_workspace",
                    path=resolved_str,
                    workspace=str(self._workspace),
                )
                raise PathViolation(
                    f"Path is outside workspace: {resolved_str}"
                ) from None

        if write:
            for ro in self._readonly:
                is_inside = False
                try:
                    resolved.relative_to(ro)
                    is_inside = True
                except ValueError:
                    pass
                if is_inside:
                    logger.warning(
                        "filesystem_guard.write_to_readonly",
                        path=resolved_str,
                        readonly=str(ro),
                    )
                    raise PathViolation(
                        f"Path is read-only: {resolved_str}"
                    )

        return resolved


__all__ = ["FilesystemGuard", "PathViolation"]

"""Souls manager — discovery and loading across the three souls tiers.

:class:`SoulsManager` orchestrates the three-tier souls system, mirroring
``skills/manager.py``:

* ``available/`` — committed source souls
* ``active/``    — runtime-enabled souls (gitignored); when non-empty it acts
  as an **allow-list** restricting which souls may be loaded (PLAN.md §10)
* ``custom/``    — AI-generated souls (gitignored)

Example::

    from prismal.souls.manager import SoulsManager

    manager = SoulsManager()
    print([m.name for m in manager.list_souls()])
    spirit = manager.load("spirit")
    triad = manager.load_triad()  # [spirit, mind, heart]
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from prismal.core.exceptions import KokoroConfigError, SoulNotFoundError, SoulValidationError
from prismal.core.logging import get_logger
from prismal.souls.base import _find_soul_md, default_souls_root, load_soul

if TYPE_CHECKING:
    from pathlib import Path

    from prismal.core.config import Settings
    from prismal.souls.base import Soul, SoulMetadata

logger = get_logger("prismal.souls.manager")

_TRIAD_SIZE = 3


class SoulsManager:
    """Manages soul discovery and loading across the souls tiers.

    Args:
        souls_root: Root of the souls tree.  Defaults to
            ``settings.souls_dir`` or the packaged ``prismal/souls`` directory.
        settings: Prismal settings; ``None`` resolves via ``get_settings()``.
    """

    def __init__(
        self,
        *,
        souls_root: Path | None = None,
        settings: Settings | None = None,
    ) -> None:
        """Initialise the manager and resolve the tier directories."""
        if settings is None:
            from prismal.core.config import get_settings

            settings = get_settings()
        self._settings = settings
        self._root = (souls_root or default_souls_root(settings)).resolve()
        self._available = self._root / "available"
        self._active = self._root / "active"
        self._custom = self._root / "custom"

    @property
    def root(self) -> Path:
        """Return the resolved souls root directory."""
        return self._root

    def _allowlist(self) -> set[str]:
        """Return the set of soul ids enabled in ``active/`` (empty = no restriction)."""
        if not self._active.is_dir():
            return set()
        return {entry.name for entry in self._active.iterdir() if entry.is_dir()}

    def _soul_dirs(self) -> dict[str, Path]:
        """Map soul id → directory across the tiers (active > available > custom).

        A soul placed directly in ``active/`` overrides one with the same id
        in ``available/``; ``custom/`` has the lowest priority.
        """
        dirs: dict[str, Path] = {}
        for tier in (self._custom, self._available, self._active):
            if not tier.is_dir():
                continue
            for entry in sorted(tier.iterdir()):
                if entry.is_dir() and _find_soul_md(entry) is not None:
                    dirs[entry.name] = entry
        return dirs

    def list_souls(self) -> list[SoulMetadata]:
        """Discover all souls under the tiers (restricted by the ``active/`` allow-list).

        Souls whose ``SOUL.md`` fails validation are skipped with a warning —
        discovery never raises for an individual bad soul.

        Returns:
            Sorted list of :class:`SoulMetadata`, one per discoverable soul.
        """
        allowed = self._allowlist()
        result: list[SoulMetadata] = []
        for soul_id, soul_dir in sorted(self._soul_dirs().items()):
            if allowed and soul_id not in allowed:
                continue
            try:
                soul = load_soul(soul_dir, souls_root=self._root, settings=self._settings)
            except SoulValidationError as exc:
                logger.warning("soul_discovery_skip", soul_id=soul_id, reason=str(exc))
                continue
            result.append(soul.metadata)
        return result

    def load(self, soul_id: str) -> Soul:
        """Load a single soul by its ``name`` id.

        Args:
            soul_id: The soul identifier (directory name / metadata ``name``).

        Returns:
            The fully-loaded :class:`Soul`.

        Raises:
            SoulNotFoundError: when the id is absent from every tier or is
                excluded by the ``active/`` allow-list.
            SoulValidationError: when the soul exists but fails validation.
        """
        allowed = self._allowlist()
        if allowed and soul_id not in allowed:
            raise SoulNotFoundError(soul_id)
        soul_dir = self._soul_dirs().get(soul_id)
        if soul_dir is None:
            raise SoulNotFoundError(soul_id)
        return load_soul(soul_dir, souls_root=self._root, settings=self._settings)

    def load_triad(self, ids: list[str] | None = None) -> list[Soul]:
        """Load exactly three souls (default: ``settings.kokoro_souls``).

        Args:
            ids: Soul ids to convene; ``None`` uses ``settings.kokoro_souls``
                (default ``["spirit", "mind", "heart"]``).

        Returns:
            List of exactly three :class:`Soul` objects, in the given order.

        Raises:
            KokoroConfigError: if the resolved list does not contain exactly
                three distinct soul ids.
            SoulNotFoundError: when any id cannot be resolved.
            SoulValidationError: when any soul fails validation.
        """
        resolved = list(ids) if ids is not None else list(self._settings.kokoro_souls)
        if len(resolved) != _TRIAD_SIZE or len(set(resolved)) != _TRIAD_SIZE:
            raise KokoroConfigError(
                f"Kokoro requires exactly {_TRIAD_SIZE} distinct souls; got {resolved!r}"
            )
        return [self.load(soul_id) for soul_id in resolved]


__all__ = ["SoulsManager"]

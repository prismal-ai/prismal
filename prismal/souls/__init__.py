"""Souls system — Markdown-authored personas for Kokoro deliberation.

Public API for the three-tier Prismal souls system (mirrors ``skills/``):

* :class:`~prismal.souls.base.SoulMetadata` — Pydantic model for soul metadata
* :class:`~prismal.souls.base.Soul` — fully-loaded soul (metadata + persona body)
* :func:`~prismal.souls.base.load_soul` — load + validate a soul from a directory
* :func:`~prismal.souls.base.parse_soul_md` — parse SOUL.md frontmatter
* :class:`~prismal.souls.manager.SoulsManager` — discovery / load / load_triad

Quick start::

    from prismal.souls import SoulsManager

    manager = SoulsManager()
    print([m.name for m in manager.list_souls()])
    triad = manager.load_triad()  # [spirit, mind, heart]
"""

from __future__ import annotations

from prismal.souls.base import Soul, SoulMetadata, load_soul, parse_soul_md
from prismal.souls.manager import SoulsManager

__all__ = [
    "Soul",
    "SoulMetadata",
    "SoulsManager",
    "load_soul",
    "parse_soul_md",
]

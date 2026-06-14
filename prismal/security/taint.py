"""Taint tracking — provenance for untrusted content (Phase H — SPEC-HRD-TNT-001).

Content stays a plain ``str``; its provenance is recorded out-of-band in a
per-run :class:`TaintRegistry` keyed by a content hash (xxhash). This avoids
invasive wrapper types across RAG/tools/media and keeps checkpointed state
serializable — the registry holds only hashes + enum values, never content.

Loaders that produce external content (``rag/loaders/*``, MCP tool results,
multimodal STT/OCR/caption, ``souls/``) call :meth:`TaintRegistry.mark_untrusted`
at their boundary. The indirect-injection detector consults the registry before
re-injecting content into the model.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

import xxhash

if TYPE_CHECKING:
    from collections.abc import Iterator


class Provenance(StrEnum):
    """Where a piece of content originated."""

    USER = "user"
    TOOL = "tool"
    RAG = "rag"
    WEB = "web"
    MEDIA = "media"  # STT / OCR / captions
    SOUL = "soul"  # Kokoro SOUL.md bodies


@dataclass(frozen=True)
class TaintTag:
    """Provenance metadata for one piece of content.

    Attributes:
        content_hash: xxhash (hex) of the content the tag describes.
        provenance: Where the content came from.
        trusted: Only USER-confirmed or system content is trusted. External
            content (tool/rag/web/media/soul) is untrusted by default.
    """

    content_hash: str
    provenance: Provenance
    trusted: bool = False


def _hash_content(content: str) -> str:
    """Return the stable xxhash hex digest of *content*."""
    return xxhash.xxh3_64_hexdigest(content.encode("utf-8"))


class TaintRegistry:
    """Per-run registry of content provenance.

    Lives under ``state['metadata']['hardening']['taint']`` (serialized via
    :meth:`to_dict`). Only hashes + enums are stored, so it is safe in
    checkpointed state.
    """

    def __init__(self) -> None:
        """Initialize an empty registry."""
        self._tags: dict[str, TaintTag] = {}

    def mark_untrusted(self, content: str, provenance: Provenance) -> TaintTag:
        """Record *content* as untrusted with the given *provenance*.

        Returns the :class:`TaintTag`. Re-marking identical content overwrites
        the prior tag (last provenance wins) but keeps the same hash key.
        """
        tag = TaintTag(content_hash=_hash_content(content), provenance=provenance, trusted=False)
        self._tags[tag.content_hash] = tag
        return tag

    def is_untrusted(self, content: str) -> bool:
        """Return True if *content* was marked untrusted in this run."""
        tag = self._tags.get(_hash_content(content))
        return tag is not None and not tag.trusted

    def tag_for(self, content: str) -> TaintTag | None:
        """Return the recorded :class:`TaintTag` for *content*, or None."""
        return self._tags.get(_hash_content(content))

    # ── Serialization (safe in checkpointed state) ─────────────────────────────

    def to_dict(self) -> dict[str, dict[str, object]]:
        """Serialize to a JSON-safe mapping of ``hash -> {provenance, trusted}``."""
        return {
            h: {"provenance": tag.provenance.value, "trusted": tag.trusted}
            for h, tag in self._tags.items()
        }

    @classmethod
    def from_dict(cls, data: dict[str, dict[str, object]]) -> TaintRegistry:
        """Rebuild a registry from :meth:`to_dict` output."""
        reg = cls()
        for h, payload in (data or {}).items():
            reg._tags[h] = TaintTag(
                content_hash=h,
                provenance=Provenance(str(payload.get("provenance", "tool"))),
                trusted=bool(payload.get("trusted", False)),
            )
        return reg


# ── Active per-run registry (so deep loaders can tag without state access) ───────

# Loaders (rag/loaders, mcp adapter, multimodal, souls) run far below the graph
# state, so the per-run registry is exposed through a ContextVar set by the node
# seam (only when hardening_enabled). When unset, the tagging helper is a no-op,
# leaving the disabled path untouched.
_ACTIVE_REGISTRY: ContextVar[TaintRegistry | None] = ContextVar(
    "prismal_active_taint_registry", default=None
)


def get_active_taint_registry() -> TaintRegistry | None:
    """Return the active per-run :class:`TaintRegistry`, or None when unset."""
    return _ACTIVE_REGISTRY.get()


def mark_untrusted_active(content: str, provenance: Provenance) -> TaintTag | None:
    """Tag *content* in the active registry; no-op (returns None) when unset.

    Loaders call this at their boundary. It is safe to call unconditionally:
    when no run has an active registry (hardening off, or outside a run) it does
    nothing.
    """
    registry = _ACTIVE_REGISTRY.get()
    if registry is None:
        return None
    return registry.mark_untrusted(content, provenance)


@contextmanager
def use_taint_registry(registry: TaintRegistry) -> Iterator[TaintRegistry]:
    """Bind *registry* as the active one for the duration of the context."""
    token = _ACTIVE_REGISTRY.set(registry)
    try:
        yield registry
    finally:
        _ACTIVE_REGISTRY.reset(token)


__all__ = [
    "Provenance",
    "TaintRegistry",
    "TaintTag",
    "get_active_taint_registry",
    "mark_untrusted_active",
    "use_taint_registry",
]

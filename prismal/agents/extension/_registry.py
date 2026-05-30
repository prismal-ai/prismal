"""Thread-safe in-process registry of nodes declared via ``@prismal_node``.

Internal module (prefixed ``_``): not part of the public extension API.
The public accessors live in :mod:`prismal.agents.extension.decorators`
(:func:`list_registered_nodes`, :func:`get_node_metadata`).
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from prismal.agents.extension.decorators import NodeMetadata

_LOCK = threading.Lock()
_REGISTERED_NODES: dict[str, NodeMetadata] = {}


def register_node(metadata: NodeMetadata) -> None:
    """Register (or overwrite) node metadata under its name, thread-safely."""
    with _LOCK:
        _REGISTERED_NODES[metadata.name] = metadata


def get_node(name: str) -> NodeMetadata | None:
    """Return metadata for ``name`` or ``None`` if unregistered."""
    with _LOCK:
        return _REGISTERED_NODES.get(name)


def all_nodes() -> list[NodeMetadata]:
    """Return a snapshot of all registered node metadata."""
    with _LOCK:
        return list(_REGISTERED_NODES.values())


def clear() -> None:
    """Remove all registered nodes (test helper)."""
    with _LOCK:
        _REGISTERED_NODES.clear()

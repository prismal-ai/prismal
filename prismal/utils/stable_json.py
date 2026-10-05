"""Deterministic JSON serialization (Phase TKE — SPEC-TKE-LAY-001).

Byte-stable output (sorted keys, compact separators, unicode kept) so tool
schemas and prompt prefixes serialize identically across requests — a
precondition for provider prompt-cache hits.
"""

from __future__ import annotations

import json


def stable_dumps(obj: object) -> str:
    """Serialize *obj* with sorted keys and compact separators; unknown types via ``str``."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


__all__ = ["stable_dumps"]

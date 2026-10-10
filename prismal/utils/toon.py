"""TOON (Token-Oriented Object Notation) encoder (Phase TKE — SPEC-TKE-TOON-001).

Encodes *uniform tabular* data — a list of flat dicts sharing identical keys and
scalar values — as a header plus CSV-like rows, which cuts tokens on flat data.
Nested or non-uniform data stays JSON (TOON is worse there).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from prismal.utils.stable_json import stable_dumps

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

_SCALARS = (str, int, float, bool, type(None))
_NEEDS_QUOTES = (",", '"', "\n", ":")


def is_uniform_tabular(obj: object) -> bool:
    """True for a non-empty list of dicts with identical keys and scalar values."""
    if not isinstance(obj, list) or not obj:
        return False
    rows = cast("list[object]", obj)
    if not all(isinstance(row, dict) for row in rows):
        return False
    dicts = cast("list[dict[str, object]]", rows)
    keys = set(dicts[0])
    if not keys:
        return False
    return all(
        set(row) == keys and all(isinstance(v, _SCALARS) for v in row.values()) for row in dicts
    )


def encode_toon(rows: Sequence[Mapping[str, object]], *, name: str = "items") -> str:
    """Encode uniform *rows* as ``name[N]{k1,k2}:`` followed by indented value rows."""
    keys = sorted(rows[0]) if rows else []
    header = f"{name}[{len(rows)}]{{{','.join(keys)}}}:"
    lines = [header]
    lines.extend("  " + ",".join(_cell(row.get(k)) for k in keys) for row in rows)
    return "\n".join(lines)


def maybe_toon(obj: object) -> str:
    """TOON when *obj* is uniform tabular, else :func:`stable_dumps`."""
    if is_uniform_tabular(obj):
        return encode_toon(cast("list[dict[str, object]]", obj))
    return stable_dumps(obj)


def _cell(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value)
    if any(ch in text for ch in _NEEDS_QUOTES) or text != text.strip():
        return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return text


__all__ = ["encode_toon", "is_uniform_tabular", "maybe_toon"]

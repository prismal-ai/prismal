"""TOON encoder (Phase TKE — SPEC-TKE-TOON-001)."""

from __future__ import annotations

from prismal.utils.stable_json import stable_dumps
from prismal.utils.toon import encode_toon, is_uniform_tabular, maybe_toon

_ROWS = [
    {"id": 1, "name": "Ana", "active": True},
    {"id": 2, "name": "Luis", "active": False},
]


def test_uniform_tabular_detection() -> None:
    assert is_uniform_tabular(_ROWS) is True
    assert is_uniform_tabular([]) is False
    assert is_uniform_tabular([{"a": 1}, {"b": 2}]) is False
    assert is_uniform_tabular([{"a": {"nested": 1}}]) is False
    assert is_uniform_tabular([{"a": [1, 2]}]) is False
    assert is_uniform_tabular({"a": 1}) is False
    assert is_uniform_tabular(["x", "y"]) is False


def test_encode_toon_golden() -> None:
    assert encode_toon(_ROWS) == "items[2]{active,id,name}:\n  true,1,Ana\n  false,2,Luis"


def test_encode_toon_quotes_values_with_delimiters() -> None:
    out = encode_toon([{"t": "a, b"}, {"t": 'say "hi"'}, {"t": None}], name="rows")
    assert out == 'rows[3]{t}:\n  "a, b"\n  "say \\"hi\\""\n  null'


def test_maybe_toon_uniform_vs_nested() -> None:
    assert maybe_toon(_ROWS).startswith("items[2]")
    nested = {"rows": _ROWS}
    assert maybe_toon(nested) == stable_dumps(nested)

"""stable_dumps + estimate_tokens (Phase TKE — SPEC-TKE-LAY-001 / SPEC-TKE-OUT-001)."""

from __future__ import annotations

import math

from prismal.utils.stable_json import stable_dumps
from prismal.utils.token_estimate import estimate_tokens


def test_stable_dumps_sorts_keys_compactly() -> None:
    assert stable_dumps({"b": 1, "a": {"d": 2, "c": [3, 4]}}) == '{"a":{"c":[3,4],"d":2},"b":1}'


def test_stable_dumps_is_insertion_order_independent() -> None:
    assert stable_dumps({"x": 1, "y": 2}) == stable_dumps({"y": 2, "x": 1})


def test_stable_dumps_keeps_unicode() -> None:
    assert stable_dumps({"k": "ñandú"}) == '{"k":"ñandú"}'


def test_stable_dumps_falls_back_to_str_for_unknown_objects() -> None:
    class Odd:
        def __str__(self) -> str:
            return "odd"

    assert stable_dumps({"o": Odd()}) == '{"o":"odd"}'


def test_estimate_tokens_is_ceil_len_over_four() -> None:
    assert estimate_tokens("") == 0
    assert estimate_tokens("abc") == 1
    assert estimate_tokens("a" * 4001) == math.ceil(4001 / 4)

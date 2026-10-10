"""AG-UI translator never leaks internal state (review finding, M5).

Canary-based: internal ``metadata`` sub-trees (security, hardening, identity on-behalf token,
multimodal media paths) and full-state dumps must never reach any ``to_sse()`` frame, whether
they arrive through the initial snapshot, ``updates`` deltas, ``values`` chunks, non-mapping
``updates`` payloads, unknown custom payloads or unparsable chunks.
"""

from __future__ import annotations

import itertools
from typing import Any

import pytest

from prismal.events.agui import (
    ALLOWED_METADATA_KEYS,
    AgUiContext,
    AgUiEvent,
    finish_run,
    start_run,
    state_view,
    to_agui_events,
    to_sse,
)

pytestmark = pytest.mark.unit

CANARY = "CANARY-7f3a9c-LEAK"


def _ctx(**kw: Any) -> AgUiContext:
    counter = itertools.count(1)
    return AgUiContext(run_id="r1", thread_id="t1", clock=lambda: next(counter), **kw)


def _poisoned_metadata() -> dict[str, Any]:
    return {
        "security": {"canary": CANARY},
        "hardening": {"taint": CANARY},
        "identity": {
            "on_behalf": {
                "token_id": CANARY,
                "subject": f"user-{CANARY}",
                "scopes": [CANARY],
                "chain": [CANARY],
            }
        },
        "mm": {"media": [{"path": f"/var/prismal/media/{CANARY}.png", "modality": "image"}]},
        "skynet": {"result": CANARY},
        "kokoro": {"verdict": {"decision": CANARY}},
        # Allowed keys carrying smuggled nested internals must be projected, not copied.
        "budget": {"enabled": True, "scope": "turn", "identity": {"token_id": CANARY}},
        "intent": {"security": CANARY},
        "_hitl_last_action": CANARY,
    }


def _poisoned_state() -> dict[str, Any]:
    return {
        "current_agent": "supervisor",
        "next_agent": "coder",
        "metadata": _poisoned_metadata(),
        "risk_score": CANARY,
    }


def _sse(events: list[AgUiEvent]) -> str:
    return "".join(to_sse(e) for e in events)


def _assert_clean(events: list[AgUiEvent]) -> None:
    out = _sse(events)
    assert CANARY not in out
    assert "on_behalf" not in out and "token_id" not in out


# ------------------------------------------------------------------------- allowlist


def test_allowlist_is_minimal_and_excludes_internals() -> None:
    assert "budget" in ALLOWED_METADATA_KEYS
    for key in ("security", "hardening", "identity", "mm", "routing", "decision"):
        assert key not in ALLOWED_METADATA_KEYS


def test_state_view_keeps_only_allowlisted_metadata_projection() -> None:
    meta = _poisoned_metadata()
    meta["intent"] = "code"
    meta["unknown_feature"] = {"x": 1}
    view = state_view({"metadata": meta})
    assert view == {"metadata": {"budget": {"enabled": True, "scope": "turn"}, "intent": "code"}}


def test_state_view_drops_metadata_entirely_when_nothing_allowed() -> None:
    assert state_view({"metadata": {"security": {"canary": CANARY}}}) == {}


# ------------------------------------------------------------------------- snapshot / deltas


def test_initial_snapshot_never_leaks() -> None:
    _assert_clean(start_run(_ctx(), initial_state=_poisoned_state()))


def test_updates_delta_never_leaks() -> None:
    ctx = _ctx()
    events = start_run(ctx, initial_state={"current_agent": "supervisor", "metadata": {}})
    events += to_agui_events(("updates", {"supervisor": _poisoned_state()}), ctx)
    events += finish_run(ctx)
    assert "STATE_DELTA" in [str(e.type) for e in events]
    _assert_clean(events)


# ------------------------------------------------------------------------- RAW fallback


@pytest.mark.parametrize(
    ("chunk", "mode", "payload_type"),
    [
        (("values", _poisoned_state()), "values", "dict"),
        (("updates", [_poisoned_state()]), "updates", "list"),
        (("messages", {"state": _poisoned_state()}), "messages", "dict"),
        (("debug", {"payload": _poisoned_state()}), "debug", "dict"),
    ],
)
def test_raw_fallback_carries_no_state(chunk: Any, mode: str, payload_type: str) -> None:
    events = to_agui_events(chunk, _ctx())
    assert [str(e.type) for e in events] == ["RAW"]
    assert events[0].to_wire()["event"] == {"mode": mode, "payload_type": payload_type}
    _assert_clean(events)


def test_unparsable_chunk_raw_carries_no_state() -> None:
    events = to_agui_events(_poisoned_state(), _ctx())
    assert events[0].to_wire()["event"] == {"mode": "unknown", "payload_type": "dict"}
    _assert_clean(events)


def test_custom_mapping_payload_is_allowlist_sanitized() -> None:
    payload = {**_poisoned_metadata(), "intent": "code"}
    events = to_agui_events(("custom", payload), _ctx())
    assert events[0].to_wire()["event"] == {
        "mode": "custom",
        "payload_type": "dict",
        "payload": {"budget": {"enabled": True, "scope": "turn"}, "intent": "code"},
    }
    _assert_clean(events)


def test_custom_full_state_payload_never_leaks() -> None:
    events = to_agui_events(("custom", _poisoned_state()), _ctx())
    assert events[0].to_wire()["event"] == {"mode": "custom", "payload_type": "dict"}
    _assert_clean(events)


def test_custom_non_mapping_payload_has_only_type() -> None:
    events = to_agui_events(("custom", [CANARY]), _ctx())
    assert events[0].to_wire()["event"] == {"mode": "custom", "payload_type": "list"}
    _assert_clean(events)


def test_routing_and_decision_customs_unchanged_and_gated() -> None:
    upd = {"metadata": {"routing": {"model": "m"}, "decision": {"label": "x"}}}
    on = to_agui_events(("updates", {"supervisor": upd}), _ctx())
    names = [e.to_wire().get("name") for e in on if str(e.type) == "CUSTOM"]
    assert names == ["prismal.routing", "prismal.decision"]
    off = to_agui_events(("updates", {"supervisor": upd}), _ctx(include_metadata=False))
    assert "CUSTOM" not in [str(e.type) for e in off]

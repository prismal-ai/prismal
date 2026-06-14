"""Per-run hardening registry (Phase H — H5-01/H5-02 seeding)."""

from __future__ import annotations

from langchain_core.messages import HumanMessage

from prismal.core.config import Settings
from prismal.security.hardening_run import (
    clear_hardening_run,
    get_injection_detector,
    get_runaway_guard,
    get_taint_registry,
    get_tool_policy,
    hardening_react_kwargs,
    maybe_seed_hardening_run,
)
from prismal.security.indirect_injection import IndirectInjectionDetector
from prismal.security.runaway import RunawayGuard
from prismal.security.taint import TaintRegistry
from prismal.security.tool_policy import RunToolPolicy


def _state(session_id: str = "s1") -> dict:
    return {"session_id": session_id, "messages": [HumanMessage(content="hi")], "metadata": {}}


def test_disabled_seeds_nothing() -> None:
    state = _state()
    maybe_seed_hardening_run(state, Settings(hardening_enabled=False))
    assert get_runaway_guard(state) is None
    assert get_injection_detector(state) is None
    assert get_tool_policy(state) is None
    assert "hardening" not in state["metadata"]
    # Disabled path passes NO extra react_loop kwargs (byte-for-byte unchanged).
    assert hardening_react_kwargs(state) == {}
    clear_hardening_run(state)


def test_enabled_react_kwargs_are_populated() -> None:
    state = _state("kw")
    try:
        maybe_seed_hardening_run(state, Settings(hardening_enabled=True))
        kwargs = hardening_react_kwargs(state)
        assert set(kwargs) == {"injection_detector", "tool_policy", "runaway_guard"}
        assert all(v is not None for v in kwargs.values())
    finally:
        clear_hardening_run(state)


def test_enabled_seeds_engines_and_marker() -> None:
    state = _state("s2")
    try:
        maybe_seed_hardening_run(state, Settings(hardening_enabled=True))
        assert isinstance(get_runaway_guard(state), RunawayGuard)
        assert isinstance(get_injection_detector(state), IndirectInjectionDetector)
        assert isinstance(get_tool_policy(state), RunToolPolicy)
        assert isinstance(get_taint_registry(state), TaintRegistry)
        assert state["metadata"]["hardening"]["enabled"] is True
    finally:
        clear_hardening_run(state)


def test_seeding_is_idempotent_within_turn() -> None:
    state = _state("s3")
    try:
        maybe_seed_hardening_run(state, Settings(hardening_enabled=True))
        guard1 = get_runaway_guard(state)
        maybe_seed_hardening_run(state, Settings(hardening_enabled=True))
        guard2 = get_runaway_guard(state)
        assert guard1 is guard2  # same engine reused across supervisor hops
    finally:
        clear_hardening_run(state)


def test_new_turn_reseeds_fresh_engine() -> None:
    state = _state("s4")
    try:
        maybe_seed_hardening_run(state, Settings(hardening_enabled=True))
        guard1 = get_runaway_guard(state)
        # A new user turn arrives → fresh engine.
        state["messages"].append(HumanMessage(content="again"))
        maybe_seed_hardening_run(state, Settings(hardening_enabled=True))
        guard2 = get_runaway_guard(state)
        assert guard1 is not guard2
    finally:
        clear_hardening_run(state)


def test_taint_only_when_tracking_enabled() -> None:
    state = _state("s5")
    try:
        maybe_seed_hardening_run(
            state, Settings(hardening_enabled=True, taint_tracking_enabled=False)
        )
        assert get_taint_registry(state) is None
    finally:
        clear_hardening_run(state)

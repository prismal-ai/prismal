"""Unit tests for the per-run observability registry (OBS2-04 — SPEC-OBS-RES-001)."""

from __future__ import annotations

import pytest

from prismal.monitoring.observability import FakeObservabilityProvider
from prismal.monitoring.observability_resolve import (
    clear_observability_run,
    get_observability_provider,
    seed_observability_run,
)


@pytest.fixture(autouse=True)
def _clean() -> object:
    # Isolate the in-process registry between tests.
    yield
    clear_observability_run("sess-x")
    clear_observability_run("sess-y")


def test_seed_returns_canonical_run_id() -> None:
    run_id = seed_observability_run(
        "sess-x", FakeObservabilityProvider(), agent_name="coder", turn=0
    )
    assert run_id == "coder.sess-x.turn0"


def test_get_returns_seeded_provider() -> None:
    provider = FakeObservabilityProvider()
    seed_observability_run("sess-x", provider, agent_name="coder", turn=0)
    assert get_observability_provider("sess-x") is provider


def test_get_unknown_session_returns_none() -> None:
    assert get_observability_provider("sess-y") is None


def test_seed_idempotent_per_turn() -> None:
    first = FakeObservabilityProvider()
    run_id_1 = seed_observability_run("sess-x", first, agent_name="coder", turn=0)
    # Same (session, turn): idempotent — keeps the first provider, same run_id.
    run_id_2 = seed_observability_run(
        "sess-x", FakeObservabilityProvider(), agent_name="coder", turn=0
    )
    assert run_id_1 == run_id_2
    assert get_observability_provider("sess-x") is first


def test_seed_new_turn_replaces_run() -> None:
    first = FakeObservabilityProvider()
    seed_observability_run("sess-x", first, agent_name="coder", turn=0)
    second = FakeObservabilityProvider()
    run_id = seed_observability_run("sess-x", second, agent_name="coder", turn=1)
    assert run_id == "coder.sess-x.turn1"
    assert get_observability_provider("sess-x") is second


def test_clear_removes_entry() -> None:
    seed_observability_run("sess-x", FakeObservabilityProvider(), agent_name="coder", turn=0)
    clear_observability_run("sess-x")
    assert get_observability_provider("sess-x") is None


def test_clear_is_idempotent() -> None:
    # Clearing an absent session must not raise.
    clear_observability_run("never-seen")

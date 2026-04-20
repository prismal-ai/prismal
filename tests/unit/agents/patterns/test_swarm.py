"""Unit tests for swarm_handoff (lightagent.agents.patterns.swarm).

Swarm handoff: a decentralised pattern where any agent can pass control to
any other agent in the allowlist. The handoff is recorded in the state's
metadata for audit and inspection.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from lightagent.agents.patterns.swarm import (
    VALID_HANDOFF_TARGETS,
    HandoffRecord,
    SwarmError,
    swarm_handoff,
)
from lightagent.core.exceptions import LightAgentError


def _state(**overrides: Any) -> dict[str, Any]:
    """Build a minimal AgentState-shaped dict for tests."""
    base = {
        "current_agent": "coder",
        "next_agent": None,
        "metadata": {},
        "session_id": "sess-test",
        "iteration_count": 0,
    }
    base.update(overrides)
    return base


# ── Dataclass / exception ─────────────────────────────────────────────────────


def test_handoff_record_is_instantiable() -> None:
    r = HandoffRecord(
        from_agent="coder",
        to_agent="critic",
        reason="needs review",
        timestamp="2026-04-19T12:00:00+00:00",
        context_snapshot={"session_id": "sess-1"},
    )
    assert r.from_agent == "coder"
    assert r.to_agent == "critic"


def test_valid_handoff_targets_is_frozenset() -> None:
    assert isinstance(VALID_HANDOFF_TARGETS, frozenset)
    # Contract: includes the core specialists per SPEC-PAT-007.
    for expected in {"coder", "researcher", "critic", "planner"}:
        assert expected in VALID_HANDOFF_TARGETS


def test_swarm_error_inherits_from_lightagent_error() -> None:
    assert issubclass(SwarmError, LightAgentError)


# ── Validation ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_handoff_rejects_self_handoff() -> None:
    state = _state(current_agent="coder")
    with pytest.raises(ValueError, match="self"):
        await swarm_handoff(
            current_agent="coder",
            target_agent="coder",
            state=state,
            reason="should fail",
        )


@pytest.mark.asyncio
async def test_handoff_rejects_unknown_target() -> None:
    state = _state()
    with pytest.raises(ValueError, match="not a valid handoff target"):
        await swarm_handoff(
            current_agent="coder",
            target_agent="nonexistent-agent",
            state=state,
            reason="should fail",
        )


@pytest.mark.asyncio
async def test_handoff_accepts_custom_valid_targets() -> None:
    state = _state()
    custom = frozenset({"custom_agent"})
    new_state = await swarm_handoff(
        current_agent="coder",
        target_agent="custom_agent",
        state=state,
        reason="custom routing",
        valid_targets=custom,
    )
    assert new_state["next_agent"] == "custom_agent"


# ── State updates ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_handoff_sets_next_agent() -> None:
    state = _state(current_agent="coder")
    new_state = await swarm_handoff(
        current_agent="coder",
        target_agent="critic",
        state=state,
        reason="code needs review",
    )
    assert new_state["next_agent"] == "critic"


@pytest.mark.asyncio
async def test_handoff_appends_to_handoff_history_in_metadata() -> None:
    state = _state(current_agent="coder", metadata={})
    new_state = await swarm_handoff(
        current_agent="coder",
        target_agent="researcher",
        state=state,
        reason="need facts",
    )
    history = new_state["metadata"]["handoff_history"]
    assert len(history) == 1
    entry = history[0]
    assert entry["from_agent"] == "coder"
    assert entry["to_agent"] == "researcher"
    assert entry["reason"] == "need facts"
    assert "timestamp" in entry


@pytest.mark.asyncio
async def test_handoff_preserves_existing_history() -> None:
    prior = {
        "from_agent": "researcher",
        "to_agent": "coder",
        "reason": "code it",
        "timestamp": "2026-04-18T00:00:00+00:00",
        "context_snapshot": {},
    }
    state = _state(metadata={"handoff_history": [prior]})
    new_state = await swarm_handoff(
        current_agent="coder",
        target_agent="critic",
        state=state,
        reason="now review",
    )
    history = new_state["metadata"]["handoff_history"]
    assert len(history) == 2
    assert history[0]["reason"] == "code it"
    assert history[1]["reason"] == "now review"


@pytest.mark.asyncio
async def test_handoff_does_not_mutate_input_state() -> None:
    """swarm_handoff is pure: input state is not modified in place."""
    state = _state(current_agent="coder", metadata={})
    before_metadata_id = id(state["metadata"])
    new_state = await swarm_handoff(
        current_agent="coder",
        target_agent="critic",
        state=state,
        reason="r",
    )
    # Input state unchanged
    assert state.get("next_agent") is None
    assert "handoff_history" not in state["metadata"]
    # Metadata dict on the new state is a fresh object
    assert id(new_state["metadata"]) != before_metadata_id


@pytest.mark.asyncio
async def test_handoff_context_snapshot_includes_session_id() -> None:
    state = _state(session_id="sess-XYZ", iteration_count=5)
    new_state = await swarm_handoff(
        current_agent="coder",
        target_agent="critic",
        state=state,
        reason="r",
    )
    snapshot = new_state["metadata"]["handoff_history"][0]["context_snapshot"]
    assert snapshot["session_id"] == "sess-XYZ"
    assert snapshot["iteration_count"] == 5


# ── Audit logging ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_handoff_emits_structured_audit_log() -> None:
    state = _state()
    with patch("lightagent.agents.patterns.swarm.logger") as mock_logger:
        await swarm_handoff(
            current_agent="coder",
            target_agent="critic",
            state=state,
            reason="for audit",
        )
    info_calls = [c for c in mock_logger.info.call_args_list if c.args]
    event_names = [c.args[0] for c in info_calls]
    assert "swarm_handoff_recorded" in event_names


# ── Missing metadata initialisation ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_handoff_initialises_metadata_when_missing() -> None:
    """If state has no 'metadata' key, handoff should create it."""
    state = {
        "current_agent": "coder",
        "session_id": "s",
        "iteration_count": 0,
    }
    new_state = await swarm_handoff(
        current_agent="coder",
        target_agent="critic",
        state=state,
        reason="r",
    )
    assert "metadata" in new_state
    assert len(new_state["metadata"]["handoff_history"]) == 1

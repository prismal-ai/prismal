"""Unit tests for the BlindnessGuard runtime backstop (Phase BRP3-03)."""

from __future__ import annotations

import pytest


def test_guard_raises_on_leak() -> None:
    """A prompt embedding serialized message history raises the violation (SPEC-BRP-REV-001)."""
    from prismal.agents.subgraphs.blind_review_pipeline.reviewer_node import BlindnessGuard
    from prismal.core.exceptions import BlindReviewBlindnessViolationError

    leaky = (
        "Review this. Context: [HumanMessage(content='secret goal'), "
        "AIMessage(content='ok'), ToolMessage(tool_call_id='abc')]"
    )

    with pytest.raises(BlindReviewBlindnessViolationError):
        BlindnessGuard.assert_no_message_leak(leaky)


def test_guard_passes_clean_text() -> None:
    """Ordinary spec/artifact text passes the guard unchanged (SPEC-BRP-REV-001)."""
    from prismal.agents.subgraphs.blind_review_pipeline.reviewer_node import BlindnessGuard

    # Must not raise.
    BlindnessGuard.assert_no_message_leak(
        "A spec for a CSV parser.",
        "def parse(path):\n    return []\n",
    )

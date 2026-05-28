"""Tests for advanced-architecture intent patterns (Phase D / D1-03).

``match_intent`` gains conservative, unambiguous regexes for the advanced
patterns/subgraphs. These names only short-circuit routing when
``enable_subgraphs`` is on (gated in the supervisor), so the matcher itself
stays pure and side-effect-free.
"""

from __future__ import annotations

import pytest

from prismal.agents.intent_router import match_intent


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("run the code review pipeline on my repo", "code_review"),
        ("please do a code review of this module", "code_review"),
        ("run an ETL pipeline to load the data", "data_etl"),
        ("extract, transform and load the dataset", "data_etl"),
        ("use tree of thoughts to solve this puzzle", "tot_agent"),
        ("hold a debate and reach a consensus on this", "debate_consensus"),
        ("run a debate between both sides", "debate_agent"),
    ],
)
def test_advanced_intents_match(text: str, expected: str) -> None:
    assert match_intent(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "fix this bug in my code",
        "write a function to sort a list",
        "what is the weather today",
        "explain how tree of thoughts works",  # educational → no short-circuit
    ],
)
def test_non_advanced_text_does_not_match(text: str) -> None:
    assert match_intent(text) is None


def test_cron_intent_still_wins() -> None:
    # Existing cron behaviour must remain intact.
    assert match_intent("list my cron jobs") == "cron_manager"

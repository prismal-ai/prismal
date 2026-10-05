"""Canary leak-detection parity LEGACY vs CACHE_STABLE (Phase TKE — TKE2-09).

Release blocker for 3.13.0 (DD-TKE-004): relocating the per-request canary must
not reduce leak detection. Every Phase H red-team input is run through both
layouts against simulated leaking models; detection recall must be identical
(and total) for each leak mode.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from prismal.core.config import Settings
from prismal.eval.types import EvalSet
from prismal.security.prompt_builder import PromptLayout, SecurePromptBuilder

_CORPUS = EvalSet.from_yaml("tests/eval/redteam/corpus.yaml")
_ATTACKS = [case.input for case in _CORPUS.cases]

LeakMode = Callable[[list[dict[str, str]]], str]


def _echo_system(messages: list[dict[str, str]]) -> str:
    return f"My instructions are: {messages[0]['content']}"


def _echo_everything(messages: list[dict[str, str]]) -> str:
    return "\n".join(m["content"] for m in messages)


def _echo_user_turn(messages: list[dict[str, str]]) -> str:
    return f"You said: {messages[1]['content']}"


_MODES: dict[str, LeakMode] = {
    "system_prompt_leak": _echo_system,
    "full_prompt_leak": _echo_everything,
}


def _recall(layout: PromptLayout, mode: LeakMode) -> float:
    settings = Settings(token_efficiency_enabled=True, prompt_canary_secret="redteam")
    detected = 0
    for attack in _ATTACKS:
        builder = SecurePromptBuilder(settings=settings)
        messages = builder.build(
            "You are a careful assistant.", attack, layout=layout, tenant="acme"
        )
        if builder.detect_leak(mode(messages)).leaked:
            detected += 1
    return detected / len(_ATTACKS)


def test_corpus_is_not_empty() -> None:
    assert len(_ATTACKS) >= 5


@pytest.mark.parametrize("mode_name", sorted(_MODES))
def test_recall_parity_and_total(mode_name: str) -> None:
    mode = _MODES[mode_name]
    legacy = _recall(PromptLayout.LEGACY, mode)
    stable = _recall(PromptLayout.CACHE_STABLE, mode)
    assert legacy == stable == 1.0


def test_cache_stable_also_catches_turn_context_echo() -> None:
    # Legacy keeps the canary out of the user turn; cache-stable puts the request
    # canary there, so echoing the user turn must be detected (never less safe).
    assert _recall(PromptLayout.CACHE_STABLE, _echo_user_turn) == 1.0

"""Unit tests for ``prismal.agents.kokoro.deliberation`` (SPEC-KOK-AGT-002).

Covers RF-KOK-05/06 and the K4 "done when" criteria: deliberation stops at the
first round whose agreement reaches the threshold, never exceeds
``max_rounds``, and ``final_positions`` has one entry per soul.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from prismal.agents.kokoro.deliberation import DeliberationResult, deliberate
from prismal.agents.kokoro.soul_agent import SoulAgent
from prismal.core.config import Settings
from prismal.core.exceptions import DeliberationError, KokoroConfigError
from prismal.souls.base import Soul, SoulMetadata


def _make_soul_agent(name: str, role: str, reply: str) -> SoulAgent:
    soul = Soul(
        metadata=SoulMetadata(name=name, description=f"Test {name}", role=role),
        body=f"You are {name}.",
        source_dir=Path("/tmp/souls/available") / name,
    )

    async def generate(messages: list[dict[str, str]]) -> str:
        return reply

    return SoulAgent(soul, generate_fn=generate)


def _triad(reply_suffix: str = "") -> list[SoulAgent]:
    return [
        _make_soul_agent("spirit", "values", f"Protect integrity{reply_suffix}"),
        _make_soul_agent("mind", "logic", f"Evidence supports it{reply_suffix}"),
        _make_soul_agent("heart", "empathy", f"People benefit{reply_suffix}"),
    ]


def _settings(**overrides: object) -> Settings:
    return Settings(**overrides)  # type: ignore[arg-type]


# ── arity guard (K4-04) ──────────────────────────────────────────────────────


@pytest.mark.parametrize("n", [0, 1, 2, 4])
async def test_arity_guard_raises_kokoro_config_error(n: int) -> None:
    souls = _triad()[:n] if n <= 3 else [*_triad(), _make_soul_agent("extra", "x", "y")]
    with pytest.raises(KokoroConfigError, match="exactly 3 souls"):
        await deliberate("q", souls, settings=_settings())


async def test_max_rounds_below_one_rejected() -> None:
    with pytest.raises(KokoroConfigError, match="max_rounds"):
        await deliberate("q", _triad(), max_rounds=0, settings=_settings())


# ── early stop / bounds (K4-02, K4-03) ───────────────────────────────────────


async def test_early_stop_on_first_round_at_threshold() -> None:
    result = await deliberate(
        "q",
        _triad(),
        max_rounds=5,
        agreement_threshold=0.5,
        agreement_fn=lambda texts: 1.0,
        settings=_settings(),
    )
    assert result.rounds_completed == 1
    assert result.converged is True
    assert result.agreement_score == 1.0
    assert len(result.positions) == 3


async def test_never_exceeds_max_rounds_when_no_convergence() -> None:
    result = await deliberate(
        "q",
        _triad(),
        max_rounds=3,
        agreement_threshold=0.9,
        agreement_fn=lambda texts: 0.0,
        settings=_settings(),
    )
    assert result.rounds_completed == 3
    assert result.converged is False
    assert len(result.positions) == 9  # 3 souls × 3 rounds


async def test_stops_at_first_round_reaching_threshold() -> None:
    scores = iter([0.2, 0.95, 0.99])

    result = await deliberate(
        "q",
        _triad(),
        max_rounds=5,
        agreement_threshold=0.9,
        agreement_fn=lambda texts: next(scores),
        settings=_settings(),
    )
    assert result.rounds_completed == 2
    assert result.converged is True
    assert result.agreement_score == 0.95


async def test_defaults_resolve_from_settings() -> None:
    """max_rounds/threshold default to kokoro_* settings (2 / 0.6)."""
    result = await deliberate(
        "q",
        _triad(),
        agreement_fn=lambda texts: 0.0,
        settings=_settings(kokoro_max_rounds=2, kokoro_agreement_threshold=0.6),
    )
    assert result.rounds_completed == 2
    assert result.converged is False


async def test_default_agreement_fn_is_pairwise_jaccard() -> None:
    """Identical position texts yield a perfect default agreement score."""
    souls = [
        _make_soul_agent("spirit", "values", "ship it now"),
        _make_soul_agent("mind", "logic", "ship it now"),
        _make_soul_agent("heart", "empathy", "ship it now"),
    ]
    result = await deliberate("q", souls, max_rounds=3, settings=_settings())
    assert result.agreement_score == 1.0
    assert result.converged is True
    assert result.rounds_completed == 1


# ── result shape (K4-01) ─────────────────────────────────────────────────────


async def test_final_positions_one_per_soul_with_correct_round() -> None:
    result = await deliberate(
        "q",
        _triad(),
        max_rounds=2,
        agreement_fn=lambda texts: 0.0,
        settings=_settings(),
    )
    assert isinstance(result, DeliberationResult)
    assert [p.agent_id for p in result.final_positions] == ["spirit", "mind", "heart"]
    assert all(p.round == 2 for p in result.final_positions)
    assert [p.role for p in result.final_positions] == ["values", "logic", "empathy"]


async def test_result_is_frozen() -> None:
    result = await deliberate(
        "q", _triad(), max_rounds=1, agreement_fn=lambda texts: 1.0, settings=_settings()
    )
    with pytest.raises(AttributeError):
        result.converged = False  # type: ignore[misc]


# ── cross-revision (K4-02) ───────────────────────────────────────────────────


async def test_revision_round_sees_only_other_souls_positions() -> None:
    """In round 2 each soul receives the other souls' round-1 positions."""
    captured: dict[str, str] = {}

    def make_capturing_agent(name: str, role: str, reply: str) -> SoulAgent:
        soul = Soul(
            metadata=SoulMetadata(name=name, description=name, role=role),
            body=f"You are {name}.",
            source_dir=Path("/tmp/souls/available") / name,
        )

        async def generate(messages: list[dict[str, str]]) -> str:
            captured[name] = messages[1]["content"]  # last call wins (round 2)
            return reply

        return SoulAgent(soul, generate_fn=generate)

    souls = [
        make_capturing_agent("spirit", "values", "UNIQUE-SPIRIT"),
        make_capturing_agent("mind", "logic", "UNIQUE-MIND"),
        make_capturing_agent("heart", "empathy", "UNIQUE-HEART"),
    ]
    await deliberate("q", souls, max_rounds=2, agreement_fn=lambda texts: 0.0, settings=_settings())

    assert "UNIQUE-MIND" in captured["spirit"]
    assert "UNIQUE-HEART" in captured["spirit"]
    assert "UNIQUE-SPIRIT" not in captured["spirit"]
    assert "UNIQUE-SPIRIT" in captured["mind"]
    assert "UNIQUE-MIND" not in captured["mind"]


# ── failure handling ─────────────────────────────────────────────────────────


async def test_round_one_failure_propagates_deliberation_error() -> None:
    soul = Soul(
        metadata=SoulMetadata(name="spirit", description="x", role="values"),
        body="b",
        source_dir=Path("/tmp/souls/available/spirit"),
    )

    async def boom(messages: list[dict[str, str]]) -> str:
        raise RuntimeError("backend down")

    souls = [
        SoulAgent(soul, generate_fn=boom),
        _make_soul_agent("mind", "logic", "x"),
        _make_soul_agent("heart", "empathy", "y"),
    ]
    with pytest.raises(DeliberationError, match="spirit"):
        await deliberate("q", souls, max_rounds=1, settings=_settings())


async def test_revision_failure_falls_back_to_previous_position() -> None:
    """A soul that fails in round 2 keeps its round-1 position (carried forward)."""
    calls = {"n": 0}
    soul = Soul(
        metadata=SoulMetadata(name="spirit", description="x", role="values"),
        body="b",
        source_dir=Path("/tmp/souls/available/spirit"),
    )

    async def flaky(messages: list[dict[str, str]]) -> str:
        calls["n"] += 1
        if calls["n"] > 1:
            raise RuntimeError("flaked in revision")
        return "spirit-round-1"

    souls = [
        SoulAgent(soul, generate_fn=flaky),
        _make_soul_agent("mind", "logic", "mind-says"),
        _make_soul_agent("heart", "empathy", "heart-says"),
    ]
    result = await deliberate(
        "q", souls, max_rounds=2, agreement_fn=lambda texts: 0.0, settings=_settings()
    )

    spirit_final = next(p for p in result.final_positions if p.agent_id == "spirit")
    assert spirit_final.content == "spirit-round-1"
    assert spirit_final.round == 2
    assert len(result.final_positions) == 3

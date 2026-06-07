"""Unit tests for ``prismal.agents.kokoro.soul_agent`` (SPEC-KOK-AGT-001).

Covers RF-KOK-04 and the K3 "done when" criteria: ``position()`` returns a
:class:`DebatePosition`; a spy proves the soul body passed through
``SecurePromptBuilder`` (sanitized ``user`` channel) and was never
raw-concatenated into the trusted system template.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from prismal.agents.kokoro.soul_agent import SoulAgent
from prismal.agents.patterns.debate import DebatePosition
from prismal.core.exceptions import DeliberationError, KokoroError
from prismal.security.prompt_builder import SecurePromptBuilder
from prismal.souls.base import Soul, SoulMetadata

_BODY = "You are **Spirit**, argue from integrity and the long-term good."


def _make_soul(name: str = "spirit", *, body: str = _BODY, model: str = "") -> Soul:
    return Soul(
        metadata=SoulMetadata(
            name=name,
            description=f"Test soul {name}",
            role="values",
            temperament="principled, calm",
            values=["integrity", "long-term-good"],
            model=model,
        ),
        body=body,
        source_dir=Path("/tmp/souls/available") / name,
    )


class SpyPromptBuilder(SecurePromptBuilder):
    """Records every build() call while delegating to the real builder."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[dict[str, str]] = []

    def build(
        self,
        system: str,
        user: str,
        docs: list[str] | None = None,
    ) -> list[dict[str, str]]:
        self.calls.append({"system": system, "user": user})
        return super().build(system, user, docs)


class FakeGenerate:
    """Deterministic generate_fn that captures the messages it receives."""

    def __init__(self, reply: str = "We should proceed with safeguards.") -> None:
        self.reply = reply
        self.received: list[list[dict[str, str]]] = []

    async def __call__(self, messages: list[dict[str, str]]) -> str:
        self.received.append(messages)
        return self.reply


# ── basics ────────────────────────────────────────────────────────────────────


async def test_position_returns_debate_position() -> None:
    agent = SoulAgent(_make_soul(), generate_fn=FakeGenerate())
    position = await agent.position("Should we ship now?")
    assert isinstance(position, DebatePosition)
    assert position.agent_id == "spirit"
    assert position.role == "values"
    assert position.content == "We should proceed with safeguards."
    assert position.round == 1


async def test_agent_id_is_soul_name() -> None:
    agent = SoulAgent(_make_soul(name="heart"), generate_fn=FakeGenerate())
    assert agent.agent_id == "heart"
    assert agent.soul.metadata.name == "heart"


async def test_position_strips_whitespace() -> None:
    agent = SoulAgent(_make_soul(), generate_fn=FakeGenerate(reply="  padded  \n"))
    position = await agent.position("q")
    assert position.content == "padded"


# ── secure prompt construction (RF-KOK-04) ───────────────────────────────────


async def test_soul_body_routes_through_secure_prompt_builder() -> None:
    """The spy proves the body went through builder.build as *user* content."""
    spy = SpyPromptBuilder()
    agent = SoulAgent(_make_soul(), generate_fn=FakeGenerate(), prompt_builder=spy)
    await agent.position("Should we ship now?")

    assert len(spy.calls) == 1
    assert _BODY in spy.calls[0]["user"]
    assert "Should we ship now?" in spy.calls[0]["user"]


async def test_soul_body_never_in_system_template() -> None:
    """The trusted system template contains no user-controlled content."""
    spy = SpyPromptBuilder()
    soul = _make_soul()
    agent = SoulAgent(soul, generate_fn=FakeGenerate(), prompt_builder=spy)
    await agent.position("Should we ship now?")

    system = spy.calls[0]["system"]
    assert _BODY not in system
    assert soul.metadata.name not in system
    assert soul.metadata.temperament not in system
    assert "Should we ship now?" not in system


async def test_generated_messages_isolate_body_in_user_input_tags() -> None:
    """generate_fn receives the body only inside the sanitized user message."""
    fake = FakeGenerate()
    agent = SoulAgent(_make_soul(), generate_fn=fake)
    await agent.position("q")

    messages = fake.received[0]
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    assert _BODY not in messages[0]["content"]
    assert _BODY in messages[1]["content"]
    assert messages[1]["content"].startswith("<user_input>")
    assert "<!-- canary:" in messages[0]["content"]


async def test_body_control_chars_are_sanitized() -> None:
    """InputSanitizer strips control characters from the persona body."""
    fake = FakeGenerate()
    agent = SoulAgent(_make_soul(body="evil\x00body\x07text"), generate_fn=fake)
    await agent.position("q")
    user_content = fake.received[0][1]["content"]
    assert "\x00" not in user_content
    assert "\x07" not in user_content
    assert "evilbodytext" in user_content


# ── rounds ────────────────────────────────────────────────────────────────────


async def test_round_inferred_from_prior_positions() -> None:
    fake = FakeGenerate()
    agent = SoulAgent(_make_soul(), generate_fn=fake)
    prior = [
        DebatePosition(agent_id="mind", role="logic", content="Evidence first.", round=1),
        DebatePosition(agent_id="heart", role="empathy", content="People first.", round=1),
    ]
    position = await agent.position("q", prior=prior)
    assert position.round == 2


async def test_prior_positions_appear_in_user_content() -> None:
    spy = SpyPromptBuilder()
    agent = SoulAgent(_make_soul(), generate_fn=FakeGenerate(), prompt_builder=spy)
    prior = [DebatePosition(agent_id="mind", role="logic", content="Evidence first.", round=1)]
    await agent.position("q", prior=prior)
    assert "Evidence first." in spy.calls[0]["user"]


async def test_first_and_revision_rounds_use_different_system_templates() -> None:
    spy = SpyPromptBuilder()
    agent = SoulAgent(_make_soul(), generate_fn=FakeGenerate(), prompt_builder=spy)
    await agent.position("q")
    prior = [DebatePosition(agent_id="mind", role="logic", content="x", round=1)]
    await agent.position("q", prior=prior)

    first_system, revision_system = spy.calls[0]["system"], spy.calls[1]["system"]
    assert first_system != revision_system
    assert "first round" in first_system
    assert "previous positions" in revision_system


# ── error handling ────────────────────────────────────────────────────────────


async def test_generate_failure_wrapped_as_deliberation_error() -> None:
    async def boom(messages: list[dict[str, str]]) -> str:
        raise RuntimeError("backend down")

    agent = SoulAgent(_make_soul(), generate_fn=boom)
    with pytest.raises(DeliberationError, match="spirit"):
        await agent.position("q")


async def test_deliberation_error_is_kokoro_error() -> None:
    async def boom(messages: list[dict[str, str]]) -> str:
        raise ValueError("nope")

    agent = SoulAgent(_make_soul(), generate_fn=boom)
    with pytest.raises(KokoroError):
        await agent.position("q")

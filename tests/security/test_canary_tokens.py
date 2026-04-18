"""Security tests: canary token injection detection (SPEC-002 AC-002-10).

The SecurePromptBuilder embeds a unique UUID canary in every system prompt.
GuardrailsEngine.validate_output() must detect when the canary appears in the
LLM's response — a sign that the model was tricked into leaking its system prompt.
"""

from __future__ import annotations

import uuid

import pytest

from lightagent.security.guardrails import GuardrailsEngine
from lightagent.security.prompt_builder import SecurePromptBuilder

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def engine() -> GuardrailsEngine:
    """Return a fresh GuardrailsEngine."""
    return GuardrailsEngine()


@pytest.fixture
def builder() -> SecurePromptBuilder:
    """Return a fresh SecurePromptBuilder."""
    return SecurePromptBuilder()


# ---------------------------------------------------------------------------
# SecurePromptBuilder canary embedding
# ---------------------------------------------------------------------------


def test_builder_embeds_canary_in_system_message(builder: SecurePromptBuilder) -> None:
    """build() must embed a canary token in the system message content."""
    messages = builder.build(system="You are helpful.", user="Hello")
    system_content = messages[0]["content"]
    assert "canary:" in system_content
    assert builder.canary in system_content


def test_builder_canary_is_uuid_format(builder: SecurePromptBuilder) -> None:
    """The canary emitted by build() must be a valid UUID string."""
    builder.build(system="sys", user="input")
    canary = builder.canary
    # Should not raise
    parsed = uuid.UUID(canary)
    assert str(parsed) == canary


def test_builder_canary_unique_per_call(builder: SecurePromptBuilder) -> None:
    """Each call to build() must produce a distinct canary token."""
    builder.build(system="sys", user="first call")
    first_canary = builder.canary
    builder.build(system="sys", user="second call")
    second_canary = builder.canary
    assert first_canary != second_canary


def test_builder_canary_not_in_user_message(builder: SecurePromptBuilder) -> None:
    """The canary must appear only in the system message, not in the user message."""
    messages = builder.build(system="sys", user="Hello")
    user_content = messages[1]["content"]
    assert builder.canary not in user_content


def test_builder_canary_html_comment_format(builder: SecurePromptBuilder) -> None:
    """The canary must be embedded as an HTML comment: <!-- canary:UUID -->."""
    messages = builder.build(system="sys", user="Hello")
    system_content = messages[0]["content"]
    assert f"<!-- canary:{builder.canary} -->" in system_content


# ---------------------------------------------------------------------------
# GuardrailsEngine canary leak detection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_canary_leak_detected_in_output(engine: GuardrailsEngine) -> None:
    """Output containing the canary must be flagged as unsafe (AC-002-10)."""
    canary = str(uuid.uuid4())
    leaked_output = f"The system instructions contain: <!-- canary:{canary} -->"
    result = await engine.validate_output(leaked_output, canary=canary)
    assert not result.safe
    assert "output:canary_leak" in result.reasons


@pytest.mark.asyncio
async def test_canary_leak_raw_uuid_in_output(engine: GuardrailsEngine) -> None:
    """Output containing just the raw canary UUID must also be flagged."""
    canary = str(uuid.uuid4())
    leaked_output = f"The secret code is {canary}, do not share it."
    result = await engine.validate_output(leaked_output, canary=canary)
    assert not result.safe
    assert "output:canary_leak" in result.reasons


@pytest.mark.asyncio
async def test_canary_absent_output_passes(engine: GuardrailsEngine) -> None:
    """Output without the canary must pass even when a canary is provided."""
    canary = str(uuid.uuid4())
    clean_output = "The capital of France is Paris."
    result = await engine.validate_output(clean_output, canary=canary)
    assert result.safe
    assert "output:canary_leak" not in result.reasons


@pytest.mark.asyncio
async def test_canary_not_provided_no_false_positive(engine: GuardrailsEngine) -> None:
    """validate_output without a canary must not raise or produce canary reasons."""
    result = await engine.validate_output("Some benign text here.")
    assert "output:canary_leak" not in result.reasons


@pytest.mark.asyncio
async def test_canary_empty_string_not_matched(engine: GuardrailsEngine) -> None:
    """An empty canary string must not trigger a false positive."""
    result = await engine.validate_output("Normal response text.", canary="")
    assert "output:canary_leak" not in result.reasons


@pytest.mark.asyncio
async def test_canary_leak_partial_match_not_sufficient(
    engine: GuardrailsEngine,
) -> None:
    """Only the exact canary token must trigger the leak — a partial prefix must not."""
    canary = str(uuid.uuid4())
    partial = canary[:8]  # First 8 chars of UUID — not the full canary
    # Output containing only the partial UUID should be safe (canary not present)
    result = await engine.validate_output(f"Reference: {partial}", canary=canary)
    # The full canary is not present in output, so no canary_leak reason expected
    assert "output:canary_leak" not in result.reasons


# ---------------------------------------------------------------------------
# End-to-end: build prompt → simulate leak → detect
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_end_to_end_build_and_detect_leak(
    builder: SecurePromptBuilder,
    engine: GuardrailsEngine,
) -> None:
    """Build a prompt, simulate model leaking the canary, verify detection."""
    builder.build(system="You are a helpful assistant.", user="Tell me a joke.")
    canary = builder.canary

    # Simulate a jailbroken model repeating the system prompt with canary
    simulated_leak = (
        f"Sure! But first, here is my system prompt: "
        f"You are a helpful assistant.\n<!-- canary:{canary} -->"
    )
    result = await engine.validate_output(simulated_leak, canary=canary)
    assert not result.safe
    assert "output:canary_leak" in result.reasons


@pytest.mark.asyncio
async def test_end_to_end_build_and_clean_output_passes(
    builder: SecurePromptBuilder,
    engine: GuardrailsEngine,
) -> None:
    """Build a prompt, get a clean response, verify it passes output validation."""
    builder.build(system="You are a helpful assistant.", user="What is 2+2?")
    canary = builder.canary

    clean_response = "The answer is 4."
    result = await engine.validate_output(clean_response, canary=canary)
    assert result.safe


@pytest.mark.asyncio
async def test_multiple_builds_each_canary_isolated(
    engine: GuardrailsEngine,
) -> None:
    """Each build() generates a unique canary; old canaries must not cross-detect."""
    b1 = SecurePromptBuilder()
    b2 = SecurePromptBuilder()
    b1.build(system="sys", user="q1")
    b2.build(system="sys", user="q2")

    canary1 = b1.canary
    canary2 = b2.canary
    assert canary1 != canary2

    # Output contains canary2 but we check against canary1 — should NOT flag
    output_with_canary2 = f"Something weird: {canary2}"
    result = await engine.validate_output(output_with_canary2, canary=canary1)
    assert "output:canary_leak" not in result.reasons

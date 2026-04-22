"""Unit tests for ConstitutionalFilter (lightagent.agents.patterns.constitutional).

Constitutional AI pattern: for each principle, ask an LLM whether the output
violates it; if so, ask the LLM to revise. Cap total revisions per principle
at ``max_revisions``. All revisions are logged.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lightagent.agents.patterns.constitutional import (
    DEFAULT_PRINCIPLES,
    ConstitutionalError,
    ConstitutionalFilter,
    ConstitutionalPrinciple,
    ConstitutionalResult,
    ConstitutionalRevision,
)
from lightagent.core.exceptions import LightAgentError

PROVIDER_REGISTRY_PATH = "lightagent.agents.patterns.constitutional.ProviderRegistry"


def _response(text: str) -> MagicMock:
    r = MagicMock()
    r.content = text
    return r


def _mock_llm(*texts: str) -> MagicMock:
    llm = MagicMock()
    llm.ainvoke = AsyncMock(side_effect=[_response(t) for t in texts])
    return llm


# ── Dataclasses / defaults ────────────────────────────────────────────────────


def test_constitutional_principle_is_instantiable() -> None:
    p = ConstitutionalPrinciple(
        id="P_TEST",
        name="test_principle",
        description="d",
        critique_prompt="c",
        revision_prompt="r",
        severity="high",
    )
    assert p.id == "P_TEST"
    assert p.severity == "high"


def test_constitutional_revision_is_instantiable() -> None:
    r = ConstitutionalRevision(
        principle_id="P1",
        original="orig",
        revised="rev",
        violation_detected="some PII",
    )
    assert r.principle_id == "P1"


def test_constitutional_result_is_instantiable() -> None:
    r = ConstitutionalResult(
        final_output="x",
        revisions=[],
        principles_checked=3,
        all_principles_satisfied=True,
    )
    assert r.final_output == "x"
    assert r.principles_checked == 3
    assert r.max_revisions_reached is False


def test_default_principles_has_at_least_three() -> None:
    assert len(DEFAULT_PRINCIPLES) >= 3
    # Each has complete fields
    for p in DEFAULT_PRINCIPLES:
        assert p.id and p.name and p.description
        assert p.critique_prompt and p.revision_prompt
        assert p.severity in {"critical", "high", "medium"}


def test_constitutional_error_inherits_from_lightagent_error() -> None:
    assert issubclass(ConstitutionalError, LightAgentError)


# ── Constructor ───────────────────────────────────────────────────────────────


def test_init_uses_default_principles_when_none() -> None:
    with patch(PROVIDER_REGISTRY_PATH) as reg:
        reg.return_value.get_llm.return_value = MagicMock()
        f = ConstitutionalFilter(settings=MagicMock())
    assert f._principles == DEFAULT_PRINCIPLES  # noqa: SLF001


def test_init_accepts_custom_principles() -> None:
    custom = [
        ConstitutionalPrinciple(
            id="X",
            name="x",
            description="d",
            critique_prompt="c",
            revision_prompt="r",
        )
    ]
    with patch(PROVIDER_REGISTRY_PATH) as reg:
        reg.return_value.get_llm.return_value = MagicMock()
        f = ConstitutionalFilter(principles=custom, settings=MagicMock())
    assert f._principles is custom  # noqa: SLF001


def test_init_rejects_nonpositive_max_revisions() -> None:
    with pytest.raises(ValueError):
        ConstitutionalFilter(max_revisions=0, settings=MagicMock())


# ── check_principle ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_check_principle_detects_violation() -> None:
    """LLM response starts with 'VIOLATION:' → (True, description)."""
    llm = _mock_llm("VIOLATION: text contains PII")
    with patch(PROVIDER_REGISTRY_PATH) as reg:
        reg.return_value.get_llm.return_value = llm
        f = ConstitutionalFilter(settings=MagicMock())
        violated, desc = await f.check_principle("some text", DEFAULT_PRINCIPLES[0])
    assert violated is True
    assert "PII" in desc


@pytest.mark.asyncio
async def test_check_principle_finds_no_violation() -> None:
    llm = _mock_llm("OK: no issues detected")
    with patch(PROVIDER_REGISTRY_PATH) as reg:
        reg.return_value.get_llm.return_value = llm
        f = ConstitutionalFilter(settings=MagicMock())
        violated, _ = await f.check_principle("benign text", DEFAULT_PRINCIPLES[0])
    assert violated is False


@pytest.mark.asyncio
async def test_check_principle_unparseable_defaults_to_not_violated() -> None:
    """Pessimistic choices would over-block benign text; default to safe path."""
    llm = _mock_llm("I don't know.")
    with patch(PROVIDER_REGISTRY_PATH) as reg:
        reg.return_value.get_llm.return_value = llm
        f = ConstitutionalFilter(settings=MagicMock())
        violated, _ = await f.check_principle("x", DEFAULT_PRINCIPLES[0])
    assert violated is False


# ── apply() — main flow ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_apply_with_no_violations_leaves_output_unchanged() -> None:
    # 3 principles × 1 check each = 3 "OK" calls, no revisions
    llm = _mock_llm(*(["OK: fine"] * len(DEFAULT_PRINCIPLES)))
    with patch(PROVIDER_REGISTRY_PATH) as reg:
        reg.return_value.get_llm.return_value = llm
        f = ConstitutionalFilter(settings=MagicMock())
        result = await f.apply("clean text")

    assert result.final_output == "clean text"
    assert result.revisions == []
    assert result.all_principles_satisfied is True
    assert result.max_revisions_reached is False
    assert result.principles_checked == len(DEFAULT_PRINCIPLES)


@pytest.mark.asyncio
async def test_apply_performs_revision_when_violation_detected() -> None:
    # 1 principle fixture; flow:
    #   check -> VIOLATION
    #   revise -> "clean version"
    #   check again -> OK
    principle = ConstitutionalPrinciple(
        id="P_X",
        name="x",
        description="d",
        critique_prompt="c",
        revision_prompt="r",
    )
    llm = _mock_llm(
        "VIOLATION: contains bad phrase",
        "clean version",
        "OK: all good now",
    )
    with patch(PROVIDER_REGISTRY_PATH) as reg:
        reg.return_value.get_llm.return_value = llm
        f = ConstitutionalFilter(principles=[principle], settings=MagicMock())
        result = await f.apply("bad phrase here")

    assert result.final_output == "clean version"
    assert len(result.revisions) == 1
    assert result.revisions[0].principle_id == "P_X"
    assert result.revisions[0].original == "bad phrase here"
    assert result.revisions[0].revised == "clean version"
    assert result.all_principles_satisfied is True


@pytest.mark.asyncio
async def test_apply_respects_max_revisions_cap() -> None:
    """When LLM keeps flagging violations, loop stops at max_revisions."""
    principle = ConstitutionalPrinciple(
        id="P_Y",
        name="y",
        description="d",
        critique_prompt="c",
        revision_prompt="r",
    )
    # LLM always flags — infinite loop without cap.
    llm = MagicMock()
    llm.ainvoke = AsyncMock(side_effect=lambda *a, **kw: _response("VIOLATION: still bad"))
    with patch(PROVIDER_REGISTRY_PATH) as reg:
        reg.return_value.get_llm.return_value = llm
        f = ConstitutionalFilter(principles=[principle], max_revisions=2, settings=MagicMock())
        result = await f.apply("bad")

    assert result.max_revisions_reached is True
    assert result.all_principles_satisfied is False
    # Should have attempted 2 revisions then stopped.
    assert len(result.revisions) == 2


@pytest.mark.asyncio
async def test_apply_logs_each_revision() -> None:
    """Each revision produces a structured log event for audit."""
    principle = ConstitutionalPrinciple(
        id="P_AUDIT",
        name="audit",
        description="d",
        critique_prompt="c",
        revision_prompt="r",
    )
    llm = _mock_llm(
        "VIOLATION: found something",
        "revised text",
        "OK: fine",
    )
    with (
        patch(PROVIDER_REGISTRY_PATH) as reg,
        patch("lightagent.agents.patterns.constitutional.logger") as mock_logger,
    ):
        reg.return_value.get_llm.return_value = llm
        f = ConstitutionalFilter(principles=[principle], settings=MagicMock())
        await f.apply("original")

    audit_calls = [
        c
        for c in mock_logger.info.call_args_list
        if c.args and c.args[0] == "constitutional_revision_applied"
    ]
    assert len(audit_calls) == 1
    kwargs = audit_calls[0].kwargs
    assert kwargs["principle_id"] == "P_AUDIT"


@pytest.mark.asyncio
async def test_apply_passes_context_through_to_llm() -> None:
    """When `context` is provided it is included in the critique prompt body."""
    principle = ConstitutionalPrinciple(
        id="P_CTX",
        name="ctx",
        description="d",
        critique_prompt="c",
        revision_prompt="r",
    )
    llm = _mock_llm("OK: ok")
    with patch(PROVIDER_REGISTRY_PATH) as reg:
        reg.return_value.get_llm.return_value = llm
        f = ConstitutionalFilter(principles=[principle], settings=MagicMock())
        await f.apply("output text", context="original user query")

    # The user-content of the first LLM call must mention the context
    call = llm.ainvoke.call_args_list[0]
    user_msg = call.args[0][1].content
    assert "original user query" in user_msg

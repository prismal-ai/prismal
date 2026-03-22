"""Unit tests for lightagent.agents.patterns — AgentPattern enum."""

from __future__ import annotations

from lightagent.agents.patterns import AgentPattern


def test_agent_pattern_is_str_enum() -> None:
    """AgentPattern must be a string enum (str subclass)."""
    assert issubclass(AgentPattern, str)


def test_agent_pattern_has_nine_values() -> None:
    """AgentPattern must define exactly 9 patterns (8 original + DEV_PIPELINE)."""
    assert len(AgentPattern) == 9


def test_supervisor_value() -> None:
    """SUPERVISOR must have string value 'supervisor'."""
    assert AgentPattern.SUPERVISOR == "supervisor"
    assert AgentPattern.SUPERVISOR.value == "supervisor"


def test_react_value() -> None:
    """REACT must have string value 'react'."""
    assert AgentPattern.REACT == "react"


def test_plan_execute_value() -> None:
    """PLAN_EXECUTE must have string value 'plan_execute'."""
    assert AgentPattern.PLAN_EXECUTE == "plan_execute"


def test_swarm_value() -> None:
    """SWARM must have string value 'swarm'."""
    assert AgentPattern.SWARM == "swarm"


def test_reflexion_value() -> None:
    """REFLEXION must have string value 'reflexion'."""
    assert AgentPattern.REFLEXION == "reflexion"


def test_codeact_value() -> None:
    """CODEACT must have string value 'codeact'."""
    assert AgentPattern.CODEACT == "codeact"


def test_crag_value() -> None:
    """CRAG must have string value 'crag'."""
    assert AgentPattern.CRAG == "crag"


def test_debate_value() -> None:
    """DEBATE must have string value 'debate'."""
    assert AgentPattern.DEBATE == "debate"


def test_dev_pipeline_value() -> None:
    """DEV_PIPELINE must have string value 'dev_pipeline' (Phase 24)."""
    assert AgentPattern.DEV_PIPELINE == "dev_pipeline"
    assert AgentPattern.DEV_PIPELINE.value == "dev_pipeline"


def test_pattern_lookup_by_value() -> None:
    """AgentPattern can be looked up by its string value."""
    assert AgentPattern("supervisor") is AgentPattern.SUPERVISOR
    assert AgentPattern("crag") is AgentPattern.CRAG


def test_pattern_equality_with_string() -> None:
    """AgentPattern values compare equal to equivalent strings (str enum)."""
    assert AgentPattern.SUPERVISOR == "supervisor"
    assert AgentPattern.CRAG == "crag"
    assert AgentPattern.DEBATE == "debate"


def test_all_patterns_have_non_empty_values() -> None:
    """All AgentPattern members must have non-empty string values."""
    for pattern in AgentPattern:
        assert pattern.value
        assert isinstance(pattern.value, str)


def test_pattern_names_are_uppercase() -> None:
    """All AgentPattern member names must be uppercase."""
    for pattern in AgentPattern:
        assert pattern.name == pattern.name.upper()

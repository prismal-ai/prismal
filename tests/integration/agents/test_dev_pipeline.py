"""Integration test: full dev_pipeline subgraph invocation with mocked LLM."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from lightagent.agents.state import create_initial_state
from lightagent.agents.subgraphs.dev_pipeline.builder import get_compiled_dev_pipeline


def _mock_llm_sequence() -> list[AIMessage]:
    """Return ordered mock LLM responses for each pipeline stage."""
    return [
        # PO Agent
        AIMessage(
            content=json.dumps(
                {
                    "id": "s1",
                    "title": "Login",
                    "description": "As a user I can log in",
                    "acceptance_criteria": ["Credentials work"],
                    "priority": "MUST",
                }
            )
        ),
        # Architect
        AIMessage(
            content=json.dumps(
                {
                    "id": "spec1",
                    "story_id": "s1",
                    "architecture": "JWT auth",
                    "design_decisions": ["bcrypt"],
                    "technology_stack": ["fastapi"],
                }
            )
        ),
        # Developer
        AIMessage(
            content=json.dumps(
                {
                    "language": "python",
                    "file_path": "auth/login.py",
                    "content": "def login(): return True",
                    "dependencies": [],
                }
            )
        ),
        # Unit Tester (passing)
        AIMessage(
            content=json.dumps(
                {
                    "tests_written": 3,
                    "tests_passed": 3,
                    "coverage_percent": 90.0,
                    "failing_tests": [],
                    "recommendations": [],
                }
            )
        ),
        # QA Agent
        AIMessage(
            content=json.dumps(
                {
                    "integration_tests_run": 2,
                    "integration_tests_passed": 2,
                    "security_findings": [],
                    "quality_score": 85.0,
                    "approved": True,
                }
            )
        ),
        # Reviewer (score >= 0.8 -> approves)
        AIMessage(
            content=json.dumps(
                {
                    "score": 0.9,
                    "approved": True,
                    "strengths": ["clean"],
                    "improvements": [],
                    "blocking_issues": [],
                }
            )
        ),
    ]


@pytest.mark.asyncio
async def test_dev_pipeline_full_happy_path() -> None:
    """Full pipeline runs end-to-end: all 6 agents produce their artifacts."""
    mock_responses = _mock_llm_sequence()
    call_count = 0

    async def mock_ainvoke(messages: list) -> AIMessage:
        """Return next mocked LLM response."""
        nonlocal call_count
        response = mock_responses[min(call_count, len(mock_responses) - 1)]
        call_count += 1
        return response

    graph = await get_compiled_dev_pipeline(checkpointer_path=":memory:")
    state = create_initial_state("integration-test")
    state["messages"] = [HumanMessage(content="Build a user login feature")]
    state["metadata"] = {"dev_pipeline": {}}

    with patch("lightagent.providers.registry.ProviderRegistry.get_llm") as mock_llm:
        mock_llm.return_value.ainvoke = mock_ainvoke
        result = await graph.ainvoke(state, config={"configurable": {"thread_id": "int-t1"}})

    dp = result["metadata"]["dev_pipeline"]
    assert "user_story" in dp
    assert "technical_spec" in dp
    assert "code_artifact" in dp
    assert "test_report" in dp
    assert "qa_report" in dp
    assert "review_result" in dp
    assert dp["review_result"]["approved"] is True

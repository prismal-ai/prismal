"""Unit tests for dev pipeline agent nodes (mocked LLM)."""

from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from lightagent.agents.state import create_initial_state


def _base_state(task: str = "Build a login feature") -> dict:
    """Create a base state for dev pipeline tests."""
    state = create_initial_state("sess-dev")
    state["messages"] = [HumanMessage(content=task)]
    state["metadata"] = {"dev_pipeline": {}}
    return state


@pytest.mark.asyncio
async def test_po_agent_adds_user_story() -> None:
    """PO agent node populates dev_pipeline.user_story in metadata."""
    from lightagent.agents.subgraphs.dev_pipeline.po_agent import po_agent_node

    mock_response = AIMessage(
        content=(
            '{"id": "s1", "title": "User Login", "description": "As a user I want to log in", '
            '"acceptance_criteria": ["Given valid credentials, I can log in"], "priority": "MUST"}'
        )
    )
    with patch("lightagent.providers.registry.ProviderRegistry.get_llm") as mock_llm:
        mock_llm.return_value.ainvoke = AsyncMock(return_value=mock_response)
        result = await po_agent_node(_base_state())

    assert result["current_agent"] == "po_agent"
    dp = result["metadata"]["dev_pipeline"]
    assert "user_story" in dp


@pytest.mark.asyncio
async def test_architect_agent_adds_tech_spec() -> None:
    """Architect agent node populates dev_pipeline.technical_spec in metadata."""
    from lightagent.agents.subgraphs.dev_pipeline.architect_agent import (
        architect_agent_node,
    )

    state = _base_state()
    state["metadata"]["dev_pipeline"]["user_story"] = {
        "id": "s1",
        "title": "Login",
        "description": "Login feature",
        "acceptance_criteria": [],
        "priority": "MUST",
    }
    mock_response = AIMessage(
        content=(
            '{"id": "spec1", "story_id": "s1", "architecture": "MVC with JWT", '
            '"design_decisions": ["use bcrypt"], "technology_stack": ["fastapi", "jwt"]}'
        )
    )
    with patch("lightagent.providers.registry.ProviderRegistry.get_llm") as mock_llm:
        mock_llm.return_value.ainvoke = AsyncMock(return_value=mock_response)
        result = await architect_agent_node(state)

    assert result["current_agent"] == "architect"
    assert "technical_spec" in result["metadata"]["dev_pipeline"]


@pytest.mark.asyncio
async def test_developer_agent_adds_code_artifact() -> None:
    """Developer agent node populates dev_pipeline.code_artifact in metadata."""
    from lightagent.agents.subgraphs.dev_pipeline.developer_agent import (
        developer_agent_node,
    )

    state = _base_state()
    state["metadata"]["dev_pipeline"]["technical_spec"] = {
        "id": "spec1",
        "story_id": "s1",
        "architecture": "MVC",
        "design_decisions": [],
        "technology_stack": ["python"],
    }
    mock_response = AIMessage(
        content=(
            '{"language": "python", "file_path": "auth/login.py", '
            '"content": "def login(): pass", "dependencies": []}'
        )
    )
    with patch("lightagent.providers.registry.ProviderRegistry.get_llm") as mock_llm:
        mock_llm.return_value.ainvoke = AsyncMock(return_value=mock_response)
        result = await developer_agent_node(state)

    assert result["current_agent"] == "developer"
    assert "code_artifact" in result["metadata"]["dev_pipeline"]


@pytest.mark.asyncio
async def test_unit_test_agent_adds_test_report() -> None:
    """Unit test agent node populates dev_pipeline.test_report in metadata."""
    from lightagent.agents.subgraphs.dev_pipeline.unit_test_agent import (
        unit_test_agent_node,
    )

    state = _base_state()
    state["metadata"]["dev_pipeline"]["code_artifact"] = {
        "language": "python",
        "file_path": "auth/login.py",
        "content": "def login(): pass",
        "dependencies": [],
    }
    mock_response = AIMessage(
        content=(
            '{"tests_written": 3, "tests_passed": 3, "coverage_percent": 90.0, '
            '"failing_tests": [], "recommendations": []}'
        )
    )
    with patch("lightagent.providers.registry.ProviderRegistry.get_llm") as mock_llm:
        mock_llm.return_value.ainvoke = AsyncMock(return_value=mock_response)
        result = await unit_test_agent_node(state)

    assert result["current_agent"] == "unit_tester"
    assert "test_report" in result["metadata"]["dev_pipeline"]


@pytest.mark.asyncio
async def test_qa_agent_adds_qa_report() -> None:
    """QA agent node populates dev_pipeline.qa_report in metadata."""
    from lightagent.agents.subgraphs.dev_pipeline.qa_agent import qa_agent_node

    state = _base_state()
    state["metadata"]["dev_pipeline"]["code_artifact"] = {
        "language": "python",
        "file_path": "auth/login.py",
        "content": "def login(): pass",
        "dependencies": [],
    }
    mock_response = AIMessage(
        content=(
            '{"integration_tests_run": 2, "integration_tests_passed": 2, '
            '"security_findings": [], "quality_score": 80.0, "approved": true}'
        )
    )
    with patch("lightagent.providers.registry.ProviderRegistry.get_llm") as mock_llm:
        mock_llm.return_value.ainvoke = AsyncMock(return_value=mock_response)
        result = await qa_agent_node(state)

    assert result["current_agent"] == "qa_agent"
    assert "qa_report" in result["metadata"]["dev_pipeline"]


@pytest.mark.asyncio
async def test_reviewer_agent_adds_review_result() -> None:
    """Reviewer agent node populates dev_pipeline.review_result in metadata."""
    from lightagent.agents.subgraphs.dev_pipeline.reviewer_agent import (
        reviewer_agent_node,
    )

    state = _base_state()
    state["metadata"]["dev_pipeline"]["code_artifact"] = {
        "language": "python",
        "file_path": "auth/login.py",
        "content": "def login(): pass",
        "dependencies": [],
    }
    state["metadata"]["dev_pipeline"]["qa_report"] = {
        "integration_tests_run": 2,
        "integration_tests_passed": 2,
        "security_findings": [],
        "quality_score": 80.0,
        "approved": True,
    }
    mock_response = AIMessage(
        content=(
            '{"score": 0.85, "approved": true, "strengths": ["clean"], '
            '"improvements": [], "blocking_issues": []}'
        )
    )
    with patch("lightagent.providers.registry.ProviderRegistry.get_llm") as mock_llm:
        mock_llm.return_value.ainvoke = AsyncMock(return_value=mock_response)
        result = await reviewer_agent_node(state)

    assert result["current_agent"] == "reviewer"
    result_data = result["metadata"]["dev_pipeline"]["review_result"]
    assert result_data["score"] == 0.85

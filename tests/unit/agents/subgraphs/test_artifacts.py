"""Unit tests for subgraph artifact Pydantic models."""
import pytest
from pydantic import ValidationError

from lightagent.agents.subgraphs.artifacts import (
    CodeArtifact,
    QAReport,
    ReviewResult,
    TechnicalSpec,
    TestReport as UnitTestReport,
    UserStory,
)


def test_user_story_defaults() -> None:
    story = UserStory(id="s1", title="Login", description="As a user I can log in")
    assert story.priority == "MUST"
    assert story.acceptance_criteria == []


def test_user_story_invalid_priority() -> None:
    with pytest.raises(ValidationError):
        UserStory(id="s1", title="t", description="d", priority="INVALID")


def test_technical_spec_fields() -> None:
    spec = TechnicalSpec(
        id="spec1",
        story_id="s1",
        architecture="microservices",
        technology_stack=["python", "fastapi"],
    )
    assert spec.design_decisions == []


def test_code_artifact_fields() -> None:
    art = CodeArtifact(
        language="python",
        file_path="auth/login.py",
        content="def login(): pass",
    )
    assert art.dependencies == []


def test_test_report_pass_rate() -> None:
    report = UnitTestReport(
        tests_written=10,
        tests_passed=9,
        coverage_percent=85.0,
    )
    assert report.failing_tests == []


def test_qa_report_defaults() -> None:
    qa = QAReport(security_findings=[], quality_score=75.0)
    assert not qa.approved
    assert qa.integration_tests_run == 0


def test_review_result_score_range() -> None:
    with pytest.raises(ValidationError):
        ReviewResult(score=1.5, approved=False, strengths=[], improvements=[])


def test_review_result_valid() -> None:
    result = ReviewResult(
        score=0.9,
        approved=True,
        strengths=["clean code"],
        improvements=[],
    )
    assert result.blocking_issues == []


def test_artifacts_json_roundtrip() -> None:
    story = UserStory(id="s1", title="T", description="D", acceptance_criteria=["AC1"])
    data = story.model_dump()
    restored = UserStory.model_validate(data)
    assert restored.id == story.id

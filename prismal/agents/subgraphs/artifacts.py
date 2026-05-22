"""Typed Pydantic v2 artifact models for subgraph agents.

Each artifact represents structured data produced by a subgraph agent node
and stored in ``AgentState.metadata[subgraph_name]``.  Agents must never
pass raw dicts between nodes — use these models and call ``.model_dump()``
when persisting to metadata.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class UserStory(BaseModel):
    """Product Owner artifact: a user story with acceptance criteria."""

    id: str = Field(..., description="Unique story identifier")
    title: str = Field(..., description="Short story title")
    description: str = Field(..., description="As [role], I want [action], so [benefit]")
    acceptance_criteria: list[str] = Field(
        default_factory=list, description="Testable acceptance criteria"
    )
    priority: Literal["MUST", "SHOULD", "COULD", "WONT"] = Field(
        default="MUST", description="MoSCoW priority"
    )


class TechnicalSpec(BaseModel):
    """Architect artifact: technical specification derived from user stories."""

    id: str = Field(..., description="Unique spec identifier")
    story_id: str = Field(..., description="Parent UserStory.id")
    architecture: str = Field(..., description="High-level architecture description")
    design_decisions: list[str] = Field(
        default_factory=list, description="Key architectural decisions"
    )
    technology_stack: list[str] = Field(default_factory=list, description="Technologies used")


class CodeArtifact(BaseModel):
    """Developer artifact: source code produced for a TechnicalSpec."""

    language: str = Field(default="python", description="Programming language")
    file_path: str = Field(..., description="Relative file path in repository")
    content: str = Field(..., description="Full source code content")
    dependencies: list[str] = Field(
        default_factory=list, description="Required package dependencies"
    )


class TestReport(BaseModel):
    """Unit-test agent artifact: pytest execution results."""

    tests_written: int = Field(default=0, ge=0, description="Total tests authored")
    tests_passed: int = Field(default=0, ge=0, description="Tests that passed")
    coverage_percent: float = Field(
        default=0.0, ge=0.0, le=100.0, description="Code coverage percentage"
    )
    failing_tests: list[str] = Field(default_factory=list, description="Names of failing tests")
    recommendations: list[str] = Field(
        default_factory=list, description="Suggestions to improve tests"
    )


class QAReport(BaseModel):
    """QA agent artifact: integration and security check results."""

    integration_tests_run: int = Field(default=0, ge=0)
    integration_tests_passed: int = Field(default=0, ge=0)
    security_findings: list[str] = Field(default_factory=list)
    quality_score: float = Field(default=0.0, ge=0.0, le=100.0, description="Overall quality 0-100")
    approved: bool = Field(default=False)


class ReviewResult(BaseModel):
    """Reviewer artifact: final code review with approval score."""

    score: float = Field(..., ge=0.0, le=1.0, description="Approval score 0.0-1.0")
    approved: bool = Field(default=False)
    strengths: list[str] = Field(default_factory=list)
    improvements: list[str] = Field(default_factory=list)
    blocking_issues: list[str] = Field(default_factory=list)


__all__ = [
    "CodeArtifact",
    "QAReport",
    "ReviewResult",
    "TechnicalSpec",
    "TestReport",
    "UserStory",
]

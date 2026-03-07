"""Auto-generated skill wrapper — edit skill.md, not this file."""
from __future__ import annotations

from pathlib import Path

from lightagent.skills.base import MarkdownSkill, SkillMetadata

_HERE = Path(__file__).parent


class SpecDrivenDesign(MarkdownSkill):
    """Skill loaded from the skill.md package in this directory."""

    def __init__(self) -> None:
        """Initialise the markdown-based skill."""
        super().__init__(_HERE)

    @property
    def metadata(self) -> SkillMetadata:
        """Return skill metadata parsed from skill.md frontmatter."""
        return SkillMetadata(
            name='spec-driven-design',
            description='Methodology and templates for Spec-Driven Design (SDD) — a structured approach where specifications are the primary artifact before writing code. Use this skill whenever the user wants to plan a new feature, design an API, create a technical design document, define a data model, write a PRD, create an implementation plan, or start any software project with proper specifications. Also trigger when the user mentions "spec", "PRD", "API spec", "technical design", "data model spec", "implementation plan", "spec-driven", "design doc", "feature planning", "architecture document", or asks to plan before coding. This skill applies to both greenfield projects and significant changes to existing systems. Even if the user just says "I need to plan a new feature" or "help me document this before building it", use this skill.',
            version='1.0.0',
            author='unknown',
            tags=[],
            safe_to_auto_activate=False,
            requires_permissions=[],
        )

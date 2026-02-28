"""Unit tests for lightagent.skills.base (T-070)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from lightagent.skills.base import BaseSkill, SkillMetadata


# ── SkillMetadata ─────────────────────────────────────────────────────────────


def test_skill_metadata_required_fields() -> None:
    """SkillMetadata accepts all fields and sets correct defaults."""
    meta = SkillMetadata(
        name="test_skill",
        description="A test skill",
        version="1.0.0",
        author="tester",
    )
    assert meta.name == "test_skill"
    assert meta.version == "1.0.0"
    assert meta.requires_permissions == []
    assert meta.safe_to_auto_activate is False
    assert meta.tags == []
    assert meta.min_python == "3.13"


def test_skill_metadata_missing_required_field_raises() -> None:
    """Missing required field raises ValidationError."""
    with pytest.raises(ValidationError):
        SkillMetadata(name="x", description="x", version="1.0.0")  # type: ignore[call-arg]


def test_skill_metadata_permissions_list() -> None:
    """requires_permissions accepts a list of strings."""
    meta = SkillMetadata(
        name="s",
        description="d",
        version="1.0.0",
        author="a",
        requires_permissions=["filesystem.read", "network.request"],
    )
    assert "filesystem.read" in meta.requires_permissions


def test_skill_metadata_safe_to_auto_activate() -> None:
    """safe_to_auto_activate defaults to False; can be overridden."""
    meta = SkillMetadata(
        name="s",
        description="d",
        version="1.0.0",
        author="a",
        safe_to_auto_activate=True,
    )
    assert meta.safe_to_auto_activate is True


def test_skill_metadata_tags() -> None:
    """Tags field stores arbitrary strings."""
    meta = SkillMetadata(
        name="s",
        description="d",
        version="1.0.0",
        author="a",
        tags=["utility", "web"],
    )
    assert "utility" in meta.tags


# ── BaseSkill (ABC) ───────────────────────────────────────────────────────────


def test_base_skill_cannot_be_instantiated_directly() -> None:
    """BaseSkill is abstract and cannot be instantiated."""
    with pytest.raises(TypeError):
        BaseSkill()  # type: ignore[abstract]


def test_concrete_skill_with_validate_false() -> None:
    """A concrete skill whose validate() returns False signals not-ready."""
    from langchain_core.tools import BaseTool

    class BadSkill(BaseSkill):
        """Broken skill for testing."""

        @property
        def metadata(self) -> SkillMetadata:
            """Return metadata."""
            return SkillMetadata(
                name="bad", description="broken", version="0.0.1", author="test"
            )

        def get_tools(self) -> list[BaseTool]:
            """Return empty tools."""
            return []

        def validate(self) -> bool:
            """Always fail."""
            return False

    skill = BadSkill()
    assert not skill.validate()


@pytest.mark.asyncio
async def test_base_skill_default_initialize_is_noop() -> None:
    """Default initialize() and teardown() complete without error."""
    from langchain_core.tools import BaseTool

    class MinimalSkill(BaseSkill):
        """Minimal concrete skill."""

        @property
        def metadata(self) -> SkillMetadata:
            """Return metadata."""
            return SkillMetadata(
                name="min", description="minimal", version="1.0.0", author="test"
            )

        def get_tools(self) -> list[BaseTool]:
            """Return empty tools."""
            return []

    skill = MinimalSkill()
    await skill.initialize()  # should not raise
    await skill.teardown()    # should not raise
    assert skill.validate() is True


@pytest.mark.asyncio
async def test_base_skill_default_validate_returns_true() -> None:
    """Default validate() returns True."""
    from langchain_core.tools import BaseTool

    class ASkill(BaseSkill):
        """Another concrete skill."""

        @property
        def metadata(self) -> SkillMetadata:
            """Return metadata."""
            return SkillMetadata(
                name="a", description="a", version="1.0.0", author="test"
            )

        def get_tools(self) -> list[BaseTool]:
            """Return empty tools."""
            return []

    assert ASkill().validate() is True

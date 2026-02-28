"""Base skill interface for LightAgent's three-tier skills system.

All LightAgent skills must inherit from :class:`BaseSkill` and implement
the :meth:`metadata` property and :meth:`get_tools` method.

Example::

    from lightagent.skills.base import BaseSkill, SkillMetadata
    from langchain_core.tools import BaseTool, tool

    class MySkill(BaseSkill):
        @property
        def metadata(self) -> SkillMetadata:
            return SkillMetadata(
                name="my_skill",
                description="Does something useful",
                version="1.0.0",
                author="me",
            )

        def get_tools(self) -> list[BaseTool]:
            @tool
            def do_thing(query: str) -> str:
                \"\"\"Do the thing.

                Args:
                    query: Input string.

                Returns:
                    Result string.
                \"\"\"
                return f"done: {query}"
            return [do_thing]
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from langchain_core.tools import BaseTool


class SkillMetadata(BaseModel):
    """Metadata describing a LightAgent skill.

    Used by :class:`~lightagent.skills.manager.SkillsManager` for discovery,
    validation, and display in ``lightagent skills list``.
    """

    name: str = Field(..., description="Unique identifier slug (snake_case)")
    description: str = Field(..., description="One-line description of the skill")
    version: str = Field(..., description="Semantic version string e.g. '1.0.0'")
    author: str = Field(..., description="Author name or handle")
    requires_permissions: list[str] = Field(
        default_factory=list,
        description="PermissionType values required by this skill",
    )
    safe_to_auto_activate: bool = Field(
        default=False,
        description="If False, explicit user confirmation is required to activate",
    )
    tags: list[str] = Field(default_factory=list, description="Categorisation tags")
    min_python: str = Field(default="3.13", description="Minimum Python version")


class BaseSkill(ABC):
    """Abstract base class for all LightAgent skills.

    Subclass this in ``skill.py`` inside each skill directory.  The skill
    is loaded and validated by :class:`~lightagent.skills.manager.SkillsManager`
    before activation.
    """

    @property
    @abstractmethod
    def metadata(self) -> SkillMetadata:
        """Return immutable metadata for this skill."""
        ...

    @abstractmethod
    def get_tools(self) -> list[BaseTool]:
        """Return the LangChain-compatible tools provided by this skill.

        Returns:
            A list of :class:`langchain_core.tools.BaseTool` instances.
        """
        ...

    async def initialize(self) -> None:  # noqa: B027
        """Optional async setup called when the skill is activated.

        Override to perform one-time initialisation (open connections,
        warm caches, etc.).
        """

    async def teardown(self) -> None:  # noqa: B027
        """Optional async cleanup called when the skill is deactivated.

        Override to release resources held since :meth:`initialize`.
        """

    def validate(self) -> bool:
        """Self-check before activation.

        Override to verify runtime prerequisites (env vars, deps, …).

        Returns:
            ``True`` if the skill is ready to be activated.
        """
        return True


__all__ = ["BaseSkill", "SkillMetadata"]

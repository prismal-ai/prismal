"""Skills system — discovery, activation, and tool aggregation.

Public API for the three-tier Prismal skills system:

* :class:`~prismal.skills.base.BaseSkill` — abstract base for all skills
* :class:`~prismal.skills.base.SkillMetadata` — Pydantic model for skill metadata
* :class:`~prismal.skills.manager.SkillsManager` — orchestrates skill lifecycle
* :class:`~prismal.skills.manager.SkillInfo` — summary DTO returned by list_skills()
* :data:`~prismal.skills.manager.SkillStatus` — literal type for skill status

Quick start::

    from prismal.skills import SkillsManager

    manager = SkillsManager()
    print(manager.list_skills())
    await manager.activate("weather", confirm=True)
    tools = manager.get_active_tools()
"""

from __future__ import annotations

from prismal.skills.base import BaseSkill, SkillMetadata
from prismal.skills.manager import SkillInfo, SkillsManager, SkillStatus

__all__ = [
    "BaseSkill",
    "SkillInfo",
    "SkillMetadata",
    "SkillStatus",
    "SkillsManager",
]

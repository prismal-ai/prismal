"""Skills system — discovery, activation, and tool aggregation.

Public API for the three-tier LightAgent skills system:

* :class:`~lightagent.skills.base.BaseSkill` — abstract base for all skills
* :class:`~lightagent.skills.base.SkillMetadata` — Pydantic model for skill metadata
* :class:`~lightagent.skills.manager.SkillsManager` — orchestrates skill lifecycle
* :class:`~lightagent.skills.manager.SkillInfo` — summary DTO returned by list_skills()
* :data:`~lightagent.skills.manager.SkillStatus` — literal type for skill status

Quick start::

    from lightagent.skills import SkillsManager

    manager = SkillsManager()
    print(manager.list_skills())
    await manager.activate("weather", confirm=True)
    tools = manager.get_active_tools()
"""

from __future__ import annotations

from lightagent.skills.base import BaseSkill, SkillMetadata
from lightagent.skills.manager import SkillInfo, SkillsManager, SkillStatus

__all__ = [
    "BaseSkill",
    "SkillInfo",
    "SkillMetadata",
    "SkillStatus",
    "SkillsManager",
]

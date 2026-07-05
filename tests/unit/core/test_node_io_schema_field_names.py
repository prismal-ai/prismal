"""AgentState drift guard for pilot node I/O models (Phase NTS — DD-NTS-003).

Every field a pilot model declares MUST be a real ``AgentState`` key. This
catches the case where ``AgentState`` is refactored (a field renamed/removed)
but a declared model is not updated — the residual gap DD-NTS-001 calls out.
"""

from __future__ import annotations

from typing import get_type_hints

import pytest

from prismal.agents.cron_manager import CronManagerInput, CronManagerOutput
from prismal.agents.file_manager import FileManagerInput, FileManagerOutput
from prismal.agents.skill_manager import SkillManagerInput, SkillManagerOutput
from prismal.agents.state import AgentState

_PILOT_MODELS = [
    FileManagerInput,
    FileManagerOutput,
    CronManagerInput,
    CronManagerOutput,
    SkillManagerInput,
    SkillManagerOutput,
]


@pytest.fixture(scope="module")
def agent_state_keys() -> set[str]:
    return set(get_type_hints(AgentState).keys())


@pytest.mark.parametrize("model", _PILOT_MODELS, ids=lambda m: m.__name__)
def test_pilot_model_fields_subset_of_agent_state(model, agent_state_keys) -> None:
    declared = set(model.model_fields.keys())
    assert declared, f"{model.__name__} declares no fields"
    orphans = declared - agent_state_keys
    assert not orphans, (
        f"{model.__name__} declares fields not in AgentState: {sorted(orphans)}"
    )


def test_pilot_models_are_wired_onto_nodes() -> None:
    from prismal.agents.cron_manager import cron_manager_node
    from prismal.agents.file_manager import file_manager_node
    from prismal.agents.skill_manager import skill_manager_node

    assert file_manager_node.__prismal_node__.input_model is FileManagerInput
    assert file_manager_node.__prismal_node__.output_model is FileManagerOutput
    assert cron_manager_node.__prismal_node__.input_model is CronManagerInput
    assert cron_manager_node.__prismal_node__.output_model is CronManagerOutput
    assert skill_manager_node.__prismal_node__.input_model is SkillManagerInput
    assert skill_manager_node.__prismal_node__.output_model is SkillManagerOutput

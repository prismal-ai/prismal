"""Unit tests for lightagent.agents.skill_creator (T-073)."""

from __future__ import annotations

import textwrap
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

if TYPE_CHECKING:
    from pathlib import Path

    from lightagent.agents.state import AgentState

# ── helpers ───────────────────────────────────────────────────────────────────

_SAMPLE_SKILL_CODE = textwrap.dedent("""\
    from __future__ import annotations
    from langchain_core.tools import BaseTool, tool
    from lightagent.skills.base import BaseSkill, SkillMetadata


    class UnitConverterSkill(BaseSkill):
        \\"\\"\\"Converts units.\\"\\"\\"

        @property
        def metadata(self) -> SkillMetadata:
            \\"\\"\\"Return metadata.\\"\\"\\"
            return SkillMetadata(
                name="unit_converter",
                description="Convert units",
                version="1.0.0",
                author="ai",
            )

        def get_tools(self) -> list[BaseTool]:
            \\"\\"\\"Return tools.\\"\\"\\"

            @tool
            def convert(value: float, from_unit: str, to_unit: str) -> str:
                \\"\\"\\"Convert a value between units.

                Args:
                    value: The numeric value to convert.
                    from_unit: Source unit (e.g. 'km').
                    to_unit: Target unit (e.g. 'miles').

                Returns:
                    Converted value as string.
                \\"\\"\\"
                return f"{value} {from_unit} = (converted) {to_unit}"

            return [convert]
""")


def _make_mock_llm(code: str) -> MagicMock:
    """Build a mock LLM that returns ``code`` as its response content."""
    llm = MagicMock()
    response = MagicMock()
    response.content = code
    llm.ainvoke = AsyncMock(return_value=response)
    return llm


# ── _slugify ──────────────────────────────────────────────────────────────────


def test_slugify_basic() -> None:
    """_slugify converts class names to snake_case slugs."""
    from lightagent.agents.skill_creator import _slugify

    assert _slugify("UnitConverterSkill") == "unitconverterskill"
    assert _slugify("My Skill") == "my_skill"
    assert _slugify("  spaces  ") == "spaces"


def test_slugify_fallback() -> None:
    """_slugify returns 'custom_skill' for empty or symbol-only input."""
    from lightagent.agents.skill_creator import _slugify

    assert _slugify("") == "custom_skill"
    assert _slugify("---") == "custom_skill"


# ── _extract_class_name ───────────────────────────────────────────────────────


def test_extract_class_name_found() -> None:
    """_extract_class_name finds BaseSkill subclass name."""
    from lightagent.agents.skill_creator import _extract_class_name

    code = "class MyAwesomeSkill(BaseSkill):\n    pass\n"
    assert _extract_class_name(code) == "MyAwesomeSkill"


def test_extract_class_name_not_found() -> None:
    """_extract_class_name returns 'CustomSkill' when no match found."""
    from lightagent.agents.skill_creator import _extract_class_name

    assert _extract_class_name("# no class here") == "CustomSkill"


# ── create_skill ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_skill_writes_files(tmp_path: Path) -> None:
    """create_skill writes skill.py and human_review_required.txt."""
    from lightagent.agents.skill_creator import create_skill

    mock_llm = _make_mock_llm(_SAMPLE_SKILL_CODE)

    with (
        patch("lightagent.agents.skill_creator.ProviderRegistry") as mock_registry,
        patch("lightagent.agents.skill_creator._run_ruff", return_value=(True, "")),
    ):
        mock_registry.return_value.get_llm.return_value = mock_llm
        await create_skill("Convert units between metric and imperial", skills_root=tmp_path)

    custom_dirs = list((tmp_path / "custom").iterdir())
    assert len(custom_dirs) == 1
    skill_dir = custom_dirs[0]
    assert (skill_dir / "skill.py").exists()
    assert (skill_dir / "human_review_required.txt").exists()


@pytest.mark.asyncio
async def test_create_skill_strips_markdown_fences(tmp_path: Path) -> None:
    """create_skill strips ```python fences from LLM output."""
    from lightagent.agents.skill_creator import create_skill

    fenced_code = f"```python\n{_SAMPLE_SKILL_CODE}```\n"
    mock_llm = _make_mock_llm(fenced_code)

    with (
        patch("lightagent.agents.skill_creator.ProviderRegistry") as mock_registry,
        patch("lightagent.agents.skill_creator._run_ruff", return_value=(True, "")),
    ):
        mock_registry.return_value.get_llm.return_value = mock_llm
        await create_skill("unit converter", skills_root=tmp_path)

    custom_dirs = list((tmp_path / "custom").iterdir())
    skill_py = custom_dirs[0] / "skill.py"
    content = skill_py.read_text()
    assert "```" not in content


@pytest.mark.asyncio
async def test_create_skill_returns_result_string(tmp_path: Path) -> None:
    """create_skill returns a descriptive result string."""
    from lightagent.agents.skill_creator import create_skill

    mock_llm = _make_mock_llm(_SAMPLE_SKILL_CODE)

    with (
        patch("lightagent.agents.skill_creator.ProviderRegistry") as mock_registry,
        patch("lightagent.agents.skill_creator._run_ruff", return_value=(True, "")),
    ):
        mock_registry.return_value.get_llm.return_value = mock_llm
        result = await create_skill("unit converter", skills_root=tmp_path)

    assert "Skill generated" in result
    assert "human_review_required.txt" in result
    assert "validated_by_human.txt" in result


@pytest.mark.asyncio
async def test_create_skill_ruff_failure_reported(tmp_path: Path) -> None:
    """create_skill reports ruff failures without aborting."""
    from lightagent.agents.skill_creator import create_skill

    mock_llm = _make_mock_llm(_SAMPLE_SKILL_CODE)

    with (
        patch("lightagent.agents.skill_creator.ProviderRegistry") as mock_registry,
        patch(
            "lightagent.agents.skill_creator._run_ruff",
            return_value=(False, "E501 line too long"),
        ),
    ):
        mock_registry.return_value.get_llm.return_value = mock_llm
        result = await create_skill("unit converter", skills_root=tmp_path)

    assert "ruff" in result.lower()
    assert "✗" in result  # ruff failure shown as ✗ icon


# ── skill_creator_node ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_node_no_human_message() -> None:
    """skill_creator_node returns error when no human message in state."""
    from langchain_core.messages import AIMessage, SystemMessage

    from lightagent.agents.skill_creator import skill_creator_node

    state: AgentState = {  # type: ignore[typeddict-item]
        "messages": [SystemMessage(content="system")],
        "current_agent": "supervisor",
        "next_agent": None,
        "task_plan": [],
        "completed_tasks": [],
        "pending_tasks": [],
        "context": {},
        "session_id": "test",
        "error": None,
        "metadata": {},
        "tool_calls": [],
        "intermediate_steps": [],
    }

    result = await skill_creator_node(state)
    msgs = list(result.get("messages", []))  # type: ignore[call-overload]
    assert any(isinstance(m, AIMessage) for m in msgs)
    last = [m for m in msgs if isinstance(m, AIMessage)][-1]
    assert "No skill specification" in str(last.content)


@pytest.mark.asyncio
async def test_node_with_human_message(tmp_path: Path) -> None:
    """skill_creator_node calls create_skill with the human message content."""
    from langchain_core.messages import AIMessage, HumanMessage

    from lightagent.agents.skill_creator import skill_creator_node

    state: AgentState = {  # type: ignore[typeddict-item]
        "messages": [HumanMessage(content="Create a unit converter skill")],
        "current_agent": "supervisor",
        "next_agent": None,
        "task_plan": [],
        "completed_tasks": [],
        "pending_tasks": [],
        "context": {},
        "session_id": "test",
        "error": None,
        "metadata": {},
        "tool_calls": [],
        "intermediate_steps": [],
    }

    with patch(
        "lightagent.agents.skill_creator.create_skill",
        new_callable=AsyncMock,
        return_value="Skill generated: unit_converter\nLocation: /tmp/x",
    ):
        result = await skill_creator_node(state)

    msgs = list(result.get("messages", []))  # type: ignore[call-overload]
    assert any(isinstance(m, AIMessage) for m in msgs)
    last = [m for m in msgs if isinstance(m, AIMessage)][-1]
    assert "unit_converter" in str(last.content)

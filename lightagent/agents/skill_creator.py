"""SkillCreatorAgent — generates new LightAgent skills from a natural language spec.

Uses the configured LLM to write a ``skill.py`` file, places it in
``skills/custom/<name>/`` together with a ``human_review_required.txt`` sentinel
(which must be renamed to ``validated_by_human.txt`` before the skill can be
activated), and runs basic quality checks on the generated code.

Example::

    from lightagent.agents.skill_creator import create_skill

    result = await create_skill("A skill that converts units (km to miles, etc.)")
    print(result)  # path to the generated skill directory

AC-006-7: SkillCreatorAgent generates new skills on request.
"""

from __future__ import annotations

import re
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import TYPE_CHECKING

from lightagent.core.logging import get_logger
from lightagent.providers.registry import ProviderRegistry
from lightagent.security.prompt_builder import SecurePromptBuilder

if TYPE_CHECKING:
    from lightagent.agents.state import AgentState

logger = get_logger("lightagent.agents.skill_creator")

_SKILLS_CUSTOM_DIR = Path(__file__).parent.parent / "skills" / "custom"

_SYSTEM_PROMPT = textwrap.dedent("""\
    You are a Python 3.13 expert specialising in writing LightAgent skills.

    A LightAgent skill lives in a single file called ``skill.py`` inside a
    subdirectory of ``lightagent/skills/available/`` (or ``custom/`` for
    AI-generated skills).

    Every skill.py MUST:
    1. Import and subclass ``BaseSkill`` from ``lightagent.skills.base``.
    2. Implement the ``metadata`` property returning a ``SkillMetadata`` instance.
    3. Implement ``get_tools()`` returning a list of LangChain BaseTool instances.
    4. Use the ``@tool`` decorator from ``langchain_core.tools`` for each tool.
    5. Start with ``from __future__ import annotations``.
    6. Include full docstrings on all public functions, methods, and classes.
    7. Use Python 3.13 type hints throughout (no ``Any`` unless unavoidable).
    8. Handle all external API/network errors gracefully (return error strings,
       never raise from tool functions).
    9. Be compatible with ``ruff check`` (line length ≤ 88 chars).

    Output ONLY the raw Python source code — no markdown fences, no explanation.
    The code must be complete and runnable.
""")


def _slugify(name: str) -> str:
    """Convert a human name to a valid Python identifier / directory name.

    Args:
        name: Free-form skill name.

    Returns:
        Lowercase, underscored slug safe for use as a directory / module name.
    """
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower().strip())
    return slug.strip("_") or "custom_skill"


def _extract_class_name(code: str) -> str:
    """Extract the first BaseSkill subclass name from generated code.

    Args:
        code: Python source code string.

    Returns:
        Class name string, or 'CustomSkill' if none is found.
    """
    match = re.search(r"^class\s+(\w+)\s*\(.*BaseSkill.*\)", code, re.MULTILINE)
    return match.group(1) if match else "CustomSkill"


def _run_ruff(skill_py: Path) -> tuple[bool, str]:
    """Run ``ruff check`` on the generated skill file.

    Args:
        skill_py: Path to the skill.py file.

    Returns:
        Tuple of (passed, output_message).
    """
    try:
        result = subprocess.run(
            [sys.executable, "-m", "ruff", "check", str(skill_py)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        passed = result.returncode == 0
        output = (result.stdout + result.stderr).strip()
        return passed, output
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)


async def create_skill(spec: str, skills_root: Path | None = None) -> str:
    """Generate a new skill from a natural language specification.

    Calls the configured LLM to generate ``skill.py`` source code, writes it to
    ``skills/custom/<slug>/skill.py``, adds a ``human_review_required.txt``
    sentinel, and runs ``ruff check`` as a basic quality gate.

    Args:
        spec: Natural language description of the skill to create
            (e.g. "A skill that converts units between metric and imperial").
        skills_root: Override for the skills root directory.  Defaults to the
            ``skills/`` package directory next to ``manager.py``.

    Returns:
        A multi-line result string describing what was created, the skill
        directory path, ruff check results, and next steps for the user.
    """
    custom_dir = (
        (skills_root / "custom") if skills_root else _SKILLS_CUSTOM_DIR
    )
    custom_dir.mkdir(parents=True, exist_ok=True)

    # Build a safe prompt
    builder = SecurePromptBuilder()
    messages = builder.build(
        system=_SYSTEM_PROMPT,
        user=(
            "Write a complete LightAgent skill.py for the following specification:\n\n"
            f"{spec}"
        ),
    )

    logger.info("skill_creator_generating", spec_length=len(spec))

    # Call the LLM
    llm = ProviderRegistry().get_llm()
    from langchain_core.messages import BaseMessage

    lc_messages = [
        BaseMessage(content=m["content"], type=m.get("role", "user"))
        for m in messages
    ]
    response = await llm.ainvoke(lc_messages)
    content = response.content if hasattr(response, "content") else str(response)
    raw_code: str = content if isinstance(content, str) else str(content)

    # Strip any accidental markdown fences
    raw_code = re.sub(r"^```[a-z]*\n?", "", raw_code, flags=re.MULTILINE)
    raw_code = re.sub(r"^```\s*$", "", raw_code, flags=re.MULTILINE)
    raw_code = raw_code.strip() + "\n"

    # Determine directory name from class name
    class_name = _extract_class_name(raw_code)
    slug = _slugify(class_name)
    skill_dir = custom_dir / slug
    skill_dir.mkdir(parents=True, exist_ok=True)

    skill_py = skill_dir / "skill.py"
    skill_py.write_text(raw_code, encoding="utf-8")

    # Write the human-review sentinel
    (skill_dir / "human_review_required.txt").write_text(
        "This skill was AI-generated and must be reviewed before activation.\n"
        "Once reviewed, rename this file to 'validated_by_human.txt' "
        "to enable activation.\n",
        encoding="utf-8",
    )

    # Run basic quality check
    ruff_passed, ruff_output = _run_ruff(skill_py)
    ruff_status = (
        "✓ ruff check passed" if ruff_passed else f"⚠ ruff issues:\n{ruff_output}"
    )

    logger.info(
        "skill_creator_done",
        slug=slug,
        ruff_passed=ruff_passed,
        skill_dir=str(skill_dir),
    )

    result_lines = [
        f"Skill generated: '{slug}'",
        f"Location: {skill_dir}",
        f"Quality: {ruff_status}",
        "",
        "Next steps:",
        f"  1. Review the generated code in {skill_py}",
        f"  2. Rename '{skill_dir}/human_review_required.txt'"
        " → 'validated_by_human.txt'",
        "  3. Activate with: SkillsManager().activate('{slug}', confirm=True)",
    ]
    return "\n".join(result_lines)


async def skill_creator_node(state: AgentState) -> dict[str, object]:
    """LangGraph node that creates a new skill from the last user message.

    Extracts the skill specification from the most recent human message
    and delegates to :func:`create_skill`.

    Args:
        state: Current LangGraph agent state.

    Returns:
        Dict with updated ``messages`` and ``current_agent`` fields.
    """
    from langchain_core.messages import AIMessage

    messages = state.get("messages", [])
    spec = ""
    for msg in reversed(messages):
        if hasattr(msg, "type") and msg.type == "human":
            spec = str(msg.content)
            break

    if not spec:
        return {
            "messages": [AIMessage(content="No skill specification provided.")],
            "current_agent": "skill_creator",
        }

    result = await create_skill(spec)
    return {
        "messages": [AIMessage(content=result)],
        "current_agent": "skill_creator",
    }


__all__ = ["create_skill", "skill_creator_node"]

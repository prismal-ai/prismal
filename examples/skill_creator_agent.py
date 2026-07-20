"""SkillCreatorAgent demo: generate a skill offline with an injected fake LLM.

Swaps the module's ``_llm_generate`` seam for a deterministic fake that returns
canned ``skill.py`` source, so the five-step workflow (GENERATE → shell guard →
VALIDATE with ruff/mypy/bandit → WRITE artefacts → REPORT) runs OFFLINE with no
LLM and no network. All artefacts go to a temporary directory — never into
``prismal/skills/``. Also shows the AC-017-8 shell-execution refusal and the
``skill_creator_node`` LangGraph entry point.

The ruff/mypy/bandit checks run for real via subprocess when those tools are
installed locally; missing tools simply show up as failed checks in the report.

Run::

    python examples/skill_creator_agent.py
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from langchain_core.messages import HumanMessage

import prismal.agents.skill_creator as skill_creator_module
from prismal.agents.skill_creator import create_skill, skill_creator_node

SPEC = "A skill that converts units (km to miles, celsius to fahrenheit)"
SHELL_SPEC = "A skill that runs arbitrary shell commands for me"

# What the LLM would generate for SPEC (a complete Prismal skill.py).
FAKE_SKILL_CODE = '''"""Unit conversion skill (generated offline for this example)."""

from __future__ import annotations

from langchain_core.tools import BaseTool, tool

from prismal.skills.base import BaseSkill, SkillMetadata


@tool
def km_to_miles(km: float) -> str:
    """Convert kilometres to miles."""
    return f"{km} km = {km * 0.621371:.3f} miles"


@tool
def celsius_to_fahrenheit(celsius: float) -> str:
    """Convert degrees Celsius to Fahrenheit."""
    return f"{celsius} C = {celsius * 9 / 5 + 32:.1f} F"


class UnitConverterSkill(BaseSkill):
    """Skill that converts between common physical units."""

    @property
    def metadata(self) -> SkillMetadata:
        """Return the skill metadata."""
        return SkillMetadata(
            name="unit_converter",
            description="Convert units (km to miles, C to F)",
            version="1.0.0",
            author="skill_creator_example",
        )

    def get_tools(self) -> list[BaseTool]:
        """Return the LangChain tools exposed by this skill."""
        return [km_to_miles, celsius_to_fahrenheit]
'''

# What the LLM would generate for SHELL_SPEC — tripping the AC-017-8 guard.
FAKE_SHELL_CODE = '''"""Shell runner skill."""

import subprocess


def run(cmd: str) -> str:
    """Run a command."""
    return subprocess.run(cmd, shell=True, capture_output=True, text=True).stdout
'''


async def fake_llm_generate(messages: list[dict[str, str]]) -> str:
    """Deterministic stand-in for the code-generation / fix-loop LLM calls.

    Picks the canned source by inspecting the (SecurePromptBuilder-built) user
    message; a real run would return freshly generated or fixed code instead.
    """
    user_content = messages[-1]["content"]
    if "shell" in user_content.lower():
        return FAKE_SHELL_CODE
    return FAKE_SKILL_CODE


async def main() -> None:
    """Run generation, the shell-guard refusal, and the LangGraph node."""
    # Inject the fake at the module's LLM seam; a real deployment leaves it
    # alone and _llm_generate wires ProviderRegistry().get_llm() lazily.
    skill_creator_module._llm_generate = fake_llm_generate  # type: ignore[assignment]
    # The fake always returns the same code, so extra fix iterations only
    # re-run the quality tools — keep the demo fast with a single retry.
    skill_creator_module.MAX_FIX_ITERATIONS = 1

    with tempfile.TemporaryDirectory(prefix="skills_") as tmp:
        skills_root = Path(tmp)

        # ── 1. Generate a skill into the temp skills root ──────────────────
        print(f"Spec: {SPEC}\n")
        result = await create_skill(SPEC, skills_root=skills_root)
        print(result)

        skill_dirs = sorted(p for p in (skills_root / "custom").iterdir() if p.is_dir())
        print("\nArtefacts written:")
        for skill_dir in skill_dirs:
            for artefact in sorted(skill_dir.iterdir()):
                print(f"  - custom/{skill_dir.name}/{artefact.name}")

        # ── 2. Shell-execution guard (AC-017-8) ────────────────────────────
        print(f"\nSpec: {SHELL_SPEC}\n")
        refusal = await create_skill(SHELL_SPEC, skills_root=skills_root)
        print(refusal)

        # ── 3. The LangGraph node (as wired behind the supervisor) ─────────
        # skill_creator_node has no skills_root parameter — it writes to the
        # module default, so point that default at the temp directory too.
        skill_creator_module._SKILLS_CUSTOM_DIR = skills_root / "custom"
        state = {"messages": [HumanMessage(content=SPEC)]}
        update = await skill_creator_node(state)  # type: ignore[arg-type]
        print(f"\nskill_creator_node -> current_agent={update['current_agent']!r}")
        print(f"AI message starts with: {update['messages'][0].content.splitlines()[0]}")


if __name__ == "__main__":
    asyncio.run(main())

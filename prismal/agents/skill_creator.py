"""SkillCreatorAgent — generates new LightAgent skills from a natural language spec.

Uses the configured LLM to write a ``skill.py`` file, places it in
``skills/custom/<name>/`` together with a ``human_review_required.txt`` sentinel
(which must be renamed to ``validated_by_human.txt`` before the skill can be
activated).

Validation pipeline (SPEC-017 AC-017-4 / AC-017-5)
----------------------------------------------------
After each generation attempt the agent runs three quality tools:

1. ``ruff check`` — style and import errors.
2. ``mypy --ignore-missing-imports --disallow-untyped-defs`` — type safety.
3. ``bandit -r -ll`` — medium-and-above security issues.

If any tool reports errors the agent feeds the output back to the LLM as a
fix prompt and retries, up to ``MAX_FIX_ITERATIONS`` times (default: 3).

Output artefacts per generated skill
--------------------------------------
``skills/custom/<slug>/``
  - ``skill.py``                  — generated code
  - ``human_review_required.txt`` — sentinel (AC-017-3)
  - ``generation_prompt.txt``     — original spec for audit (AC-017-7)
  - ``quality_report.json``       — tool results (AC-017-4)

Example::

    from prismal.agents.skill_creator import create_skill

    result = await create_skill("A skill that converts units (km to miles, etc.)")
    print(result)  # path to the generated skill directory

AC-006-7: SkillCreatorAgent generates new skills on request.
SPEC-017: Full acceptance criteria documented in the SPEC.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import textwrap
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from prismal.core.logging import get_logger
from prismal.providers.registry import ProviderRegistry
from prismal.security.prompt_builder import SecurePromptBuilder

if TYPE_CHECKING:
    from prismal.agents.state import AgentState

logger = get_logger("lightagent.agents.skill_creator")

_SKILLS_CUSTOM_DIR = Path(__file__).parent.parent / "skills" / "custom"

# Maximum number of LLM fix iterations after failed validation.
MAX_FIX_ITERATIONS: int = 3

# Patterns in generated code that indicate shell/subprocess usage (AC-017-8).
# These are detection strings, not calls.
_SHELL_DETECT_TERMS: tuple[str, ...] = (
    "shell.execute",
    "subprocess.run",
    "subprocess.Popen",
    "subprocess.call",
    "subprocess.check_output",
    # os module shell invocations detected via pattern
    "popen",
)

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
    10. All function parameters and return values must have explicit type annotations.

    Output ONLY the raw Python source code — no markdown fences, no explanation.
    The code must be complete and runnable.
""")

_FIX_SYSTEM_PROMPT = textwrap.dedent("""\
    You are a Python 3.13 expert fixing quality issues in a LightAgent skill file.

    You will be given the current skill.py code and a list of quality tool errors.
    Fix ALL reported errors. Output ONLY the corrected Python source code —
    no markdown fences, no explanation.

    Rules:
    - Do NOT change the overall structure or purpose of the skill.
    - Fix type annotations, import order, line lengths, and security issues.
    - Keep all docstrings and public API intact.
    - Output ONLY the raw Python source code.
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


def _strip_fences(code: str) -> str:
    """Strip any accidental markdown code fences from LLM output.

    Args:
        code: Raw LLM response string.

    Returns:
        Clean Python source code.
    """
    code = re.sub(r"^```[a-z]*\n?", "", code, flags=re.MULTILINE)
    code = re.sub(r"^```\s*$", "", code, flags=re.MULTILINE)
    return code.strip() + "\n"


def _contains_shell_execute(code: str) -> bool:
    """Return True if the code uses shell / subprocess patterns.

    Used to enforce AC-017-8: the agent refuses shell-execute skills without
    explicit confirmation.  Checks for common subprocess and shell-invocation
    patterns as string tokens in the generated source.

    Args:
        code: Python source code string.

    Returns:
        True if any shell-execution pattern is detected.
    """
    return any(term in code for term in _SHELL_DETECT_TERMS)


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
            check=False,
        )
        return result.returncode == 0, (result.stdout + result.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)


def _run_mypy(skill_py: Path) -> tuple[bool, str]:
    """Run ``mypy`` type checking on the generated skill file.

    Uses ``--ignore-missing-imports`` to tolerate optional dependencies and
    ``--disallow-untyped-defs`` to enforce explicit type annotations.

    Args:
        skill_py: Path to the skill.py file.

    Returns:
        Tuple of (passed, output_message).
    """
    try:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "mypy",
                "--ignore-missing-imports",
                "--disallow-untyped-defs",
                str(skill_py),
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        return result.returncode == 0, (result.stdout + result.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)


def _run_bandit(skill_py: Path) -> tuple[bool, str]:
    """Run ``bandit`` security checks on the generated skill file.

    Reports medium-and-above severity issues (``-ll`` flag).

    Args:
        skill_py: Path to the skill.py file.

    Returns:
        Tuple of (passed, output_message).
    """
    try:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "bandit",
                "-r",
                "-ll",
                str(skill_py),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        return result.returncode == 0, (result.stdout + result.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)


def _validate_code(skill_py: Path) -> tuple[bool, dict[str, object]]:
    """Run all three quality tools and return aggregated results.

    Args:
        skill_py: Path to the skill.py file.

    Returns:
        Tuple of (all_passed, results_dict) where results_dict has keys
        ``ruff``, ``mypy``, ``bandit``, each with ``passed`` and ``output``.
    """
    ruff_ok, ruff_out = _run_ruff(skill_py)
    mypy_ok, mypy_out = _run_mypy(skill_py)
    bandit_ok, bandit_out = _run_bandit(skill_py)

    all_passed = ruff_ok and mypy_ok and bandit_ok
    results: dict[str, object] = {
        "ruff": {"passed": ruff_ok, "output": ruff_out},
        "mypy": {"passed": mypy_ok, "output": mypy_out},
        "bandit": {"passed": bandit_ok, "output": bandit_out},
        "all_passed": all_passed,
    }
    return all_passed, results


async def _llm_generate(messages: list[dict[str, str]]) -> str:
    """Invoke the LLM and return the text response.

    Args:
        messages: List of role/content dicts suitable for the LLM.

    Returns:
        Generated text string.
    """
    from langchain_core.messages import BaseMessage

    llm = ProviderRegistry().get_llm()
    lc_messages = [BaseMessage(content=m["content"], type=m.get("role", "user")) for m in messages]
    response = await llm.ainvoke(lc_messages)
    content = response.content if hasattr(response, "content") else str(response)
    return content if isinstance(content, str) else str(content)


async def create_skill(
    spec: str,
    skills_root: Path | None = None,
    allow_shell: bool = False,
) -> str:
    """Generate a new skill from a natural language specification.

    Implements the five-step SkillCreatorAgent workflow:

    1. **GENERATE** — LLM writes ``skill.py``.
    2. **CHECK** — shell.execute guard (AC-017-8).
    3. **VALIDATE** — ruff + mypy + bandit; retry up to
       ``MAX_FIX_ITERATIONS`` times on failure.
    4. **WRITE** — Save all artefacts to ``skills/custom/<slug>/``.
    5. **REPORT** — Return a summary string.

    Args:
        spec: Natural language description of the skill to create.
        skills_root: Override for the skills root directory.  Defaults to the
            ``skills/`` package directory next to the agents package.
        allow_shell: When True, allow generation of skills that use shell /
            subprocess patterns.  Defaults to False (AC-017-8).

    Returns:
        A multi-line result string describing the outcome, artefact paths,
        quality results, and next steps for the user.
    """
    custom_dir = (skills_root / "custom") if skills_root else _SKILLS_CUSTOM_DIR
    custom_dir.mkdir(parents=True, exist_ok=True)

    builder = SecurePromptBuilder()
    generation_messages = builder.build(
        system=_SYSTEM_PROMPT,
        user=(f"Write a complete LightAgent skill.py for the following specification:\n\n{spec}"),
    )

    logger.info("skill_creator_generating", spec_length=len(spec))

    # ── 1. GENERATE ─────────────────────────────────────────────────────────
    raw_code = _strip_fences(await _llm_generate(generation_messages))

    # ── 2. Shell.execute guard (AC-017-8) ──────────────────────────────────
    if _contains_shell_execute(raw_code) and not allow_shell:
        logger.warning("skill_creator_shell_execute_detected", spec_length=len(spec))
        return (
            "Refused: the generated skill contains shell/subprocess calls.\n"
            "Re-run with allow_shell=True (or pass --allow-shell flag) to permit "
            "shell execution in the generated skill.\n"
            "WARNING: Skills with shell access can execute arbitrary commands."
        )

    # ── Determine directory name ─────────────────────────────────────────
    class_name = _extract_class_name(raw_code)
    slug = _slugify(class_name)
    skill_dir = custom_dir / slug
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_py = skill_dir / "skill.py"

    # ── 3. VALIDATE + fix loop ──────────────────────────────────────────────
    current_code = raw_code
    quality_results: dict[str, object] = {}
    all_passed = False
    iterations_used = 0

    for iteration in range(1, MAX_FIX_ITERATIONS + 2):  # +1 for final check
        skill_py.write_text(current_code, encoding="utf-8")
        all_passed, quality_results = _validate_code(skill_py)

        if all_passed:
            break

        if iteration > MAX_FIX_ITERATIONS:
            logger.warning(
                "skill_creator_validation_failed_after_max_iterations",
                slug=slug,
                iterations=MAX_FIX_ITERATIONS,
            )
            break

        # Build error summary for the fix prompt
        errors: list[str] = []
        for tool in ("ruff", "mypy", "bandit"):
            tool_result = quality_results.get(tool, {})
            if isinstance(tool_result, dict) and not tool_result.get("passed"):
                errors.append(f"=== {tool.upper()} ERRORS ===\n{tool_result.get('output', '')}")
        error_summary = "\n\n".join(errors)

        fix_messages = builder.build(
            system=_FIX_SYSTEM_PROMPT,
            user=(
                f"Fix the following errors in this skill.py:\n\n"
                f"ERRORS:\n{error_summary}\n\n"
                f"CURRENT CODE:\n{current_code}"
            ),
        )

        logger.info(
            "skill_creator_fix_iteration",
            slug=slug,
            iteration=iteration,
            error_count=len(errors),
        )
        current_code = _strip_fences(await _llm_generate(fix_messages))
        iterations_used = iteration

    # ── 4. WRITE artefacts ───────────────────────────────────────────────────
    skill_py.write_text(current_code, encoding="utf-8")

    # AC-017-3: human review sentinel
    (skill_dir / "human_review_required.txt").write_text(
        "This skill was AI-generated and must be reviewed before activation.\n"
        "Once reviewed, rename this file to 'validated_by_human.txt' "
        "to enable activation.\n",
        encoding="utf-8",
    )

    # AC-017-7: original spec for audit
    (skill_dir / "generation_prompt.txt").write_text(
        f"Generated: {datetime.now(UTC).isoformat()}\n\nOriginal specification:\n{spec}\n",
        encoding="utf-8",
    )

    # AC-017-4: quality report
    report: dict[str, object] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "slug": slug,
        "class_name": class_name,
        "fix_iterations_used": iterations_used,
        "all_passed": all_passed,
        "tools": quality_results,
    }
    (skill_dir / "quality_report.json").write_text(
        json.dumps(report, indent=2),
        encoding="utf-8",
    )

    logger.info(
        "skill_creator_done",
        slug=slug,
        all_passed=all_passed,
        iterations=iterations_used,
        skill_dir=str(skill_dir),
    )

    # ── 5. REPORT ────────────────────────────────────────────────────────────
    ruff_icon = "✓" if _tool_passed(quality_results, "ruff") else "✗"
    mypy_icon = "✓" if _tool_passed(quality_results, "mypy") else "✗"
    bandit_icon = "✓" if _tool_passed(quality_results, "bandit") else "✗"
    overall_icon = "✅" if all_passed else "⚠️ "

    result_lines = [
        f"{overall_icon} Skill generated: '{slug}'",
        f"   Location:  {skill_dir}",
        f"   ruff:      {ruff_icon}",
        f"   mypy:      {mypy_icon}",
        f"   bandit:    {bandit_icon}",
        f"   Fixes:     {iterations_used} iteration(s)",
        "",
        "Next steps:",
        f"  1. Review generated code in {skill_py}",
        "  2. Check quality_report.json for tool output",
        f"  3. Rename '{skill_dir}/human_review_required.txt' → 'validated_by_human.txt'",
        f"  4. Activate:  lightagent skills enable {slug}",
    ]
    return "\n".join(result_lines)


def _tool_passed(results: dict[str, object], tool: str) -> bool:
    """Return True if the given tool passed in the quality results dict.

    Args:
        results: Quality results dict from :func:`_validate_code`.
        tool: Tool key (``"ruff"``, ``"mypy"``, or ``"bandit"``).

    Returns:
        True if the tool was run and reported no issues.
    """
    entry = results.get(tool, {})
    return bool(isinstance(entry, dict) and entry.get("passed"))


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


__all__ = ["MAX_FIX_ITERATIONS", "create_skill", "skill_creator_node"]

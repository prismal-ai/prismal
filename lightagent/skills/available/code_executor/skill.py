"""Code executor skill — runs Python code snippets in a sandboxed subprocess.

Requires ``LIGHTAGENT_SHELL_ENABLED=true`` (or ``settings.shell_enabled=True``).
If shell execution is disabled the tool returns an error without running anything.

Safety notes:

* Execution is isolated via ``subprocess`` with a configurable timeout.
* ``LIGHTAGENT_CODE_EXEC_TIMEOUT`` controls the timeout in seconds (default: 10).
* Only Python is supported. No shell passthrough is provided.
* Always gated by :func:`lightagent.core.config.Settings.shell_enabled`.

Example::

    skill = CodeExecutorSkill()
    tools = skill.get_tools()
    result = tools[0].invoke({"code": "print(1 + 1)"})
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

from langchain_core.tools import BaseTool, tool

from lightagent.core.logging import get_logger
from lightagent.skills.base import BaseSkill, SkillMetadata

logger = get_logger("lightagent.skills.code_executor")

_DEFAULT_TIMEOUT = 10


def _is_shell_enabled() -> bool:
    """Check whether shell execution is permitted.

    Returns:
        True if LIGHTAGENT_SHELL_ENABLED is 'true' (case-insensitive).
    """
    return os.getenv("LIGHTAGENT_SHELL_ENABLED", "false").lower() == "true"


def _run_python(code: str, timeout: int) -> str:
    """Execute Python code in an isolated subprocess.

    Args:
        code: Python source code to execute.
        timeout: Maximum execution time in seconds.

    Returns:
        Combined stdout/stderr output from the subprocess, or an error message.
    """
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8") as tmp:
        tmp.write(code)
        tmp_path = tmp.name

    try:
        result = subprocess.run(
            [sys.executable, tmp_path],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        output = result.stdout
        if result.stderr:
            output += f"\n[stderr]\n{result.stderr}"
        if result.returncode != 0:
            output += f"\n[exit code: {result.returncode}]"
        return output.strip() or "(no output)"
    except subprocess.TimeoutExpired:
        return f"[code_executor] Execution timed out after {timeout}s."
    except OSError as exc:
        return f"[code_executor] OS error running code: {exc}"
    finally:
        Path(tmp_path).unlink(missing_ok=True)


class CodeExecutorSkill(BaseSkill):
    """Executes Python code snippets in a sandboxed subprocess."""

    @property
    def metadata(self) -> SkillMetadata:
        """Return code executor skill metadata."""
        return SkillMetadata(
            name="code_executor",
            description="Execute Python code snippets and return their output",
            version="1.0.0",
            author="lightagent",
            safe_to_auto_activate=False,
            requires_permissions=["shell.execute"],
            tags=["utility", "code", "python", "execution"],
        )

    def get_tools(self) -> list[BaseTool]:
        """Return code execution tools.

        Returns:
            List containing the execute_python tool.
        """

        @tool
        def execute_python(code: str, timeout: int = _DEFAULT_TIMEOUT) -> str:
            """Execute a Python code snippet and return the output.

            Args:
                code: Valid Python source code to run (multi-line supported).
                timeout: Maximum execution time in seconds (default: 10).

            Returns:
                Stdout/stderr output from the execution, or an error message.
            """
            if not _is_shell_enabled():
                return (
                    "[code_executor] Shell execution is disabled. "
                    "Set LIGHTAGENT_SHELL_ENABLED=true to enable."
                )

            configured_timeout = int(os.getenv("LIGHTAGENT_CODE_EXEC_TIMEOUT", str(timeout)))
            logger.info(
                "code_executor_run",
                code_length=len(code),
                timeout=configured_timeout,
            )
            return _run_python(code, configured_timeout)

        return [execute_python]

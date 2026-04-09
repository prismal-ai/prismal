"""CodeAct agent — direct Python code generation with auto-correction.

SPEC-040 / Phase 38. Unlike the classic :mod:`lightagent.agents.coder`
node which uses a ReAct tool-calling loop (``sandbox_exec`` JSON tool
calls processed by :func:`react_loop`), CodeAct asks the LLM to emit
executable Python directly wrapped in ``<code>...</code>`` tags. A
single block can combine I/O, computation and validation, cutting token
usage by ~30 % on multi-step coding tasks (arxiv:2402.01030).

Safety pipeline (hard requirements from CLAUDE.md Phase 38 rules):

1. Parse the first ``<code>...</code>`` block from the LLM response.
2. :func:`_validate_imports` rejects any non-allowlisted ``import`` or
   ``from ... import`` statement before anything reaches the sandbox —
   a lightweight AST walk, not a regex, so comments / strings don't
   false positive.
3. :meth:`ActionInterceptor.check_shell` gates execution on
   ``LIGHTAGENT_SHELL_ENABLED``. When the gate is closed we return a
   graceful ``AIMessage`` explaining how to enable it instead of silently
   failing.
4. Code runs in the existing :class:`SandboxExecutor` (via
   ``asyncio.to_thread`` because ``run_code`` is sync and performs real
   subprocess I/O — we must never block the event loop).
5. Stdout, stderr and exit code are appended to the running message list
   so the next iteration can auto-correct errors. The loop bails out on
   a clean run with the special marker ``__CODEACT_RESULT__`` present in
   stdout, when no further ``<code>`` block is emitted, when
   ``max_iterations`` is reached, or after ``_MAX_CONSECUTIVE_FAILURES``
   failures on the same request.
"""

from __future__ import annotations

import ast
import asyncio
import re
from typing import TYPE_CHECKING, Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from lightagent.core.config import get_settings
from lightagent.core.logging import get_logger
from lightagent.providers.registry import ProviderRegistry
from lightagent.security.action_interceptor import ActionInterceptor

if TYPE_CHECKING:
    from lightagent.agents.state import AgentState

logger = get_logger("lightagent.agents.codeact")


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MAX_CONSECUTIVE_FAILURES: int = 3
"""Hard cap on same-request failures before a best-effort reply (AC-040-2)."""

_HISTORY_WINDOW: int = 8
"""Messages from state.messages sent to the LLM as context."""

_CODE_BLOCK_RE: re.Pattern[str] = re.compile(
    r"<code>(.*?)</code>", re.DOTALL | re.IGNORECASE
)

_RESULT_MARKER: str = "__CODEACT_RESULT__"
"""Printed by the LLM's last line when the `result` variable is set.

The LLM is instructed to end every successful code block with
``print("__CODEACT_RESULT__", result)`` so we can detect completion
without introspecting the sandbox process globals (which we cannot do
through a subprocess).
"""

CODEACT_SYSTEM_PROMPT: str = """You are CodeAct, a programming agent that \
runs Python code directly instead of calling tools.

## Purpose
Solve the user's coding task by writing executable Python code blocks. Each
block is run in an isolated sandbox and its stdout/stderr is fed back to you
as the next message, so you can iterate, validate, and self-correct.

## Output Format (MANDATORY)
Wrap every block of runnable Python in <code>...</code> tags. You may include
prose before or after the block to explain your plan, but ONLY the content
inside the tags is executed. Emit at most one <code> block per response.

Every code block MUST end with:

    print("__CODEACT_RESULT__", result)

where ``result`` is the final value you want to return to the user. When the
task is fully solved, that print is your termination signal.

## Import Rules
Only the packages listed in ``LIGHTAGENT_CODEACT_IMPORT_ALLOWLIST`` may be
imported. Attempting to import anything else will reject the block before it
runs. Never try to ``import lightagent`` or reach into project internals —
you run in an isolated sandbox.

## Iteration Rules
- If the sandbox returns stdout/stderr indicating an error, analyse it and
  emit a fixed <code> block in your next response.
- If the task is complete, respond with a short natural-language summary and
  NO <code> block — that signals the end of the loop.
- Prefer a single comprehensive block over many small ones; one well-written
  block with I/O, computation and validation is better than 5 tiny blocks.
- Never dynamically evaluate untrusted strings."""


_SHELL_DISABLED_REPLY: str = (
    "CodeAct cannot run code because `LIGHTAGENT_SHELL_ENABLED=false`. "
    "Set `LIGHTAGENT_SHELL_ENABLED=true` in your environment to enable the "
    "sandbox, or route this task to a non-execution agent."
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_import_allowlist() -> frozenset[str]:
    """Parse the allowlist from settings into a frozenset of top-level names."""
    raw = get_settings().codeact_import_allowlist or ""
    return frozenset(
        name.strip() for name in raw.split(",") if name.strip()
    )


def _validate_imports(code: str) -> tuple[bool, str]:
    """Walk the AST and reject any non-allowlisted import.

    Checking with ``ast.parse`` (rather than a regex) means comments,
    strings and docstrings containing the word ``import`` don't trigger
    false positives, and keeps the allowlist honest against tricks like
    ``__import__("os")`` which we also reject because it does not appear
    as an ``Import`` / ``ImportFrom`` node in the AST but as a ``Call``.

    Args:
        code: The Python source extracted from a ``<code>`` block.

    Returns:
        ``(True, "")`` when every import is allowlisted, otherwise
        ``(False, "<reason>")`` with a human-readable rejection.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return False, f"SyntaxError before import validation: {exc}"

    allowlist = _get_import_allowlist()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".", 1)[0]
                if top not in allowlist:
                    return False, (
                        f"Import of '{alias.name}' is not in the CodeAct "
                        f"allowlist. Allowed top-level packages: "
                        f"{', '.join(sorted(allowlist))}."
                    )
        elif isinstance(node, ast.ImportFrom):
            module = (node.module or "").split(".", 1)[0]
            if module and module not in allowlist:
                return False, (
                    f"Import 'from {node.module} import ...' is not in the "
                    f"CodeAct allowlist. Allowed top-level packages: "
                    f"{', '.join(sorted(allowlist))}."
                )
        elif isinstance(node, ast.Call):
            # Block __import__("foo") — would otherwise slip past the
            # Import/ImportFrom AST checks.
            func = node.func
            if isinstance(func, ast.Name) and func.id == "__import__":
                return False, (
                    "Calls to `__import__()` are forbidden in CodeAct blocks."
                )

    return True, ""


def _extract_code_block(content: str) -> str | None:
    """Return the first ``<code>...</code>`` block, stripped, or ``None``."""
    match = _CODE_BLOCK_RE.search(content)
    if match is None:
        return None
    code = match.group(1).strip()
    return code or None


async def _run_code_in_sandbox(code: str) -> tuple[bool, str, str, int]:
    """Run ``code`` in the project sandbox on a worker thread.

    ``SandboxExecutor.run_code`` is synchronous and spawns real
    subprocesses, so we offload it to ``asyncio.to_thread`` to avoid
    stalling the event loop. Any unexpected exception is normalised into
    a failed :class:`tuple` so callers can surface the error to the LLM
    as feedback without crashing the graph.

    Returns:
        ``(success, stdout, stderr, exit_code)``. ``success`` is ``True``
        when ``exit_code == 0``.
    """
    from lightagent.sandbox.executor import SandboxExecutor

    def _run() -> tuple[str, str, int]:
        executor = SandboxExecutor()
        result = executor.run_code(code, language="python")
        return result.stdout, result.stderr, result.exit_code

    try:
        stdout, stderr, exit_code = await asyncio.to_thread(_run)
    except Exception as exc:
        logger.warning("codeact_sandbox_error", error=str(exc))
        return False, "", f"Sandbox run failed: {exc}", 1

    return exit_code == 0, stdout, stderr, exit_code


def _format_execution_feedback(
    stdout: str, stderr: str, exit_code: int
) -> str:
    """Render sandbox output as a message the LLM can act on."""
    parts: list[str] = [f"Execution finished with exit code {exit_code}."]
    if stdout.strip():
        parts.append(f"stdout:\n{stdout.strip()}")
    if stderr.strip():
        parts.append(f"stderr:\n{stderr.strip()}")
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Main node
# ---------------------------------------------------------------------------


async def codeact_node(state: AgentState) -> dict[str, Any]:
    """Run the CodeAct generate → execute → auto-correct loop.

    The node is an ``async`` function because it awaits the LLM and the
    sandbox. It never raises: every failure mode (shell disabled, import
    rejected, sandbox crash, max iterations exceeded) is mapped to a
    conventional ``AIMessage`` describing the outcome so the supervisor
    can simply route it to END.

    Args:
        state: Current LangGraph shared state.

    Returns:
        Partial state dict with the final ``AIMessage`` appended under
        ``messages`` and ``current_agent="codeact"``.
    """
    settings = get_settings()

    if not settings.codeact_enabled:
        logger.debug("codeact_disabled_by_setting")
        return {
            "current_agent": "codeact",
            "messages": [
                AIMessage(
                    content=(
                        "CodeAct is disabled "
                        "(`LIGHTAGENT_CODEACT_ENABLED=false`). "
                        "The supervisor should route this task to "
                        "`coder` instead."
                    )
                )
            ],
        }

    max_iterations = settings.codeact_max_iterations
    llm = ProviderRegistry().get_llm_with_fallback()

    # Seed the conversation with the last ``_HISTORY_WINDOW`` messages so
    # we carry over the user request and any prior context without
    # replaying the entire session.
    working_messages: list[Any] = list(state["messages"][-_HISTORY_WINDOW:])

    consecutive_failures = 0
    last_feedback: str = ""
    final_reply: str | None = None

    for iteration in range(max_iterations):
        response = await llm.ainvoke(
            [SystemMessage(content=CODEACT_SYSTEM_PROMPT), *working_messages]
        )
        raw_content = str(getattr(response, "content", ""))
        working_messages.append(AIMessage(content=raw_content))

        code = _extract_code_block(raw_content)
        if code is None:
            # No more code blocks → LLM considers the task complete.
            final_reply = raw_content.strip() or (
                "CodeAct finished without emitting a code block."
            )
            logger.info(
                "codeact_completed_without_code",
                iteration=iteration,
            )
            break

        # 1) Import allowlist — AC-040-4.
        allowed, reason = _validate_imports(code)
        if not allowed:
            logger.warning(
                "codeact_import_rejected",
                iteration=iteration,
                reason=reason,
            )
            consecutive_failures += 1
            if consecutive_failures >= _MAX_CONSECUTIVE_FAILURES:
                final_reply = (
                    f"CodeAct aborted after {consecutive_failures} blocked "
                    f"code blocks. Last reason: {reason}"
                )
                break
            working_messages.append(
                HumanMessage(
                    content=(
                        f"Code block rejected: {reason} "
                        f"Please emit a new <code>...</code> block that only "
                        f"imports allowlisted packages."
                    )
                )
            )
            last_feedback = reason
            continue

        # 2) Shell gate — AC-040-1. We feed the synthetic ``python -c``
        # command into ``check_shell`` purely for logging; the real
        # execution happens inside the sandbox, not via subprocess.
        if not ActionInterceptor.check_shell(["python", "-c", code]):
            final_reply = _SHELL_DISABLED_REPLY
            logger.warning("codeact_shell_disabled")
            break

        # 3) Sandbox run.
        success, stdout, stderr, exit_code = await _run_code_in_sandbox(code)
        feedback = _format_execution_feedback(stdout, stderr, exit_code)
        last_feedback = feedback
        working_messages.append(HumanMessage(content=feedback))

        if success:
            consecutive_failures = 0
            # 4) Completion check — stop as soon as we see the result
            # marker. The LLM is prompted to always print it on the last
            # successful step.
            if _RESULT_MARKER in stdout:
                final_reply = (
                    raw_content.strip()
                    + "\n\n---\n"
                    + feedback
                )
                logger.info(
                    "codeact_completed_with_result",
                    iteration=iteration,
                )
                break
            continue

        # 5) Failure path — AC-040-2 auto-correction.
        consecutive_failures += 1
        logger.info(
            "codeact_iteration_failed",
            iteration=iteration,
            consecutive_failures=consecutive_failures,
            exit_code=exit_code,
        )
        if consecutive_failures >= _MAX_CONSECUTIVE_FAILURES:
            final_reply = (
                f"CodeAct gave up after {consecutive_failures} consecutive "
                f"failed code blocks. Last feedback:\n{feedback}"
            )
            break

    if final_reply is None:
        final_reply = (
            f"CodeAct reached max_iterations={max_iterations} without a "
            f"terminating result. Last feedback:\n{last_feedback}"
        )
        logger.warning("codeact_max_iterations_reached", limit=max_iterations)

    return {
        "current_agent": "codeact",
        "messages": [AIMessage(content=final_reply)],
    }


__all__ = [
    "CODEACT_SYSTEM_PROMPT",
    "codeact_node",
]

"""CodeAct agent — direct Python code generation with auto-correction.

SPEC-040 / Phase 38, hardened by SPEC-044 / Phase 42. Unlike the
classic :mod:`lightagent.agents.coder` node which uses a ReAct
tool-calling loop (``sandbox_exec`` JSON tool calls processed by
:func:`react_loop`), CodeAct asks the LLM to emit executable Python
directly wrapped in ``<code>...</code>`` tags. A single block can
combine I/O, computation and validation, cutting token usage by ~30 %
on multi-step coding tasks (arxiv:2402.01030).

.. warning::

   **The current runtime is NOT an isolated sandbox.**

   :class:`~lightagent.sandbox.executor.SandboxExecutor` writes the
   code to a temporary ``.py`` file and spawns a plain host subprocess
   with the LightAgent process's full privileges — no container, no
   ``chroot``, no seccomp, no network namespace, no cgroup limits.
   The ``sandbox`` name in this module is historical.

   Real process isolation is tracked under SPEC-045 (Phase 43,
   ``lightagent/sandbox/isolation.py``). Until that lands, CodeAct
   should be treated as *"run arbitrary Python as the LightAgent
   user"*: any prompt-injection path that reaches the CodeAct LLM can
   achieve host code execution. For that reason
   ``LIGHTAGENT_CODEACT_ENABLED`` defaults to ``False`` after SPEC-044
   and the supervisor downgrades any ``codeact`` routing decision to
   the classic ``coder`` node when the flag is off.

   Operators running in environments where SPEC-045 is not yet active
   must keep the flag off. Once SPEC-045 ships a working backend,
   ``codeact_enabled=true`` is safe again — see
   ``docs/report_security_v2_202060409.md`` for the full threat
   analysis.

Safety pipeline (hard requirements from CLAUDE.md Phases 38 + 42
rules):

1. Parse the first ``<code>...</code>`` block from the LLM response.
2. :func:`_validate_imports` enforces a six-layer AST check
   (SPEC-044 AC-044-1..6): name-call denylist, attribute-call
   denylist, dunder identifier rule, subscript base rule, import
   allowlist, and syntax validation. The denylist layers catch every
   known single-step bypass pattern documented in the security
   report (``getattr(__builtins__, "__import__")``, subclass
   traversal via ``__subclasses__``, dynamic-evaluation built-ins
   wrapped around string literals, ``globals()["__builtins__"]``).
   None of this is a substitute for real process isolation — it is
   defense-in-depth layered on top of SPEC-045.
3. :meth:`ActionInterceptor.check_shell` gates execution on
   ``LIGHTAGENT_SHELL_ENABLED``. When the gate is closed we return a
   graceful ``AIMessage`` explaining how to enable it instead of
   silently failing.
4. Code runs in the existing :class:`SandboxExecutor` (via
   ``asyncio.to_thread`` because ``run_code`` is sync and performs
   real subprocess I/O — we must never block the event loop). See
   the warning above: the executor is a host subprocess runner
   today, not a sandbox.
5. Stdout, stderr and exit code are appended to the running message
   list so the next iteration can auto-correct errors. The loop
   bails out on a clean run with the special marker
   ``__CODEACT_RESULT__`` present in stdout, when no further
   ``<code>`` block is emitted, when ``max_iterations`` is reached,
   or after ``_MAX_CONSECUTIVE_FAILURES`` failures on the same
   request.
"""

from __future__ import annotations

import ast
import asyncio
import re
from typing import TYPE_CHECKING, Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from prismal.core.config import get_settings
from prismal.core.logging import get_logger
from prismal.providers.registry import ProviderRegistry
from prismal.security.action_interceptor import ActionInterceptor

if TYPE_CHECKING:
    from prismal.agents.state import AgentState

logger = get_logger("lightagent.agents.codeact")


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MAX_CONSECUTIVE_FAILURES: int = 3
"""Hard cap on same-request failures before a best-effort reply (AC-040-2)."""

_HISTORY_WINDOW: int = 8
"""Messages from state.messages sent to the LLM as context."""

_CODE_BLOCK_RE: re.Pattern[str] = re.compile(r"<code>(.*?)</code>", re.DOTALL | re.IGNORECASE)

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
block is run in a restricted Python subprocess and its stdout/stderr is fed
back to you as the next message, so you can iterate, validate, and
self-correct.

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
runs. Never try to ``import prismal`` or reach into project internals —
the restricted runtime actively rejects imports of the project source tree.

## Forbidden Calls (will be rejected before execution)
The AST validator blocks every known sandbox-escape pattern before the code
runs. Do NOT attempt to use any of these; the validator will reject the
block and you will need to emit a fixed version:
- Dynamic evaluation built-ins (``eval``, ``exec``, ``compile``)
- Reflection built-ins (``getattr``, ``setattr``, ``delattr``)
- Scope escape (``globals``, ``locals``, ``vars``)
- File I/O built-in (``open``)
- Dynamic import (``__import__``)
- Dunder attribute traversal (``.__class__``, ``.__bases__``,
  ``.__subclasses__``, ``.__globals__``, ``.__builtins__``,
  ``.__reduce__``, ``.__dict__``, ``.__mro__``)
- Shell / subprocess bridges (``.system``, ``.popen``, ``subprocess.Popen``
  and friends)
- Subscript on ``__builtins__`` / ``globals()`` / ``locals()``

## Iteration Rules
- If the runtime returns stdout/stderr indicating an error, analyse it and
  emit a fixed <code> block in your next response.
- If the task is complete, respond with a short natural-language summary and
  NO <code> block — that signals the end of the loop.
- Prefer a single comprehensive block over many small ones; one well-written
  block with I/O, computation and validation is better than 5 tiny blocks.
- Never dynamically evaluate untrusted strings."""


_SHELL_DISABLED_REPLY: str = (
    "CodeAct cannot run code because `LIGHTAGENT_SHELL_ENABLED=false`. "
    "Set `LIGHTAGENT_SHELL_ENABLED=true` in your environment to enable "
    "the restricted host runtime, or route this task to a non-execution "
    "agent. Note that SPEC-045 (Phase 43) will replace the current "
    "host-subprocess runtime with a real isolation backend — until "
    "then CodeAct should stay disabled in production."
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_import_allowlist() -> frozenset[str]:
    """Parse the allowlist from settings into a frozenset of top-level names."""
    raw = get_settings().codeact_import_allowlist or ""
    return frozenset(name.strip() for name in raw.split(",") if name.strip())


# ---------------------------------------------------------------------------
# Phase 42 / SPEC-044 AC-044-1, AC-044-2 — denylists
# ---------------------------------------------------------------------------

#: Built-in identifiers that must never appear as the *callee* of an
#: ``ast.Call`` with a bare ``ast.Name`` head. Covers every known single-
#: step bypass of the import allowlist: dynamic evaluation
#: (``eval``/``exec``/``compile``), attribute reflection
#: (``getattr``/``setattr``/``delattr``), scope escape
#: (``globals``/``locals``/``vars``), arbitrary file I/O (``open``),
#: dynamic import (``__import__``), interactive UI hooks
#: (``input``/``breakpoint``). Rejected regardless of the allowlist
#: contents — these calls are categorically forbidden in CodeAct blocks.
_DANGEROUS_NAME_CALLS: frozenset[str] = frozenset(
    {
        "eval",
        "exec",
        "compile",
        "getattr",
        "setattr",
        "delattr",
        "globals",
        "locals",
        "vars",
        "open",
        "__import__",
        "input",
        "breakpoint",
    }
)

#: Dunder identifiers that remain legal inside a CodeAct block. The
#: broader dunder rule (AC-044-3) rejects every ``ast.Name`` /
#: ``ast.Attribute`` whose identifier starts with double underscore,
#: so this allowlist whitelists the handful of benign dunders that
#: real code still needs: ``__name__`` for ``if __name__ == "__main__"``
#: idioms, ``__doc__`` for introspection of the current module, and
#: ``__file__`` for path resolution relative to the code block's
#: source file. ``__main__`` is included so a comparison against the
#: string-equivalent Name (if the LLM produces one) still passes,
#: though string-literal ``"__main__"`` already bypasses the dunder
#: check because it is an ``ast.Constant``, not an ``ast.Name``.
_ALLOWED_DUNDERS: frozenset[str] = frozenset(
    {
        "__name__",
        "__main__",
        "__doc__",
        "__file__",
    }
)

#: Method names that must never be called via attribute access. Covers
#: shell bridges on modules (``system``/``popen``), the whole
#: ``subprocess`` family (``run``/``call``/``check_output``/
#: ``check_call``/``Popen``), the ``os.spawn*`` and ``os.exec*`` suites,
#: classic Python sandbox-escape primitives
#: (``__subclasses__``/``__bases__``/``__class__``), globals / builtins
#: reflection (``__globals__``/``__builtins__``/``__import__``), and
#: serialization reduce hooks (``__reduce__``/``__reduce_ex__``). The
#: check fires regardless of the receiver — even ``"foo".__class__`` is
#: blocked because ``__class__`` alone is enough to pivot into subclass
#: traversal.
_DANGEROUS_ATTR_CALLS: frozenset[str] = frozenset(
    {
        "system",
        "popen",
        "call",
        "run",
        "check_output",
        "check_call",
        "Popen",
        "spawnl",
        "spawnv",
        "spawnve",
        "execve",
        "execvp",
        "execvpe",
        "__subclasses__",
        "__bases__",
        "__class__",
        "__globals__",
        "__builtins__",
        "__import__",
        "__reduce__",
        "__reduce_ex__",
        "__getattribute__",
    }
)


def _validate_imports(code: str) -> tuple[bool, str]:
    """Walk the AST and reject unsafe CodeAct code blocks.

    Enforces six rejection layers (Phase 42 / SPEC-044 hardening), in
    order of precedence:

    1. **Name-call denylist** (AC-044-1): any ``ast.Call`` whose
       ``func`` is a bare ``ast.Name`` with ``id`` in
       :data:`_DANGEROUS_NAME_CALLS` is rejected outright. Covers
       ``eval``/``exec``/``compile``, reflection built-ins
       (``getattr``/``setattr``/``delattr``), scope escape
       (``globals``/``locals``/``vars``), file I/O (``open``),
       dynamic import (``__import__``), and interactive hooks
       (``input``/``breakpoint``).
    2. **Attribute-call denylist** (AC-044-2): any ``ast.Call`` whose
       ``func`` is an ``ast.Attribute`` with ``attr`` in
       :data:`_DANGEROUS_ATTR_CALLS` is rejected regardless of the
       receiver. Covers shell bridges, the ``subprocess`` family,
       ``os.spawn*`` / ``os.exec*``, and classic sandbox-escape
       pivots like ``__subclasses__``/``__class__``.
    3. **Dunder identifier rule** (AC-044-3): any ``ast.Name`` or
       ``ast.Attribute`` whose identifier starts with double
       underscore and is not in :data:`_ALLOWED_DUNDERS` is rejected.
       Catches dunder *traversal* patterns (``foo.__class__.__bases__``)
       that the Call-based layers miss because the dunder is not the
       head of a call — it is only walked through on the way to
       something else. The allowlist (``__name__``, ``__main__``,
       ``__doc__``, ``__file__``) preserves the common
       ``if __name__ == "__main__"`` idiom.
    4. **Subscript base rule** (AC-044-4): any ``ast.Subscript`` whose
       ``value`` is ``Name(id="__builtins__")``, ``Call(func=Name("globals"))``
       or ``Call(func=Name("locals"))`` is rejected. Closes the
       ``__builtins__["open"]`` / ``globals()['__builtins__']`` family
       of bypasses. Ordinary dict / list subscripts are unaffected.
    5. **Import allowlist**: ``ast.Import`` and ``ast.ImportFrom``
       nodes must reference top-level modules listed in
       ``LIGHTAGENT_CODEACT_IMPORT_ALLOWLIST``.
    6. **Syntax**: ``ast.parse`` failures are wrapped as a rejection
       with the raw ``SyntaxError`` message for operator visibility.

    The denylist layers (1) and (2) run **before** any allowlist
    decision so a banned call wrapped around otherwise-allowlisted
    identifiers is still rejected. The AST-based approach avoids
    false positives on comments, docstrings, and string literals
    that merely contain the word ``import`` — at the cost of also
    being unable to see ``import`` statements *inside* string
    literals fed to the dynamic-eval built-ins. That blind spot is
    closed by rule (1) which rejects those built-ins categorically.

    This validator is defense-in-depth layered on top of the Phase 43
    process isolation (SPEC-045). When isolation is unavailable
    CodeAct should be disabled (``LIGHTAGENT_CODEACT_ENABLED=false``);
    the denylist is not a substitute for real sandboxing.

    Args:
        code: The Python source extracted from a ``<code>`` block.

    Returns:
        ``(True, "")`` when every layer passes, otherwise
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
            # Phase 42 / SPEC-044 AC-044-1 + AC-044-2 — deny dangerous
            # calls regardless of the allowlist. These rules run ahead
            # of any allowlist decision so a banned call (e.g. ``eval``
            # wrapped around a string literal) is rejected even when
            # every surrounding identifier is allowlisted.
            func = node.func
            if isinstance(func, ast.Name) and func.id in _DANGEROUS_NAME_CALLS:
                return False, (
                    f"Call to built-in '{func.id}()' is forbidden "
                    f"in CodeAct blocks. This bypasses the import "
                    f"allowlist and is categorically denied."
                )
            if isinstance(func, ast.Attribute) and func.attr in _DANGEROUS_ATTR_CALLS:
                return False, (
                    f"Call to attribute '.{func.attr}()' is "
                    f"forbidden in CodeAct blocks. This is a known "
                    f"sandbox-escape pivot and is categorically "
                    f"denied regardless of the receiver."
                )
        elif isinstance(node, ast.Name):
            # AC-044-3: reject dunder *identifiers* referenced as
            # Name. Catches ``foo = __builtins__`` and similar cases
            # where the dunder is traversed but never directly called
            # — the Call-based checks above only see dunders when
            # they are the head of an ``ast.Call``. The small allow-
            # list (``__name__``, ``__main__``, ``__doc__``,
            # ``__file__``) preserves the ``if __name__ == "__main__"``
            # idiom which is harmless.
            if node.id.startswith("__") and node.id not in _ALLOWED_DUNDERS:
                return False, (
                    f"Dunder identifier '{node.id}' is forbidden "
                    f"in CodeAct blocks. Dunder access is a known "
                    f"sandbox-escape primitive; only "
                    f"{sorted(_ALLOWED_DUNDERS)} are allowed."
                )
        elif isinstance(node, ast.Attribute):
            # AC-044-3: same rule for attribute access. Catches
            # ``foo.__class__.__bases__`` and every other chain that
            # traverses dunder attributes without calling them.
            if node.attr.startswith("__") and node.attr not in _ALLOWED_DUNDERS:
                return False, (
                    f"Dunder attribute access '.{node.attr}' is "
                    f"forbidden in CodeAct blocks. Dunder traversal "
                    f"is a known sandbox-escape primitive."
                )
        elif isinstance(node, ast.Subscript):
            # AC-044-4: reject subscripts whose base resolves to one
            # of the three known pivots into ``__builtins__``. Other
            # subscripts (``my_dict["key"]``, ``my_list[0]``) are
            # completely unaffected — only the specific bases below
            # are denied.
            base = node.value
            if isinstance(base, ast.Name) and base.id == "__builtins__":
                return False, ("Subscript on '__builtins__' is forbidden in CodeAct blocks.")
            if (
                isinstance(base, ast.Call)
                and isinstance(base.func, ast.Name)
                and base.func.id in {"globals", "locals"}
            ):
                return False, (f"Subscript on '{base.func.id}()' is forbidden in CodeAct blocks.")

    return True, ""


def _extract_code_block(content: str) -> str | None:
    """Return the first ``<code>...</code>`` block, stripped, or ``None``."""
    match = _CODE_BLOCK_RE.search(content)
    if match is None:
        return None
    code = match.group(1).strip()
    return code or None


async def _run_code_in_sandbox(code: str) -> tuple[bool, str, str, int]:
    """Run ``code`` in the LightAgent code runtime on a worker thread.

    .. warning::

       As of SPEC-044 (Phase 42), the "sandbox" name is historical.
       :class:`~lightagent.sandbox.executor.SandboxExecutor` currently
       runs the supplied code in a plain host subprocess with the
       LightAgent process's full privileges — no container, no
       seccomp, no network namespace. Real isolation is tracked under
       SPEC-045 (Phase 43). The function name is kept for backward
       compatibility with the existing call sites; the underlying
       behaviour will change transparently once the isolation backend
       lands.

    ``SandboxExecutor.run_code`` is synchronous and spawns real
    subprocesses, so we offload it to ``asyncio.to_thread`` to avoid
    stalling the event loop. Any unexpected exception is normalised
    into a failed :class:`tuple` so callers can surface the error to
    the LLM as feedback without crashing the graph.

    Returns:
        ``(success, stdout, stderr, exit_code)``. ``success`` is
        ``True`` when ``exit_code == 0``.
    """
    from prismal.sandbox.executor import SandboxExecutor

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


def _format_execution_feedback(stdout: str, stderr: str, exit_code: int) -> str:
    """Render runtime output as a message the LLM can act on."""
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
            final_reply = raw_content.strip() or ("CodeAct finished without emitting a code block.")
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
                final_reply = raw_content.strip() + "\n\n---\n" + feedback
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

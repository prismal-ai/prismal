"""Tests for :mod:`lightagent.agents.codeact_agent` — SPEC-040 AC-040-5.

The CodeAct loop has four moving parts (LLM, import validator, shell
gate, sandbox) and the only safe way to exercise the auto-correction
machinery deterministically is to mock them. These tests drive the
:func:`codeact_node` through every branch the spec calls out:

* single successful block terminates via the ``__CODEACT_RESULT__`` marker
* sandbox errors trigger an auto-correction iteration with the error fed
  back as a ``HumanMessage``
* ``max_iterations`` is respected even when the LLM keeps generating code
* the import allowlist blocks forbidden packages *before* shell or
  sandbox ever run (AC-040-4)
* ``ActionInterceptor.check_shell`` is called *before* every sandbox
  invocation (CLAUDE.md Phase 38 rule #2)
* LLM response without a ``<code>`` block terminates immediately with
  the prose as the final reply
* three consecutive failures short-circuit the loop with a best-effort
  message (AC-040-2)

Every test uses ``patch`` on module-level symbols so real provider calls
and real subprocess I/O are never invoked.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from langchain_core.messages import HumanMessage

from lightagent.agents.codeact_agent import (
    _CODE_BLOCK_RE,
    _RESULT_MARKER,
    _extract_code_block,
    _format_execution_feedback,
    _get_import_allowlist,
    _validate_imports,
    codeact_node,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_llm_response(content: str) -> MagicMock:
    """Build a LangChain-style LLM response mock with the given text."""
    resp = MagicMock()
    resp.content = content
    return resp


def _make_llm_sequence(*contents: str) -> MagicMock:
    """Create an LLM mock whose ``ainvoke`` yields the given responses in order.

    When ``contents`` is shorter than the number of calls the last item
    is repeated — this matches how tests check "max_iterations reached"
    without having to pre-compute exactly how many calls happen.
    """
    responses = [_make_llm_response(c) for c in contents]

    async def _ainvoke(*_args: Any, **_kwargs: Any) -> MagicMock:
        if len(responses) == 1:
            return responses[0]
        return responses.pop(0) if len(responses) > 1 else responses[0]

    llm = MagicMock()
    llm.ainvoke = AsyncMock(side_effect=_ainvoke)
    return llm


def _patch_codeact(
    llm: MagicMock,
    *,
    shell_enabled: bool = True,
    sandbox_results: list[tuple[bool, str, str, int]] | None = None,
) -> Any:
    """Context manager that patches every external dependency of codeact_node.

    Args:
        llm: LLM mock returned by :func:`ProviderRegistry`.
        shell_enabled: Value returned by
            :meth:`ActionInterceptor.check_shell`.
        sandbox_results: Sequence of
            ``(success, stdout, stderr, exit_code)`` tuples returned by
            :func:`_run_code_in_sandbox` on successive calls. If ``None``
            a single successful no-op is used.

    Returns:
        A :class:`contextlib.ExitStack`-compatible context manager. Use
        as ``with _patch_codeact(...) as mocks:`` to get back a dict of
        the individual mocks for assertions.
    """
    import contextlib

    results = sandbox_results or [(True, f"{_RESULT_MARKER} ok", "", 0)]
    sandbox_mock = AsyncMock(side_effect=results)
    check_shell_mock = MagicMock(return_value=shell_enabled)
    provider_mock = MagicMock()
    provider_mock.return_value.get_llm_with_fallback.return_value = llm

    @contextlib.contextmanager
    def _cm() -> Any:
        with (
            patch(
                "lightagent.agents.codeact_agent.ProviderRegistry",
                new=provider_mock,
            ),
            patch(
                "lightagent.agents.codeact_agent.ActionInterceptor.check_shell",
                new=check_shell_mock,
            ),
            patch(
                "lightagent.agents.codeact_agent._run_code_in_sandbox",
                new=sandbox_mock,
            ),
        ):
            yield {
                "sandbox": sandbox_mock,
                "check_shell": check_shell_mock,
                "llm": llm,
            }

    return _cm()


def _make_state(message: str = "compute 2+2") -> Any:
    """Build the minimal state dict ``codeact_node`` expects.

    Typed as :class:`~typing.Any` because ``AgentState`` is a
    :class:`~typing.TypedDict` with many required fields we don't need
    here — the test dict is accepted at runtime but would trip mypy
    otherwise.
    """
    return {
        "messages": [HumanMessage(content=message)],
        "session_id": "u1-12345",
    }


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class TestExtractCodeBlock:
    def test_extracts_simple_block(self) -> None:
        assert _extract_code_block("<code>print(1)</code>") == "print(1)"

    def test_strips_surrounding_whitespace(self) -> None:
        assert (
            _extract_code_block("<code>  result = 1  </code>")
            == "result = 1"
        )

    def test_multiline_block(self) -> None:
        src = "<code>a = 1\nb = 2\nprint(a + b)</code>"
        assert _extract_code_block(src) == "a = 1\nb = 2\nprint(a + b)"

    def test_returns_none_when_no_block(self) -> None:
        assert _extract_code_block("no tags here") is None

    def test_returns_none_when_block_is_empty(self) -> None:
        assert _extract_code_block("<code>   </code>") is None

    def test_case_insensitive_tags(self) -> None:
        assert _extract_code_block("<CODE>x=1</CODE>") == "x=1"

    def test_only_first_block_is_returned(self) -> None:
        src = "<code>a=1</code> and then <code>b=2</code>"
        assert _extract_code_block(src) == "a=1"

    def test_regex_uses_dotall_for_newlines(self) -> None:
        assert _CODE_BLOCK_RE.search("<code>line1\nline2</code>") is not None


class TestValidateImports:
    def test_allows_allowlisted_imports(self) -> None:
        ok, reason = _validate_imports("import os\nimport json\nresult = 1")
        assert ok, reason

    def test_allows_from_imports_in_allowlist(self) -> None:
        ok, _ = _validate_imports("from pathlib import Path")
        assert ok

    def test_blocks_non_allowlisted_import(self) -> None:
        ok, reason = _validate_imports("import socket")
        assert not ok
        assert "socket" in reason
        assert "allowlist" in reason.lower()

    def test_blocks_lightagent_imports(self) -> None:
        """CodeAct must never reach into project internals."""
        ok, reason = _validate_imports("from lightagent.memory import x")
        assert not ok
        assert "lightagent" in reason

    def test_blocks_dynamic_import_call(self) -> None:
        ok, reason = _validate_imports('x = __import__("os")')
        assert not ok
        assert "__import__" in reason

    def test_allows_dotted_submodule_when_top_level_allowed(self) -> None:
        ok, _ = _validate_imports("import os.path")
        assert ok

    def test_syntax_error_reported_gracefully(self) -> None:
        ok, reason = _validate_imports("def broken(:\n    pass")
        assert not ok
        assert "SyntaxError" in reason

    def test_comment_mentioning_import_is_ignored(self) -> None:
        ok, _ = _validate_imports(
            "# todo: import socket later\nresult = 42"
        )
        assert ok

    def test_string_mentioning_import_is_ignored(self) -> None:
        ok, _ = _validate_imports('msg = "please import socket"\nresult = 1')
        assert ok


class TestGetImportAllowlist:
    def test_default_contains_key_packages(self) -> None:
        allowlist = _get_import_allowlist()
        for pkg in ("os", "json", "pandas", "numpy", "httpx"):
            assert pkg in allowlist

    def test_custom_allowlist_from_settings(self) -> None:
        with patch(
            "lightagent.agents.codeact_agent.get_settings"
        ) as mock_settings:
            mock_settings.return_value.codeact_import_allowlist = (
                "alpha, beta ,gamma"
            )
            allowlist = _get_import_allowlist()
        assert allowlist == frozenset({"alpha", "beta", "gamma"})


class TestFormatExecutionFeedback:
    def test_includes_exit_code(self) -> None:
        fb = _format_execution_feedback("ok", "", 0)
        assert "exit code 0" in fb

    def test_omits_empty_streams(self) -> None:
        fb = _format_execution_feedback("hello", "", 0)
        assert "hello" in fb
        assert "stderr" not in fb

    def test_includes_both_streams_on_failure(self) -> None:
        fb = _format_execution_feedback("partial", "boom", 2)
        assert "exit code 2" in fb
        assert "partial" in fb
        assert "boom" in fb


# ---------------------------------------------------------------------------
# codeact_node — behaviour covered by AC-040-5
# ---------------------------------------------------------------------------


class TestCodeactNodeSuccess:
    async def test_single_successful_code_block(self) -> None:
        """Happy path: one block, ``__CODEACT_RESULT__`` in stdout, done."""
        llm = _make_llm_sequence(
            f'<code>result = 2 + 2\nprint("{_RESULT_MARKER}", result)</code>'
        )
        with _patch_codeact(
            llm,
            sandbox_results=[(True, f"{_RESULT_MARKER} 4", "", 0)],
        ) as mocks:
            out = await codeact_node(_make_state())

        assert out["current_agent"] == "codeact"
        reply = out["messages"][0].content
        assert "__CODEACT_RESULT__ 4" in reply
        mocks["sandbox"].assert_awaited_once()
        mocks["llm"].ainvoke.assert_awaited_once()  # no retries

    async def test_no_code_block_returns_direct_response(self) -> None:
        """When the LLM answers with prose only, that prose is the reply."""
        llm = _make_llm_sequence(
            "This is a pure explanation; no code needed. The answer is 42."
        )
        with _patch_codeact(llm) as mocks:
            out = await codeact_node(_make_state("explain X"))

        assert "answer is 42" in out["messages"][0].content
        mocks["sandbox"].assert_not_awaited()
        mocks["check_shell"].assert_not_called()

    async def test_multiple_successful_blocks_until_marker(self) -> None:
        """Intermediate blocks without the marker continue the loop."""
        llm = _make_llm_sequence(
            "<code>x = 1\nprint(x)</code>",  # no marker → continue
            f'<code>y = x + 1\nprint("{_RESULT_MARKER}", y)</code>',
        )
        with _patch_codeact(
            llm,
            sandbox_results=[
                (True, "1", "", 0),  # first block: success, no marker
                (True, f"{_RESULT_MARKER} 2", "", 0),  # second: done
            ],
        ) as mocks:
            out = await codeact_node(_make_state())

        assert "__CODEACT_RESULT__ 2" in out["messages"][0].content
        assert mocks["sandbox"].await_count == 2
        assert mocks["llm"].ainvoke.await_count == 2


class TestCodeactNodeAutoCorrection:
    async def test_auto_correction_on_sandbox_error(self) -> None:
        """Failed block → error fed to LLM → fixed block succeeds."""
        llm = _make_llm_sequence(
            "<code>result = undefined_var\n"
            f'print("{_RESULT_MARKER}", result)</code>',
            f'<code>result = 42\nprint("{_RESULT_MARKER}", result)</code>',
        )
        with _patch_codeact(
            llm,
            sandbox_results=[
                (False, "", "NameError: name 'undefined_var'", 1),
                (True, f"{_RESULT_MARKER} 42", "", 0),
            ],
        ) as mocks:
            out = await codeact_node(_make_state())

        assert "__CODEACT_RESULT__ 42" in out["messages"][0].content
        assert mocks["sandbox"].await_count == 2
        assert mocks["llm"].ainvoke.await_count == 2

        # Second LLM call must have received the error feedback as the
        # most recent message so it can auto-correct.
        second_call_args = mocks["llm"].ainvoke.await_args_list[1]
        messages = second_call_args.args[0]
        feedback_texts = [
            getattr(m, "content", "") for m in messages if m is not None
        ]
        assert any("NameError" in text for text in feedback_texts)

    async def test_consecutive_failures_trigger_best_effort(self) -> None:
        """Three consecutive sandbox failures abort with a best-effort reply."""
        llm = _make_llm_sequence(
            "<code>result = 1/0</code>",
            "<code>result = 1/0</code>",
            "<code>result = 1/0</code>",
            "<code>result = 1/0</code>",  # would never be reached
        )
        with _patch_codeact(
            llm,
            sandbox_results=[
                (False, "", "ZeroDivisionError", 1),
                (False, "", "ZeroDivisionError", 1),
                (False, "", "ZeroDivisionError", 1),
            ],
        ) as mocks:
            out = await codeact_node(_make_state())

        reply = out["messages"][0].content
        assert "gave up" in reply or "consecutive" in reply
        assert mocks["sandbox"].await_count == 3
        # Loop aborted at 3 failures — LLM called exactly 3 times, not 4.
        assert mocks["llm"].ainvoke.await_count == 3


class TestCodeactNodeMaxIterations:
    async def test_max_iterations_limit_respected(self) -> None:
        """Successful blocks without the result marker eventually hit the cap."""
        block = "<code>x = 1\nprint(x)</code>"
        # 10 responses to guarantee we saturate any reasonable cap.
        llm = _make_llm_sequence(*([block] * 10))
        with (
            patch(
                "lightagent.agents.codeact_agent.get_settings"
            ) as mock_settings,
            _patch_codeact(
                llm,
                sandbox_results=[(True, "1", "", 0)] * 10,
            ) as mocks,
        ):
            mock_settings.return_value.codeact_enabled = True
            mock_settings.return_value.codeact_max_iterations = 3
            mock_settings.return_value.codeact_import_allowlist = (
                "os,pathlib,json"
            )
            out = await codeact_node(_make_state())

        assert "max_iterations=3" in out["messages"][0].content
        assert mocks["llm"].ainvoke.await_count == 3
        assert mocks["sandbox"].await_count == 3


class TestCodeactNodeImportAllowlist:
    async def test_import_allowlist_blocks_forbidden_import(self) -> None:
        """A block that imports a non-allowlisted package is rejected
        *before* the shell gate or sandbox runs (AC-040-4).
        """
        llm = _make_llm_sequence(
            "<code>import socket\nresult = 1</code>",
            # After rejection, the LLM gets a feedback HumanMessage and
            # emits a fixed block.
            f'<code>import os\nresult = 1\nprint("{_RESULT_MARKER}", result)</code>',
        )
        with _patch_codeact(
            llm,
            sandbox_results=[(True, f"{_RESULT_MARKER} 1", "", 0)],
        ) as mocks:
            out = await codeact_node(_make_state())

        assert "__CODEACT_RESULT__ 1" in out["messages"][0].content
        # Sandbox ran exactly once — the rejected block never reached it.
        assert mocks["sandbox"].await_count == 1
        # And the shell gate was only consulted for the valid block.
        assert mocks["check_shell"].call_count == 1

    async def test_three_blocked_imports_abort_the_loop(self) -> None:
        """Import rejections also count toward ``_MAX_CONSECUTIVE_FAILURES``."""
        llm = _make_llm_sequence(
            "<code>import socket\nresult = 1</code>",
            "<code>import urllib\nresult = 1</code>",
            "<code>import ctypes\nresult = 1</code>",
            "<code>import os\nresult = 1</code>",  # never reached
        )
        with _patch_codeact(llm) as mocks:
            out = await codeact_node(_make_state())

        assert "aborted" in out["messages"][0].content.lower()
        # Sandbox never runs — every block was rejected by the validator.
        mocks["sandbox"].assert_not_awaited()
        assert mocks["llm"].ainvoke.await_count == 3


class TestCodeactNodeShellGate:
    async def test_action_interceptor_called_before_execution(self) -> None:
        """``ActionInterceptor.check_shell`` must run before the sandbox.

        We verify ordering by recording call timestamps on the mocks and
        asserting ``check_shell`` was called before ``_run_code_in_sandbox``.
        """
        call_log: list[str] = []

        llm = _make_llm_sequence(
            f'<code>result = 1\nprint("{_RESULT_MARKER}", result)</code>'
        )

        def _shell_side_effect(cmd: list[str]) -> bool:
            call_log.append("check_shell")
            return True

        async def _sandbox_side_effect(code: str) -> tuple[bool, str, str, int]:
            call_log.append("sandbox")
            return True, f"{_RESULT_MARKER} 1", "", 0

        provider_mock = MagicMock()
        provider_mock.return_value.get_llm_with_fallback.return_value = llm
        with (
            patch(
                "lightagent.agents.codeact_agent.ProviderRegistry",
                new=provider_mock,
            ),
            patch(
                "lightagent.agents.codeact_agent.ActionInterceptor.check_shell",
                side_effect=_shell_side_effect,
            ),
            patch(
                "lightagent.agents.codeact_agent._run_code_in_sandbox",
                new=AsyncMock(side_effect=_sandbox_side_effect),
            ),
        ):
            await codeact_node(_make_state())

        assert call_log == ["check_shell", "sandbox"]

    async def test_shell_disabled_returns_graceful_error(self) -> None:
        """When ``check_shell`` returns False the node never hits the sandbox."""
        llm = _make_llm_sequence("<code>result = 1</code>")
        with _patch_codeact(llm, shell_enabled=False) as mocks:
            out = await codeact_node(_make_state())

        assert "LIGHTAGENT_SHELL_ENABLED" in out["messages"][0].content
        mocks["sandbox"].assert_not_awaited()


# ---------------------------------------------------------------------------
# Feature flag
# ---------------------------------------------------------------------------


class TestCodeactDisabled:
    async def test_codeact_disabled_returns_message_without_llm_call(
        self,
    ) -> None:
        """When ``codeact_enabled=False`` the node short-circuits."""
        llm = MagicMock()
        llm.ainvoke = AsyncMock()
        with (
            patch(
                "lightagent.agents.codeact_agent.get_settings"
            ) as mock_settings,
            patch(
                "lightagent.agents.codeact_agent.ProviderRegistry"
            ) as pr,
        ):
            mock_settings.return_value.codeact_enabled = False
            pr.return_value.get_llm_with_fallback.return_value = llm
            out = await codeact_node(_make_state())

        assert "CodeAct is disabled" in out["messages"][0].content
        llm.ainvoke.assert_not_called()

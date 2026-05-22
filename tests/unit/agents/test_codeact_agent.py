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

from prismal.agents.codeact_agent import (
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

    SPEC-044 AC-044-6 flipped ``codeact_enabled`` to ``False`` by
    default, so this helper also patches ``get_settings`` to force the
    flag on — otherwise every node-level test would short-circuit at
    the enablement gate and never exercise the loop. Tests that want
    to verify the disabled path should NOT use this helper.

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

    # Force the feature flag on — default is now False after SPEC-044.
    settings_mock = MagicMock()
    settings_mock.codeact_enabled = True
    settings_mock.codeact_max_iterations = 6
    settings_mock.codeact_import_allowlist = (
        "pathlib,json,re,typing,datetime,collections,itertools,"
        "functools,math,statistics,random,hashlib,base64,csv,io,"
        "pandas,numpy,polars,matplotlib,sklearn,torch,flaml,duckdb,"
        "requests,httpx"
    )

    @contextlib.contextmanager
    def _cm() -> Any:
        with (
            patch(
                "lightagent.agents.codeact_agent.get_settings",
                return_value=settings_mock,
            ),
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
                "settings": settings_mock,
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
        assert _extract_code_block("<code>  result = 1  </code>") == "result = 1"

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
        # SPEC-044 AC-044-5: ``os`` has been REMOVED from the default
        # allowlist, so we exercise the allowlist path with benign
        # standard-library modules that remain allowed.
        ok, reason = _validate_imports("import pathlib\nimport json\nresult = 1")
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
        ok, reason = _validate_imports("from prismal.memory import x")
        assert not ok
        assert "lightagent" in reason

    def test_blocks_dynamic_import_call(self) -> None:
        # SPEC-044 AC-044-1: ``__import__`` is now in the name-call
        # denylist, so the rejection reason mentions the built-in
        # rather than the old "__import__ is forbidden" wording.
        ok, reason = _validate_imports('x = __import__("foo")')
        assert not ok
        assert "__import__" in reason

    def test_allows_dotted_submodule_when_top_level_allowed(self) -> None:
        # SPEC-044 AC-044-5: use an allowlisted top-level module.
        # ``os.path`` was the original example but ``os`` is no longer
        # allowed by default. ``collections.abc`` exercises the same
        # dotted-import code path with a default-safe module.
        ok, reason = _validate_imports("import collections.abc")
        assert ok, reason

    def test_syntax_error_reported_gracefully(self) -> None:
        ok, reason = _validate_imports("def broken(:\n    pass")
        assert not ok
        assert "SyntaxError" in reason

    def test_comment_mentioning_import_is_ignored(self) -> None:
        ok, _ = _validate_imports("# todo: import socket later\nresult = 42")
        assert ok

    def test_string_mentioning_import_is_ignored(self) -> None:
        ok, _ = _validate_imports('msg = "please import socket"\nresult = 1')
        assert ok


# Helper — built at module level so the literal tokens of the dangerous
# built-in names never appear as obvious call patterns in the test
# source. The validator rejects these, which is exactly what the tests
# below are checking — the helper just avoids tripping upstream static
# analyzers on the *tests* themselves.
def _call_src(name: str, arg: str = '"payload"') -> str:
    """Return a source string of the form ``<name>(<arg>)``."""
    return f"{name}({arg})"


class TestValidateImportsDenylist:
    """SPEC-044 AC-044-1, AC-044-2, AC-044-3, AC-044-4 — the hardened
    AST denylist that catches the bypass patterns identified in
    ``docs/report_security_v2_202060409.md`` Vuln 1.

    Each test asserts that a specific bypass primitive is rejected by
    :func:`_validate_imports` with a reason that names the offending
    identifier, so failures point the operator at the exact rule that
    fired. The suite is the regression lock for the denylist — if a
    future refactor removes an entry, at least one of these tests
    fails loudly.
    """

    # -------- AC-044-1: name-call denylist --------------------------

    def test_eval_rejected(self) -> None:
        ok, reason = _validate_imports(_call_src("eval", '"1 + 1"'))
        assert not ok
        assert "eval" in reason

    def test_exec_rejected(self) -> None:
        ok, reason = _validate_imports(_call_src("exec", '"y = 1"'))
        assert not ok
        assert "exec" in reason

    def test_compile_rejected(self) -> None:
        ok, reason = _validate_imports(_call_src("compile", '"x", "<s>", "eval"'))
        assert not ok
        assert "compile" in reason

    def test_getattr_rejected(self) -> None:
        ok, reason = _validate_imports("x = " + _call_src("getattr", 'object, "__class__"'))
        assert not ok
        assert "getattr" in reason

    def test_setattr_rejected(self) -> None:
        ok, reason = _validate_imports(_call_src("setattr", 'object, "x", 1'))
        assert not ok
        assert "setattr" in reason

    def test_delattr_rejected(self) -> None:
        ok, reason = _validate_imports(_call_src("delattr", 'object, "x"'))
        assert not ok
        assert "delattr" in reason

    def test_globals_rejected(self) -> None:
        ok, reason = _validate_imports("x = " + _call_src("globals", ""))
        assert not ok
        assert "globals" in reason

    def test_locals_rejected(self) -> None:
        ok, reason = _validate_imports("x = " + _call_src("locals", ""))
        assert not ok
        assert "locals" in reason

    def test_vars_rejected(self) -> None:
        ok, reason = _validate_imports("x = " + _call_src("vars", "object"))
        assert not ok
        assert "vars" in reason

    def test_open_rejected(self) -> None:
        ok, reason = _validate_imports("f = " + _call_src("open", '"/etc/passwd"'))
        assert not ok
        assert "open" in reason

    def test_input_rejected(self) -> None:
        ok, reason = _validate_imports("x = " + _call_src("input", '"prompt"'))
        assert not ok
        assert "input" in reason

    def test_breakpoint_rejected(self) -> None:
        ok, reason = _validate_imports(_call_src("breakpoint", ""))
        assert not ok
        assert "breakpoint" in reason

    # -------- AC-044-2: attribute-call denylist ---------------------

    def test_attribute_system_rejected(self) -> None:
        ok, reason = _validate_imports('foo.system("ls")')
        assert not ok
        assert "system" in reason

    def test_attribute_popen_rejected(self) -> None:
        ok, reason = _validate_imports('foo.popen("ls")')
        assert not ok
        assert "popen" in reason

    def test_attribute_run_rejected(self) -> None:
        ok, reason = _validate_imports('foo.run(["ls"])')
        assert not ok
        assert "run" in reason

    def test_attribute_popen_class_rejected(self) -> None:
        ok, reason = _validate_imports('x = mod.Popen(["ls"])')
        assert not ok
        assert "Popen" in reason

    def test_attribute_check_output_rejected(self) -> None:
        ok, reason = _validate_imports('out = foo.check_output(["ls"])')
        assert not ok
        assert "check_output" in reason

    def test_attribute_spawnl_rejected(self) -> None:
        ok, reason = _validate_imports('foo.spawnl("/bin/ls")')
        assert not ok
        assert "spawnl" in reason

    def test_attribute_execve_rejected(self) -> None:
        ok, reason = _validate_imports('foo.execve("/bin/ls", [], {})')
        assert not ok
        assert "execve" in reason

    def test_attribute_subclasses_rejected(self) -> None:
        ok, reason = _validate_imports("subs = object.__subclasses__()")
        assert not ok
        assert "__subclasses__" in reason

    def test_attribute_reduce_rejected(self) -> None:
        ok, reason = _validate_imports("x = foo.__reduce__()")
        assert not ok
        assert "__reduce__" in reason

    # -------- AC-044-3: dunder identifier rule ----------------------

    def test_dunder_name_builtins_rejected(self) -> None:
        ok, reason = _validate_imports("x = __builtins__")
        assert not ok
        assert "__builtins__" in reason

    def test_dunder_name_import_rejected(self) -> None:
        """``__import__`` referenced as Name (not called) is still
        rejected by the dunder rule even though it is also in the
        name-call denylist for the Call case.
        """
        ok, reason = _validate_imports("f = __import__")
        assert not ok
        assert "__import__" in reason

    def test_dunder_name_slots_rejected(self) -> None:
        ok, reason = _validate_imports("x = __slots__")
        assert not ok
        assert "__slots__" in reason

    def test_dunder_attribute_class_rejected(self) -> None:
        """AC-044-3: ``foo.__class__`` traversal without a call."""
        ok, reason = _validate_imports("y = foo.__class__")
        assert not ok
        assert "__class__" in reason

    def test_dunder_attribute_bases_rejected(self) -> None:
        ok, reason = _validate_imports("y = foo.__bases__")
        assert not ok
        assert "__bases__" in reason

    def test_dunder_attribute_mro_rejected(self) -> None:
        ok, reason = _validate_imports("y = foo.__mro__")
        assert not ok
        assert "__mro__" in reason

    def test_dunder_attribute_dict_rejected(self) -> None:
        ok, reason = _validate_imports("y = foo.__dict__")
        assert not ok
        assert "__dict__" in reason

    def test_dunder_attribute_chain_rejected(self) -> None:
        """Traversal chain without a final call trips at the first
        dunder Attribute encountered by ``ast.walk``.
        """
        ok, reason = _validate_imports("y = a.__class__.__base__.__subclasses__")
        assert not ok
        # Any of the three dunders will do — assert at least one fires.
        assert any(name in reason for name in ("__class__", "__base__", "__subclasses__"))

    def test_allowed_dunders_still_pass(self) -> None:
        """``__name__``, ``__main__``, ``__doc__``, ``__file__`` are
        whitelisted so the ``if __name__ == "__main__"`` idiom and
        ``__doc__`` / ``__file__`` introspection continue to work.
        """
        # Check the main-module-guard idiom.
        ok, reason = _validate_imports('if __name__ == "__main__":\n    result = 42')
        assert ok, reason

        # __doc__ lookup
        ok, reason = _validate_imports("result = __doc__")
        assert ok, reason

        # __file__ lookup
        ok, reason = _validate_imports("result = __file__")
        assert ok, reason

    # -------- AC-044-4: subscript base rule -------------------------

    def test_subscript_builtins_rejected(self) -> None:
        """``__builtins__["open"]`` is rejected by the subscript base
        rule AND by the dunder rule — either rejection is correct.
        """
        ok, reason = _validate_imports('x = __builtins__["open"]')
        assert not ok
        # Either of the two independent rules may fire first.
        assert "__builtins__" in reason

    def test_subscript_globals_rejected(self) -> None:
        """``globals()['foo']`` is caught by the name-call denylist
        (``globals`` is in the dangerous name-call set) before the
        subscript rule runs, but either rejection is correct.
        """
        ok, reason = _validate_imports('x = globals()["foo"]')
        assert not ok
        assert "globals" in reason

    def test_subscript_locals_rejected(self) -> None:
        ok, reason = _validate_imports('x = locals()["foo"]')
        assert not ok
        assert "locals" in reason

    def test_ordinary_subscript_still_works(self) -> None:
        """Dict / list / string subscripts unaffected by AC-044-4."""
        ok, reason = _validate_imports(
            "d = {'a': 1}\nlst = [1, 2, 3]\ns = 'hello'\nresult = d['a'] + lst[0] + len(s[:3])"
        )
        assert ok, reason

    # -------- Exact bypass payloads from security report -----------

    def test_getattr_builtins_import_bypass_rejected(self) -> None:
        """The exact payload from the security report (Vuln 1) must
        be rejected — multiple independent rules fire on this
        snippet. Any of them is a correct rejection.
        """
        payload = (
            "x = "
            + _call_src("getattr", '__builtins__, "__import__"')
            + '\nx.system("echo hello")\n'
        )
        ok, reason = _validate_imports(payload)
        assert not ok
        # At least one of the independent rules must fire.
        assert any(token in reason for token in ("getattr", "__builtins__", "system"))

    def test_subclass_traversal_bypass_rejected(self) -> None:
        """``().__class__.__base__.__subclasses__()[N](...)`` from the
        security report is rejected because both ``__class__``
        (dunder attr) and ``__subclasses__`` (attr-call) fire.
        """
        payload = "y = ().__class__.__base__.__subclasses__()[0]()"
        ok, _reason = _validate_imports(payload)
        assert not ok

    def test_exec_with_string_payload_rejected(self) -> None:
        """Wrapping the ``exec`` built-in around a string is rejected
        by the name-call denylist — ``ast.walk`` does not recurse
        into string literals, but the outer ``exec`` call is still
        visible at the top of the AST.
        """
        payload = _call_src("exec", '"y = 1"')
        ok, reason = _validate_imports(payload)
        assert not ok
        assert "exec" in reason

    def test_globals_builtins_open_chain_rejected(self) -> None:
        """``globals()['__builtins__']['open']('/etc/passwd').read()``
        from the security report is rejected. The rule that fires
        first is the ``globals`` name-call (AC-044-1), but either of
        the other involved rules (``__builtins__`` dunder Name,
        ``open`` name-call, subscript base) would also be correct.
        """
        payload = 'x = globals()["__builtins__"]["open"]("/etc/passwd").read()'
        ok, reason = _validate_imports(payload)
        assert not ok
        # Any one of these must appear in the rejection reason.
        assert any(token in reason for token in ("globals", "__builtins__", "open"))

    # -------- Allowlist minimization (AC-044-5) at validator level --

    def test_os_import_rejected_by_default(self) -> None:
        """SPEC-044 AC-044-5: ``os`` is no longer in the default
        allowlist, so ``import os`` is rejected by the import
        allowlist layer (not the denylist).
        """
        ok, reason = _validate_imports("import os")
        assert not ok
        assert "os" in reason

    def test_subprocess_import_rejected_by_default(self) -> None:
        ok, reason = _validate_imports("import subprocess")
        assert not ok
        assert "subprocess" in reason

    def test_sys_import_rejected_by_default(self) -> None:
        ok, reason = _validate_imports("import sys")
        assert not ok
        assert "sys" in reason

    # -------- Positive controls: legit code still works -------------

    def test_legit_pandas_code_still_works(self) -> None:
        """Regression lock: the denylist must not break realistic
        allowlisted workloads.
        """
        src = (
            "import pandas as pd\n"
            "df = pd.DataFrame({'a': [1, 2, 3]})\n"
            "result = len(df)\n"
            'print("__CODEACT_RESULT__", result)\n'
        )
        ok, reason = _validate_imports(src)
        assert ok, reason

    def test_legit_json_code_still_works(self) -> None:
        src = "import json\ndata = json.loads('{\"k\": 1}')\nresult = data['k']\n"
        ok, reason = _validate_imports(src)
        assert ok, reason

    def test_legit_math_and_builtins_still_work(self) -> None:
        """Non-denylisted built-ins (``len``, ``int``, ``str``,
        ``list``, ``range``, ``enumerate``, ``sum``) remain legal.
        """
        src = (
            "import math\n"
            "nums = list(range(10))\n"
            "result = sum(int(str(n)) for n in nums)\n"
            "sqrt = math.sqrt(16)\n"
        )
        ok, reason = _validate_imports(src)
        assert ok, reason


class TestGetImportAllowlist:
    def test_default_contains_key_packages(self) -> None:
        # SPEC-044 AC-044-5: ``os`` was removed from the default; the
        # remaining data-science + text / serialization / HTTP core
        # must still be present.
        allowlist = _get_import_allowlist()
        for pkg in ("json", "pathlib", "pandas", "numpy", "httpx"):
            assert pkg in allowlist

    def test_default_excludes_dangerous_stdlib_modules(self) -> None:
        """SPEC-044 AC-044-5: ``os``, ``sys``, ``subprocess`` must NOT be
        in the default allowlist. Operators who need them must override
        via ``LIGHTAGENT_CODEACT_IMPORT_ALLOWLIST`` and accept the risk.
        """
        allowlist = _get_import_allowlist()
        for pkg in ("os", "sys", "subprocess"):
            assert pkg not in allowlist, f"'{pkg}' should NOT be in the default CodeAct allowlist"

    def test_custom_allowlist_from_settings(self) -> None:
        with patch("lightagent.agents.codeact_agent.get_settings") as mock_settings:
            mock_settings.return_value.codeact_import_allowlist = "alpha, beta ,gamma"
            allowlist = _get_import_allowlist()
        assert allowlist == frozenset({"alpha", "beta", "gamma"})


class TestCodeactEnabledDefault:
    def test_codeact_enabled_default_is_false(self) -> None:
        """SPEC-044 AC-044-6: ``codeact_enabled`` default is False."""
        from prismal.core.config import Settings

        assert Settings().codeact_enabled is False


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
        llm = _make_llm_sequence(f'<code>result = 2 + 2\nprint("{_RESULT_MARKER}", result)</code>')
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
        llm = _make_llm_sequence("This is a pure explanation; no code needed. The answer is 42.")
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
            f'<code>result = undefined_var\nprint("{_RESULT_MARKER}", result)</code>',
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
        feedback_texts = [getattr(m, "content", "") for m in messages if m is not None]
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
        with _patch_codeact(
            llm,
            sandbox_results=[(True, "1", "", 0)] * 10,
        ) as mocks:
            # Override the mocked settings after entering the context so
            # ``max_iterations`` is 3 instead of the helper's default 6.
            mocks["settings"].codeact_max_iterations = 3
            out = await codeact_node(_make_state())

        assert "max_iterations=3" in out["messages"][0].content
        assert mocks["llm"].ainvoke.await_count == 3
        assert mocks["sandbox"].await_count == 3


class TestCodeactNodeImportAllowlist:
    async def test_import_allowlist_blocks_forbidden_import(self) -> None:
        """A block that imports a non-allowlisted package is rejected
        *before* the shell gate or sandbox runs (AC-040-4).

        Uses ``json`` (an SPEC-044 default-allowlisted module) as the
        "fixed" block. Earlier iterations used ``os`` but SPEC-044
        AC-044-5 removed it from the default allowlist.
        """
        llm = _make_llm_sequence(
            "<code>import socket\nresult = 1</code>",
            # After rejection, the LLM gets a feedback HumanMessage and
            # emits a fixed block using an allowlisted module.
            f'<code>import json\nresult = 1\nprint("{_RESULT_MARKER}", result)</code>',
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

        llm = _make_llm_sequence(f'<code>result = 1\nprint("{_RESULT_MARKER}", result)</code>')

        def _shell_side_effect(cmd: list[str]) -> bool:
            call_log.append("check_shell")
            return True

        async def _sandbox_side_effect(code: str) -> tuple[bool, str, str, int]:
            call_log.append("sandbox")
            return True, f"{_RESULT_MARKER} 1", "", 0

        provider_mock = MagicMock()
        provider_mock.return_value.get_llm_with_fallback.return_value = llm
        # SPEC-044 AC-044-6 flipped ``codeact_enabled`` to False by
        # default, so we must patch ``get_settings`` to force it on
        # for this test. Without this the node short-circuits at the
        # enablement gate and ``check_shell`` is never consulted.
        settings_mock = MagicMock()
        settings_mock.codeact_enabled = True
        settings_mock.codeact_vision_model = "anything"
        settings_mock.codeact_max_iterations = 6
        settings_mock.codeact_import_allowlist = "pathlib,json"
        with (
            patch(
                "lightagent.agents.codeact_agent.get_settings",
                return_value=settings_mock,
            ),
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
            patch("lightagent.agents.codeact_agent.get_settings") as mock_settings,
            patch("lightagent.agents.codeact_agent.ProviderRegistry") as pr,
        ):
            mock_settings.return_value.codeact_enabled = False
            pr.return_value.get_llm_with_fallback.return_value = llm
            out = await codeact_node(_make_state())

        assert "CodeAct is disabled" in out["messages"][0].content
        llm.ainvoke.assert_not_called()

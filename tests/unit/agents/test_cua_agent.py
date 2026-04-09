"""Tests for :mod:`lightagent.agents.cua_agent` — SPEC-043 AC-043-6.

The CUA loop has four moving parts (vision LLM, Playwright screenshot
adapter, risk classifier, HITL interrupt) and every one of them is
mocked here so the tests never touch a real browser or a real model.
Coverage includes:

* the action-type taxonomy and :func:`classify_risk` (including the
  fail-closed behaviour for unknown action types);
* :func:`_parse_action` JSON tolerance (plain, markdown fence, prose);
* the ``cua_enabled`` / ``cua_vision_model`` enablement gates;
* the SAFE / MODERATE / HIGH_RISK execution branches;
* HITL approve / reject flows via a mocked ``interrupt``;
* audit-entry emission per action (AC-043-5);
* the ``max_actions`` cap and graceful ``finish`` short-circuit;
* screenshot failure and action-executor failure paths.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from langchain_core.messages import HumanMessage

from lightagent.agents.cua_agent import (
    CUA_SYSTEM_PROMPT,
    HIGH_RISK_ACTIONS,
    MODERATE_ACTIONS,
    SAFE_ACTIONS,
    CuaAction,
    _parse_action,
    classify_risk,
    cua_node,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _state(message: str = "automate the UI") -> Any:
    """Minimal state dict the CUA node reads."""
    return {
        "messages": [HumanMessage(content=message)],
        "session_id": "u-1",
    }


def _settings_mock(
    *,
    enabled: bool = True,
    vision_model: str = "claude-opus-4-6",
    max_actions: int = 5,
    interval_ms: int = 0,
) -> MagicMock:
    """Build a MagicMock that stands in for ``get_settings()``."""
    settings = MagicMock()
    settings.cua_enabled = enabled
    settings.cua_vision_model = vision_model
    settings.cua_max_actions = max_actions
    settings.cua_screenshot_interval_ms = interval_ms
    return settings


def _vlm_mock(*contents: str) -> MagicMock:
    """Vision LLM mock whose ``ainvoke`` returns ``contents`` in order."""
    responses = [MagicMock(content=c) for c in contents]

    async def _ainvoke(*_args: Any, **_kwargs: Any) -> Any:
        if len(responses) == 1:
            return responses[0]
        return responses.pop(0) if len(responses) > 1 else responses[0]

    llm = MagicMock()
    llm.ainvoke = AsyncMock(side_effect=_ainvoke)
    return llm


def _patch_runtime(
    *,
    settings: MagicMock,
    vlm: MagicMock,
    screenshots: bytes | list[bytes] | None = None,
    execute_results: list[str] | None = None,
    execute_side_effect: Any = None,
    interrupt_return: Any = None,
) -> Any:
    """Context manager patching every external dependency of ``cua_node``.

    Args:
        settings: The mocked settings object returned by ``get_settings()``.
        vlm: The mocked vision LLM returned by ``get_vision_llm()``.
        screenshots: Either a single ``bytes`` (repeated forever) or a
            list to consume in order.
        execute_results: List of strings returned by
            ``execute_browser_action`` on successive calls.
        execute_side_effect: Optional override — if set, used as the
            mock's ``side_effect`` (takes precedence over
            ``execute_results``).
        interrupt_return: Value the mocked ``interrupt`` should return
            when called. When ``None``, ``interrupt`` is not patched
            and the default LangGraph behaviour would apply (no HITL
            expected in that test).
    """
    import contextlib

    if screenshots is None:
        screenshot_list: list[bytes] = [b"\x89PNG fake"]
    elif isinstance(screenshots, bytes):
        screenshot_list = [screenshots]
    else:
        screenshot_list = list(screenshots)

    async def _take_screenshot() -> bytes:
        return (
            screenshot_list.pop(0)
            if len(screenshot_list) > 1
            else screenshot_list[0]
        )

    if execute_side_effect is not None:
        execute_mock = AsyncMock(side_effect=execute_side_effect)
    else:
        results = list(execute_results or ["ok"])

        async def _execute(_action: CuaAction) -> str:
            if len(results) == 1:
                return results[0]
            return results.pop(0) if len(results) > 1 else results[0]

        execute_mock = AsyncMock(side_effect=_execute)

    provider_mock = MagicMock()
    provider_mock.return_value.get_vision_llm = MagicMock(return_value=vlm)

    @contextlib.contextmanager
    def _cm() -> Any:
        patches: list[Any] = [
            patch(
                "lightagent.agents.cua_agent.get_settings",
                return_value=settings,
            ),
            patch(
                "lightagent.agents.cua_agent.ProviderRegistry",
                new=provider_mock,
            ),
            patch(
                "lightagent.agents.cua_agent.take_screenshot",
                new=AsyncMock(side_effect=_take_screenshot),
            ),
            patch(
                "lightagent.agents.cua_agent.execute_browser_action",
                new=execute_mock,
            ),
            patch(
                "lightagent.agents.cua_agent.AuditLogger"
            ),
        ]
        if interrupt_return is not None:
            patches.append(
                patch(
                    "lightagent.agents.cua_agent.interrupt",
                    return_value=interrupt_return,
                )
            )
        with contextlib.ExitStack() as stack:
            entered = [stack.enter_context(p) for p in patches]
            # Return the important mocks by name for assertions.
            audit_mock = entered[4]
            interrupt_mock = entered[5] if interrupt_return is not None else None
            yield {
                "execute": execute_mock,
                "audit": audit_mock,
                "interrupt": interrupt_mock,
                "vlm": vlm,
                "provider": provider_mock,
            }

    return _cm()


def _action_json(
    action_type: str,
    *,
    target: str = "",
    value: str = "",
    rationale: str = "",
) -> str:
    """Return a JSON blob matching the VLM output contract."""
    import json

    return json.dumps(
        {
            "action_type": action_type,
            "target": target,
            "value": value,
            "rationale": rationale,
        }
    )


# ---------------------------------------------------------------------------
# Risk classification (AC-043-2)
# ---------------------------------------------------------------------------


class TestClassifyRisk:
    def test_safe_navigate(self) -> None:
        assert classify_risk(CuaAction(action_type="navigate")) == "SAFE"

    def test_safe_click_link(self) -> None:
        assert (
            classify_risk(CuaAction(action_type="click_link")) == "SAFE"
        )

    def test_safe_finish(self) -> None:
        """``finish`` is SAFE so the termination path doesn't trip HITL."""
        assert (
            classify_risk(CuaAction(action_type="finish")) == "SAFE"
        )

    def test_moderate_type(self) -> None:
        assert (
            classify_risk(CuaAction(action_type="type")) == "MODERATE"
        )

    def test_moderate_file_upload(self) -> None:
        assert (
            classify_risk(CuaAction(action_type="file_upload"))
            == "MODERATE"
        )

    def test_high_risk_click_submit(self) -> None:
        assert (
            classify_risk(CuaAction(action_type="click_submit"))
            == "HIGH_RISK"
        )

    def test_high_risk_click_pay(self) -> None:
        assert (
            classify_risk(CuaAction(action_type="click_pay"))
            == "HIGH_RISK"
        )

    def test_unknown_action_defaults_to_high_risk(self) -> None:
        """Fail-closed: unknown action_type → HIGH_RISK with warning."""
        assert (
            classify_risk(CuaAction(action_type="mystery_move"))
            == "HIGH_RISK"
        )

    def test_taxonomies_are_disjoint(self) -> None:
        """No action can appear in more than one risk bucket."""
        assert SAFE_ACTIONS.isdisjoint(HIGH_RISK_ACTIONS)
        assert SAFE_ACTIONS.isdisjoint(MODERATE_ACTIONS)
        assert MODERATE_ACTIONS.isdisjoint(HIGH_RISK_ACTIONS)

    def test_all_high_risk_actions_classified(self) -> None:
        """Every entry in HIGH_RISK_ACTIONS must classify as HIGH_RISK."""
        for action_type in HIGH_RISK_ACTIONS:
            assert (
                classify_risk(CuaAction(action_type=action_type))
                == "HIGH_RISK"
            )


# ---------------------------------------------------------------------------
# Action parsing
# ---------------------------------------------------------------------------


class TestParseAction:
    def test_plain_json(self) -> None:
        a = _parse_action(
            '{"action_type": "click_link", "target": ".btn"}'
        )
        assert a is not None
        assert a.action_type == "click_link"
        assert a.target == ".btn"

    def test_markdown_fence_with_json_tag(self) -> None:
        raw = '```json\n{"action_type": "scroll"}\n```'
        a = _parse_action(raw)
        assert a is not None
        assert a.action_type == "scroll"

    def test_plain_markdown_fence(self) -> None:
        raw = '```\n{"action_type": "read"}\n```'
        a = _parse_action(raw)
        assert a is not None
        assert a.action_type == "read"

    def test_with_surrounding_prose(self) -> None:
        raw = (
            'Here is my next action: {"action_type": "navigate", '
            '"target": "https://x.com"} - let me know if approved.'
        )
        a = _parse_action(raw)
        assert a is not None
        assert a.action_type == "navigate"
        assert a.target == "https://x.com"

    def test_invalid_json_returns_none(self) -> None:
        assert _parse_action("not json at all") is None

    def test_empty_returns_none(self) -> None:
        assert _parse_action("") is None

    def test_malformed_braces_returns_none(self) -> None:
        assert _parse_action("{broken json}") is None

    def test_missing_action_type_returns_none(self) -> None:
        """Pydantic validation failure → None, not exception."""
        assert _parse_action('{"target": ".btn"}') is None

    def test_non_dict_json_returns_none(self) -> None:
        assert _parse_action('["not", "a", "dict"]') is None


# ---------------------------------------------------------------------------
# Enablement gates (AC-043-3)
# ---------------------------------------------------------------------------


class TestCuaDisabled:
    async def test_cua_disabled_returns_disabled_message(self) -> None:
        with patch(
            "lightagent.agents.cua_agent.get_settings",
            return_value=_settings_mock(enabled=False),
        ):
            out = await cua_node(_state())
        assert out["current_agent"] == "cua"
        assert "disabled" in out["messages"][0].content.lower()

    async def test_no_vision_model_returns_graceful_error(self) -> None:
        with patch(
            "lightagent.agents.cua_agent.get_settings",
            return_value=_settings_mock(vision_model=""),
        ):
            out = await cua_node(_state())
        assert "vision" in out["messages"][0].content.lower()

    async def test_provider_without_vision_support_returns_error(
        self,
    ) -> None:
        """When the registry has no ``get_vision_llm`` attribute, we
        return a graceful error instead of raising AttributeError.
        """
        provider = MagicMock()
        # Remove the attribute so getattr() returns None.
        del provider.return_value.get_vision_llm
        with (
            patch(
                "lightagent.agents.cua_agent.get_settings",
                return_value=_settings_mock(),
            ),
            patch(
                "lightagent.agents.cua_agent.ProviderRegistry",
                new=provider,
            ),
        ):
            out = await cua_node(_state())
        assert "vision" in out["messages"][0].content.lower()

    async def test_vision_llm_init_failure_returns_graceful_error(
        self,
    ) -> None:
        provider = MagicMock()
        provider.return_value.get_vision_llm = MagicMock(
            side_effect=RuntimeError("model not found")
        )
        with (
            patch(
                "lightagent.agents.cua_agent.get_settings",
                return_value=_settings_mock(),
            ),
            patch(
                "lightagent.agents.cua_agent.ProviderRegistry",
                new=provider,
            ),
        ):
            out = await cua_node(_state())
        assert "model not found" in out["messages"][0].content


# ---------------------------------------------------------------------------
# SAFE action path (AC-043-1, AC-043-2, AC-043-5)
# ---------------------------------------------------------------------------


class TestSafeActionPath:
    async def test_safe_action_executes_without_interrupt(self) -> None:
        """A SAFE action runs without HITL and writes an audit entry."""
        settings = _settings_mock()
        # First: navigate (SAFE). Second: finish (terminate loop).
        vlm = _vlm_mock(
            _action_json("navigate", target="https://x.com"),
            _action_json("finish", rationale="task complete"),
        )
        with _patch_runtime(
            settings=settings,
            vlm=vlm,
            execute_results=["navigated"],
        ) as mocks:
            out = await cua_node(_state())

        # Execute was called exactly once (the navigate action).
        # The finish action does not hit execute_browser_action.
        assert mocks["execute"].await_count == 1
        # Audit called twice: navigate + finish.
        assert mocks["audit"].return_value.log_tool_call.call_count == 2
        assert out["current_agent"] == "cua"
        assert "task complete" in out["messages"][0].content

    async def test_finish_first_action_short_circuits(self) -> None:
        """``finish`` as the first emitted action ends the loop without
        calling ``execute_browser_action``.
        """
        settings = _settings_mock()
        vlm = _vlm_mock(_action_json("finish", rationale="nothing to do"))
        with _patch_runtime(
            settings=settings,
            vlm=vlm,
        ) as mocks:
            out = await cua_node(_state())

        mocks["execute"].assert_not_awaited()
        assert "nothing to do" in out["messages"][0].content

    async def test_audit_entry_contains_screenshot_hash(self) -> None:
        """AC-043-5: the screenshot hash must appear in audit params."""
        settings = _settings_mock()
        vlm = _vlm_mock(
            _action_json("scroll"),
            _action_json("finish"),
        )
        with _patch_runtime(
            settings=settings,
            vlm=vlm,
            screenshots=b"\x89PNG synthetic-bytes",
        ) as mocks:
            await cua_node(_state())

        calls = mocks["audit"].return_value.log_tool_call.call_args_list
        assert len(calls) >= 1
        first_params = calls[0].kwargs["params"]
        assert "screenshot_hash" in first_params
        # SHA-256 of "\\x89PNG synthetic-bytes" is a 64-char hex string.
        assert len(first_params["screenshot_hash"]) == 64
        assert first_params["action_type"] == "scroll"
        assert first_params["risk_level"] == "SAFE"


# ---------------------------------------------------------------------------
# HIGH_RISK action path (AC-043-2: HITL)
# ---------------------------------------------------------------------------


class TestHighRiskActionPath:
    async def test_high_risk_action_triggers_interrupt(self) -> None:
        """HIGH_RISK → ``interrupt()`` called → approved → execute."""
        settings = _settings_mock()
        vlm = _vlm_mock(
            _action_json("click_submit", target="#submit-btn"),
            _action_json("finish", rationale="submitted"),
        )
        with _patch_runtime(
            settings=settings,
            vlm=vlm,
            execute_results=["submitted"],
            interrupt_return={"action": "approve"},
        ) as mocks:
            out = await cua_node(_state())

        # Interrupt was invoked exactly once for the HIGH_RISK action.
        assert mocks["interrupt"].call_count == 1
        payload = mocks["interrupt"].call_args.args[0]
        assert payload["action_type"] == "click_submit"
        assert payload["risk_level"] == "HIGH_RISK"
        assert payload["target"] == "#submit-btn"
        assert "Approve" in payload["question"]
        # After approval, the action executes.
        assert mocks["execute"].await_count == 1
        # Audit records the approval decision.
        audit_calls = mocks["audit"].return_value.log_tool_call.call_args_list
        approve_call = audit_calls[0].kwargs["params"]
        assert approve_call["risk_level"] == "HIGH_RISK"
        assert approve_call["hitl_decision"] == "approve"
        assert "submitted" in out["messages"][0].content

    async def test_high_risk_rejected_stops_loop(self) -> None:
        """HIGH_RISK rejected → action NOT executed, loop stops."""
        settings = _settings_mock()
        vlm = _vlm_mock(
            _action_json("click_delete", target="#delete-btn"),
            _action_json("finish"),  # should never be reached
        )
        with _patch_runtime(
            settings=settings,
            vlm=vlm,
            execute_results=["wont happen"],
            interrupt_return={"action": "reject"},
        ) as mocks:
            out = await cua_node(_state())

        assert mocks["interrupt"].call_count == 1
        # Critical: execute must NOT have run for the rejected action.
        mocks["execute"].assert_not_awaited()
        # Audit records the rejection.
        audit_params = (
            mocks["audit"].return_value.log_tool_call.call_args_list[0]
            .kwargs["params"]
        )
        assert audit_params["hitl_decision"] == "reject"
        assert "denied" in out["messages"][0].content.lower()

    async def test_unknown_action_type_treated_as_high_risk(self) -> None:
        """Fail-closed: unknown ``action_type`` routes through HITL."""
        settings = _settings_mock()
        vlm = _vlm_mock(
            _action_json("mystery_ritual"),
            _action_json("finish"),
        )
        with _patch_runtime(
            settings=settings,
            vlm=vlm,
            interrupt_return={"action": "reject"},
        ) as mocks:
            await cua_node(_state())

        # Unknown action → HIGH_RISK → interrupt() invoked.
        assert mocks["interrupt"].call_count == 1
        mocks["execute"].assert_not_awaited()

    async def test_hitl_decision_non_dict_treated_as_reject(self) -> None:
        """Malformed HITL payload defaults to rejection (fail-closed)."""
        settings = _settings_mock()
        vlm = _vlm_mock(
            _action_json("click_pay", target="#pay"),
            _action_json("finish"),
        )
        with _patch_runtime(
            settings=settings,
            vlm=vlm,
            interrupt_return="not a dict",
        ) as mocks:
            out = await cua_node(_state())

        mocks["execute"].assert_not_awaited()
        assert "denied" in out["messages"][0].content.lower()


# ---------------------------------------------------------------------------
# Loop control
# ---------------------------------------------------------------------------


class TestMaxActions:
    async def test_max_actions_limit_respected(self) -> None:
        """Without ``finish`` the loop stops after ``cua_max_actions``."""
        settings = _settings_mock(max_actions=3)
        # Emit scroll forever — loop should stop at iteration 3.
        vlm = _vlm_mock(
            _action_json("scroll"),
            _action_json("scroll"),
            _action_json("scroll"),
            _action_json("scroll"),  # never reached
        )
        with _patch_runtime(
            settings=settings,
            vlm=vlm,
        ) as mocks:
            out = await cua_node(_state())

        # Exactly 3 executions, no more.
        assert mocks["execute"].await_count == 3
        assert "max_actions=3" in out["messages"][0].content

    async def test_unparseable_vlm_response_ends_loop(self) -> None:
        settings = _settings_mock()
        vlm = _vlm_mock("this is definitely not JSON at all")
        with _patch_runtime(
            settings=settings,
            vlm=vlm,
        ) as mocks:
            out = await cua_node(_state())

        mocks["execute"].assert_not_awaited()
        assert "unparseable" in out["messages"][0].content.lower()

    async def test_screenshot_failure_ends_loop_gracefully(self) -> None:
        settings = _settings_mock()
        vlm = _vlm_mock(_action_json("scroll"))
        provider_mock = MagicMock()
        provider_mock.return_value.get_vision_llm = MagicMock(
            return_value=vlm
        )
        with (
            patch(
                "lightagent.agents.cua_agent.get_settings",
                return_value=settings,
            ),
            patch(
                "lightagent.agents.cua_agent.ProviderRegistry",
                new=provider_mock,
            ),
            patch(
                "lightagent.agents.cua_agent.take_screenshot",
                new=AsyncMock(side_effect=RuntimeError("browser down")),
            ),
            patch(
                "lightagent.agents.cua_agent.execute_browser_action",
                new=AsyncMock(),
            ) as execute_mock,
            patch("lightagent.agents.cua_agent.AuditLogger"),
        ):
            out = await cua_node(_state())

        execute_mock.assert_not_awaited()
        assert "browser down" in out["messages"][0].content

    async def test_execute_failure_writes_audit_and_stops(self) -> None:
        """A raised exception during ``execute_browser_action`` must be
        caught, audited with ``exception:`` prefix, and end the loop.
        """
        settings = _settings_mock()
        vlm = _vlm_mock(_action_json("scroll"))
        with _patch_runtime(
            settings=settings,
            vlm=vlm,
            execute_side_effect=RuntimeError("click missed"),
        ) as mocks:
            out = await cua_node(_state())

        mocks["execute"].assert_awaited_once()
        audit_params = (
            mocks["audit"].return_value.log_tool_call.call_args_list[0]
            .kwargs["params"]
        )
        assert audit_params["action_type"] == "scroll"
        # The audit result_preview contains the exception message.
        result_preview = (
            mocks["audit"].return_value.log_tool_call.call_args_list[0]
            .kwargs["result"]
        )
        assert "exception" in result_preview
        assert "click missed" in result_preview
        assert "click missed" in out["messages"][0].content


# ---------------------------------------------------------------------------
# System prompt contract
# ---------------------------------------------------------------------------


class TestSystemPromptContract:
    def test_prompt_enumerates_all_taxonomies(self) -> None:
        """Any action_type used in production must be documented in
        the VLM prompt so the model knows which strings to emit.
        """
        for safe in SAFE_ACTIONS:
            assert safe in CUA_SYSTEM_PROMPT, safe
        for high in HIGH_RISK_ACTIONS:
            assert high in CUA_SYSTEM_PROMPT, high

    def test_prompt_mentions_hitl(self) -> None:
        """The prompt must warn the VLM about HITL for HIGH_RISK."""
        assert "HUMAN APPROVAL" in CUA_SYSTEM_PROMPT.upper()

    def test_prompt_requires_single_action_per_turn(self) -> None:
        assert "ONE" in CUA_SYSTEM_PROMPT.upper()

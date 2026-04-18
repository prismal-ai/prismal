"""Unit tests for react_loop in lightagent.agents.tool_registry.

Covers the normal path, iteration cap, per-tool failure budget, the
all-tools-failed early-exit (permanent errors such as Tavily 400), and the
rate-limit (429) handling that keeps the tool available across iterations.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from lightagent.agents.tool_registry import react_loop

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_tool(name: str, result: str = "ok", *, raises: Exception | None = None) -> MagicMock:
    """Return a mock BaseTool whose ainvoke returns *result* or raises *raises*."""
    tool = MagicMock()
    tool.name = name
    if raises is not None:
        tool.ainvoke = AsyncMock(side_effect=raises)
    else:
        tool.ainvoke = AsyncMock(return_value=result)
    return tool


def _ai_with_tool_calls(*tool_names: str) -> AIMessage:
    """Return an AIMessage that requests tool calls for *tool_names*."""
    calls = [
        {"name": name, "args": {"query": f"test {name}"}, "id": f"call_{i}"}
        for i, name in enumerate(tool_names)
    ]
    return AIMessage(content="", tool_calls=calls)


def _ai_final(content: str = "final answer") -> AIMessage:
    """Return an AIMessage with no tool calls (final answer)."""
    return AIMessage(content=content, tool_calls=[])


def _make_llm(*responses: AIMessage) -> MagicMock:
    """Return a mock LLM whose ainvoke yields *responses* in order."""
    llm = MagicMock()
    llm.ainvoke = AsyncMock(side_effect=list(responses))
    return llm


# ---------------------------------------------------------------------------
# Normal path — final answer on first LLM call
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_tool_calls_returns_immediately() -> None:
    """LLM returns final answer on first call → no tools are invoked."""
    llm = _make_llm(_ai_final("hello"))
    tool = _make_tool("search", "result")

    messages = [HumanMessage(content="hi")]
    response = await react_loop(llm, [tool], messages, agent_name="test")

    assert response.content == "hello"
    tool.ainvoke.assert_not_awaited()


# ---------------------------------------------------------------------------
# Normal path — one tool call then final answer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_one_tool_call_then_final() -> None:
    """Single tool call followed by a final answer — normal happy path."""
    tool = _make_tool("search", "some results")
    llm = _make_llm(
        _ai_with_tool_calls("search"),
        _ai_final("based on results: X"),
    )

    messages = [HumanMessage(content="query")]
    response = await react_loop(llm, [tool], messages, agent_name="test")

    assert response.content == "based on results: X"
    tool.ainvoke.assert_awaited_once()


# ---------------------------------------------------------------------------
# All-tools-failed early exit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_all_tools_fail_triggers_early_exit() -> None:
    """If every tool in an iteration fails, the loop exits early and asks LLM to synthesise."""
    failing_tool = _make_tool("tavily_search", raises=RuntimeError("400 Bad Request"))
    synthesis = _ai_final("I could not retrieve results, but here is what I know…")

    # Iteration 0: LLM requests tool → fails
    # Early-exit step: LLM asked to synthesise → returns final answer
    llm = _make_llm(
        _ai_with_tool_calls("tavily_search", "tavily_search", "tavily_search"),
        synthesis,
    )

    messages = [HumanMessage(content="latest news")]
    response = await react_loop(llm, [failing_tool], messages, agent_name="test")

    assert "could not retrieve" in response.content.lower() or response.content
    # LLM was called twice: once for the tool request, once for the synthesis prompt
    assert llm.ainvoke.await_count == 2

    # The synthesis prompt injected by react_loop must contain guidance text
    synth_call_args: list[Any] = llm.ainvoke.call_args_list[1][0][0]
    last_human = next((m for m in reversed(synth_call_args) if isinstance(m, HumanMessage)), None)
    assert last_human is not None
    assert "unavailable" in last_human.content.lower() or "failed" in last_human.content.lower()


@pytest.mark.asyncio
async def test_all_tools_fail_six_parallel_calls() -> None:
    """Regression: 6 parallel Tavily 400 errors → single synthesis, no further iterations.

    Within a single iteration the failure budget is consumed after
    ``_MAX_TOOL_FAILURES`` real calls; subsequent parallel requests to the
    same tool are skipped without calling the service.  The all-tools-failed
    exit then fires and asks the LLM to synthesise — only 2 LLM round-trips.
    """
    from lightagent.agents.tool_registry import _MAX_TOOL_FAILURES

    error = RuntimeError("Tavily API error: Request failed with status code 400")
    tavily = _make_tool("tavily_search", raises=error)
    synthesis = _ai_final("I was unable to search the web right now.")

    # Simulate what the screenshot showed: 6 parallel tavily_search calls in iter 0
    llm = _make_llm(
        _ai_with_tool_calls(*["tavily_search"] * 6),
        synthesis,
    )

    response = await react_loop(
        llm, [tavily], [HumanMessage(content="news")], agent_name="researcher"
    )

    # Only 2 LLM calls (tool request + synthesis) — not 6+ iterations
    assert llm.ainvoke.await_count == 2
    # The tool is called at most _MAX_TOOL_FAILURES times; remaining parallel
    # requests are skipped once the budget is exhausted within the same iteration.
    assert tavily.ainvoke.await_count == _MAX_TOOL_FAILURES


# ---------------------------------------------------------------------------
# Per-tool failure budget — skip after N failures
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_skipped_after_failure_budget_exhausted() -> None:
    """After _MAX_TOOL_FAILURES failures, the tool is skipped in subsequent iterations.

    Setup: two tools — one that always fails ('broken') and one that always
    succeeds ('good').  Because 'good' succeeds, the all-tools-failed early
    exit does NOT fire and we continue iterating.  This lets us observe the
    per-tool skip behaviour across iterations.
    """
    from lightagent.agents.tool_registry import _MAX_TOOL_FAILURES

    error = RuntimeError("service unavailable")
    bad_tool = _make_tool("broken", raises=error)
    good_tool = _make_tool("good", "good result")

    # Iterations 0 … _MAX_TOOL_FAILURES-1: both tools requested; 'broken' fails
    # each time until its budget is exhausted.
    # Iteration _MAX_TOOL_FAILURES: both requested; 'broken' is skipped.
    # Final call: LLM returns a final answer with no tool calls.
    num_mixed_iters = _MAX_TOOL_FAILURES + 1  # enough to exhaust + one skip iteration
    call_responses = [
        *[_ai_with_tool_calls("broken", "good")] * num_mixed_iters,
        _ai_final("giving up"),
    ]
    llm = _make_llm(*call_responses)

    response = await react_loop(
        llm,
        [bad_tool, good_tool],
        [HumanMessage(content="test")],
        agent_name="test",
        max_iterations=num_mixed_iters + 2,
    )

    # 'broken' is actually called exactly _MAX_TOOL_FAILURES times, then skipped.
    assert bad_tool.ainvoke.await_count == _MAX_TOOL_FAILURES
    # 'good' is called every iteration (it always succeeds).
    assert good_tool.ainvoke.await_count == num_mixed_iters
    assert response.content == "giving up"


# ---------------------------------------------------------------------------
# Mixed iteration — some tools succeed, some fail
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mixed_success_failure_continues_loop() -> None:
    """If at least one tool succeeds, the loop must NOT trigger early exit."""
    good_tool = _make_tool("search", "good results")
    bad_tool = _make_tool("broken", raises=RuntimeError("bad"))

    llm = _make_llm(
        _ai_with_tool_calls("search", "broken"),  # iter 0: one success, one fail
        _ai_final("answer from partial results"),
    )

    response = await react_loop(
        llm, [good_tool, bad_tool], [HumanMessage(content="test")], agent_name="test"
    )

    assert response.content == "answer from partial results"
    # Both tools attempted in iter 0
    good_tool.ainvoke.assert_awaited_once()
    bad_tool.ainvoke.assert_awaited_once()
    # LLM called twice: iter 0 + final answer (no synthesis injection)
    assert llm.ainvoke.await_count == 2


# ---------------------------------------------------------------------------
# Tool result truncation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_result_truncated_at_4000_chars() -> None:
    """Tool results longer than 4000 chars must be truncated with a suffix."""
    long_result = "x" * 5000
    tool = _make_tool("search", long_result)
    llm = _make_llm(
        _ai_with_tool_calls("search"),
        _ai_final("done"),
    )

    await react_loop(llm, [tool], [HumanMessage(content="test")], agent_name="test")

    # The ToolMessage sent back must be ≤ 4000 + len("…[truncated]")
    second_llm_call_messages: list[Any] = llm.ainvoke.call_args_list[1][0][0]
    tool_messages = [m for m in second_llm_call_messages if isinstance(m, ToolMessage)]
    assert len(tool_messages) == 1
    assert len(tool_messages[0].content) <= 4_020  # 4000 + suffix
    assert "truncated" in tool_messages[0].content


# ---------------------------------------------------------------------------
# Iteration cap
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_iteration_cap_triggers_synthesis() -> None:
    """Reaching max_iterations must inject a synthesis prompt and call LLM once more."""
    tool = _make_tool("search", "partial")
    final = _ai_final("forced synthesis answer")

    # Each LLM call requests the tool; after max_iterations, a synthesis is forced.
    max_iter = 2
    llm_responses = [_ai_with_tool_calls("search")] * max_iter + [final]
    llm = _make_llm(*llm_responses)

    response = await react_loop(
        llm,
        [tool],
        [HumanMessage(content="test")],
        agent_name="test",
        max_iterations=max_iter,
    )

    assert response.content == "forced synthesis answer"
    # LLM called max_iter times in the loop + 1 for the forced synthesis
    assert llm.ainvoke.await_count == max_iter + 1


# ---------------------------------------------------------------------------
# Rate-limit (429) — transient, must NOT burn the permanent failure budget
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rate_limit_does_not_burn_failure_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 429 rate-limit error must not count toward the permanent failure budget."""
    import lightagent.agents.tool_registry as reg

    monkeypatch.setattr(reg, "_RATE_LIMIT_BACKOFF", 0.0)  # no real sleep in tests

    rate_error = RuntimeError("Brave API error: 429 Too Many Requests")
    brave = _make_tool("brave_web_search", raises=rate_error)
    final = _ai_final("here is what I found")

    # Iter 0: 1 call → 429 (rate-limited)
    # Iter 1: 1 call → still callable (budget NOT burned) → success
    brave.ainvoke.side_effect = [rate_error, "brave results"]  # type: ignore[union-attr]

    llm = _make_llm(
        _ai_with_tool_calls("brave_web_search"),  # iter 0 → rate limited
        _ai_with_tool_calls("brave_web_search"),  # iter 1 → succeeds
        final,
    )

    response = await react_loop(
        llm, [brave], [HumanMessage(content="news")], agent_name="researcher"
    )

    # Tool was called in both iterations (budget not burned by 429)
    assert brave.ainvoke.await_count == 2
    assert response.content == "here is what I found"


@pytest.mark.asyncio
async def test_rate_limit_skips_subsequent_parallel_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: 3 parallel brave_web_search calls — first succeeds, 2nd triggers 429.

    The 3rd call must be skipped (same iteration, already rate-limited) without
    making an API call.  The tool must remain available in the next iteration.
    """
    import lightagent.agents.tool_registry as reg

    monkeypatch.setattr(reg, "_RATE_LIMIT_BACKOFF", 0.0)

    rate_error = RuntimeError("Brave API error: 429 Too Many Requests rate_limit exceeded")

    call_count = {"n": 0}
    actual_calls: list[str] = []

    async def brave_side_effect(*_args: object, **_kwargs: object) -> str:
        call_count["n"] += 1
        actual_calls.append(f"call-{call_count['n']}")
        if call_count["n"] == 1:
            return "news about topic A"
        raise rate_error

    brave = MagicMock()
    brave.name = "brave_web_search"
    brave.ainvoke = AsyncMock(side_effect=brave_side_effect)

    llm = _make_llm(
        _ai_with_tool_calls("brave_web_search", "brave_web_search", "brave_web_search"),
        _ai_final("partial answer from one search"),
    )

    response = await react_loop(
        llm, [brave], [HumanMessage(content="news")], agent_name="researcher"
    )

    # Only 2 actual API calls: call-1 (success) + call-2 (429 triggers rate-limit).
    # call-3 must be SKIPPED (already rate-limited in this iteration).
    assert len(actual_calls) == 2, f"Expected 2 actual calls, got {actual_calls}"
    assert response.content == "partial answer from one search"


@pytest.mark.asyncio
async def test_rate_limit_different_from_permanent_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 429 must NOT increment the permanent fail counter; a 400 MUST."""
    import lightagent.agents.tool_registry as reg

    monkeypatch.setattr(reg, "_RATE_LIMIT_BACKOFF", 0.0)

    rate_error = RuntimeError("429 rate limit exceeded")
    perm_error = RuntimeError("400 Bad Request: invalid API key")

    rate_tool = _make_tool("rate_tool", raises=rate_error)
    perm_tool = _make_tool("perm_tool", raises=perm_error)

    llm = _make_llm(
        _ai_with_tool_calls("rate_tool", "perm_tool"),
        _ai_final("done"),
    )

    await react_loop(llm, [rate_tool, perm_tool], [HumanMessage(content="test")], agent_name="test")

    # rate_tool: called once (rate-limit branch); perm_tool: called once (error branch)
    rate_tool.ainvoke.assert_awaited_once()
    perm_tool.ainvoke.assert_awaited_once()


# ---------------------------------------------------------------------------
# Tool-not-found
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unknown_tool_returns_error_message() -> None:
    """A tool call for an unknown name must produce a 'not found' ToolMessage."""
    llm = _make_llm(
        _ai_with_tool_calls("ghost_tool"),
        _ai_final("no tool available"),
    )

    response = await react_loop(llm, [], [HumanMessage(content="test")], agent_name="test")

    # The second LLM call must have received a ToolMessage containing "not found"
    second_call_msgs: list[Any] = llm.ainvoke.call_args_list[1][0][0]
    tm = next((m for m in second_call_msgs if isinstance(m, ToolMessage)), None)
    assert tm is not None
    assert "not found" in tm.content.lower()


# ---------------------------------------------------------------------------
# LLM rate-limit retry with exponential backoff (Phase 44)
# ---------------------------------------------------------------------------


class _FakeRateLimitError(Exception):
    """Stand-in for ``litellm.RateLimitError`` that matches ``_RATE_LIMIT_RE``."""

    def __init__(self, msg: str = "AnthropicException rate_limit_error 429") -> None:
        super().__init__(msg)


@pytest.mark.asyncio
async def test_extract_retry_after_from_attribute() -> None:
    """``_extract_retry_after`` reads ``exc.retry_after`` numeric attribute."""
    from lightagent.agents.tool_registry import _extract_retry_after

    class RetryAfterAttrError(Exception):
        retry_after = 4

    assert _extract_retry_after(RetryAfterAttrError()) == 4.0


@pytest.mark.asyncio
async def test_extract_retry_after_from_headers() -> None:
    """``_extract_retry_after`` parses ``exc.headers['Retry-After']``."""
    from lightagent.agents.tool_registry import _extract_retry_after

    class RetryAfterHeaderError(Exception):
        headers = {"Retry-After": "7"}

    assert _extract_retry_after(RetryAfterHeaderError()) == 7.0


@pytest.mark.asyncio
async def test_extract_retry_after_returns_none_when_absent() -> None:
    """No hint on the exception → ``None`` so caller falls back to backoff."""
    from lightagent.agents.tool_registry import _extract_retry_after

    assert _extract_retry_after(Exception("boom")) is None


@pytest.mark.asyncio
async def test_llm_rate_limit_succeeds_after_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LLM rate-limited once then succeeds → final response reaches the user."""
    import lightagent.agents.tool_registry as reg
    from lightagent.core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "llm_rate_limit_max_retries", 3)
    monkeypatch.setattr(settings, "llm_rate_limit_base_delay_seconds", 0.0)

    call_count = {"n": 0}

    async def flaky_ainvoke(_msgs: list[object]) -> AIMessage:
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise _FakeRateLimitError()
        return _ai_final("Here are the news")

    llm = MagicMock()
    llm.ainvoke = flaky_ainvoke

    response = await reg.react_loop(
        llm,
        [],
        [HumanMessage(content="news please")],
        agent_name="researcher",
    )

    assert call_count["n"] == 2
    assert response.content == "Here are the news"


@pytest.mark.asyncio
async def test_llm_rate_limit_exhausts_retries_returns_apology(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When every retry is rate-limited, the helper re-raises and react_loop
    returns the canonical apology string (existing behaviour preserved).
    """
    import lightagent.agents.tool_registry as reg
    from lightagent.core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "llm_rate_limit_max_retries", 2)
    monkeypatch.setattr(settings, "llm_rate_limit_base_delay_seconds", 0.0)

    call_count = {"n": 0}

    async def always_rate_limited(_msgs: list[object]) -> AIMessage:
        call_count["n"] += 1
        raise _FakeRateLimitError()

    llm = MagicMock()
    llm.ainvoke = always_rate_limited

    response = await reg.react_loop(
        llm,
        [],
        [HumanMessage(content="hi")],
        agent_name="researcher",
    )

    # 1 initial attempt + 2 retries = 3 calls before giving up.
    assert call_count["n"] == 3
    assert "rate-limited" in response.content.lower()


@pytest.mark.asyncio
async def test_llm_rate_limit_retry_disabled_when_max_retries_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``llm_rate_limit_max_retries=0`` reverts to fail-fast legacy behaviour."""
    import lightagent.agents.tool_registry as reg
    from lightagent.core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "llm_rate_limit_max_retries", 0)
    monkeypatch.setattr(settings, "llm_rate_limit_base_delay_seconds", 0.0)

    call_count = {"n": 0}

    async def always_rate_limited(_msgs: list[object]) -> AIMessage:
        call_count["n"] += 1
        raise _FakeRateLimitError()

    llm = MagicMock()
    llm.ainvoke = always_rate_limited

    response = await reg.react_loop(
        llm,
        [],
        [HumanMessage(content="hi")],
        agent_name="researcher",
    )

    assert call_count["n"] == 1
    assert "rate-limited" in response.content.lower()


@pytest.mark.asyncio
async def test_llm_rate_limit_retry_respects_retry_after_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the exception carries ``retry_after``, that hint drives the delay."""
    import lightagent.agents.tool_registry as reg
    from lightagent.core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "llm_rate_limit_max_retries", 2)
    monkeypatch.setattr(settings, "llm_rate_limit_base_delay_seconds", 99.0)

    sleeps: list[float] = []

    async def fake_sleep(d: float) -> None:
        sleeps.append(d)

    monkeypatch.setattr(reg.asyncio, "sleep", fake_sleep)

    class HintedRateLimitError(_FakeRateLimitError):
        retry_after = 2

    call_count = {"n": 0}

    async def flaky(_msgs: list[object]) -> AIMessage:
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise HintedRateLimitError
        return _ai_final("ok")

    llm = MagicMock()
    llm.ainvoke = flaky

    response = await reg.react_loop(
        llm,
        [],
        [HumanMessage(content="hi")],
        agent_name="researcher",
    )

    assert response.content == "ok"
    # Slept exactly once, using the hint (2s) and not the 99s base delay.
    assert sleeps == [2.0]


# ---------------------------------------------------------------------------
# Permanent LLM errors (billing, auth, invalid request) — no retry
# ---------------------------------------------------------------------------


class _FakeBillingError(Exception):
    """Stand-in for the ``credit_balance`` BadRequestError from Anthropic."""

    def __init__(self) -> None:
        super().__init__(
            'litellm.BadRequestError: AnthropicException - {"type":"error",'
            '"error":{"type":"invalid_request_error","message":"Your credit '
            'balance is too low to access the Anthropic API."}}'
        )


@pytest.mark.asyncio
async def test_permanent_error_skips_retry_and_returns_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Credit-balance / auth errors must NOT be retried; react_loop returns
    the dedicated 'service unavailable' message on the first attempt."""
    import lightagent.agents.tool_registry as reg
    from lightagent.core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "llm_rate_limit_max_retries", 3)
    monkeypatch.setattr(settings, "llm_rate_limit_base_delay_seconds", 0.0)

    call_count = {"n": 0}

    async def always_billing(_msgs: list[object]) -> AIMessage:
        call_count["n"] += 1
        raise _FakeBillingError()

    llm = MagicMock()
    llm.ainvoke = always_billing

    response = await reg.react_loop(
        llm,
        [],
        [HumanMessage(content="hi")],
        agent_name="researcher",
    )

    assert call_count["n"] == 1
    assert "unavailable" in response.content.lower()
    assert "billing" in response.content.lower() or "credit" in response.content.lower()


@pytest.mark.asyncio
async def test_permanent_error_in_retry_helper_raises_without_sleeping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`_invoke_llm_with_backoff` must re-raise immediately on permanent
    errors — never sleep, never retry."""
    import lightagent.agents.tool_registry as reg
    from lightagent.core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "llm_rate_limit_max_retries", 3)
    monkeypatch.setattr(settings, "llm_rate_limit_base_delay_seconds", 0.0)

    sleeps: list[float] = []

    async def fake_sleep(d: float) -> None:
        sleeps.append(d)

    monkeypatch.setattr(reg.asyncio, "sleep", fake_sleep)

    call_count = {"n": 0}

    async def always_billing(_msgs: list[object]) -> AIMessage:
        call_count["n"] += 1
        raise _FakeBillingError()

    llm = MagicMock()
    llm.ainvoke = always_billing

    with pytest.raises(_FakeBillingError):
        await reg._invoke_llm_with_backoff(
            llm,
            [HumanMessage(content="hi")],
            agent_name="t",
            session_id=None,
            iteration=0,
        )

    assert call_count["n"] == 1
    assert sleeps == []


# ---------------------------------------------------------------------------
# Synthetic function-call JSON unwrapping (Ollama / local models)
# ---------------------------------------------------------------------------


def test_unwrap_synthetic_respond() -> None:
    """The canonical 'respond' shape emitted by Ollama is unwrapped."""
    from lightagent.agents.tool_registry import _unwrap_synthetic_tool_call

    raw = '{"function": "respond", "arguments": {"response": "Hola. ¿En qué puedo ayudarte hoy?"}}'
    assert _unwrap_synthetic_tool_call(raw) == "Hola. ¿En qué puedo ayudarte hoy?"


def test_unwrap_synthetic_name_parameters_text() -> None:
    """Alternative shape with name/parameters/text is also supported."""
    from lightagent.agents.tool_registry import _unwrap_synthetic_tool_call

    raw = '{"name": "final_answer", "parameters": {"text": "42"}}'
    assert _unwrap_synthetic_tool_call(raw) == "42"


def test_unwrap_synthetic_leaves_real_tool_calls_alone() -> None:
    """A JSON blob invoking a real tool (not a final-answer wrapper) is
    ignored so the ReAct loop can still dispatch it normally."""
    from lightagent.agents.tool_registry import _unwrap_synthetic_tool_call

    raw = '{"function": "cron_add", "arguments": {"name": "x"}}'
    assert _unwrap_synthetic_tool_call(raw) is None


def test_unwrap_synthetic_ignores_plain_text() -> None:
    """Plain conversational text is returned as None (no unwrap)."""
    from lightagent.agents.tool_registry import _unwrap_synthetic_tool_call

    assert _unwrap_synthetic_tool_call("Hola, ¿cómo estás?") is None
    assert _unwrap_synthetic_tool_call("") is None


def test_unwrap_synthetic_handles_code_fence() -> None:
    """Ollama often wraps JSON in ```json ... ``` fences — also unwrap that."""
    from lightagent.agents.tool_registry import _unwrap_synthetic_tool_call

    raw = '```json\n{"function": "respond", "arguments": {"response": "hi"}}\n```'
    assert _unwrap_synthetic_tool_call(raw) == "hi"


@pytest.mark.asyncio
async def test_react_loop_unwraps_synthetic_json_final_answer() -> None:
    """When the LLM returns a synthetic JSON wrapper on its first call,
    ``react_loop`` must deliver the inner reply as clean text instead
    of the raw JSON."""
    llm = _make_llm(
        _ai_final(
            '{"function": "respond", "arguments": '
            '{"response": "Hola. ¿En qué puedo ayudarte hoy, Ernesto?"}}'
        )
    )

    response = await react_loop(
        llm,
        [],
        [HumanMessage(content="hola")],
        agent_name="supervisor",
    )

    assert response.content == "Hola. ¿En qué puedo ayudarte hoy, Ernesto?"

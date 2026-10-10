"""BatchExecutor (AMR5-04..06 — SPEC-AMR-BEX-001).

Fake adapters, a fake clock/sleep and a fixed RNG make every schedule
deterministic. No network, no LLM.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from typing import Any

import pytest

from prismal.budget.meter import CostMeter
from prismal.budget.types import Usage
from prismal.core.config import Settings
from prismal.core.exceptions import BatchExecutorError
from prismal.providers import cost as cost_mod
from prismal.providers.batch_adapters import BatchRequest, BatchResult, BatchStatus
from prismal.routing import batch as batch_mod
from prismal.routing.batch import BatchExecutor, backoff_delays

# ── fakes ─────────────────────────────────────────────────────────────────────


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class FixedRng:
    """``uniform`` always returns a fixed point inside [a, b]."""

    def __init__(self, at: float = 0.5) -> None:
        self.at = at

    def uniform(self, a: float, b: float) -> float:
        return a + (b - a) * self.at


class FakeAdapter:
    def __init__(
        self,
        family: str = "anthropic",
        *,
        statuses: Sequence[BatchStatus] = (BatchStatus.COMPLETED,),
        results: dict[str, BatchResult] | None = None,
        submit_error: Exception | None = None,
        cancel_error: Exception | None = None,
    ) -> None:
        self.family = family
        self._statuses = list(statuses)
        self._results = results
        self._submit_error = submit_error
        self._cancel_error = cancel_error
        self.submitted: list[list[BatchRequest]] = []
        self.polls = 0
        self.cancelled: list[str] = []

    async def submit(self, requests: Sequence[BatchRequest]) -> str:
        if self._submit_error is not None:
            raise self._submit_error
        self.submitted.append(list(requests))
        return f"job-{self.family}"

    async def poll(self, job_id: str) -> BatchStatus:
        self.polls += 1
        idx = min(self.polls - 1, len(self._statuses) - 1)
        return self._statuses[idx]

    async def results(self, job_id: str) -> Sequence[BatchResult]:
        if self._results is not None:
            return list(self._results.values())
        return [
            BatchResult(
                custom_id=r.custom_id,
                content=f"batch:{r.custom_id}",
                usage=Usage(prompt_tokens=1000, completion_tokens=500, calls=1),
            )
            for r in self.submitted[-1]
        ]

    async def cancel(self, job_id: str) -> None:
        self.cancelled.append(job_id)
        if self._cancel_error is not None:
            raise self._cancel_error


class SyncRecorder:
    def __init__(self, error: Exception | None = None) -> None:
        self.calls: list[str] = []
        self.error = error

    async def __call__(self, request: BatchRequest) -> BatchResult:
        self.calls.append(request.custom_id)
        if self.error is not None:
            raise self.error
        return BatchResult(
            custom_id=request.custom_id,
            content=f"sync:{request.custom_id}",
            usage=Usage(prompt_tokens=1000, completion_tokens=500, calls=1),
        )


def _req(cid: str, model: str = "anthropic/claude-x") -> BatchRequest:
    return BatchRequest(
        custom_id=cid, model_ref=model, messages=[{"role": "user", "content": cid}], max_tokens=8
    )


def _settings(**kw: Any) -> Settings:
    base: dict[str, Any] = {
        "batch_api_enabled": True,
        "batch_poll_initial_s": 10.0,
        "batch_poll_max_s": 300.0,
        "batch_price_ratio": 0.5,
    }
    base.update(kw)
    return Settings(**base)


@pytest.fixture(autouse=True)
def _price(monkeypatch: pytest.MonkeyPatch) -> None:
    # 1000 prompt + 500 completion ⇒ $0.003 list price.
    monkeypatch.setattr(cost_mod.litellm, "cost_per_token", lambda **kw: (0.001, 0.002))


@pytest.fixture
def jobs(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    seen: list[str] = []
    monkeypatch.setattr(batch_mod, "_count_job", seen.append)
    return seen


def _executor(
    adapters: dict[str, Any] | None = None,
    *,
    settings: Settings | None = None,
    meter: CostMeter | None = None,
    sync: SyncRecorder | None = None,
    clock: FakeClock | None = None,
    rng: Any = None,
) -> BatchExecutor:
    clk = clock or FakeClock()
    return BatchExecutor(
        settings=settings or _settings(),
        adapters=adapters or {},
        meter=meter,
        sync_fn=sync or SyncRecorder(),
        sleep=clk.sleep,
        clock=clk.clock,
        rng=rng or FixedRng(),
    )


# ── backoff ───────────────────────────────────────────────────────────────────


def test_backoff_schedule_doubles_and_caps_with_fixed_jitter() -> None:
    delays = backoff_delays(10.0, 300.0, FixedRng(0.5))
    assert [next(delays) for _ in range(8)] == [10, 20, 40, 80, 160, 300, 300, 300]


def test_backoff_jitter_is_within_twenty_percent() -> None:
    rng = random.Random(7)
    delays = backoff_delays(10.0, 300.0, rng)
    base = [10, 20, 40, 80, 160, 300, 300, 300, 300, 300]
    for expected in base:
        got = next(delays)
        assert expected * 0.8 <= got <= expected * 1.2


def test_backoff_extremes_of_jitter() -> None:
    assert next(backoff_delays(10.0, 300.0, FixedRng(0.0))) == pytest.approx(8.0)
    assert next(backoff_delays(10.0, 300.0, FixedRng(1.0))) == pytest.approx(12.0)


async def test_poll_schedule_verified_with_fake_clock(jobs: list[str]) -> None:
    adapter = FakeAdapter(
        statuses=[BatchStatus.RUNNING] * 6 + [BatchStatus.COMPLETED],
    )
    clk = FakeClock()
    out = await _executor({"anthropic": adapter}, clock=clk).run([_req("a")])
    assert clk.sleeps == [10, 20, 40, 80, 160, 300, 300]
    assert adapter.polls == 7
    assert out[0].content == "batch:a"
    assert jobs == ["submitted", "completed"]


# ── happy path + metering ─────────────────────────────────────────────────────


async def test_job_completes_and_is_metered_at_batch_price(jobs: list[str]) -> None:
    adapter = FakeAdapter(statuses=[BatchStatus.RUNNING, BatchStatus.COMPLETED])
    meter = CostMeter(settings=_settings())
    sync = SyncRecorder()
    out = await _executor({"anthropic": adapter}, meter=meter, sync=sync).run(
        [_req("a"), _req("b")]
    )
    assert [r.custom_id for r in out] == ["a", "b"]
    assert [r.content for r in out] == ["batch:a", "batch:b"]
    assert len(adapter.submitted) == 1 and len(adapter.submitted[0]) == 2
    assert sync.calls == []
    for r in out:
        assert r.error is None
        assert r.usage.cost_usd == pytest.approx(0.0015)  # 0.003 × 0.5
        assert r.usage.cost_source == "litellm"
    assert meter.usage.cost_usd == pytest.approx(0.003)
    assert meter.usage.calls == 2


async def test_one_job_per_family_and_order_preserved(jobs: list[str]) -> None:
    anth = FakeAdapter("anthropic")
    oai = FakeAdapter("openai")
    out = await _executor({"anthropic": anth, "openai": oai}).run(
        [_req("a1"), _req("o1", "openai/gpt-4o"), _req("a2", "claude-y")]
    )
    assert [r.custom_id for r in out] == ["a1", "o1", "a2"]
    assert [[r.custom_id for r in anth.submitted[0]]] == [["a1", "a2"]]
    assert [[r.custom_id for r in oai.submitted[0]]] == [["o1"]]
    assert jobs.count("submitted") == 2


async def test_empty_requests() -> None:
    assert await _executor().run([]) == []


async def test_duplicate_custom_ids_rejected() -> None:
    with pytest.raises(BatchExecutorError):
        await _executor().run([_req("a"), _req("a")])


# ── fallback ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("status", "label"), [(BatchStatus.FAILED, "failed"), (BatchStatus.EXPIRED, "expired")]
)
async def test_failed_or_expired_job_falls_back_to_sync(
    jobs: list[str], status: BatchStatus, label: str
) -> None:
    adapter = FakeAdapter(statuses=[status])
    meter = CostMeter(settings=_settings())
    sync = SyncRecorder()
    out = await _executor({"anthropic": adapter}, meter=meter, sync=sync).run([_req("a")])
    assert sync.calls == ["a"]
    assert out[0].content == "sync:a"
    assert out[0].usage.cost_usd == pytest.approx(0.003)  # full price
    assert meter.usage.cost_usd == pytest.approx(0.003)
    assert jobs == ["submitted", label, "fallback"]


async def test_submit_error_falls_back(jobs: list[str]) -> None:
    adapter = FakeAdapter(submit_error=BatchExecutorError("400"))
    sync = SyncRecorder()
    out = await _executor({"anthropic": adapter}, sync=sync).run([_req("a")])
    assert out[0].content == "sync:a"
    assert jobs == ["failed", "fallback"]


async def test_unexpected_adapter_exception_falls_back(jobs: list[str]) -> None:
    adapter = FakeAdapter(submit_error=RuntimeError("boom"))
    out = await _executor({"anthropic": adapter}).run([_req("a")])
    assert out[0].content == "sync:a"


async def test_no_adapter_or_unknown_family_goes_sync(jobs: list[str]) -> None:
    sync = SyncRecorder()
    out = await _executor({}, sync=sync).run([_req("a"), _req("g", "gemini/x")])
    assert sync.calls == ["a", "g"]
    assert [r.content for r in out] == ["sync:a", "sync:g"]
    assert "submitted" not in jobs


async def test_deadline_cancels_and_falls_back(jobs: list[str]) -> None:
    adapter = FakeAdapter(statuses=[BatchStatus.RUNNING])
    clk = FakeClock()
    sync = SyncRecorder()
    out = await _executor({"anthropic": adapter}, clock=clk, sync=sync).run(
        [_req("a")], deadline_s=35.0
    )
    assert adapter.cancelled == ["job-anthropic"]
    assert sync.calls == ["a"]
    assert out[0].content == "sync:a"
    assert clk.sleeps == [10, 20, 5]  # last sleep trimmed to the deadline
    assert jobs == ["submitted", "expired", "fallback"]


async def test_deadline_cancel_error_is_suppressed() -> None:
    adapter = FakeAdapter(statuses=[BatchStatus.RUNNING], cancel_error=RuntimeError("x"))
    out = await _executor({"anthropic": adapter}).run([_req("a")], deadline_s=5.0)
    assert out[0].content == "sync:a"


async def test_per_request_error_and_missing_result_fall_back() -> None:
    adapter = FakeAdapter(
        results={
            "a": BatchResult(custom_id="a", content=None, error="errored"),
            "zz": BatchResult(custom_id="zz", content="stray"),
        }
    )
    sync = SyncRecorder()
    out = await _executor({"anthropic": adapter}, sync=sync).run([_req("a"), _req("b")])
    assert sorted(sync.calls) == ["a", "b"]
    assert [r.content for r in out] == ["sync:a", "sync:b"]


async def test_sync_failure_becomes_error_result() -> None:
    sync = SyncRecorder(error=RuntimeError("provider down"))
    out = await _executor({}, sync=sync).run([_req("a")])
    assert out[0].content is None
    assert out[0].error is not None and "RuntimeError" in out[0].error


# ── fallback disabled ─────────────────────────────────────────────────────────


async def test_job_failure_without_fallback_raises() -> None:
    adapter = FakeAdapter(statuses=[BatchStatus.FAILED])
    sync = SyncRecorder()
    with pytest.raises(BatchExecutorError):
        await _executor({"anthropic": adapter}, sync=sync).run(
            [_req("a")], allow_sync_fallback=False
        )
    assert sync.calls == []


async def test_no_adapter_without_fallback_returns_error_results() -> None:
    sync = SyncRecorder()
    out = await _executor({}, sync=sync).run([_req("a")], allow_sync_fallback=False)
    assert out[0].error == "no_adapter"
    assert sync.calls == []


async def test_per_request_error_without_fallback_kept_as_error() -> None:
    adapter = FakeAdapter(results={"a": BatchResult(custom_id="a", content=None, error="expired")})
    out = await _executor({"anthropic": adapter}).run(
        [_req("a"), _req("b")], allow_sync_fallback=False
    )
    assert out[0].error == "expired"
    assert out[1].error == "missing_result"


# ── exclusions (AMR5-06) ──────────────────────────────────────────────────────


async def test_tainted_and_hitl_requests_never_submitted() -> None:
    adapter = FakeAdapter()
    sync = SyncRecorder()
    out = await _executor({"anthropic": adapter}, sync=sync).run(
        [_req("t"), _req("h"), _req("ok")],
        tainted_ids=frozenset({"t"}),
        hitl_gated_ids=frozenset({"h"}),
    )
    submitted = [r.custom_id for job in adapter.submitted for r in job]
    assert submitted == ["ok"]
    assert sorted(sync.calls) == ["h", "t"]
    assert [r.content for r in out] == ["sync:t", "sync:h", "batch:ok"]


async def test_tainted_without_fallback_is_error_and_never_submitted() -> None:
    adapter = FakeAdapter()
    out = await _executor({"anthropic": adapter}).run(
        [_req("t"), _req("h")],
        tainted_ids=frozenset({"t"}),
        hitl_gated_ids=frozenset({"h"}),
        allow_sync_fallback=False,
    )
    assert adapter.submitted == []
    assert [r.error for r in out] == ["tainted", "hitl_gated"]


async def test_data_policy_rejection_is_never_uploaded() -> None:
    adapter = FakeAdapter()

    def policy(req: BatchRequest) -> bool:
        if req.custom_id == "boom":
            raise ValueError("policy crashed")
        return req.custom_id != "pii"

    out = await _executor({"anthropic": adapter}).run(
        [_req("pii"), _req("boom"), _req("ok")],
        data_policy_fn=policy,
        allow_sync_fallback=False,
    )
    assert [r.custom_id for job in adapter.submitted for r in job] == ["ok"]
    assert [r.error for r in out] == ["data_policy", "data_policy", None]


async def test_batch_api_disabled_runs_everything_sync(jobs: list[str]) -> None:
    adapter = FakeAdapter()
    sync = SyncRecorder()
    out = await _executor(
        {"anthropic": adapter}, settings=_settings(batch_api_enabled=False), sync=sync
    ).run([_req("a")])
    assert adapter.submitted == []
    assert out[0].content == "sync:a"
    disabled = await _executor(
        {"anthropic": adapter}, settings=_settings(batch_api_enabled=False)
    ).run([_req("a")], allow_sync_fallback=False)
    assert disabled[0].error == "batch_disabled"


# ── defaults ──────────────────────────────────────────────────────────────────


async def test_default_sync_fn_uses_provider_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    from langchain_core.messages import AIMessage

    seen: dict[str, Any] = {}

    class _LLM:
        async def ainvoke(self, messages: Any) -> AIMessage:
            seen["messages"] = messages
            return AIMessage(
                content="from-llm",
                usage_metadata={"input_tokens": 1000, "output_tokens": 500, "total_tokens": 1500},
            )

    class _Registry:
        def __init__(self, settings: Any = None, **kw: Any) -> None:
            seen["settings"] = settings

        def get_llm(self, model: str | None = None, **kw: Any) -> _LLM:
            seen["model"] = model
            return _LLM()

    monkeypatch.setattr("prismal.providers.registry.ProviderRegistry", _Registry)
    settings = _settings()
    clk = FakeClock()
    executor = BatchExecutor(settings=settings, adapters={}, sleep=clk.sleep, clock=clk.clock)
    out = await executor.run([_req("a")])
    assert out[0].content == "from-llm"
    assert out[0].usage.prompt_tokens == 1000
    assert out[0].usage.cost_usd == pytest.approx(0.003)
    assert seen["model"] == "anthropic/claude-x"
    assert seen["settings"] is settings
    assert seen["messages"] == [{"role": "user", "content": "a"}]


async def test_default_sync_fn_non_string_content(monkeypatch: pytest.MonkeyPatch) -> None:
    from langchain_core.messages import AIMessage

    class _LLM:
        async def ainvoke(self, messages: Any) -> AIMessage:
            return AIMessage(content=[{"type": "text", "text": "x"}])

    class _Registry:
        def __init__(self, **kw: Any) -> None: ...

        def get_llm(self, model: str | None = None, **kw: Any) -> _LLM:
            return _LLM()

    monkeypatch.setattr("prismal.providers.registry.ProviderRegistry", _Registry)
    clk = FakeClock()
    out = await BatchExecutor(
        settings=_settings(), adapters={}, sleep=clk.sleep, clock=clk.clock
    ).run([_req("a")])
    assert out[0].content is not None and "x" in out[0].content


def test_default_adapters_built_from_settings() -> None:
    from pydantic import SecretStr

    ex = BatchExecutor(settings=_settings(anthropic_api_key=SecretStr("k")))
    assert "anthropic" in ex.adapters


def test_count_job_never_raises() -> None:
    batch_mod._count_job("submitted")


# ── review fixes: no double billing on results() failure, bounded sync gather ──


class FlakyResultsAdapter(FakeAdapter):
    """Job completes, but ``results()`` fails the first *failures* times."""

    def __init__(self, failures: int) -> None:
        super().__init__()
        self.failures = failures
        self.results_calls = 0

    async def results(self, job_id: str) -> Sequence[BatchResult]:
        self.results_calls += 1
        if self.results_calls <= self.failures:
            raise BatchExecutorError("results fetch failed")
        return await super().results(job_id)


async def test_results_failure_after_completion_is_retried(jobs: list[str]) -> None:
    adapter = FlakyResultsAdapter(failures=2)
    clk = FakeClock()
    sync = SyncRecorder()
    out = await _executor({"anthropic": adapter}, clock=clk, sync=sync).run([_req("a")])
    assert adapter.results_calls == 3
    assert sync.calls == []
    assert out[0].content == "batch:a"
    assert clk.sleeps == [10, 20, 40]  # poll, then the same backoff between retries
    assert jobs == ["submitted", "completed"]


@pytest.mark.parametrize("allow", [True, False])
async def test_results_unavailable_never_reruns_billed_requests(
    jobs: list[str], allow: bool
) -> None:
    adapter = FlakyResultsAdapter(failures=10**6)
    clk = FakeClock()
    sync = SyncRecorder()
    out = await _executor({"anthropic": adapter}, clock=clk, sync=sync).run(
        [_req("a"), _req("b")], deadline_s=100.0, allow_sync_fallback=allow
    )
    assert sync.calls == []  # already billed by the provider: no sync re-run
    assert [r.error for r in out] == ["results_unavailable:job-anthropic"] * 2
    assert all(r.content is None for r in out)
    assert clk.now <= 100.0 + 1e-9
    assert adapter.results_calls >= 2
    assert jobs == ["submitted", "failed"]


async def test_results_retry_without_deadline_is_bounded(jobs: list[str]) -> None:
    adapter = FlakyResultsAdapter(failures=10**6)
    out = await _executor({"anthropic": adapter}).run([_req("a")])
    assert out[0].error == "results_unavailable:job-anthropic"
    assert 1 < adapter.results_calls < 20


async def test_sync_fallback_concurrency_is_bounded_by_parallel_max_workers() -> None:
    import asyncio

    active = 0
    peak = 0

    async def slow_sync(request: BatchRequest) -> BatchResult:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return BatchResult(custom_id=request.custom_id, content="s")

    ex = BatchExecutor(
        settings=_settings(batch_api_enabled=False, parallel_max_workers=2),
        adapters={},
        sync_fn=slow_sync,
    )
    out = await ex.run([_req(str(i)) for i in range(7)])
    assert [r.content for r in out] == ["s"] * 7
    assert peak == 2

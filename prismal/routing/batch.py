"""BatchExecutor — provider batch APIs with sync fallback (AMR5-04..06, SPEC-AMR-BEX-001).

Groups requests by provider family, submits **one job per family**, polls with
exponential backoff (``batch_poll_initial_s`` doubling up to ``batch_poll_max_s``,
then ±20 % jitter), and meters every batch result at batch price
(``compute_cost_usd(..., batch=True)``, ADR-WAVE-001 §2.3).

Fallback rules (DD-AMR-008):

- No adapter for a family, a submit/poll/results error, a ``failed`` or
  ``expired`` job, or an exceeded ``deadline_s`` (the job is cancelled first)
  ⇒ the affected requests run synchronously via ``ProviderRegistry`` when
  ``allow_sync_fallback`` is True. A per-request error inside an otherwise
  completed job, or a missing result, falls back the same way.
- With ``allow_sync_fallback=False``: a **whole-family job failure** raises
  :class:`BatchExecutorError` (the only raise, per the SPEC); every other
  non-batchable request comes back as ``BatchResult(error=<reason>)``.
- Tainted or HITL-gated requests, and requests rejected (or crashed on) by
  ``data_policy_fn``, are **never submitted to a batch** — checked before any
  upload; they take the sync path (or an error result).
- ``batch_api_enabled=False`` ⇒ nothing is batched.
- A job that **completed** but whose ``results()`` fetch fails is never re-run
  synchronously (the provider already billed it): the fetch is retried with the
  same backoff until ``deadline_s`` (or ``_RESULTS_MAX_ATTEMPTS`` without a
  deadline); if it still fails every request gets
  ``BatchResult(error="results_unavailable:<job_id>")`` and the job counts as
  ``batch_jobs{status="failed"}``.
- Sync-fallback calls run with at most ``parallel_max_workers`` in flight.

Sync-fallback calls are metered at the normal (non-batch) price. Provider batch
APIs are reached only through :mod:`prismal.providers.batch_adapters`.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable, Iterator, Mapping, Sequence
from contextlib import suppress
from dataclasses import replace
from typing import TYPE_CHECKING, Any, Protocol

from prismal.budget.types import Usage
from prismal.core.exceptions import BatchExecutorError
from prismal.core.logging import get_logger
from prismal.providers.batch_adapters import (
    BatchApiPort,
    BatchRequest,
    BatchResult,
    BatchStatus,
    default_adapters,
    family_of,
)
from prismal.providers.cost import compute_cost_usd

if TYPE_CHECKING:
    from prismal.budget.meter import CostMeter
    from prismal.core.config import Settings

__all__ = [
    "BatchApiPort",
    "BatchExecutor",
    "BatchRequest",
    "BatchResult",
    "BatchStatus",
    "backoff_delays",
]

logger = get_logger(__name__)

_JITTER = 0.2
_RESULTS_MAX_ATTEMPTS = 5  # results() retries for a completed job with no deadline

SyncFn = Callable[[BatchRequest], Awaitable[BatchResult]]


class _Rng(Protocol):
    def uniform(self, a: float, b: float) -> float: ...


class _JobFailedError(Exception):
    """Internal: a family job failed (carries the batch_jobs status label)."""

    def __init__(self, status: str) -> None:
        super().__init__(status)
        self.status = status


class _ResultsUnavailableError(Exception):
    """Internal: a job completed (billed) but its results could not be fetched."""

    def __init__(self, job_id: str) -> None:
        super().__init__(job_id)
        self.job_id = job_id


def backoff_delays(initial_s: float, max_s: float, rng: _Rng) -> Iterator[float]:
    """Yield poll delays: ``initial_s · 2ⁿ`` capped at ``max_s``, each times (1 +/- 20 %)."""
    base = initial_s
    while True:
        yield min(base, max_s) * (1.0 + rng.uniform(-_JITTER, _JITTER))
        base = min(base * 2.0, max_s)


def _count_job(status: str) -> None:
    """Increment ``prismal.batch_jobs_total{status}``; never raises."""
    with suppress(Exception):
        from prismal.monitoring.otel import OTelManager

        OTelManager().increment_counter("batch_jobs", 1, attributes={"status": status})


def _pricing_model(model_ref: str) -> str:
    return model_ref.removeprefix("gw:")


class BatchExecutor:
    """Run chat requests through provider batch APIs, falling back to sync calls."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        adapters: Mapping[str, BatchApiPort] | None = None,
        meter: CostMeter | None = None,
        sync_fn: SyncFn | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
        clock: Callable[[], float] | None = None,
        rng: _Rng | None = None,
    ) -> None:
        if settings is None:
            from prismal.core.config import get_settings

            settings = get_settings()
        self._settings = settings
        self.adapters: dict[str, BatchApiPort] = (
            dict(adapters) if adapters is not None else default_adapters(settings)
        )
        self._meter = meter
        self._sync_fn: SyncFn = sync_fn or self._default_sync
        self._sleep = sleep or asyncio.sleep
        self._clock = clock or time.monotonic
        self._rng: _Rng = rng or random.Random()  # noqa: S311  # nosec B311 - jitter, not crypto

    # ── public ────────────────────────────────────────────────────────────────

    async def run(
        self,
        requests: Sequence[BatchRequest],
        *,
        deadline_s: float | None = None,
        allow_sync_fallback: bool = True,
        tainted_ids: frozenset[str] = frozenset(),
        hitl_gated_ids: frozenset[str] = frozenset(),
        data_policy_fn: Callable[[BatchRequest], bool] | None = None,
    ) -> Sequence[BatchResult]:
        """Execute *requests*; results come back in input order."""
        ids = [r.custom_id for r in requests]
        if len(set(ids)) != len(ids):
            raise BatchExecutorError("duplicate custom_id in batch requests")
        if not requests:
            return []

        results: dict[str, BatchResult] = {}
        to_sync: list[tuple[BatchRequest, str]] = []
        groups: dict[str, list[BatchRequest]] = {}
        enabled = bool(getattr(self._settings, "batch_api_enabled", False))

        for req in requests:
            reason = self._exclusion(req, enabled, tainted_ids, hitl_gated_ids, data_policy_fn)
            if reason is not None:
                to_sync.append((req, reason))
                continue
            family = family_of(req.model_ref)
            if family is None or family not in self.adapters:
                to_sync.append((req, "no_adapter"))
                continue
            groups.setdefault(family, []).append(req)

        outcomes = await asyncio.gather(
            *(self._run_family(fam, reqs, deadline_s) for fam, reqs in groups.items())
        )
        for (family, reqs), outcome in zip(groups.items(), outcomes, strict=True):
            if isinstance(outcome, _ResultsUnavailableError):
                # Already billed by the provider: never re-run synchronously.
                _count_job(BatchStatus.FAILED.value)
                error = f"results_unavailable:{outcome.job_id}"
                for req in reqs:
                    results[req.custom_id] = BatchResult(
                        custom_id=req.custom_id, content=None, error=error
                    )
                continue
            if isinstance(outcome, _JobFailedError):
                _count_job(outcome.status)
                if not allow_sync_fallback:
                    raise BatchExecutorError(
                        f"{family} batch job {outcome.status} and sync fallback is disabled"
                    )
                _count_job(BatchStatus.FALLBACK.value)
                to_sync.extend((r, outcome.status) for r in reqs)
                continue
            _count_job(BatchStatus.COMPLETED.value)
            for req in reqs:
                got = outcome.get(req.custom_id)
                if got is None or got.error is not None:
                    to_sync.append((req, got.error if got and got.error else "missing_result"))
                    continue
                results[req.custom_id] = self._meter_result(req, got, batch=True)

        if to_sync:
            if allow_sync_fallback:
                limit = asyncio.Semaphore(max(1, int(self._settings.parallel_max_workers)))

                async def bounded(req: BatchRequest) -> BatchResult:
                    async with limit:
                        return await self._run_sync(req)

                synced = await asyncio.gather(*(bounded(r) for r, _ in to_sync))
                for (req, _), res in zip(to_sync, synced, strict=True):
                    results[req.custom_id] = res
            else:
                for req, reason in to_sync:
                    results[req.custom_id] = BatchResult(
                        custom_id=req.custom_id, content=None, error=reason
                    )
        return [results[cid] for cid in ids]

    # ── internals ─────────────────────────────────────────────────────────────

    @staticmethod
    def _exclusion(
        req: BatchRequest,
        enabled: bool,
        tainted_ids: frozenset[str],
        hitl_gated_ids: frozenset[str],
        data_policy_fn: Callable[[BatchRequest], bool] | None,
    ) -> str | None:
        """Why *req* must not be batched (checked before any upload), or None."""
        if not enabled:
            return "batch_disabled"
        if req.custom_id in tainted_ids:
            return "tainted"
        if req.custom_id in hitl_gated_ids:
            return "hitl_gated"
        if data_policy_fn is not None:
            try:
                allowed = bool(data_policy_fn(req))
            except Exception:
                allowed = False  # fail closed: never upload on a policy crash
            if not allowed:
                return "data_policy"
        return None

    async def _run_family(
        self, family: str, reqs: list[BatchRequest], deadline_s: float | None
    ) -> dict[str, BatchResult] | _JobFailedError | _ResultsUnavailableError:
        adapter = self.adapters[family]
        try:
            job_id = await adapter.submit(reqs)
        except Exception as exc:
            logger.warning("batch_submit_failed", family=family, error=type(exc).__name__)
            return _JobFailedError(BatchStatus.FAILED.value)
        _count_job(BatchStatus.SUBMITTED.value)
        start = self._clock()
        delays = backoff_delays(
            float(self._settings.batch_poll_initial_s),
            float(self._settings.batch_poll_max_s),
            self._rng,
        )
        try:
            while True:
                delay = next(delays)
                if deadline_s is not None:
                    delay = min(delay, max(0.0, deadline_s - (self._clock() - start)))
                await self._sleep(delay)
                status = await adapter.poll(job_id)
                if status is BatchStatus.COMPLETED:
                    break
                if status in (BatchStatus.FAILED, BatchStatus.EXPIRED):
                    return _JobFailedError(status.value)
                if deadline_s is not None and self._clock() - start >= deadline_s:
                    with suppress(Exception):
                        await adapter.cancel(job_id)
                    return _JobFailedError(BatchStatus.EXPIRED.value)
        except Exception as exc:
            logger.warning("batch_job_failed", family=family, error=type(exc).__name__)
            return _JobFailedError(BatchStatus.FAILED.value)
        return await self._fetch_results(adapter, family, job_id, delays, start, deadline_s)

    async def _fetch_results(
        self,
        adapter: BatchApiPort,
        family: str,
        job_id: str,
        delays: Iterator[float],
        start: float,
        deadline_s: float | None,
    ) -> dict[str, BatchResult] | _ResultsUnavailableError:
        """Fetch a completed job's results, retrying with backoff (never re-billing)."""
        attempts = 0
        while True:
            attempts += 1
            try:
                return {r.custom_id: r for r in await adapter.results(job_id)}
            except Exception as exc:
                logger.warning(
                    "batch_results_failed",
                    family=family,
                    attempt=attempts,
                    error=type(exc).__name__,
                )
            if deadline_s is None:
                if attempts >= _RESULTS_MAX_ATTEMPTS:
                    return _ResultsUnavailableError(job_id)
                await self._sleep(next(delays))
                continue
            remaining = deadline_s - (self._clock() - start)
            if remaining <= 0:
                return _ResultsUnavailableError(job_id)
            await self._sleep(min(next(delays), remaining))

    async def _run_sync(self, req: BatchRequest) -> BatchResult:
        try:
            res = await self._sync_fn(req)
        except Exception as exc:
            return BatchResult(
                custom_id=req.custom_id, content=None, error=f"sync_failed:{type(exc).__name__}"
            )
        return self._meter_result(req, res, batch=False)

    def _meter_result(self, req: BatchRequest, res: BatchResult, *, batch: bool) -> BatchResult:
        """Price *res* (batch or list price) and record it on the meter."""
        estimate = compute_cost_usd(
            _pricing_model(req.model_ref),
            res.usage.prompt_tokens,
            res.usage.completion_tokens,
            settings=self._settings,
            batch=batch,
        )
        usage = replace(
            res.usage,
            cost_usd=estimate.cost_usd,
            calls=max(1, res.usage.calls),
            estimated=estimate.estimated,
            cost_source=estimate.source,
        )
        if self._meter is not None:
            self._meter.record(
                usage, pattern="batch" if batch else "batch_fallback", model=req.model_ref
            )
        return replace(res, usage=usage)

    async def _default_sync(self, req: BatchRequest) -> BatchResult:
        """Run *req* synchronously through ``ProviderRegistry`` (lazy import)."""
        from prismal.budget.usage import extract_token_usage
        from prismal.providers.registry import ProviderRegistry

        llm = ProviderRegistry(settings=self._settings).get_llm(model=req.model_ref)
        message: Any = await llm.ainvoke([dict(m) for m in req.messages])
        tokens = extract_token_usage(message)
        content = message.content
        return BatchResult(
            custom_id=req.custom_id,
            content=content if isinstance(content, str) else str(content),
            usage=Usage(
                prompt_tokens=tokens.prompt_tokens,
                completion_tokens=tokens.completion_tokens,
                calls=1,
            ),
        )

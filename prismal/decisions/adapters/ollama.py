"""Ollama ``/v1/systemone`` decision backend (DEC delta — SPEC-DEC-OLL-001…007, 010, 011).

``"ollama"`` in ``decision_backend_order`` expands into up to two sub-tier
backends, ``ollama:fast`` (Tev1) then ``ollama:accurate`` (Nimble), sharing one
:class:`OllamaTierGroup` so each knows its sibling. Both speak the Jev wire of
:class:`SystemOneHttpDecisionAdapter` against ``<base>/v1/systemone``.

* **Preflight** (lazy, once per process per ``(base_url, model)``, under a lock):
  ``/api/version`` ≥ ``decision_ollama_min_version``, ``/api/show`` lists the
  ``decision`` capability (``num_ctx`` parsed from ``parameters``, else 2048 —
  ``model_info.*context_length`` is the architecture max, not the served
  window), ``/api/tags`` gives the digest. A refusal is permanent; a transport
  error retries after 30 s. Until it succeeds every question abstains
  ``"unsupported"`` (fail-closed → next tier). Afterwards ``model`` is
  ``<name>@<digest12>`` so calibration keys on the local digest (OLL-003).
* **Limits** (OLL-004) are checked before sending; exceeded ⇒ ``"unsupported"``
  (never ``"over_limit"``, which the cascade treats as terminal), state is never
  truncated. HTTP ≥ 400 or a timeout ⇒ abstain, no retry (OLL-005).
* **Routing** (OLL-007): ``fast`` answers per-turn questions; ``accurate``
  answers low-frequency ones and per-turn ones only when they overflow ``fast``
  (or ``fast`` is gone) and its rolling p95 is within budget.
* **Batching** (OLL-011): one question per request on ``fast``; up to
  ``decision_ollama_max_batch`` on ``accurate`` unless a ``batch@<model>@<digest12>``
  record shows a > 2 point accuracy loss.

Every failure is an abstention; nothing here raises to the caller.
"""

from __future__ import annotations

import asyncio
import json
import math
import re
import time
import weakref
from collections import deque
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import httpx
from pydantic import SecretStr

from prismal.budget.types import Usage
from prismal.core.logging import get_logger
from prismal.decisions._telemetry import inc
from prismal.decisions.adapters._wire import count_model_mismatch, parse_answers, wire_questions
from prismal.decisions.adapters.systemone_http import SystemOneHttpDecisionAdapter
from prismal.decisions.catalog import LOW_FREQUENCY_INTEGRATIONS, integration_of
from prismal.decisions.ports import abstain_all
from prismal.decisions.types import DecisionKind, QuestionSpec
from prismal.utils.token_estimate import estimate_tokens

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from prismal.budget.meter import CostMeter
    from prismal.core.config import Settings
    from prismal.decisions.types import Decision, GatePolicy, Reason

logger = get_logger(__name__)

Tier = Literal["fast", "accurate"]

DEFAULT_BASE_URL = "http://localhost:11434"
DEFAULT_NUM_CTX = 2048
MIN_OPTIONS, MAX_OPTIONS = 2, 26
MAX_QUESTIONS_PER_REQUEST = 64
MAX_BODY_BYTES = 64 * 1024
CONTEXT_MARGIN = 0.9
TRANSIENT_RETRY_S = 30.0
PREFLIGHT_TTL_S = 60.0  # a successful preflight re-checks /api/tags after this (re-pulls)
P95_WINDOW = 50
BATCH_ACCURACY_TOLERANCE = 0.02
_PLACEHOLDER_KEY = SecretStr("ollama")  # Ollama has no auth; the header only keeps the wire uniform
_NUM_CTX_RE = re.compile(r"^\s*num_ctx\s+(\d+)\s*$", re.MULTILINE)
_VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)(?:\.(\d+))?")

_WARMUP_QUESTION = QuestionSpec(
    id="ollama.warmup",
    kind=DecisionKind.NOUL,
    instructions="¿El texto es un saludo?",
)

# Indirection so tests can drive the 30 s retry window without sleeping.
_monotonic = time.monotonic


# ── process-wide preflight cache ──────────────────────────────────────────────


@dataclass(frozen=True)
class OllamaPreflight:
    """Outcome of the per-model preflight (OLL-002/003)."""

    ok: bool
    reason: str = ""
    num_ctx: int = DEFAULT_NUM_CTX
    digest12: str = ""
    retry_at: float | None = None  # set for transient failures only
    recheck_at: float | None = None  # set for successes only: /api/tags re-check due


_PREFLIGHT: dict[tuple[str, str], OllamaPreflight] = {}
# One lock per (key, event loop): an ``asyncio.Lock`` binds to the loop of its first
# contention, so sharing it across loops raises. Weak keys drop a closed loop's locks.
_LOCKS: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, dict[tuple[str, str], asyncio.Lock]]
_LOCKS = weakref.WeakKeyDictionary()


def clear_preflight_cache() -> None:
    """Forget every preflight outcome (tests; a host re-pulling models)."""
    _PREFLIGHT.clear()
    _LOCKS.clear()


def _lock_for(key: tuple[str, str]) -> asyncio.Lock:
    per_loop = _LOCKS.setdefault(asyncio.get_running_loop(), {})
    return per_loop.setdefault(key, asyncio.Lock())


def _recheck_due(result: OllamaPreflight) -> bool:
    return result.ok and (result.recheck_at is None or _monotonic() >= result.recheck_at)


def _expire_success(key: tuple[str, str]) -> None:
    """Force a digest re-check on the next call (e.g. after a ``model_mismatch``)."""
    result = _PREFLIGHT.get(key)
    if result is not None and result.ok:
        _PREFLIGHT[key] = replace(result, recheck_at=None)


def _cached(key: tuple[str, str]) -> OllamaPreflight | None:
    result = _PREFLIGHT.get(key)
    if result is None:
        return None
    if result.retry_at is not None and _monotonic() >= result.retry_at:
        return None
    return result


def _version_tuple(text: object) -> tuple[int, int, int] | None:
    if not isinstance(text, str):
        return None
    match = _VERSION_RE.match(text.strip())
    if match is None:
        return None
    major, minor, patch = match.groups()
    return int(major), int(minor), int(patch or 0)


def _parse_num_ctx(parameters: object) -> int:
    if isinstance(parameters, str):
        match = _NUM_CTX_RE.search(parameters)
        if match is not None and int(match.group(1)) > 0:
            return int(match.group(1))
    return DEFAULT_NUM_CTX


def _is_low_frequency(question_id: str) -> bool:
    return integration_of(question_id) in LOW_FREQUENCY_INTEGRATIONS


# ── pure helpers (OLL-011) ────────────────────────────────────────────────────


def batching_allowed(directory: str | Path | None, model_name: str, digest12: str) -> bool:
    """False only when ``batch@<model>@<digest12>.json`` shows a > 2 point batch loss.

    Missing or unreadable records allow batching (the measured default, §1.4).
    """
    if directory is None:
        return True
    path = Path(directory) / f"batch@{model_name}@{digest12}.json"
    try:
        record = json.loads(path.read_text("utf-8"))
        single = float(record["single_accuracy"])
        batch = float(record["batch_accuracy"])
    except (OSError, ValueError, KeyError, TypeError):
        return True
    return not batch < single - BATCH_ACCURACY_TOLERANCE - 1e-9


def plan_chunks(
    ids: Sequence[str], *, max_batch: int, fits: Callable[[list[str]], bool]
) -> list[list[str]]:
    """Greedy, order-preserving grouping: ≤ ``max_batch`` (and ≤ 64) ids per chunk,
    closing a chunk early when adding the next id would not ``fits``."""
    size = max(1, min(max_batch, MAX_QUESTIONS_PER_REQUEST))
    chunks: list[list[str]] = []
    current: list[str] = []
    for qid in ids:
        candidate = [*current, qid]
        if current and (len(candidate) > size or not fits(candidate)):
            chunks.append(current)
            current = [qid]
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def _p95(samples: Sequence[float]) -> float | None:
    if not samples:
        return None
    ordered = sorted(samples)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]


# ── tier group ────────────────────────────────────────────────────────────────


@dataclass
class OllamaTierGroup:
    """Links the two sub-tier adapters so routing can consult the sibling."""

    fast: OllamaDecisionAdapter | None = None
    accurate: OllamaDecisionAdapter | None = None

    @classmethod
    def link(
        cls,
        *,
        fast: OllamaDecisionAdapter | None = None,
        accurate: OllamaDecisionAdapter | None = None,
    ) -> OllamaTierGroup:
        """Group *fast* and *accurate* and point each adapter's ``group`` at it."""
        group = cls(fast=fast, accurate=accurate)
        for adapter in (fast, accurate):
            if adapter is not None:
                adapter.group = group
        return group


# ── adapter ───────────────────────────────────────────────────────────────────


class OllamaDecisionAdapter(SystemOneHttpDecisionAdapter):
    """One Ollama sub-tier (``ollama:fast`` / ``ollama:accurate``) as a ``DecisionPort``."""

    def __init__(
        self,
        *,
        base_url: str,
        model_name: str,
        tier: Tier,
        keep_alive: str = "30m",
        min_version: str = "0.35.0",
        max_batch: int = 4,
        accurate_max_p95_ms: int = 1000,
        budget_ms: int = 2000,
        calibration_dir: str | None = None,
        settings: Settings | None = None,
        cost_meter: CostMeter | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base = base_url.rstrip("/")
        super().__init__(
            base_url=f"{self._base}/v1",
            api_key=_PLACEHOLDER_KEY,
            model=model_name,
            name=f"ollama:{tier}",
            max_choice_options=MAX_OPTIONS,
            settings=settings,
            cost_meter=cost_meter,
            transport=transport,
        )
        self.model_name = model_name
        self.tier: Tier = tier
        self._keep_alive = keep_alive
        self._min_version = _version_tuple(min_version) or (0, 0, 0)
        self._max_batch = max_batch
        self._max_p95_ms = accurate_max_p95_ms
        #: Own time budget; the cascade does not charge it to ``decision_timeout_ms``.
        self.budget_ms = budget_ms
        self._calibration_dir = calibration_dir
        self._batch_size: int | None = None
        self._batch_digest = ""
        self.latency_samples: deque[float] = deque(maxlen=P95_WINDOW)
        self.group = OllamaTierGroup(**{tier: self})

    def __repr__(self) -> str:
        return f"OllamaDecisionAdapter(name={self.name!r}, model={self.model!r})"

    @property
    def _key(self) -> tuple[str, str]:
        return (self._base, self.model_name)

    # ── DecisionPort ──────────────────────────────────────────────────────────

    def supports(self, question: QuestionSpec, *, language: str) -> bool:
        """Choice/Score need between ``MIN_OPTIONS`` and ``MAX_OPTIONS``; any language."""
        del language  # the decision models are multilingual (es/en verified, DEC0-10)
        if question.kind is DecisionKind.CHOICE:
            return MIN_OPTIONS <= len(question.options) <= MAX_OPTIONS
        if question.kind is DecisionKind.SCORE:
            return MIN_OPTIONS <= len(question.levels) <= MAX_OPTIONS
        return True

    async def decide_batch(
        self,
        states: Sequence[Mapping[str, str]],
        questions: Mapping[str, QuestionSpec],
        *,
        policy: GatePolicy,
    ) -> list[Mapping[str, Decision]]:
        """Ollama has no ``/systemone/batch``: one ``decide`` per state, in order."""
        return [await self.decide(state, questions, policy=policy) for state in states]

    async def warmup(self) -> bool:
        """Preflight + one tiny Noul so the cold load never lands on a user turn (OLL-006).

        Never raises; the warm-up latency is not recorded for the p95 gate.
        """
        try:
            preflight = await self._ensure_preflight()
            if not preflight.ok:
                return False
            out = await self._send(
                {"texto": "hola"}, {_WARMUP_QUESTION.id: _WARMUP_QUESTION}, record=False
            )
            return not out[_WARMUP_QUESTION.id].abstained
        except Exception as exc:
            logger.warning("decision_ollama_warmup_failed", error_type=type(exc).__name__)
            return False

    # ── sibling view ──────────────────────────────────────────────────────────

    @property
    def preflight(self) -> OllamaPreflight | None:
        """Cached preflight outcome (``None`` before the first attempt / after expiry)."""
        return _cached(self._key)

    @property
    def refused(self) -> bool:
        """True when a known preflight outcome rules this model out right now."""
        result = self.preflight
        return result is not None and not result.ok

    def p95_ms(self) -> float | None:
        """p95 of the recent latency samples, or ``None``."""
        return _p95(list(self.latency_samples))

    # ── internals ─────────────────────────────────────────────────────────────

    async def _ensure_preflight(self) -> OllamaPreflight:
        result = _cached(self._key)
        if result is None or _recheck_due(result):
            async with _lock_for(self._key):
                result = _cached(self._key)
                if result is None or _recheck_due(result):
                    result = await (
                        self._run_preflight() if result is None else self._recheck(result)
                    )
                    _PREFLIGHT[self._key] = result
                    if not result.ok:
                        inc(
                            "decision_ollama_preflight_refused",
                            {"tier": self.tier, "reason": result.reason},
                        )
                        logger.warning(
                            "decision_ollama_preflight_refused",
                            backend=self.name,
                            model=self.model_name,
                            reason=result.reason,
                        )
        if result.ok:
            self.model = f"{self.model_name}@{result.digest12}"
            if self._batch_digest != result.digest12:
                self._batch_digest = result.digest12
                self._batch_size = None  # the batch record is keyed by digest
            if self._batch_size is None:
                allowed = self.tier == "accurate" and batching_allowed(
                    self._calibration_dir, self.model_name, result.digest12
                )
                self._batch_size = self._max_batch if allowed else 1
        return result

    async def _run_preflight(self) -> OllamaPreflight:
        client = self._http()
        try:
            resp = await client.get(f"{self._base}/api/version")
            if resp.status_code >= 400:
                return _transient("version_http")
            version = _version_tuple(resp.json().get("version"))
            if version is None or version < self._min_version:
                return OllamaPreflight(ok=False, reason="version")
            return await self._show_and_tags(client)
        except Exception as exc:  # transport / malformed body ⇒ retry later
            return self._preflight_failed(exc)

    async def _recheck(self, previous: OllamaPreflight) -> OllamaPreflight:
        """TTL / mismatch re-check: ``/api/tags`` only, ``/api/show`` if the digest moved."""
        client = self._http()
        try:
            digest = await self._tag_digest(client)
            if digest is None:
                return _transient("tags_http")
            if digest[:12] == previous.digest12:
                return replace(previous, recheck_at=_monotonic() + PREFLIGHT_TTL_S)
            logger.info(
                "decision_ollama_digest_changed",
                backend=self.name,
                model=self.model_name,
                previous=previous.digest12,
                current=digest[:12],
            )
            return await self._show_and_tags(client)
        except Exception as exc:
            return self._preflight_failed(exc)

    async def _tag_digest(self, client: httpx.AsyncClient) -> str | None:
        """The model's ``/api/tags`` digest; ``""`` when untagged, ``None`` on HTTP error."""
        resp = await client.get(f"{self._base}/api/tags")
        if resp.status_code >= 400:
            return None
        digest = next(
            (
                m.get("digest")
                for m in resp.json().get("models") or []
                if isinstance(m, dict) and m.get("name") == self.model_name
            ),
            None,
        )
        return digest if isinstance(digest, str) and len(digest) >= 12 else ""

    def _preflight_failed(self, exc: Exception) -> OllamaPreflight:
        logger.warning(
            "decision_ollama_preflight_failed",
            backend=self.name,
            error_type=type(exc).__name__,
        )
        return _transient("transport")

    async def _show_and_tags(self, client: httpx.AsyncClient) -> OllamaPreflight:
        resp = await client.post(f"{self._base}/api/show", json={"model": self.model_name})
        if resp.status_code == 404:
            return OllamaPreflight(ok=False, reason="missing")
        if resp.status_code >= 400:
            return _transient("show_http")
        info = resp.json()
        if "decision" not in (info.get("capabilities") or []):
            return OllamaPreflight(ok=False, reason="capability")
        num_ctx = _parse_num_ctx(info.get("parameters"))
        digest = await self._tag_digest(client)
        if digest is None:
            return _transient("tags_http")
        if not digest:
            return OllamaPreflight(ok=False, reason="missing")
        return OllamaPreflight(
            ok=True,
            num_ctx=num_ctx,
            digest12=digest[:12],
            recheck_at=_monotonic() + PREFLIGHT_TTL_S,
        )

    def _body(
        self, state: Mapping[str, str], questions: Mapping[str, QuestionSpec]
    ) -> dict[str, Any]:
        return {
            "model": self.model_name,
            "keep_alive": self._keep_alive,
            "state": dict(state),
            "questions": wire_questions(questions),
        }

    @staticmethod
    def _fits(body: dict[str, Any], num_ctx: int) -> bool:
        if len(body["questions"]) > MAX_QUESTIONS_PER_REQUEST:
            return False
        text = json.dumps(body, ensure_ascii=False)
        if len(text.encode("utf-8")) > MAX_BODY_BYTES:
            return False
        return estimate_tokens(text) <= int(num_ctx * CONTEXT_MARGIN)

    def _routes_here(self, state: Mapping[str, str], q: QuestionSpec) -> bool:
        low = _is_low_frequency(q.id)
        if self.tier == "fast":
            accurate = self.group.accurate
            return not (low and accurate is not None and not accurate.refused)
        if low:
            return True
        fast = self.group.fast
        fast_pf = fast.preflight if fast is not None else None
        if fast is None or (fast_pf is not None and not fast_pf.ok):
            fast_unavailable = True
        elif fast_pf is None:
            fast_unavailable = False  # fast has not been asked yet: it keeps per-turn work
        else:
            body = fast._body(state, {q.id: q})
            fast_unavailable = not self._fits(body, fast_pf.num_ctx)
        if not fast_unavailable:
            return False
        p95 = self.p95_ms()
        return p95 is None or p95 <= self._max_p95_ms

    async def _decide(
        self, state: Mapping[str, str], questions: Mapping[str, QuestionSpec]
    ) -> dict[str, Decision]:
        preflight = await self._ensure_preflight()
        if not preflight.ok:
            return abstain_all(questions, backend=self.name, model=self.model, reason="unsupported")
        out: dict[str, Decision] = {}
        routed: dict[str, QuestionSpec] = {}
        for qid, q in questions.items():
            if self._routes_here(state, q) and self._fits(
                self._body(state, {qid: q}), preflight.num_ctx
            ):
                routed[qid] = q
            else:
                out.update(self._skip({qid: q}))
        chunks = plan_chunks(
            list(routed),
            max_batch=self._batch_size or 1,
            fits=lambda ids: self._fits(
                self._body(state, {i: routed[i] for i in ids}), preflight.num_ctx
            ),
        )
        for chunk in chunks:
            out.update(await self._send(state, {qid: routed[qid] for qid in chunk}))
        return out

    def _skip(self, questions: Mapping[str, QuestionSpec]) -> dict[str, Decision]:
        return abstain_all(questions, backend=self.name, model=self.model, reason="unsupported")

    async def _send(
        self,
        state: Mapping[str, str],
        questions: Mapping[str, QuestionSpec],
        *,
        record: bool = True,
    ) -> dict[str, Decision]:
        body = self._body(state, questions)
        start = time.perf_counter()
        reason: Reason = "backend_error"
        try:
            response = await self._http().post(self._url, json=body, headers=self._headers())
        except httpx.TimeoutException:
            reason = "timeout"
            response = None
        except httpx.HTTPError as exc:
            logger.warning(
                "decision_ollama_request_failed", backend=self.name, error_type=type(exc).__name__
            )
            response = None
        finally:
            # Every attempt feeds the p95 gate — errors, timeouts and an outer
            # cancellation (guarded_decide's wait_for) included — so a slow or
            # failing tier never looks healthy. CancelledError still propagates.
            latency = (time.perf_counter() - start) * 1000.0
            if record:
                self.latency_samples.append(latency)
        if response is None or response.status_code >= 400:
            if response is not None:
                # 400 = oversize / invalid: never retried on the same model (OLL-005).
                logger.warning(
                    "decision_ollama_http_error", backend=self.name, status=response.status_code
                )
            return abstain_all(
                questions, backend=self.name, model=self.model, reason=reason, latency_ms=latency
            )
        try:
            payload = response.json()
        except ValueError:
            payload = None
        return self._parse(payload, questions, latency)

    def _parse(
        self, payload: Any, questions: Mapping[str, QuestionSpec], latency: float
    ) -> dict[str, Decision]:
        if not isinstance(payload, dict):
            return abstain_all(
                questions,
                backend=self.name,
                model=self.model,
                reason="backend_error",
                latency_ms=latency,
            )
        if payload.get("model") != self.model_name:
            count_model_mismatch(self.name)
            _expire_success(self._key)  # the served weights may have moved: re-check digest
            logger.warning("decision_model_mismatch", backend=self.name, pinned=self.model_name)
            return abstain_all(
                questions,
                backend=self.name,
                model=self.model,
                reason="model_mismatch",
                latency_ms=latency,
            )
        self._meter(payload.get("usage"))
        return parse_answers(
            questions,
            payload.get("answers"),
            backend=self.name,
            model=self.model,
            latency_ms=latency,
        )

    def _meter(self, usage: Any) -> None:
        """Local inference: tokens are metered, cost is zero."""
        if self._cost_meter is None or not isinstance(usage, dict):
            return
        try:
            self._cost_meter.record(
                Usage(
                    prompt_tokens=int(usage.get("input_tokens", 0) or 0),
                    completion_tokens=int(usage.get("output_tokens", 0) or 0),
                    cost_usd=0.0,
                    calls=1,
                    estimated=False,
                    cost_source="decision",
                ),
                pattern="decision",
                model=self.model,
            )
        except Exception as exc:  # metering is fail-open
            logger.warning("decision_metering_failed", error_type=type(exc).__name__)


def _transient(reason: str) -> OllamaPreflight:
    return OllamaPreflight(ok=False, reason=reason, retry_at=_monotonic() + TRANSIENT_RETRY_S)


# ── construction from settings (OLL-001, OLL-010) ─────────────────────────────


def resolve_base_url(settings: Settings) -> str:
    """``decision_ollama_base_url`` → ``ollama_base_url`` → ``http://localhost:11434``."""
    return (
        settings.decision_ollama_base_url
        or getattr(settings, "ollama_base_url", None)
        or DEFAULT_BASE_URL
    )


def build_ollama_backends(
    settings: Settings,
    *,
    cost_meter: CostMeter | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> list[OllamaDecisionAdapter]:
    """``ollama:fast`` then ``ollama:accurate`` for the tiers configured (no network I/O)."""
    from prismal.core.config import is_loopback_url

    base_url = resolve_base_url(settings)
    if not is_loopback_url(base_url):
        if not settings.decision_ollama_allow_remote:
            # Per-tenant overrides (model_copy) skip the Settings validator, and an unset
            # decision_ollama_base_url may fall back to a remote ollama_base_url: refuse
            # here so no request ever leaves the host (OLL-010, SSRF guard).
            logger.warning(
                "decision_ollama_remote_refused",
                detail="non-loopback Ollama base URL without decision_ollama_allow_remote",
            )
            return []
        logger.warning(
            "decision_ollama_remote_base_url",
            base_url=base_url,
            detail="Ollama has no authentication; never expose port 11434.",
        )
    adapters: dict[str, OllamaDecisionAdapter] = {}
    for tier in ("fast", "accurate"):
        model_name = settings.decision_ollama_models.get(tier)
        if not model_name:
            continue
        adapters[tier] = OllamaDecisionAdapter(
            base_url=base_url,
            model_name=model_name,
            tier=tier,
            keep_alive=settings.decision_ollama_keep_alive,
            min_version=settings.decision_ollama_min_version,
            max_batch=settings.decision_ollama_max_batch,
            accurate_max_p95_ms=settings.decision_ollama_accurate_max_p95_ms,
            budget_ms=settings.decision_ollama_timeout_ms,
            calibration_dir=settings.decision_calibration_dir,
            settings=settings,
            cost_meter=cost_meter,
            transport=transport,
        )
    OllamaTierGroup.link(fast=adapters.get("fast"), accurate=adapters.get("accurate"))
    return list(adapters.values())

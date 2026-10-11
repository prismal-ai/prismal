"""Host read surface — view types and read functions (HRS — DELTA-2026-10 §3/§4).

``prismal-server`` reads engine state only through the methods on
:class:`~prismal.composition.runtime.RuntimeContext` (``budget_status``,
``quota_windows``, ``audit_page``, ``verify_audit_chain``, ``decisions_report``,
``routing_report``), which delegate to the functions here. The host may import
the frozen view dataclasses below for annotations / ``isinstance`` only; their
field names are the host wire names (``prismal-server`` SPEC-RH2-BUD/ADM).

Every function here is **read-only** (no engine state is mutated) and is
expected to be called through the ``RuntimeContext`` wrappers, which add the
never-raise fallbacks and the ``prismal.host_surface_errors_total{method}``
counter (SPEC-HRS-SUR-002).
"""

from __future__ import annotations

import hashlib
import json
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import IO, TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Mapping
    from pathlib import Path

    from prismal.budget.guard import BudgetGuard
    from prismal.budget.types import Budget, Usage
    from prismal.core.config import Settings
    from prismal.monitoring.host_stats import DecisionStatsRecorder, RoutingStatsRecorder
    from prismal.providers.gateways.base import QuotaSnapshot

# ── view types (SPEC-HRS-SUR-001) ─────────────────────────────────────────────

BudgetStatusLiteral = Literal["within", "soft_exceeded", "hard_exceeded", "disabled", "unknown"]


@dataclass(frozen=True)
class BudgetAmounts:
    """One value per budget dimension (``None`` = not applicable / unlimited)."""

    tokens: int | None
    cost_usd: float | None
    calls: int | None
    wall_clock_s: float | None


@dataclass(frozen=True)
class BudgetStatusView:
    """Budget verdict for the runtime's tenant or one thread (SPEC-HRS-BUD-001)."""

    enabled: bool
    scope: str
    status: BudgetStatusLiteral
    breached_dimension: str | None
    soft_ratio: float | None
    hard_cap: bool | None
    limits: BudgetAmounts | None
    consumed: BudgetAmounts | None
    remaining: BudgetAmounts | None


@dataclass(frozen=True)
class QuotaWindowView:
    """One gateway quota window (SPEC-HRS-QTA-001)."""

    gateway: str
    window: Literal["credit", "5h", "daily", "weekly", "monthly"]
    unit: Literal["usd", "usd_equivalent", "requests", "tokens"]
    limit: float | None
    used: float
    remaining: float | None
    resets_at: datetime | None
    source: Literal["api", "ledger"]
    as_of: datetime


@dataclass(frozen=True)
class AuditEntryView:
    """One audit record, hash-first — never content (SPEC-HRS-AUD-001)."""

    seq: int
    ts: datetime | None
    event: str
    actor: str | None
    org_id: str | None
    hash: str
    prev_hash: str
    fields: Mapping[str, Any]


@dataclass(frozen=True)
class AuditPageView:
    """A newest-first page of audit records."""

    page: int
    page_size: int
    total: int | None
    entries: tuple[AuditEntryView, ...]


@dataclass(frozen=True)
class AuditChainStatus:
    """Result of re-verifying the audit hash chain (SPEC-HRS-AUD-002)."""

    status: Literal["valid", "broken", "unverified"]
    checked: int
    first_broken_seq: int | None


@dataclass(frozen=True)
class ReportWindow:
    """Effective (clamped) report window — ``since`` may be later than requested."""

    since: datetime
    until: datetime


@dataclass(frozen=True)
class DecisionIntegrationStats:
    """One DEC integration row (SPEC-HRS-DEC-001)."""

    integration: str
    mode: str
    samples: int
    agreement_ratio: float | None
    accepted: int
    escalated: int
    abstained: int
    llm_calls_avoided: int
    window: ReportWindow


@dataclass(frozen=True)
class DecisionsReportView:
    """DEC report; ``enabled=False`` and no rows when decisions are disabled."""

    enabled: bool
    integrations: tuple[DecisionIntegrationStats, ...]


@dataclass(frozen=True)
class RoutingPolicyStats:
    """One AMR routing policy row (SPEC-HRS-RTE-001)."""

    policy: str
    decisions: int
    decisions_by_tier: Mapping[str, int]
    escalations: int
    escalations_by_reason: Mapping[str, int]
    plan_cache_hits: int
    estimated_savings_usd: float
    window: ReportWindow


@dataclass(frozen=True)
class RoutingReportView:
    """AMR report; ``enabled=False`` and no rows when routing is disabled."""

    enabled: bool
    mode: str
    policies: tuple[RoutingPolicyStats, ...]


@dataclass(frozen=True)
class A2ARouteSpec:
    """Tenant-scoped A2A route shape (SPEC-HRS-A2A-001; method lands with HRS1-08)."""

    card_path: str
    rpc_path: str
    tenant_scoped: bool


# ── shared helpers ────────────────────────────────────────────────────────────


def resolve_settings(settings: Settings | None) -> Settings:
    """The runtime's effective settings, else the process settings."""
    if settings is not None:
        return settings
    from prismal.core.config import get_settings

    return get_settings()


def _attr(items: tuple[tuple[str, str], ...], name: str) -> str | None:
    for key, value in items:
        if key == name:
            return value
    return None


# ── budget_status (SPEC-HRS-BUD-001) ──────────────────────────────────────────

#: BudgetGuard dimension name → host wire name.
_DIMENSION_WIRE = {
    "tokens": "tokens",
    "cost": "cost_usd",
    "calls": "calls",
    "wall_clock": "wall_clock_s",
}


def _limits(budget: Budget) -> BudgetAmounts:
    return BudgetAmounts(
        tokens=budget.max_tokens,
        cost_usd=budget.max_cost_usd,
        calls=budget.max_calls,
        wall_clock_s=budget.max_wall_clock_s,
    )


def _consumed(usage: Usage) -> BudgetAmounts:
    return BudgetAmounts(
        tokens=usage.total_tokens,
        cost_usd=usage.cost_usd,
        calls=usage.calls,
        wall_clock_s=usage.wall_clock_s,
    )


def _remaining(limit: float, used: float) -> float | None:
    return None if limit <= 0 else max(0.0, limit - used)


def _remaining_amounts(budget: Budget, usage: Usage) -> BudgetAmounts:
    tokens = _remaining(budget.max_tokens, usage.total_tokens)
    calls = _remaining(budget.max_calls, usage.calls)
    return BudgetAmounts(
        tokens=None if tokens is None else int(tokens),
        cost_usd=_remaining(budget.max_cost_usd, usage.cost_usd),
        calls=None if calls is None else int(calls),
        wall_clock_s=_remaining(budget.max_wall_clock_s, usage.wall_clock_s),
    )


def _thread_guard(thread_id: str | None, org_id: str | None) -> BudgetGuard | None:
    """The per-run guard of *thread_id* when it belongs to *org_id* (read-only lookup)."""
    if not thread_id:
        return None
    from prismal.budget.resolve import get_budget_guard

    guard = get_budget_guard({"session_id": thread_id})
    if guard is None:
        return None
    if org_id is not None and getattr(guard.meter, "_tenant", None) != org_id:
        return None  # another tenant's run is never visible
    return guard


def budget_status(
    settings: Settings, *, org_id: str | None, thread_id: str | None = None
) -> BudgetStatusView:
    """Budget verdict from ``BudgetGuard.check()`` over the applicable meter.

    ``turn`` / ``session`` scope reads the thread's per-run meter (Phase C keys it
    by ``session_id`` = the host's ``thread_id``). ``tenant`` scope has no
    tenant-wide meter in the engine (Phase C meters are per run), so it reports
    ``unknown`` until one exists. Read-only: the per-run registry is only read.
    """
    from prismal.budget.resolve import resolve_budget

    scope = str(settings.budget_scope)
    guard = None if scope == "tenant" else _thread_guard(thread_id, org_id)
    if not settings.budget_enabled:
        return BudgetStatusView(
            enabled=False,
            scope=scope,
            status="disabled",
            breached_dimension=None,
            soft_ratio=None,
            hard_cap=None,
            limits=None,
            consumed=None if guard is None else _consumed(guard.meter.usage),
            remaining=None,
        )
    if guard is None:
        return BudgetStatusView(
            enabled=True,
            scope=scope,
            status="unknown",
            breached_dimension=None,
            soft_ratio=settings.budget_soft_ratio,
            hard_cap=settings.budget_hard_cap,
            limits=_limits(resolve_budget(settings, org_id=org_id)),
            consumed=None,
            remaining=None,
        )
    verdict = guard.check()
    status: BudgetStatusLiteral = (
        "hard_exceeded"
        if verdict.hard_exceeded
        else "soft_exceeded"
        if verdict.soft_exceeded
        else "within"
    )
    dim = verdict.breached_dimension
    return BudgetStatusView(
        enabled=True,
        scope=scope,
        status=status,
        breached_dimension=None if dim is None else _DIMENSION_WIRE.get(dim, dim),
        soft_ratio=guard.soft_ratio,
        hard_cap=guard.hard_cap,
        limits=_limits(verdict.budget),
        consumed=_consumed(verdict.usage),
        remaining=_remaining_amounts(verdict.budget, verdict.usage),
    )


# ── quota_windows (SPEC-HRS-QTA-001) ──────────────────────────────────────────

_WINDOW_WIRE: dict[str, Literal["credit", "5h", "daily", "weekly", "monthly"]] = {
    "credit": "credit",
    "5h": "5h",
    "day": "daily",
    "week": "weekly",
    "month": "monthly",
}


def _quota_view(snap: QuotaSnapshot, as_of: datetime) -> QuotaWindowView | None:
    window = _WINDOW_WIRE.get(snap.window)
    if window is None:
        return None
    # Ledger windows track flat-fee subscriptions: USD there is list-price equivalent.
    unit: Literal["usd", "usd_equivalent", "requests", "tokens"] = (
        "usd_equivalent" if snap.unit == "usd" and snap.source == "ledger" else snap.unit
    )
    return QuotaWindowView(
        gateway=snap.gateway,
        window=window,
        unit=unit,
        limit=snap.limit,
        used=snap.used,
        remaining=snap.remaining,
        resets_at=snap.resets_at,
        source=snap.source,
        as_of=as_of,
    )


async def _gateway_snapshots(gateway: Any, name: str, ledger: Any | None) -> list[QuotaSnapshot]:
    many = getattr(gateway, "quotas", None)
    snaps: list[QuotaSnapshot] = []
    if callable(many):
        snaps.extend(await many())
    else:
        one = await gateway.quota()
        if one is not None:
            snaps.append(one)
    if ledger is not None:
        snaps.extend(ledger.snapshot(name))
    return snaps


async def quota_windows(
    router: Any | None, *, on_error: Callable[[str, BaseException], None]
) -> list[QuotaWindowView]:
    """Flatten each gateway's quota snapshots into one view per window.

    A failing gateway is omitted and reported through *on_error* (the caller
    counts ``host_surface_errors{method="quota_windows"}``). Duplicate
    ``(window, source)`` pairs (e.g. OpenCode Go's ``quota()`` echoing its ledger)
    are reported once.
    """
    if router is None:
        return []
    as_of = datetime.now(UTC)
    ledger = getattr(router, "ledger", None)
    views: list[QuotaWindowView] = []
    for name in router.names():
        gateway = router.get(name)
        if gateway is None:
            continue
        try:
            snaps = await _gateway_snapshots(gateway, name, ledger)
        except Exception as exc:
            on_error("quota_windows", exc)
            continue
        seen: set[tuple[str, str]] = set()
        for snap in snaps:
            view = _quota_view(snap, as_of)
            if view is None or (view.window, view.source) in seen:
                continue
            seen.add((view.window, view.source))
            views.append(view)
    return views


# ── audit (SPEC-HRS-AUD-001/002) ──────────────────────────────────────────────

_GENESIS_HASH = "0" * 64
_MAX_PAGE_SIZE = 200
_MAX_FIELD_CHARS = 256
#: Hard bound on the records one :func:`audit_page` call parses (newest first).
AUDIT_SCAN_MAX_RECORDS = 100_000
#: Longest audit line (in characters) the host surface will load; longer lines are
#: drained in bounded chunks and never parsed (resource-exhaustion guard).
AUDIT_MAX_LINE_CHARS = 64 * 1024
_ENVELOPE_KEYS = frozenset({"timestamp", "event", "prev_hash", "entry_hash", "org_id", "actor"})
_ACTOR_KEYS = ("actor", "agent_id", "user_id")
#: Field names whose *scalar* values are safe to show (identifiers, names,
#: verdicts, counts, durations, hashes) — derived from what ``AuditLogger`` and
#: the ``log_event`` callers write. Everything else is reduced to a digest.
_SAFE_FIELD_KEYS = frozenset(
    {
        # identifiers / actors
        "session_id", "run_id", "task_id", "thread_id", "agent_did", "identity",
        "decision_id", "question_id", "calibration_version",
        # names
        "agent", "tool", "tool_name", "node_name", "skill", "name", "group", "model",
        "served_model", "backend", "integration", "gateway", "policy", "tier",
        # verdicts / classifications
        "status", "outcome", "mode", "scope", "phase", "method", "action", "effect", "rule",
        "direction", "category", "modality", "media_event", "permission_type", "flow",
        "vector", "dimension", "language", "risk_tier", "kind",
        # flags / numbers
        "granted", "blocked", "ok", "complete", "converged", "executed", "replan", "tainted",
        "shadow_agree", "risk", "risk_score", "tokens", "cost_usd", "round", "rounds", "used",
        "limit", "tau", "margin", "answer_confidence", "spread_confidence", "sha256",
    }
)  # fmt: skip
#: Name suffixes that mark hash / count / measure fields (``goal_hash``, ``text_length``…).
_SAFE_FIELD_SUFFIXES = (
    "_hash", "_count", "_length", "_ms", "_s", "_bytes", "_id", "_score", "_ratio", "_usd",
    "_tokens", "_size", "_requested", "_effective", "_version",
)  # fmt: skip


def audit_log_path(audit: Any) -> Path | None:
    """The JSONL path of a real :class:`AuditLogger`, else ``None``."""
    from prismal.security.audit import AuditLogger

    if isinstance(audit, AuditLogger):
        return audit._path  # read-only access to the logger's file
    return None


def _digest(value: Any) -> str:
    raw = value if isinstance(value, str) else json.dumps(value, sort_keys=True, default=str)
    return "sha256:" + hashlib.sha256(raw.encode()).hexdigest()[:16]


def _is_safe_key(key: str) -> bool:
    return key in _SAFE_FIELD_KEYS or key.endswith(_SAFE_FIELD_SUFFIXES)


def _scrub_field(key: str, value: Any) -> Any:
    """Hash-first projection of one top-level audit field (allowlist, never content).

    Numbers, booleans and ``None`` pass under any key; a short string passes only
    under an allowlisted key; every other value — any other string, and every
    nested dict / list regardless of key — is replaced by a ``sha256:`` digest.
    """
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str) and _is_safe_key(key) and len(value) <= _MAX_FIELD_CHARS:
        return value
    return _digest(value)


def _parse_ts(raw: Any) -> datetime | None:
    if not isinstance(raw, str):
        return None
    try:
        ts = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return ts if ts.tzinfo is not None else ts.replace(tzinfo=UTC)


def _entry_view(seq: int, entry: Mapping[str, Any]) -> AuditEntryView:
    actor = next((str(entry[k]) for k in _ACTOR_KEYS if entry.get(k)), None)
    org = entry.get("org_id")
    fields = {str(k): _scrub_field(str(k), v) for k, v in entry.items() if k not in _ENVELOPE_KEYS}
    return AuditEntryView(
        seq=seq,
        ts=_parse_ts(entry.get("timestamp")),
        event=str(entry.get("event", "")),
        actor=actor,
        org_id=None if org is None else str(org),
        hash=str(entry.get("entry_hash", "")),
        prev_hash=str(entry.get("prev_hash", "")),
        fields=fields,
    )


def _iter_records(fh: IO[str]) -> Iterator[str | None]:
    """Yield each non-blank line of *fh*, or ``None`` for an oversized one.

    Never holds more than :data:`AUDIT_MAX_LINE_CHARS` + 1 characters of a line:
    an oversized line is drained chunk by chunk and reported as ``None`` so the
    caller keeps the absolute ``seq`` numbering without ever parsing it.
    """
    limit = AUDIT_MAX_LINE_CHARS
    while True:
        line = fh.readline(limit + 1)
        if not line:
            return
        if len(line) > limit and not line.endswith("\n"):
            while True:
                rest = fh.readline(limit)
                if not rest or rest.endswith("\n"):
                    break
            yield None
        elif line.strip():
            yield line


def _count_records(path: Path) -> int:
    with path.open(encoding="utf-8") as fh:
        return sum(1 for _ in _iter_records(fh))


def _scan_cap(max_records: int | None) -> int:
    """Clamp a caller-supplied record bound to ``[0, AUDIT_SCAN_MAX_RECORDS]``."""
    hard = AUDIT_SCAN_MAX_RECORDS
    return min(hard, max(0, int(hard if max_records is None else max_records)))


def audit_page(
    audit: Any,
    *,
    org_id: str | None,
    page: int = 1,
    page_size: int = 50,
    since: datetime | None = None,
    max_records: int | None = None,
) -> AuditPageView:
    """Newest-first page of the tenant's audit records (hash-first fields only).

    ``org_id=None`` sees every record; a tenant sees only records whose
    ``org_id`` field equals it (records without one belong to no tenant).
    ``total`` is ``None`` when the audit port is not a file-backed logger.

    Bounded work: only the newest *max_records* records (default
    :data:`AUDIT_SCAN_MAX_RECORDS`) are parsed — the rest of the file is merely
    counted, line by line — and only the entries up to the requested page are
    kept. When the log holds more records than the bound, older ones are not
    visible and ``total`` is ``None`` (unknown). ``seq`` stays the absolute
    1-based position among non-blank lines, matching :func:`verify_audit_chain`.
    *max_records* is clamped to :data:`AUDIT_SCAN_MAX_RECORDS`; a page wholly past
    the scan window returns empty without reading the file; a line longer than
    :data:`AUDIT_MAX_LINE_CHARS` keeps its ``seq`` but is skipped unparsed.
    """
    page = max(1, int(page))
    page_size = min(_MAX_PAGE_SIZE, max(1, int(page_size)))
    cap = max(1, _scan_cap(max_records))
    start = (page - 1) * page_size
    path = audit_log_path(audit)
    if path is None or start >= cap:
        # Past the scan window: nothing visible, nothing to read (total unknown).
        return AuditPageView(page=page, page_size=page_size, total=None, entries=())
    if not path.exists():
        return AuditPageView(page=page, page_size=page_size, total=0, entries=())
    cutoff = None
    if since is not None:
        cutoff = since if since.tzinfo is not None else since.replace(tzinfo=UTC)
    skip = max(0, _count_records(path) - cap)
    # The newest page*page_size matches are all a page needs (bounded by the cap).
    kept: deque[tuple[int, dict[str, Any]]] = deque(maxlen=min(page * page_size, cap))
    matched = 0
    with path.open(encoding="utf-8") as fh:
        # seq numbers non-blank lines, matching verify_audit_chain.
        for seq, line in enumerate(_iter_records(fh), start=1):
            if seq <= skip or line is None:
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if not isinstance(entry, dict):
                continue
            if org_id is not None and entry.get("org_id") != org_id:
                continue
            if cutoff is not None:
                ts = _parse_ts(entry.get("timestamp"))
                if ts is None or ts < cutoff:
                    continue
            matched += 1
            kept.append((seq, entry))
    newest = list(reversed(kept))
    entries = tuple(_entry_view(seq, e) for seq, e in newest[start : start + page_size])
    total = None if skip else matched
    return AuditPageView(page=page, page_size=page_size, total=total, entries=entries)


def verify_audit_chain(audit: Any, *, max_records: int = 10_000) -> AuditChainStatus:
    """Recompute the audit hash chain over at most *max_records* records.

    Deviation from the DELTA text (which says xxhash): the L5 ``AuditLogger``
    chains with **SHA-256** over ``json.dumps(entry_without_entry_hash,
    sort_keys=True)`` and links ``prev_hash`` from the genesis ``"0" * 64`` — this
    verifies what the code actually writes. The chain is one process-wide file,
    so it is verified as a whole regardless of the context's tenant.

    Bounded work: the log is streamed, *max_records* is clamped to
    :data:`AUDIT_SCAN_MAX_RECORDS`, counting stops at the first record past the
    cap (⇒ ``unverified``), and an oversized line is never loaded (⇒ ``unverified``).
    """
    unverified = AuditChainStatus(status="unverified", checked=0, first_broken_seq=None)
    cap = _scan_cap(max_records)
    path = audit_log_path(audit)
    if path is None:
        return unverified
    if not path.exists():
        return AuditChainStatus(status="valid", checked=0, first_broken_seq=None)
    try:
        # Pass 1 (streamed, no parsing): stop as soon as the log exceeds the cap.
        with path.open(encoding="utf-8") as fh:
            for count, _ in enumerate(_iter_records(fh), start=1):
                if count > cap:
                    return unverified
        with path.open(encoding="utf-8") as fh:
            return _verify_stream(_iter_records(fh), unverified)
    except (OSError, UnicodeDecodeError):
        return unverified


def _verify_stream(records: Iterator[str | None], unverified: AuditChainStatus) -> AuditChainStatus:
    """Walk the SHA-256 chain over already-bounded *records*.

    An oversized record cannot be loaded, so the chain is ``unverified`` (not
    proven broken) from that point.
    """
    expected_prev = _GENESIS_HASH
    seq = 0
    for seq, line in enumerate(records, start=1):
        if line is None:
            return unverified
        try:
            entry = json.loads(line)
            stored = entry.pop("entry_hash")
            digest = hashlib.sha256(json.dumps(entry, sort_keys=True).encode()).hexdigest()
            ok = entry.get("prev_hash") == expected_prev and digest == stored
        except (ValueError, KeyError, AttributeError, TypeError):
            ok = False
        if not ok:
            return AuditChainStatus(status="broken", checked=seq, first_broken_seq=seq)
        expected_prev = str(stored)
    return AuditChainStatus(status="valid", checked=seq, first_broken_seq=None)


# ── decisions_report (SPEC-HRS-DEC-001) ───────────────────────────────────────


def decisions_report(
    settings: Settings,
    *,
    decisions: Any | None,
    org_id: str | None,
    since: datetime | None = None,
    until: datetime | None = None,
    recorder: DecisionStatsRecorder | None = None,
) -> DecisionsReportView:
    """One row per DEC integration: shadow agreement + outcome counts."""
    if not settings.decisions_enabled:
        return DecisionsReportView(enabled=False, integrations=())
    from prismal.decisions.catalog import INTEGRATION_FLAGS
    from prismal.decisions.integrations._common import resolve_mode
    from prismal.monitoring.host_stats import get_decision_stats_recorder

    rec = recorder if recorder is not None else get_decision_stats_recorder()
    agg = rec.aggregate(
        since=since,
        until=until,
        org_id=org_id,
        retention_h=settings.host_surface_stats_retention_h,
    )
    window = ReportWindow(since=agg.since, until=agg.until)
    counts: dict[str, dict[str, int]] = {}
    for (metric, items), amount in agg.totals.items():
        integration = _attr(items, "integration")
        if integration is None:
            continue
        row = counts.setdefault(integration, {})
        bucket = _attr(items, "outcome") if metric == "decisions" else "llm_calls_avoided"
        if bucket is not None:
            row[bucket] = row.get(bucket, 0) + int(amount)

    shadow = getattr(decisions, "shadow", None) if decisions is not None else None
    rows: list[DecisionIntegrationStats] = []
    for integration in INTEGRATION_FLAGS:
        samples, ratio = 0, None
        if shadow is not None:
            report = shadow.report(integration)
            samples = int(report.n)
            ratio = float(report.agreement_accepted) if samples > 0 else None
        row = counts.get(integration, {})
        rows.append(
            DecisionIntegrationStats(
                integration=integration,
                mode=str(resolve_mode(settings, integration).value),
                samples=samples,
                agreement_ratio=ratio,
                accepted=row.get("accepted", 0),
                escalated=row.get("escalated", 0),
                abstained=row.get("abstained", 0),
                llm_calls_avoided=row.get("llm_calls_avoided", 0),
                window=window,
            )
        )
    return DecisionsReportView(enabled=True, integrations=tuple(rows))


# ── routing_report (SPEC-HRS-RTE-001) ─────────────────────────────────────────


@dataclass
class _PolicyAcc:
    decisions: int = 0
    by_tier: dict[str, int] | None = None
    escalations: int = 0
    by_reason: dict[str, int] | None = None
    plan_cache_hits: int = 0
    savings_micro: float = 0.0


def routing_report(
    settings: Settings,
    *,
    org_id: str | None,
    since: datetime | None = None,
    until: datetime | None = None,
    recorder: RoutingStatsRecorder | None = None,
) -> RoutingReportView:
    """Per-policy decisions / escalations / plan-cache hits / savings.

    ``routing_escalations`` and ``plan_cache_hits`` carry no ``policy`` label at
    their emit sites (cascade / plan cache), so they are attributed to the
    configured ``routing_policy``. ``estimated_savings_usd`` is the sum of the
    ``prismal.routing_savings_usd`` micro-USD increments divided by 1e6 — the
    same frontier-counterfactual figure, never recomputed.
    """
    if not settings.adaptive_routing_enabled:
        return RoutingReportView(enabled=False, mode=settings.routing_mode, policies=())
    from prismal.monitoring.host_stats import get_routing_stats_recorder

    rec = recorder if recorder is not None else get_routing_stats_recorder()
    agg = rec.aggregate(
        since=since,
        until=until,
        org_id=org_id,
        retention_h=settings.host_surface_stats_retention_h,
    )
    window = ReportWindow(since=agg.since, until=agg.until)
    configured = str(settings.routing_policy)
    accs: dict[str, _PolicyAcc] = {configured: _PolicyAcc()}
    for (metric, items), amount in agg.totals.items():
        policy = _attr(items, "policy") or configured
        acc = accs.setdefault(policy, _PolicyAcc())
        if metric == "routing_decisions":
            acc.decisions += int(amount)
            tier = _attr(items, "tier") or "unknown"
            acc.by_tier = acc.by_tier or {}
            acc.by_tier[tier] = acc.by_tier.get(tier, 0) + int(amount)
        elif metric == "routing_escalations":
            acc.escalations += int(amount)
            reason = _attr(items, "reason") or "unknown"
            acc.by_reason = acc.by_reason or {}
            acc.by_reason[reason] = acc.by_reason.get(reason, 0) + int(amount)
        elif metric == "plan_cache_hits":
            acc.plan_cache_hits += int(amount)
        elif metric == "routing_savings_usd":
            acc.savings_micro += amount
    policies = tuple(
        RoutingPolicyStats(
            policy=name,
            decisions=acc.decisions,
            decisions_by_tier=dict(acc.by_tier or {}),
            escalations=acc.escalations,
            escalations_by_reason=dict(acc.by_reason or {}),
            plan_cache_hits=acc.plan_cache_hits,
            estimated_savings_usd=acc.savings_micro / 1e6,
            window=window,
        )
        for name, acc in sorted(accs.items())
    )
    return RoutingReportView(enabled=True, mode=settings.routing_mode, policies=policies)


__all__ = [
    "A2ARouteSpec",
    "AuditChainStatus",
    "AuditEntryView",
    "AuditPageView",
    "BudgetAmounts",
    "BudgetStatusView",
    "DecisionIntegrationStats",
    "DecisionsReportView",
    "QuotaWindowView",
    "ReportWindow",
    "RoutingPolicyStats",
    "RoutingReportView",
    "audit_page",
    "budget_status",
    "decisions_report",
    "quota_windows",
    "routing_report",
    "verify_audit_chain",
]

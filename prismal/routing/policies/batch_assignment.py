"""Batch model assignment (Phase AMR — SPEC-AMR-BAT-001/002, ARCHITECTURE §3.4).

Given N independent items (e.g. Skynet sub-orders) and K candidate models, pick
one model per item minimizing predicted cost subject to a mean predicted
accuracy floor ``alpha`` and per-model concurrency caps (scaled by waves).

* :func:`solve_dual` — Lagrangian dual decomposition: per-item argmin of
  ``ĉ - λ·a + μ_j``, projected subgradient on ``λ`` / ``μ_j`` with
  ``η_t = η0 / sqrt(t + 1)`` (η0 halved after 5 non-improving dual values),
  primal recovery and a relative duality-gap stop (``eps``).
* :func:`solve_greedy` — regret-ordered cheapest start, then Δa/Δĉ max-heap
  upgrades (``Δĉ <= 0`` first).
* max-accuracy capacity-feasible assignment when ``alpha`` is unreachable
  (``feasible=False``, counted, never raised).

Documented deviations from the ARCHITECTURE text (all deterministic):

1. The step is applied to the ∞-norm-normalized *active* subgradient and
   expressed in λ units (median Δĉ/Δa over items): with raw ``/N`` subgradients
   an accuracy deficit of O(1/N) needs O(N²) iterations to move λ.
2. Primal recovery also *repairs* iterates (fix caps, upgrade by reduced cost
   per accuracy gained, one downgrade sweep) on a 1, 2, 4, … plus every-8th
   schedule, then polishes the incumbent by single-move descent. Pure iterates
   of a Lagrangian relaxation are rarely exactly feasible (near-identical
   items flip together under one λ). Hence ``solve_dual`` returns ``[]``
   (with ``gap = inf``) only when neither an iterate nor a repair was feasible.
3. Greedy processes items by **descending** regret (Vogel's rule: the items
   that lose most when denied their cheapest model go first; "ascending" in the
   text defeats the purpose), and an upgrade onto a full model is tried once as
   an accuracy-improving exchange with an item already there.

Pure Python (lists, heapq, math); ties break on ``(cost, model_ref, item_id)``.
"""

from __future__ import annotations

import heapq
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from prismal.core.exceptions import RoutingConfigError
from prismal.core.logging import get_logger
from prismal.routing.types import (
    TIER_ORDER,
    ModelChoice,
    ModelTier,
    TaskFeatures,
    tier_at_least,
    tier_index,
)

if TYPE_CHECKING:
    from prismal.budget.types import BudgetStatus
    from prismal.core.config import Settings
    from prismal.routing.ports import SuccessPredictorPort
    from prismal.routing.types import RoutingOutcome

logger = get_logger(__name__)

PriceFn = Callable[[str, int, int], float]
EligibleFn = Callable[[TaskFeatures, str], bool]
Matrix = Sequence[Sequence[float]]
Mask = Sequence[Sequence[bool]]
Solver = Literal["dual", "greedy", "max_accuracy"]

_TOL = 1e-9
_OSCILLATION_WINDOW = 5
_REPAIR_PERIOD = 8


@dataclass(frozen=True)
class BatchItem:
    """One independent unit of work to route."""

    item_id: str
    features: TaskFeatures


@dataclass(frozen=True)
class BatchAssignment:
    """The solved assignment. Items without any eligible model are absent from ``choices``."""

    choices: Mapping[str, ModelChoice]
    predicted_mean_accuracy: float
    predicted_cost_usd: float
    feasible: bool  # mean >= alpha, caps respected, every item routed
    solver: Solver
    iterations: int
    duality_gap: float | None
    waves: int


# -- solvers -----------------------------------------------------------------------

Row = list[tuple[float, int, float]]  # eligible (cost, j, acc) sorted by (cost, j)


def _rows(cost: Matrix, acc: Matrix, k: int, eligible: Mask | None) -> list[Row]:
    """Per item: eligible ``(cost, j, acc)`` sorted by ``(cost, j)``."""
    return [
        sorted((cost[i][j], j, acc[i][j]) for j in range(k) if eligible is None or eligible[i][j])
        for i in range(len(cost))
    ]


def _assignment_cost(x: Sequence[int], cost: Matrix) -> float:
    return sum(cost[i][j] for i, j in enumerate(x))


def _polish(x: list[int], rows: list[Row], caps: Sequence[int], need: float) -> list[int]:
    """Deterministic single-move descent: apply the largest feasible saving until none."""
    count = [0] * len(caps)
    lookup = [{j: (c, a) for c, j, a in row} for row in rows]
    cur_c = [lookup[i][j][0] for i, j in enumerate(x)]
    cur_a = [lookup[i][j][1] for i, j in enumerate(x)]
    for j in x:
        count[j] += 1
    total_a = sum(cur_a)
    while True:
        best: tuple[float, int, int] | None = None  # (-saving, i, j)
        for i, row in enumerate(rows):
            ci, ai = cur_c[i], cur_a[i]
            for c, j, a in row:
                if c >= ci - _TOL:
                    break  # rows are cost-sorted
                if count[j] >= caps[j] or total_a - ai + a < need - _TOL:
                    continue
                cand = (c - ci, i, j)
                if best is None or cand < best:
                    best = cand
        if best is None:
            return x
        _, i, j = best
        count[x[i]] -= 1
        count[j] += 1
        c, a = lookup[i][j]
        total_a += a - cur_a[i]
        x[i], cur_c[i], cur_a[i] = j, c, a


def _upgrade(
    x: list[int],
    rows: list[Row],
    cost: Matrix,
    acc: Matrix,
    caps: Sequence[int],
    need: float,
) -> bool:
    """Raise mean accuracy in place by Δa/Δĉ max-heap upgrades; True when *need* is met.

    *x* must respect *caps*. ``Δĉ ≤ 0`` upgrades rank first (taken immediately);
    an upgrade onto a full model is tried as an accuracy-improving exchange with
    an item already there, else parked until that model's membership changes.
    """
    k = len(caps)
    members: list[set[int]] = [set() for _ in range(k)]
    for i, j in enumerate(x):
        members[j].add(i)
    total_a = sum(acc[i][j] for i, j in enumerate(x))
    # (-ratio, i, j, from_j, exchange_tried)
    heap: list[tuple[float, int, int, int, bool]] = []
    parked: list[list[tuple[float, int, int, int, bool]]] = [[] for _ in range(k)]

    def push(i: int) -> None:
        """Queue every accuracy-improving upgrade of item *i* by its Δa/Δĉ ratio."""
        cur = x[i]
        a0, c0 = acc[i][cur], cost[i][cur]
        for c, j, a in rows[i]:
            if a > a0 + _TOL:
                dc = c - c0
                ratio = math.inf if dc <= _TOL else (a - a0) / dc
                heapq.heappush(heap, (-ratio, i, j, cur, False))

    elig = [{j for _, j, _ in row} for row in rows]

    def move(i: int, j: int, *, frees: bool) -> None:
        """Reassign item *i* to model *j*; *frees* re-queues upgrades parked on the old model."""
        members[x[i]].discard(i)
        if frees:  # capacity freed on x[i] ⇒ parked upgrades onto it may proceed
            for entry in parked[x[i]]:
                heapq.heappush(heap, entry)
            parked[x[i]].clear()
        members[j].add(i)
        x[i] = j

    for i in range(len(x)):
        push(i)
    while total_a < need - _TOL and heap:
        entry = heapq.heappop(heap)
        neg_ratio, i, j, frm, tried = entry
        if x[i] != frm:  # stale entry: superseded by a later move of item i
            continue
        if len(members[j]) < caps[j]:
            total_a += acc[i][j] - acc[i][frm]
            move(i, j, frees=True)
            push(i)
            continue
        if tried:
            parked[j].append(entry)
            continue
        best_k, best_key = -1, (0.0, 0.0, 0)
        gain_i = acc[i][j] - acc[i][frm]
        for kk in members[j]:
            if frm not in elig[kk]:
                continue
            gain = gain_i + acc[kk][frm] - acc[kk][j]
            if gain <= _TOL:
                continue
            key = (-gain, cost[i][j] - cost[i][frm] + cost[kk][frm] - cost[kk][j], kk)
            if best_k < 0 or key < best_key:
                best_k, best_key = kk, key
        if best_k < 0:  # exchanges are tried once per entry; plain upgrades wait for capacity
            parked[j].append((neg_ratio, i, j, frm, True))
            continue
        total_a += gain_i + acc[best_k][frm] - acc[best_k][j]
        move(best_k, frm, frees=False)
        move(i, j, frees=False)
        push(i)
        push(best_k)
    return total_a >= need - _TOL


def _sweep_polish(
    x: list[int], rows: list[Row], cost: Matrix, acc: Matrix, caps: Sequence[int], need: float
) -> list[int]:
    """One deterministic sweep of single-item downgrades, largest saving first."""
    count = [0] * len(caps)
    for j in x:
        count[j] += 1
    total_a = sum(acc[i][j] for i, j in enumerate(x))
    moves: list[tuple[float, int, int]] = []
    for i, row in enumerate(rows):
        ci = cost[i][x[i]] - _TOL
        for c, j, _ in row:
            if c >= ci:
                break  # rows are cost-sorted
            moves.append((c - ci, i, j))
    moves.sort()
    for _, i, j in moves:
        cur = x[i]
        if cost[i][j] >= cost[i][cur] - _TOL or count[j] >= caps[j]:
            continue
        new_a = total_a - acc[i][cur] + acc[i][j]
        if new_a < need - _TOL:
            continue
        count[cur] -= 1
        count[j] += 1
        total_a = new_a
        x[i] = j
    return x


def _repair(
    x: Sequence[int],
    rows: list[Row],
    cost: Matrix,
    acc: Matrix,
    caps: Sequence[int],
    need: float,
    lam: float,
    mu: Sequence[float],
) -> list[int] | None:
    """Lagrangian-guided primal repair of an iterate (O(N·K log N)); None when it fails.

    Over-cap items move to the eligible model with residual capacity of least
    reduced cost; then items are upgraded in order of reduced-cost increase per
    unit of accuracy gained until the floor holds; then one downgrade sweep.
    """
    y = list(x)
    count = [0] * len(caps)
    for j in y:
        count[j] += 1
    if any(cnt > cp for cnt, cp in zip(count, caps, strict=True)):
        order = sorted(range(len(y)), key=lambda i: (lam * acc[i][y[i]] - cost[i][y[i]], i))
        for i in order:
            j = y[i]
            if count[j] <= caps[j]:
                continue
            options = [
                (c - lam * a + mu[jj], c, jj) for c, jj, a in rows[i] if count[jj] < caps[jj]
            ]
            if not options:
                return None
            jj = min(options)[2]
            count[j] -= 1
            count[jj] += 1
            y[i] = jj
    total_a = sum(acc[i][j] for i, j in enumerate(y))
    if total_a < need - _TOL:
        upgrades: list[tuple[float, int, int]] = []
        for i, row in enumerate(rows):
            cur = y[i]
            a0 = acc[i][cur]
            r0 = cost[i][cur] - lam * a0 + mu[cur]
            for c, j, a in row:
                if a > a0 + _TOL:
                    upgrades.append((max(c - lam * a + mu[j] - r0, 0.0) / (a - a0), i, j))
        upgrades.sort()
        moved: set[int] = set()
        for _, i, j in upgrades:
            if total_a >= need - _TOL:
                break
            if i in moved or count[j] >= caps[j]:
                continue
            cur = y[i]
            count[cur] -= 1
            count[j] += 1
            total_a += acc[i][j] - acc[i][cur]
            y[i] = j
            moved.add(i)
        if total_a < need - _TOL and not _upgrade(y, rows, cost, acc, caps, need):
            return None
    return _sweep_polish(y, rows, cost, acc, caps, need)


def _multiplier_scale(rows: list[Row]) -> float:
    """Typical price of accuracy: median over items of Δĉ/Δa (cheapest → most accurate).

    Multiplies ``η0`` so the step is expressed in the problem's own λ units (with
    normalized costs a fixed ``η0 = 1`` overshoots λ* by orders of magnitude).
    """
    ratios: list[float] = []
    for row in rows:
        c0, _, a0 = row[0]
        c1, _, a1 = max(row, key=lambda r: (r[2], -r[0], -r[1]))
        if a1 > a0 + _TOL:
            ratios.append(max(c1 - c0, 0.0) / (a1 - a0))
    if not ratios:
        return 1.0
    ratios.sort()
    return max(ratios[len(ratios) // 2], 1e-6)


@dataclass
class _Iterate:
    """One Lagrangian subproblem solution: per-item argmin of the reduced cost."""

    x: list[int]
    counts: list[int]
    total_a: float
    total_c: float
    dual: float


def _lagrangian_iterate(
    rows: list[Row], caps: Sequence[int], need: float, lam: float, mu: Sequence[float]
) -> _Iterate:
    """Solve the separable Lagrangian subproblem at ``(λ, μ)`` and its dual value."""
    x: list[int] = []
    counts = [0] * len(caps)
    total_a = total_c = dual = 0.0
    for row in rows:
        values = [c - lam * a + mu[j] for c, j, a in row]
        bv = min(values)
        bc, bj, ba = row[values.index(bv)]  # first minimum ⇒ ties go to (cost, model_ref)
        x.append(bj)
        counts[bj] += 1
        total_a += ba
        total_c += bc
        dual += bv
    dual += lam * need - sum(m * cp for m, cp in zip(mu, caps, strict=True))
    return _Iterate(x, counts, total_a, total_c, dual)


def _is_slack_optimal(
    it: _Iterate, caps: Sequence[int], need: float, lam: float, mu: Sequence[float]
) -> bool:
    """Complementary slackness holds for a feasible iterate ⇒ it is optimal."""
    return abs(lam * (it.total_a - need)) <= _TOL and all(
        abs(m * (cp - cnt)) <= _TOL for m, cp, cnt in zip(mu, caps, it.counts, strict=True)
    )


def _subgradient(
    it: _Iterate, caps: Sequence[int], need: float, n: int, lam: float, mu: Sequence[float]
) -> tuple[float, list[float], float]:
    """Projected subgradient ``(g_λ, g_μ, ‖g‖∞)``; zero multipliers pushed below 0 are inactive."""
    g_lam = (need - it.total_a) / n
    if lam <= 0.0 and g_lam < 0.0:
        g_lam = 0.0
    g_mu = [
        0.0 if m <= 0.0 and cnt < cp else (cnt - cp) / n
        for m, cnt, cp in zip(mu, it.counts, caps, strict=True)
    ]
    norm = max(abs(g_lam), max(abs(g) for g in g_mu))
    return g_lam, g_mu, norm


@dataclass
class _DualSearch:
    """Mutable state of the subgradient search: multipliers, bounds and step control."""

    lam: float
    mu: list[float]
    best_x: list[int] | None = None
    best_cost: float = math.inf
    max_d: float = -math.inf
    stall: int = 0
    shrink: float = 1.0  # halved whenever the dual value oscillates
    next_repair: int = 1  # repair schedule t = 1, 2, 4, 8, …

    def repair_due(self, t: int) -> bool:
        """True on the 1, 2, 4, 8, ... schedule or every ``_REPAIR_PERIOD`` iterations."""
        if t == self.next_repair:
            self.next_repair *= 2
            return True
        return t % _REPAIR_PERIOD == 0

    def track_dual(self, dual: float) -> None:
        """Record a dual value; halve the step scale when it stalls for the oscillation window."""
        if dual > self.max_d + 1e-12:
            self.max_d = dual
            self.stall = 0
        else:  # the dual value oscillates ⇒ halve η0
            self.stall += 1
            if self.stall >= _OSCILLATION_WINDOW:
                self.shrink /= 2.0
                self.stall = 0

    def offer(self, x: list[int], total_c: float) -> None:
        """Keep *x* as the incumbent when it is cheaper than the best so far."""
        if total_c < self.best_cost - 1e-12:
            self.best_cost, self.best_x = total_c, x

    def converged(self, eps: float) -> bool:
        """True when the relative primal/dual gap is within *eps*."""
        return (
            self.best_x is not None
            and (self.best_cost - self.max_d) / max(self.best_cost, 1e-9) <= eps
        )

    def step(self, eta: float, g_lam: float, g_mu: Sequence[float]) -> None:
        """Projected subgradient step on the multipliers (kept non-negative)."""
        self.lam = max(0.0, self.lam + eta * g_lam)
        self.mu = [max(0.0, m + eta * g) for m, g in zip(self.mu, g_mu, strict=True)]


def solve_dual(
    cost: Matrix,
    acc: Matrix,
    caps: Sequence[int],
    alpha: float,
    *,
    max_iter: int,
    eta0: float,
    eps: float,
    eligible: Mask | None = None,
) -> tuple[list[int], int, float]:
    """Lagrangian dual with primal recovery; ``([], iterations, inf)`` when none was found.

    Primal recovery keeps the cheapest feasible among the Lagrangian iterates and
    their repairs (at iterations 1, 2, 4, 8, …), then polishes it.
    """
    n, k = len(cost), len(caps)
    if n == 0:
        return [], 0, 0.0
    rows = _rows(cost, acc, k, eligible)
    if any(not row for row in rows):
        return [], 0, math.inf
    need = n * alpha
    s = _DualSearch(lam=0.0, mu=[0.0] * k)
    scale = _multiplier_scale(rows)
    t = 0
    while t < max_iter:
        it = _lagrangian_iterate(rows, caps, need, s.lam, s.mu)
        t += 1
        due = s.repair_due(t)
        s.track_dual(it.dual)
        cap_ok = all(cnt <= cp for cnt, cp in zip(it.counts, caps, strict=True))
        if cap_ok and it.total_a >= need - _TOL:
            s.offer(it.x, it.total_c)
            if _is_slack_optimal(it, caps, need, s.lam, s.mu):
                s.max_d = max(s.max_d, s.best_cost)
                break
        elif due and it.total_c < s.best_cost - 1e-12:
            # Repair infeasible iterates on a 1, 2, 4, … plus every-_REPAIR_PERIOD schedule
            # when their Lagrangian cost could still beat the incumbent.
            repaired = _repair(it.x, rows, cost, acc, caps, need, s.lam, s.mu)
            if repaired is not None:
                s.offer(repaired, _assignment_cost(repaired, cost))
        if s.converged(eps):
            break
        g_lam, g_mu, norm = _subgradient(it, caps, need, n, s.lam, s.mu)
        if norm <= 1e-15:
            break
        # η_t = η0/sqrt(t+1) on the ∞-normalized active subgradient, in λ units (``scale``):
        # with normalized costs a raw η0 = 1 over/undershoots λ* by orders of magnitude.
        s.step(eta0 * s.shrink * scale / math.sqrt(t + 1) / norm, g_lam, g_mu)
    if s.best_x is None:
        return [], t, math.inf
    polished = _polish(list(s.best_x), rows, caps, need)
    final_cost = _assignment_cost(polished, cost)
    gap = max(0.0, (final_cost - s.max_d) / max(final_cost, 1e-9))
    return polished, t, gap


def solve_greedy(
    cost: Matrix,
    acc: Matrix,
    caps: Sequence[int],
    alpha: float,
    eligible: Mask | None = None,
) -> tuple[list[int], bool]:
    """Regret-ordered cheapest start + Δa/Δĉ heap upgrades; ``(x, reached_alpha)``.

    Returns ``([], False)`` when some item has no eligible model with capacity.
    """
    n, k = len(cost), len(caps)
    if n == 0:
        return [], True
    rows = _rows(cost, acc, k, eligible)
    if any(not row for row in rows):
        return [], False

    def regret(i: int) -> float:
        """Cost gap between item *i*'s two cheapest eligible models (inf if one)."""
        row = rows[i]
        return row[1][0] - row[0][0] if len(row) > 1 else math.inf

    residual = list(caps)
    x = [-1] * n
    for i in sorted(range(n), key=lambda i: (-regret(i), i)):
        for _, j, _ in rows[i]:
            if residual[j] > 0:
                residual[j] -= 1
                x[i] = j
                break
        else:
            return [], False
    need = n * alpha
    if not _upgrade(x, rows, cost, acc, caps, need):
        return x, False
    return _polish(x, rows, caps, need), True


def _max_accuracy(
    cost: Matrix, acc: Matrix, caps: Sequence[int], eligible: Mask | None
) -> list[int]:
    """Capacity-feasible per-item argmax accuracy (largest spread first); -1 = unroutable."""
    n, k = len(cost), len(caps)
    rows = _rows(cost, acc, k, eligible)

    def spread(i: int) -> float:
        """Accuracy range of item *i* across its eligible models (-1 if none)."""
        accs = [a for _, _, a in rows[i]]
        return max(accs) - min(accs) if accs else -1.0

    residual = list(caps)
    x = [-1] * n
    for i in sorted(range(n), key=lambda i: (-spread(i), i)):
        for _, j, _ in sorted(rows[i], key=lambda r: (-r[2], r[0], r[1])):
            if residual[j] > 0:
                residual[j] -= 1
                x[i] = j
                break
    return x


# -- policy ------------------------------------------------------------------------


def _default_price(model_ref: str, tokens_in: int, tokens_out: int) -> float:
    # Deferred, resolved per call (patchable seam); pricing is the only providers import.
    from prismal.providers.cost import compute_cost_usd

    return compute_cost_usd(model_ref, tokens_in, tokens_out).cost_usd


def _count_infeasible() -> None:
    try:
        from prismal.monitoring.otel import OTelManager

        OTelManager().increment_counter(
            "routing_decisions", attributes={"policy": "batch", "tier": "infeasible"}
        )
    except Exception:  # observability never breaks routing
        logger.debug("routing.batch.metric_failed")


class BatchAssignmentPolicy:
    """Cost-minimal batch routing under an accuracy floor and concurrency caps."""

    name = "batch"

    def __init__(
        self,
        *,
        predictor: SuccessPredictorPort,
        candidates: Sequence[str],
        settings: Settings | None = None,
        price_fn: PriceFn | None = None,
        tier_of: Callable[[str], ModelTier] | None = None,
        floor: Mapping[str, str] | None = None,
        capabilities: Mapping[str, Sequence[str]] | None = None,
        eligible_fn: EligibleFn | None = None,
    ) -> None:
        if settings is None:
            from prismal.core.config import get_settings

            settings = get_settings()
        self._settings = settings
        self._predictor = predictor
        self._price = price_fn or _default_price
        self._eligible_fn = eligible_fn
        tiers: dict[str, ModelTier] = {}
        for ref in sorted(set(candidates)):
            tier = self._safe_tier(tier_of, ref)
            if tier is None or tier is ModelTier.AUTO:  # BAT-002 / GW-001: never a candidate
                continue
            tiers[ref] = tier
        if not tiers:
            raise RoutingConfigError(
                "BatchAssignmentPolicy has no candidate with a known, ordered (non-auto) tier."
            )
        self.candidates: tuple[str, ...] = tuple(tiers)
        self._tiers = tiers
        raw_floor = floor if floor is not None else settings.routing_risk_floor
        try:
            self._floor = {risk: ModelTier(t) for risk, t in raw_floor.items()}
        except ValueError as exc:
            raise RoutingConfigError(f"invalid risk floor {dict(raw_floor)!r}") from exc
        if any(t not in TIER_ORDER for t in self._floor.values()):
            raise RoutingConfigError("'auto' is never a risk floor")
        raw_caps = capabilities if capabilities is not None else settings.routing_tier_capabilities
        self._caps = {tier: frozenset(c) for tier, c in raw_caps.items()}

    @staticmethod
    def _safe_tier(tier_of: Callable[[str], ModelTier] | None, ref: str) -> ModelTier | None:
        if tier_of is None:
            return None
        try:
            return ModelTier(tier_of(ref))
        except Exception:
            return None

    # -- eligibility & matrices ----------------------------------------------------

    def _eligible(self, features: TaskFeatures, ref: str) -> bool:
        tier = self._tiers[ref]
        floor = self._floor.get(features.risk_tier.value)
        if floor is not None and not tier_at_least(tier, floor):
            return False
        needed = set()
        if features.needs_tools:
            needed.add("tools")
        if features.needs_structured_output:
            needed.add("structured_output")
        if not needed <= self._caps.get(tier.value, frozenset()):
            return False
        if self._eligible_fn is not None:
            try:
                return bool(self._eligible_fn(features, ref))
            except Exception:  # gateway data policy unknown ⇒ fail closed
                return False
        return True

    def _cap(self, ref: str, concurrency: Mapping[str, int] | None) -> int:
        if concurrency is not None and ref in concurrency:
            return max(0, int(concurrency[ref]))
        cfg = self._settings.routing_model_concurrency
        return max(0, int(cfg.get(ref, self._settings.parallel_max_workers)))

    # -- public API ----------------------------------------------------------------

    def assign(
        self,
        items: Sequence[BatchItem],
        *,
        alpha: float | None = None,
        concurrency: Mapping[str, int] | None = None,
    ) -> BatchAssignment:
        """Solve the batch; never raises (internal errors ⇒ empty, infeasible result)."""
        try:
            return self._assign(items, alpha, concurrency)
        except Exception as exc:
            logger.warning("routing.batch.assign_failed", error=type(exc).__name__)
            _count_infeasible()
            return BatchAssignment({}, 0.0, 0.0, False, "max_accuracy", 0, None, 1)

    def _assign(
        self,
        items: Sequence[BatchItem],
        alpha: float | None,
        concurrency: Mapping[str, int] | None,
    ) -> BatchAssignment:
        target = self._settings.routing_batch_alpha if alpha is None else alpha
        ordered = sorted(items, key=lambda it: it.item_id)
        refs = self.candidates
        caps_base = [self._cap(ref, concurrency) for ref in refs]
        total_cap = sum(caps_base)
        n_all = len(ordered)
        if n_all == 0:
            return BatchAssignment({}, 0.0, 0.0, True, "dual", 0, 0.0, 1)
        if total_cap == 0:
            _count_infeasible()
            return BatchAssignment({}, 0.0, 0.0, False, "max_accuracy", 0, None, 1)
        waves = max(1, math.ceil(n_all / total_cap))
        caps = [c * waves for c in caps_base]

        routable: list[BatchItem] = []
        mask: list[list[bool]] = []
        acc: list[list[float]] = []
        raw: list[list[float]] = []
        for item in ordered:
            row_mask = [self._eligible(item.features, ref) for ref in refs]
            if not any(row_mask):
                continue
            row_a: list[float] = []
            row_c: list[float] = []
            for ref, ok in zip(refs, row_mask, strict=True):
                if not ok:
                    row_a.append(0.0)
                    row_c.append(0.0)
                    continue
                f = item.features
                row_a.append(min(1.0, max(0.0, float(self._predictor.p_success(f, ref)))))
                out = int(self._predictor.expected_output_tokens(f, ref))
                row_c.append(max(0.0, float(self._price(ref, f.est_input_tokens, out))))
            routable.append(item)
            mask.append(row_mask)
            acc.append(row_a)
            raw.append(row_c)
        all_routed = len(routable) == n_all

        cmax = max((c for row in raw for c in row), default=0.0)
        norm = [[c / cmax if cmax > 0 else 0.0 for c in row] for row in raw]
        s = self._settings
        x, iterations, gap_val = solve_dual(
            norm,
            acc,
            caps,
            target,
            max_iter=s.routing_batch_max_iter,
            eta0=1.0,
            eps=s.routing_batch_eps,
            eligible=mask,
        )
        solver: Solver = "dual"
        gap: float | None = gap_val
        reached = bool(x) or not routable
        if routable and not x:
            gap = None
            solver = "greedy"
            x, reached = solve_greedy(norm, acc, caps, target, eligible=mask)
            if not reached:
                solver = "max_accuracy"
                x = _max_accuracy(norm, acc, caps, mask)
        feasible = reached and all_routed and all(j >= 0 for j in x)
        if not feasible:
            _count_infeasible()

        choices: dict[str, ModelChoice] = {}
        total_acc = total_cost = 0.0
        for i, (item, j) in enumerate(zip(routable, x, strict=True)):
            if j < 0:
                continue
            ref = refs[j]
            choices[item.item_id] = ModelChoice(
                model_ref=ref, tier=self._tiers[ref], reason="batch", policy="batch"
            )
            total_acc += acc[i][j]
            total_cost += raw[i][j]
        mean = total_acc / len(choices) if choices else 0.0
        return BatchAssignment(
            choices=choices,
            predicted_mean_accuracy=mean,
            predicted_cost_usd=total_cost,
            feasible=feasible,
            solver=solver,
            iterations=iterations,
            duality_gap=gap,
            waves=waves,
        )

    def select(self, features: TaskFeatures, *, budget: BudgetStatus | None = None) -> ModelChoice:
        """N=1 case; no eligible model ⇒ the most capable candidate (``fallback:no_eligible``)."""
        del budget  # the accuracy floor, not the budget, drives batch routing
        result = self.assign([BatchItem("_select", features)])
        choice = result.choices.get("_select")
        if choice is not None:
            return choice
        ref = min(self.candidates, key=lambda r: (-tier_index(self._tiers[r]), r))
        return ModelChoice(
            model_ref=ref, tier=self._tiers[ref], reason="fallback:no_eligible", policy="batch"
        )

    def on_outcome(self, choice: ModelChoice, outcome: RoutingOutcome) -> ModelChoice | None:
        """Batch choices are final (escalation belongs to CascadePolicy)."""
        del choice, outcome
        return None

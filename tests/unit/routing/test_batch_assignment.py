"""BatchAssignmentPolicy and its solvers (SPEC-AMR-BAT-001, BAT-002; ARCHITECTURE §3.4)."""

from __future__ import annotations

import hashlib
import itertools
import math
import random
from collections.abc import Sequence
from unittest.mock import patch

import pytest

from prismal.core.config import Settings
from prismal.core.exceptions import RoutingConfigError
from prismal.decisions.types import RiskTier
from prismal.monitoring.otel import OTelManager
from prismal.routing.policies.batch_assignment import (
    BatchAssignment,
    BatchAssignmentPolicy,
    BatchItem,
    solve_dual,
    solve_greedy,
)
from prismal.routing.ports import RoutingPolicyPort
from prismal.routing.types import ModelTier, TaskFeatures

Matrix = Sequence[Sequence[float]]


# -- brute force reference ---------------------------------------------------------


def _feasible(
    x: Sequence[int], acc: Matrix, caps: Sequence[int], alpha: float, elig: list[list[bool]] | None
) -> bool:
    n = len(x)
    if any(elig is not None and not elig[i][j] for i, j in enumerate(x)):
        return False
    if any(x.count(j) > c for j, c in enumerate(caps)):
        return False
    return sum(acc[i][j] for i, j in enumerate(x)) >= n * alpha - 1e-9


def _cost(x: Sequence[int], cost: Matrix) -> float:
    return sum(cost[i][j] for i, j in enumerate(x))


def _brute(
    cost: Matrix, acc: Matrix, caps: Sequence[int], alpha: float, elig: list[list[bool]] | None
) -> float | None:
    best: float | None = None
    k = len(caps)
    for x in itertools.product(range(k), repeat=len(cost)):
        if _feasible(x, acc, caps, alpha, elig):
            c = _cost(x, cost)
            best = c if best is None else min(best, c)
    return best


# Hand-crafted feasible fixtures: (cost, acc, caps, alpha, eligible)
Fixture = tuple[list[list[float]], list[list[float]], list[int], float, list[list[bool]] | None]
FIXTURES: list[Fixture] = [
    # cheapest already feasible
    ([[0.1, 1.0], [0.1, 1.0]], [[0.9, 0.95], [0.9, 0.95]], [2, 2], 0.8, None),
    # one upgrade needed — the one with the best Δa/Δc
    (
        [[0.1, 1.0], [0.1, 1.0], [0.1, 1.0]],
        [[0.5, 0.9], [0.6, 0.7], [0.6, 0.65]],
        [3, 3],
        0.6,
        None,
    ),
    # capacity binds on the cheap model
    ([[0.1, 0.5], [0.1, 0.6], [0.1, 0.4]], [[0.8, 0.8], [0.8, 0.8], [0.8, 0.8]], [1, 3], 0.5, None),
    # eligibility forces item 0 onto model 1
    (
        [[0.1, 1.0], [0.1, 1.0]],
        [[0.9, 0.9], [0.9, 0.9]],
        [2, 2],
        0.5,
        [[False, True], [True, True]],
    ),
    # three models, mixed upgrades
    (
        [[0.1, 0.4, 1.0], [0.2, 0.5, 0.9], [0.1, 0.3, 1.0], [0.1, 0.3, 0.8]],
        [[0.3, 0.7, 0.9], [0.4, 0.6, 0.95], [0.5, 0.8, 0.85], [0.2, 0.5, 0.9]],
        [4, 4, 4],
        0.7,
        None,
    ),
]


@pytest.mark.parametrize(("cost", "acc", "caps", "alpha", "elig"), FIXTURES)
def test_dual_equals_brute_force_optimum(
    cost: list[list[float]],
    acc: list[list[float]],
    caps: list[int],
    alpha: float,
    elig: list[list[bool]] | None,
) -> None:
    opt = _brute(cost, acc, caps, alpha, elig)
    assert opt is not None
    x, iters, gap = solve_dual(
        cost, acc, caps, alpha, max_iter=200, eta0=1.0, eps=0.01, eligible=elig
    )
    assert x, "dual must recover a feasible primal on feasible fixtures"
    assert _feasible(x, acc, caps, alpha, elig)
    assert _cost(x, cost) == pytest.approx(opt)
    assert 1 <= iters <= 200
    assert gap >= -1e-9


def test_dual_cheapest_feasible_stops_at_first_iteration() -> None:
    cost, acc, caps, alpha, _ = FIXTURES[0]
    x, iters, gap = solve_dual(cost, acc, caps, alpha, max_iter=200, eta0=1.0, eps=0.01)
    assert x == [0, 0]
    assert iters == 1
    assert gap == pytest.approx(0.0)


def test_dual_repairs_iterates_into_feasible_primal() -> None:
    # One iteration (λ=0, cheapest, infeasible) is enough: the iterate's repair is feasible.
    cost, acc, caps, alpha, _ = FIXTURES[4]
    x, iters, _ = solve_dual(cost, acc, caps, alpha, max_iter=1, eta0=1.0, eps=0.01)
    assert iters == 1
    assert _feasible(x, acc, caps, alpha, None)


def test_dual_infeasible_returns_empty() -> None:
    x, iters, gap = solve_dual(
        [[0.1, 1.0]], [[0.5, 0.6]], [1, 1], 0.9, max_iter=50, eta0=1.0, eps=0.01
    )
    assert x == []
    assert iters == 50
    assert math.isinf(gap)


def test_dual_item_without_eligible_model_returns_empty() -> None:
    x, iters, _ = solve_dual(
        [[0.1]], [[0.9]], [1], 0.5, max_iter=10, eta0=1.0, eps=0.01, eligible=[[False]]
    )
    assert x == []
    assert iters == 0


def test_solvers_empty_input() -> None:
    assert solve_dual([], [], [1], 0.5, max_iter=10, eta0=1.0, eps=0.01) == ([], 0, 0.0)
    assert solve_greedy([], [], [1], 0.5) == ([], True)


@pytest.mark.parametrize(("cost", "acc", "caps", "alpha", "elig"), FIXTURES)
def test_greedy_is_feasible_on_feasible_fixtures(
    cost: list[list[float]],
    acc: list[list[float]],
    caps: list[int],
    alpha: float,
    elig: list[list[bool]] | None,
) -> None:
    x, reached = solve_greedy(cost, acc, caps, alpha, eligible=elig)
    assert reached
    assert _feasible(x, acc, caps, alpha, elig)


def test_greedy_takes_free_upgrade_immediately() -> None:
    # model 1 is both cheaper-or-equal and more accurate for item 0 once cap forces item 1 off it
    x, reached = solve_greedy([[0.5, 0.5], [0.1, 0.2]], [[0.2, 0.9], [0.5, 0.5]], [2, 2], 0.7)
    assert reached
    assert x[0] == 1


def test_greedy_unreachable_alpha_reports_false() -> None:
    x, reached = solve_greedy([[0.1, 1.0], [0.1, 1.0]], [[0.5, 0.6], [0.5, 0.6]], [2, 2], 0.9)
    assert not reached
    assert len(x) == 2


def test_greedy_no_capacity_for_eligible_returns_empty() -> None:
    x, reached = solve_greedy(
        [[0.1, 0.2], [0.1, 0.2]],
        [[0.9, 0.9], [0.9, 0.9]],
        [1, 1],
        0.5,
        eligible=[[True, False], [True, False]],
    )
    assert (x, reached) == ([], False)


def _random_instance(rng: random.Random) -> Fixture:
    n = rng.randint(1, 6)
    k = rng.randint(1, 4)
    base_c = sorted(rng.uniform(0.01, 1.0) for _ in range(k))
    cost = [[round(base_c[j] * rng.uniform(0.8, 1.2), 4) for j in range(k)] for _ in range(n)]
    acc = [
        [
            round(min(1.0, 0.3 + 0.6 * j / max(1, k - 1) * rng.uniform(0.7, 1.1)), 4)
            for j in range(k)
        ]
        for _ in range(n)
    ]
    caps = [rng.randint(0, n) for _ in range(k)]
    if sum(caps) < n:
        caps[-1] += n - sum(caps)
    alpha = round(rng.uniform(0.2, 0.95), 3)
    elig = None
    if rng.random() < 0.3:
        elig = [[rng.random() < 0.8 for _ in range(k)] for _ in range(n)]
    return cost, acc, caps, alpha, elig


def test_property_random_instances_seeded() -> None:
    """Property test (seeded loop; hypothesis is not a dependency of this repo)."""
    rng = random.Random(20261006)
    feasible = exact = 0
    for _ in range(400):
        cost, acc, caps, alpha, elig = _random_instance(rng)
        opt = _brute(cost, acc, caps, alpha, elig)
        x, _, gap = solve_dual(
            cost, acc, caps, alpha, max_iter=200, eta0=1.0, eps=0.01, eligible=elig
        )
        gx, reached = solve_greedy(cost, acc, caps, alpha, eligible=elig)
        if opt is None:
            assert x == []
            assert not reached
            continue
        feasible += 1
        if reached:
            assert _feasible(gx, acc, caps, alpha, elig)
            assert _cost(gx, cost) >= opt - 1e-9
        if x:
            assert _feasible(x, acc, caps, alpha, elig)
            assert _cost(x, cost) >= opt - 1e-9
            assert gap >= -1e-9
        best = min([_cost(x, cost)] * bool(x) + [_cost(gx, cost)] * bool(reached), default=None)
        assert best is not None, "a feasible instance must be solved by dual or greedy"
        if best <= opt + 1e-9:
            exact += 1
    assert feasible >= 100
    assert exact / feasible >= 0.9


def test_solvers_are_deterministic() -> None:
    rng = random.Random(7)
    for _ in range(50):
        cost, acc, caps, alpha, elig = _random_instance(rng)
        args = (cost, acc, caps, alpha)
        assert solve_dual(*args, max_iter=100, eta0=1.0, eps=0.01, eligible=elig) == solve_dual(
            *args, max_iter=100, eta0=1.0, eps=0.01, eligible=elig
        )
        assert solve_greedy(*args, eligible=elig) == solve_greedy(*args, eligible=elig)


# Exact (x, iterations, gap) of ``solve_dual`` over seeded instances and solver settings,
# recorded before ``solve_dual`` was split into helpers (radon E → ≤ D). Any numerical
# drift — a reordered float operation, a changed tie-break, a shifted repair schedule —
# changes the digest. Update it only for an intentional algorithm change.
_SOLVE_DUAL_GOLDEN_DIGEST = "2f4a29ec63bc46a117134a338497d6b6b0643e1a24beb2de1e8ffb69d6495209"


def test_solve_dual_characterization_digest() -> None:
    rng = random.Random(20261010)
    digest = hashlib.sha256()
    for _ in range(300):
        cost, acc, caps, alpha, elig = _random_instance(rng)
        for max_iter, eta0, eps in (
            (1, 1.0, 0.01),
            (7, 0.5, 0.0),
            (200, 1.0, 0.01),
            (60, 2.0, 0.0),
        ):
            result = solve_dual(
                cost, acc, caps, alpha, max_iter=max_iter, eta0=eta0, eps=eps, eligible=elig
            )
            digest.update(repr(result).encode())
    assert digest.hexdigest() == _SOLVE_DUAL_GOLDEN_DIGEST


# -- policy -------------------------------------------------------------------------

_TIERS = {
    "a-nano": ModelTier.NANO,
    "b-small": ModelTier.SMALL,
    "c-medium": ModelTier.MEDIUM,
    "d-frontier": ModelTier.FRONTIER,
    "gw:openrouter/openrouter/auto": ModelTier.AUTO,
}
_ACC = {"a-nano": 0.4, "b-small": 0.6, "c-medium": 0.8, "d-frontier": 0.95}
_PRICE = {"a-nano": 0.001, "b-small": 0.01, "c-medium": 0.05, "d-frontier": 0.2}


class _Pred:
    version = "test"

    def __init__(self, acc: dict[str, float] | None = None) -> None:
        self.acc = acc or _ACC

    def p_success(self, features: TaskFeatures, model_ref: str) -> float:
        return self.acc[model_ref]

    def expected_output_tokens(self, features: TaskFeatures, model_ref: str) -> int:
        return 100

    def update(self, features: TaskFeatures, model_ref: str, success: bool) -> None:
        return None


def _price(model_ref: str, tokens_in: int, tokens_out: int) -> float:
    return _PRICE[model_ref] * (tokens_in + tokens_out) / 1000


def _feat(**kw: object) -> TaskFeatures:
    base: dict[str, object] = {
        "agent": "worker",
        "intent": "research",
        "est_input_tokens": 900,
        "needs_tools": False,
        "needs_structured_output": False,
    }
    base.update(kw)
    return TaskFeatures(**base)  # type: ignore[arg-type]


def _items(n: int, **kw: object) -> list[BatchItem]:
    return [BatchItem(f"o{i}", _feat(**kw)) for i in range(n)]


def _policy(
    candidates: Sequence[str] | None = None, *, settings: Settings | None = None, **kw: object
) -> BatchAssignmentPolicy:
    return BatchAssignmentPolicy(
        predictor=kw.pop("predictor", _Pred()),  # type: ignore[arg-type]
        candidates=list(candidates or _TIERS),
        settings=settings or Settings(),
        price_fn=kw.pop("price_fn", _price),  # type: ignore[arg-type]
        tier_of=kw.pop("tier_of", _TIERS.__getitem__),  # type: ignore[arg-type]
        **kw,  # type: ignore[arg-type]
    )


class TestPolicy:
    def test_conforms_to_port(self) -> None:
        assert isinstance(_policy(), RoutingPolicyPort)
        assert _policy().name == "batch"

    def test_assign_cheapest_meeting_alpha(self) -> None:
        res = _policy().assign(_items(4), alpha=0.6)
        assert isinstance(res, BatchAssignment)
        assert res.feasible
        assert res.solver == "dual"
        assert {c.model_ref for c in res.choices.values()} == {"b-small"}
        choice = res.choices["o0"]
        assert (choice.reason, choice.policy, choice.tier) == ("batch", "batch", ModelTier.SMALL)
        assert res.predicted_mean_accuracy == pytest.approx(0.6)
        assert res.predicted_cost_usd == pytest.approx(4 * 0.01)
        assert res.waves == 1
        assert res.duality_gap is not None

    def test_mixed_assignment_hits_alpha(self) -> None:
        res = _policy().assign(_items(4), alpha=0.7)
        assert res.feasible
        assert res.predicted_mean_accuracy >= 0.7 - 1e-9
        refs = sorted(c.model_ref for c in res.choices.values())
        assert refs == ["b-small", "b-small", "c-medium", "c-medium"]

    def test_alpha_defaults_to_settings(self) -> None:
        res = _policy(settings=Settings(routing_batch_alpha=0.95)).assign(_items(2))
        assert {c.model_ref for c in res.choices.values()} == {"d-frontier"}

    def test_auto_is_never_a_candidate(self) -> None:
        pol = _policy()
        assert "gw:openrouter/openrouter/auto" not in pol.candidates
        res = pol.assign(_items(3), alpha=0.1)
        assert all(c.tier is not ModelTier.AUTO for c in res.choices.values())

    def test_only_auto_candidates_rejected(self) -> None:
        with pytest.raises(RoutingConfigError):
            _policy(["gw:openrouter/openrouter/auto"])

    def test_risk_floor(self) -> None:
        items = [
            BatchItem("crit", _feat(risk_tier=RiskTier.CRITICAL)),
            BatchItem("high", _feat(risk_tier=RiskTier.HIGH)),
            BatchItem("low", _feat()),
        ]
        res = _policy().assign(items, alpha=0.1)
        assert res.choices["crit"].tier is ModelTier.FRONTIER
        assert res.choices["high"].tier is ModelTier.MEDIUM
        assert res.choices["low"].tier is ModelTier.NANO

    def test_floor_override(self) -> None:
        res = _policy(floor={"low": "small"}).assign(_items(1), alpha=0.1)
        assert res.choices["o0"].tier is ModelTier.SMALL

    def test_capabilities_mask(self) -> None:
        res = _policy().assign(
            [
                BatchItem("t", _feat(needs_tools=True)),
                BatchItem("s", _feat(needs_structured_output=True)),
            ],
            alpha=0.1,
        )
        assert res.choices["t"].tier is ModelTier.SMALL
        assert res.choices["s"].tier is ModelTier.SMALL

    def test_capabilities_override(self) -> None:
        res = _policy(capabilities={"nano": ["tools"]}).assign(
            [BatchItem("t", _feat(needs_tools=True))], alpha=0.1
        )
        assert res.choices["t"].tier is ModelTier.NANO

    def test_eligible_fn_masks_and_fails_closed(self) -> None:
        res = _policy(eligible_fn=lambda f, m: m != "a-nano").assign(_items(1), alpha=0.1)
        assert res.choices["o0"].model_ref == "b-small"

        def boom(f: TaskFeatures, m: str) -> bool:
            if m == "b-small":
                raise RuntimeError("policy store down")
            return m != "a-nano"

        res = _policy(eligible_fn=boom).assign(_items(1), alpha=0.1)
        assert res.choices["o0"].model_ref == "c-medium"

    def test_waves_and_concurrency_precedence(self) -> None:
        settings = Settings(
            routing_model_concurrency={"a-nano": 1, "b-small": 1}, parallel_max_workers=2
        )
        pol = _policy(["a-nano", "b-small", "c-medium"], settings=settings)
        # caps 1 + 1 + 2 = 4 → N=9 ⇒ 3 waves
        res = pol.assign(_items(9), alpha=0.1)
        assert res.waves == 3
        refs = [c.model_ref for c in res.choices.values()]
        assert refs.count("a-nano") == 3
        assert refs.count("b-small") == 3
        assert refs.count("c-medium") == 3
        # explicit concurrency wins
        res = pol.assign(_items(4), alpha=0.1, concurrency={"a-nano": 4})
        assert res.waves == 1
        assert {c.model_ref for c in res.choices.values()} == {"a-nano"}

    def test_zero_total_capacity_is_infeasible(self) -> None:
        with patch.object(OTelManager, "increment_counter") as inc:
            res = _policy(["a-nano"]).assign(_items(2), alpha=0.1, concurrency={"a-nano": 0})
        assert not res.feasible
        assert res.choices == {}
        inc.assert_called_with(
            "routing_decisions", attributes={"policy": "batch", "tier": "infeasible"}
        )

    def test_greedy_path_when_dual_finds_nothing(self) -> None:
        pol = _policy()
        with patch(
            "prismal.routing.policies.batch_assignment.solve_dual",
            return_value=([], 200, math.inf),
        ):
            res = pol.assign(_items(4), alpha=0.7)
        assert res.solver == "greedy"
        assert res.iterations == 200
        assert res.feasible
        assert res.duality_gap is None
        assert res.predicted_mean_accuracy >= 0.7 - 1e-9

    def test_unreachable_alpha_returns_max_accuracy(self) -> None:
        with patch.object(OTelManager, "increment_counter") as inc:
            res = _policy().assign(_items(3), alpha=1.0)
        assert not res.feasible
        assert res.solver == "max_accuracy"
        assert {c.model_ref for c in res.choices.values()} == {"d-frontier"}
        inc.assert_called_once_with(
            "routing_decisions", attributes={"policy": "batch", "tier": "infeasible"}
        )

    def test_item_without_eligible_model_left_to_static(self) -> None:
        res = _policy(eligible_fn=lambda f, m: f.agent != "blocked").assign(
            [BatchItem("x", _feat(agent="blocked")), BatchItem("y", _feat())], alpha=0.1
        )
        assert not res.feasible
        assert "x" not in res.choices
        assert res.choices["y"].model_ref == "a-nano"

    def test_internal_error_never_raises(self) -> None:
        class Broken(_Pred):
            def p_success(self, features: TaskFeatures, model_ref: str) -> float:
                raise RuntimeError("boom")

        with patch.object(OTelManager, "increment_counter") as inc:
            res = _policy(predictor=Broken()).assign(_items(2), alpha=0.5)
        assert not res.feasible
        assert res.choices == {}
        inc.assert_called_once()

    def test_empty_batch(self) -> None:
        res = _policy().assign([], alpha=0.9)
        assert res.feasible
        assert res.choices == {}
        assert res.waves == 1

    def test_deterministic_and_candidate_order_independent(self) -> None:
        items = _items(7)
        a = _policy(["d-frontier", "b-small", "a-nano", "c-medium"]).assign(items, alpha=0.75)
        b = _policy(["a-nano", "b-small", "c-medium", "d-frontier"]).assign(items, alpha=0.75)
        c = _policy(["a-nano", "b-small", "c-medium", "d-frontier"]).assign(items, alpha=0.75)
        assert a == b == c

    def test_tie_break_by_model_ref(self) -> None:
        pol = _policy(
            ["z-small", "b-small"],
            predictor=_Pred({"z-small": 0.9, "b-small": 0.9}),
            price_fn=lambda m, i, o: 1.0,
            tier_of=lambda m: ModelTier.SMALL,
        )
        res = pol.assign(_items(2), alpha=0.5)
        assert {c.model_ref for c in res.choices.values()} == {"b-small"}

    def test_default_price_fn_uses_compute_cost_usd(self) -> None:
        settings = Settings()  # before importing providers (LiteLLM leaks .env on import)
        from prismal.providers.cost import CostEstimate

        calls: list[tuple[str, int, int]] = []

        def fake(
            model: str, prompt_tokens: int, completion_tokens: int, **_: object
        ) -> CostEstimate:
            calls.append((model, prompt_tokens, completion_tokens))
            return CostEstimate(_PRICE[model], "table")

        with patch("prismal.providers.cost.compute_cost_usd", fake):
            pol = BatchAssignmentPolicy(
                predictor=_Pred(),  # type: ignore[arg-type]
                candidates=["a-nano", "b-small"],
                settings=settings,
                tier_of=_TIERS.__getitem__,
            )
            res = pol.assign(_items(1), alpha=0.5)
        assert res.choices["o0"].model_ref == "b-small"
        assert ("a-nano", 900, 100) in calls

    def test_unresolvable_tiers_fail_closed(self) -> None:
        # Without tier_of no tier (hence no risk floor) can be established ⇒ refuse.
        with pytest.raises(RoutingConfigError):
            BatchAssignmentPolicy(
                predictor=_Pred(),  # type: ignore[arg-type]
                candidates=["a-nano"],
                settings=Settings(),
                price_fn=_price,
            )

    def test_select_single_item(self) -> None:
        choice = _policy().select(_feat(), budget=None)
        assert choice.reason == "batch"
        assert choice.model_ref == "c-medium"  # α=0.8 default

    def test_select_without_eligible_falls_back_to_most_capable(self) -> None:
        choice = _policy(eligible_fn=lambda f, m: False).select(_feat())
        assert choice.model_ref == "d-frontier"
        assert choice.reason.startswith("fallback:")
        assert choice.policy == "batch"

    def test_on_outcome_never_escalates(self) -> None:
        pol = _policy()
        assert pol.on_outcome(pol.select(_feat()), None) is None  # type: ignore[arg-type]

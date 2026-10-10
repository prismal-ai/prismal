"""Table success predictor (Phase AMR — SPEC-AMR-PRD-001, DD-AMR-005).

A Beta-Binomial table keyed by ``(intent or "_", tier)``:
``p_success = (s + a) / (n + a + b)``. A missing cell falls back to the
posterior mean of the aggregated tier row, and an empty row to the prior
``a / (a + b)``. Output length comes from a median table.

Deviation from the SPEC text: the SPEC names a ``DecisionPort`` Score blend,
but ``DecisionPort`` is async while ``SuccessPredictorPort`` is sync and on the
hot path. The blend therefore takes a **sync** seam
``score_fn(features, model_ref) -> float | None`` (callers adapt their
DecisionPort outside the hot path); ``None`` or an exception keeps the table
value. ``update()`` gains an optional ``served_model`` so outcomes are
attributed to the tier of the model the gateway actually served.

The eval exporter reads :class:`~prismal.eval.types.Scorecard`, which carries
neither the model nor the intent: the caller passes ``model_ref`` (one
scorecard = one model run) and optionally the :class:`EvalCase` list, whose
``intent:<name>`` tag gives the intent (untagged ⇒ ``"_"``). Output tokens are
``trajectory.tokens - trajectory.prompt_tokens`` when the split is known.
"""

from __future__ import annotations

import json
import re
import statistics
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from prismal.core.exceptions import RoutingConfigError
from prismal.core.logging import get_logger
from prismal.routing.types import TIER_ORDER, ModelTier, TaskFeatures

if TYPE_CHECKING:
    from prismal.eval.types import EvalCase, Scorecard

logger = get_logger(__name__)

ANY_INTENT = "_"
_INTENT_TAG = "intent:"
_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

ScoreFn = Callable[[TaskFeatures, str], float | None]
TierOf = Callable[[str], ModelTier]


@dataclass(frozen=True)
class PredictorCell:
    """Observed outcomes for one ``(intent, tier)`` cell."""

    successes: int
    trials: int
    median_output_tokens: int = 0


def _clamp01(x: float) -> float:
    return min(1.0, max(0.0, x))


class TableSuccessPredictor:
    """Beta-Binomial ``(intent, tier)`` predictor; conforms to ``SuccessPredictorPort``."""

    def __init__(
        self,
        table: Mapping[tuple[str, ModelTier], PredictorCell],
        *,
        tier_of: TierOf,
        prior_a: float = 1.0,
        prior_b: float = 1.0,
        score_fn: ScoreFn | None = None,
        score_weight: float = 0.0,
        version: str = "v0",
        default_output_tokens: int = 512,
    ) -> None:
        if prior_a <= 0.0 or prior_b <= 0.0:
            raise ValueError("prior_a and prior_b must be > 0")
        if not 0.0 <= score_weight <= 1.0:
            raise ValueError("score_weight must be in [0, 1]")
        self.version = version
        self._tier_of = tier_of
        self._a = prior_a
        self._b = prior_b
        self._score_fn = score_fn
        self._w = score_weight
        self._default_tokens = max(1, default_output_tokens)
        self._lock = threading.Lock()
        # (intent, tier) -> [successes, trials]; medians are static (from the table).
        self._counts: dict[tuple[str, ModelTier], list[int]] = {
            key: [cell.successes, cell.trials] for key, cell in table.items()
        }
        self._medians: dict[tuple[str, ModelTier], int] = {
            key: cell.median_output_tokens
            for key, cell in table.items()
            if cell.median_output_tokens > 0
        }

    # -- helpers ---------------------------------------------------------------

    def _tier(self, model_ref: str) -> ModelTier | None:
        try:
            tier = self._tier_of(model_ref)
        except Exception:  # unknown model ⇒ prior, never raise on the hot path
            return None
        return tier if tier in TIER_ORDER else None

    @staticmethod
    def _intent(features: TaskFeatures) -> str:
        return features.intent or ANY_INTENT

    def _table_p(self, intent: str, tier: ModelTier | None) -> float:
        a, b = self._a, self._b
        if tier is None:
            return a / (a + b)
        with self._lock:
            cell = self._counts.get((intent, tier))
            if cell is not None:
                s, n = cell
            else:
                s = n = 0
                for (_, t), (cs, cn) in self._counts.items():
                    if t is tier:
                        s += cs
                        n += cn
        return _clamp01((s + a) / (n + a + b))

    # -- SuccessPredictorPort --------------------------------------------------

    def p_success(self, features: TaskFeatures, model_ref: str) -> float:
        """Posterior mean P(success), optionally blended with ``score_fn``."""
        p = self._table_p(self._intent(features), self._tier(model_ref))
        if self._score_fn is None or self._w <= 0.0:
            return p
        try:
            score = self._score_fn(features, model_ref)
        except Exception as exc:
            logger.debug("routing.predictor.score_failed", error=type(exc).__name__)
            return p
        if score is None:
            return p
        return _clamp01((1.0 - self._w) * p + self._w * _clamp01(float(score)))

    def expected_output_tokens(self, features: TaskFeatures, model_ref: str) -> int:
        """Cell median ⇒ tier-row median ⇒ ``default_output_tokens``."""
        tier = self._tier(model_ref)
        if tier is None:
            return self._default_tokens
        median = self._medians.get((self._intent(features), tier))
        if median is not None:
            return median
        row = [m for (_, t), m in self._medians.items() if t is tier]
        if row:
            return int(statistics.median(row))
        return self._default_tokens

    def update(
        self,
        features: TaskFeatures,
        model_ref: str,
        success: bool,
        *,
        served_model: str | None = None,
    ) -> None:
        """Record one outcome; keyed by ``served_model``'s tier when it differs."""
        ref = served_model if served_model and served_model != model_ref else model_ref
        tier = self._tier(ref)
        if tier is None:
            return
        key = (self._intent(features), tier)
        with self._lock:
            cell = self._counts.setdefault(key, [0, 0])
            cell[0] += int(success)
            cell[1] += 1


# -- versioned JSON table --------------------------------------------------------


def _parse_tier(raw: object, where: str) -> ModelTier:
    try:
        tier = ModelTier(str(raw))
    except ValueError:
        raise RoutingConfigError(f"{where}: unknown tier {raw!r}") from None
    if tier not in TIER_ORDER:
        raise RoutingConfigError(f"{where}: tier 'auto' cannot appear in a predictor table")
    return tier


def _parse_cell(raw: object, idx: int) -> tuple[tuple[str, ModelTier], PredictorCell]:
    where = f"cells[{idx}]"
    if not isinstance(raw, dict):
        raise RoutingConfigError(f"{where}: expected an object")
    intent = raw.get("intent")
    if not isinstance(intent, str) or not intent:
        raise RoutingConfigError(f"{where}: missing intent")
    tier = _parse_tier(raw.get("tier"), where)
    try:
        s = int(raw.get("successes", 0))
        n = int(raw["trials"])
        med = int(raw.get("median_output_tokens", 0))
    except (KeyError, TypeError, ValueError):
        raise RoutingConfigError(f"{where}: successes/trials must be integers") from None
    if n < 0 or s < 0 or s > n or med < 0:
        raise RoutingConfigError(f"{where}: need 0 <= successes <= trials, tokens >= 0")
    return (intent, tier), PredictorCell(s, n, med)


def load_predictor_table(
    path: str,
    *,
    tier_of: TierOf | None = None,
    score_fn: ScoreFn | None = None,
    score_weight: float = 0.0,
) -> TableSuccessPredictor:
    """Load ``config/routing_predictor/<version>.json`` (fail-closed: ``RoutingConfigError``).

    Model tiers come from the file's ``model_tiers`` map, then the injected
    *tier_of*; a model known to neither gets the prior.
    """
    try:
        payload: Any = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RoutingConfigError(f"cannot read predictor table {path!r}: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("version"), str):
        raise RoutingConfigError(f"{path}: expected an object with a string 'version'")
    raw_cells = payload.get("cells", [])
    if not isinstance(raw_cells, list):
        raise RoutingConfigError(f"{path}: 'cells' must be a list")
    table = dict(_parse_cell(raw, i) for i, raw in enumerate(raw_cells))
    raw_tiers = payload.get("model_tiers", {})
    if not isinstance(raw_tiers, dict):
        raise RoutingConfigError(f"{path}: 'model_tiers' must be an object")
    model_tiers = {str(m): _parse_tier(t, f"model_tiers[{m!r}]") for m, t in raw_tiers.items()}
    prior = payload.get("prior", {})
    prior_a = float(prior.get("a", 1.0)) if isinstance(prior, dict) else 1.0
    prior_b = float(prior.get("b", 1.0)) if isinstance(prior, dict) else 1.0
    fallback = tier_of

    def _resolve(model_ref: str) -> ModelTier:
        tier = model_tiers.get(model_ref)
        if tier is not None:
            return tier
        if fallback is None:
            raise KeyError(model_ref)
        return fallback(model_ref)

    try:
        return TableSuccessPredictor(
            table,
            tier_of=_resolve,
            prior_a=prior_a,
            prior_b=prior_b,
            score_fn=score_fn,
            score_weight=score_weight,
            version=payload["version"],
        )
    except ValueError as exc:
        raise RoutingConfigError(f"{path}: {exc}") from exc


def _intent_of_case(case: EvalCase) -> str:
    for tag in case.tags:
        if tag.startswith(_INTENT_TAG) and len(tag) > len(_INTENT_TAG):
            return tag[len(_INTENT_TAG) :]
    return ANY_INTENT


def export_predictor_table(
    report: Scorecard,
    *,
    out_dir: str,
    tier_of: TierOf,
    model_ref: str,
    cases: Sequence[EvalCase] | None = None,
    version: str | None = None,
) -> str:
    """Write ``<out_dir>/<version>.json`` from one eval run of *model_ref*; return its path.

    Intent per case = the ``intent:<name>`` tag of the matching *cases* entry
    (else ``"_"``); success = ``CaseResult.passed``. Default version is
    ``"<suite>-<scorecard version>"``.
    """
    tier = tier_of(model_ref)
    if tier not in TIER_ORDER:
        raise RoutingConfigError(f"model {model_ref!r} has non-ordered tier {tier!r}")
    ver = version or f"{report.suite}-{report.version}"
    if not _VERSION_RE.match(ver):
        raise RoutingConfigError(f"invalid predictor table version {ver!r}")
    intents = {c.id: _intent_of_case(c) for c in cases or ()}
    agg: dict[str, list[int]] = {}
    outputs: dict[str, list[int]] = {}
    for result in report.cases:
        intent = intents.get(result.case_id, ANY_INTENT)
        cell = agg.setdefault(intent, [0, 0])
        cell[0] += int(result.passed)
        cell[1] += 1
        traj = result.trajectory
        if traj.prompt_tokens > 0 and traj.tokens >= traj.prompt_tokens:
            outputs.setdefault(intent, []).append(traj.tokens - traj.prompt_tokens)
    cells = [
        {
            "intent": intent,
            "tier": tier.value,
            "successes": s,
            "trials": n,
            "median_output_tokens": int(statistics.median(outputs[intent]))
            if outputs.get(intent)
            else 0,
        }
        for intent, (s, n) in sorted(agg.items())
    ]
    payload = {
        "version": ver,
        "source": {"suite": report.suite, "scorecard_version": report.version},
        "prior": {"a": 1.0, "b": 1.0},
        "model_tiers": {model_ref: tier.value},
        "cells": cells,
    }
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{ver}.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return str(path)

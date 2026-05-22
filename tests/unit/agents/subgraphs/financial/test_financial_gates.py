# ruff: noqa: S106 -- ``on_pass`` is a routing target, not a password
"""Tests for the Phase 39 / SPEC-041 financial quality gates.

Covers AC-041-2 (data-availability gate), AC-041-3 (technical
confidence gate), AC-041-4 (optional HITL gate) and the supporting
scoring helpers added to ``market_data_collector`` and
``technical_analyst``.

The pipeline's actual LLM-driven nodes are never invoked — we drive
the gates directly with hand-built state dicts and patch
``get_settings`` where needed to exercise the HITL branches. Topology
assertions are done against the un-compiled ``SubgraphDefinition`` so
the tests stay fast and independent of the SqliteSaver lifecycle.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from prismal.agents.subgraphs.financial.artifacts import (
    MarketSnapshot,
    TechnicalAnalysis,
)
from prismal.agents.subgraphs.financial.builder import (
    _FINANCIAL_METADATA_KEY,
    _insufficient_data_report_node,
    _limited_technical_disclaimer_node,
    _make_definition,
    _market_data_availability_gate,
    _technical_confidence_gate,
)
from prismal.agents.subgraphs.financial.market_data_collector import (
    _EXPECTED_MARKET_FIELDS,
    _score_market_snapshot,
)
from prismal.agents.subgraphs.financial.technical_analyst import (
    _score_technical_indicators,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _market_state(*, missing_fields: list[str], data_confidence: float) -> dict[str, Any]:
    """Build a state dict whose market_snapshot has the given quality fields."""
    return {
        "metadata": {
            _FINANCIAL_METADATA_KEY: {
                "market_snapshot": {
                    "symbol": "AAPL",
                    "asset_type": "equity",
                    "missing_fields": missing_fields,
                    "data_confidence": data_confidence,
                }
            }
        }
    }


def _technical_state(*, data_confidence: float) -> dict[str, Any]:
    """Build a state dict whose technical_analysis has the given confidence."""
    return {
        "metadata": {
            _FINANCIAL_METADATA_KEY: {
                "technical_analysis": {
                    "symbol": "AAPL",
                    "indicators": {"RSI": 50.0},
                    "data_confidence": data_confidence,
                }
            }
        }
    }


# ---------------------------------------------------------------------------
# Market-data scorer (T-333)
# ---------------------------------------------------------------------------


class TestScoreMarketSnapshot:
    """AC-041-1: the collector computes confidence + missing fields."""

    def test_full_snapshot_scores_one(self) -> None:
        snap = MarketSnapshot(
            symbol="AAPL",
            asset_type="equity",
            current_price=175.5,
            currency="USD",
            ohlcv_path="data/workspace/financial/AAPL/ohlcv.parquet",
            data_provider="yfinance",
            data_points_count=180,
            market_cap=2.7e12,
            volume_24h=55_321_000,
        )
        conf, missing = _score_market_snapshot(snap)
        assert conf == 1.0
        assert missing == []

    def test_minimal_snapshot_scores_partial(self) -> None:
        """Pydantic defaults (``currency``, ``data_provider``) still count."""
        snap = MarketSnapshot(symbol="UNKNOWN", asset_type="equity")
        conf, missing = _score_market_snapshot(snap)
        assert abs(conf - 2 / 7) < 0.01
        assert set(missing) == {
            "current_price",
            "ohlcv_path",
            "data_points_count",
            "market_cap",
            "volume_24h",
        }

    def test_zero_price_counts_as_missing(self) -> None:
        snap = MarketSnapshot(symbol="AAPL", asset_type="equity", current_price=0.0)
        _, missing = _score_market_snapshot(snap)
        assert "current_price" in missing

    def test_unknown_provider_counts_as_missing(self) -> None:
        snap = MarketSnapshot(
            symbol="AAPL",
            asset_type="equity",
            data_provider="UNKNOWN",
        )
        _, missing = _score_market_snapshot(snap)
        assert "data_provider" in missing

    def test_partial_snapshot_scores_proportionally(self) -> None:
        snap = MarketSnapshot(
            symbol="AAPL",
            asset_type="equity",
            current_price=175.5,
            currency="USD",
            data_provider="yfinance",
            data_points_count=100,
        )
        conf, missing = _score_market_snapshot(snap)
        assert abs(conf - 4 / 7) < 0.01
        assert set(missing) == {"ohlcv_path", "market_cap", "volume_24h"}

    def test_every_expected_field_is_considered(self) -> None:
        """Guard against someone silently renaming a field."""
        assert len(_EXPECTED_MARKET_FIELDS) == 7


# ---------------------------------------------------------------------------
# Technical indicator scorer (T-333)
# ---------------------------------------------------------------------------


class TestScoreTechnicalIndicators:
    def test_empty_indicators_zero(self) -> None:
        assert _score_technical_indicators({}) == 0.0

    def test_all_seven_families(self) -> None:
        indicators = {
            "RSI": 50.0,
            "MACD": 0.1,
            "BB_upper": 180.0,
            "SMA_20": 170.0,
            "EMA_20": 172.0,
            "Stoch_K": 60.0,
            "ADX": 22.0,
        }
        assert _score_technical_indicators(indicators) == 1.0

    def test_three_families(self) -> None:
        c = _score_technical_indicators({"RSI": 50.0, "MACD": 0.1, "SMA_20": 100.0})
        assert abs(c - 3 / 7) < 0.01

    def test_duplicates_in_same_family_not_inflated(self) -> None:
        """Three BB_* entries must still count as a single family."""
        c = _score_technical_indicators({"BB_upper": 180.0, "BB_lower": 165.0, "BB_middle": 172.5})
        assert abs(c - 1 / 7) < 0.01

    def test_non_canonical_indicators_ignored(self) -> None:
        c = _score_technical_indicators({"RSI": 50.0, "OBV": 1_000_000, "ATR": 2.1})
        # Only RSI matches a canonical family.
        assert abs(c - 1 / 7) < 0.01

    def test_case_insensitive_and_alias_match(self) -> None:
        c = _score_technical_indicators({"rsi": 50.0, "macd": 0.1, "bollinger_upper": 180.0})
        assert abs(c - 3 / 7) < 0.01


# ---------------------------------------------------------------------------
# Market-data availability gate (AC-041-2)
# ---------------------------------------------------------------------------


class TestMarketDataGate:
    def test_low_confidence_aborts_at_data_gate(self) -> None:
        """``missing_fields`` non-empty AND conf < threshold → ``on_fail``."""
        gate = _market_data_availability_gate(
            on_pass="technical_analyst",
            on_fail="insufficient_data_report",
            min_confidence=0.4,
        )
        state = _market_state(
            missing_fields=["current_price", "volume_24h"],
            data_confidence=0.2,
        )
        assert gate(state) == "insufficient_data_report"

    def test_missing_fields_but_high_confidence_still_passes(self) -> None:
        """A single missing field with high confidence is not a blocker."""
        gate = _market_data_availability_gate(
            on_pass="technical_analyst",
            on_fail="insufficient_data_report",
            min_confidence=0.4,
        )
        state = _market_state(missing_fields=["market_cap"], data_confidence=0.9)
        assert gate(state) == "technical_analyst"

    def test_low_confidence_no_missing_fields_passes(self) -> None:
        """Without missing fields the gate always passes, even at low conf."""
        gate = _market_data_availability_gate(
            on_pass="technical_analyst",
            on_fail="insufficient_data_report",
            min_confidence=0.4,
        )
        state = _market_state(missing_fields=[], data_confidence=0.1)
        assert gate(state) == "technical_analyst"

    def test_missing_snapshot_routes_to_fail(self) -> None:
        gate = _market_data_availability_gate(
            on_pass="technical_analyst",
            on_fail="insufficient_data_report",
            min_confidence=0.4,
        )
        assert gate({"metadata": {}}) == "insufficient_data_report"

    def test_non_numeric_confidence_treated_as_zero(self) -> None:
        """Malformed snapshots must not crash the gate."""
        gate = _market_data_availability_gate(
            on_pass="technical_analyst",
            on_fail="insufficient_data_report",
            min_confidence=0.4,
        )
        state = {
            "metadata": {
                _FINANCIAL_METADATA_KEY: {
                    "market_snapshot": {
                        "missing_fields": ["current_price"],
                        "data_confidence": "not-a-number",
                    }
                }
            }
        }
        # confidence_f falls back to 0.0 → 0.0 < 0.4 AND missing non-empty
        assert gate(state) == "insufficient_data_report"

    def test_threshold_read_from_settings_at_call_time(self) -> None:
        """Runtime settings changes are honoured when ``min_confidence`` is None."""
        gate = _market_data_availability_gate(
            on_pass="technical_analyst",
            on_fail="insufficient_data_report",
        )
        state = _market_state(missing_fields=["market_cap"], data_confidence=0.5)
        with patch("prismal.agents.subgraphs.financial.builder.get_settings") as mock_settings:
            mock_settings.return_value.financial_min_confidence = 0.3
            # 0.5 >= 0.3 → pass
            assert gate(state) == "technical_analyst"
        with patch("prismal.agents.subgraphs.financial.builder.get_settings") as mock_settings:
            mock_settings.return_value.financial_min_confidence = 0.9
            # 0.5 < 0.9 AND missing non-empty → fail
            assert gate(state) == "insufficient_data_report"


# ---------------------------------------------------------------------------
# Technical confidence gate (AC-041-3)
# ---------------------------------------------------------------------------


class TestTechnicalConfidenceGate:
    def test_low_technical_confidence_skips_fundamental(self) -> None:
        """``confidence < threshold`` → route to the limited-data disclaimer."""
        gate = _technical_confidence_gate(
            on_full="fundamental_analyst",
            on_limited="limited_technical_disclaimer",
            min_confidence=0.6,
        )
        assert gate(_technical_state(data_confidence=0.3)) == "limited_technical_disclaimer"

    def test_high_technical_confidence_runs_fundamental(self) -> None:
        gate = _technical_confidence_gate(
            on_full="fundamental_analyst",
            on_limited="limited_technical_disclaimer",
            min_confidence=0.6,
        )
        assert gate(_technical_state(data_confidence=0.8)) == "fundamental_analyst"

    def test_confidence_exactly_at_threshold_passes(self) -> None:
        gate = _technical_confidence_gate(
            on_full="fundamental_analyst",
            on_limited="limited_technical_disclaimer",
            min_confidence=0.6,
        )
        assert gate(_technical_state(data_confidence=0.6)) == "fundamental_analyst"

    def test_missing_technical_artifact_limits(self) -> None:
        gate = _technical_confidence_gate(
            on_full="fundamental_analyst",
            on_limited="limited_technical_disclaimer",
            min_confidence=0.6,
        )
        assert gate({"metadata": {}}) == "limited_technical_disclaimer"

    def test_non_numeric_confidence_treated_as_zero(self) -> None:
        gate = _technical_confidence_gate(
            on_full="fundamental_analyst",
            on_limited="limited_technical_disclaimer",
            min_confidence=0.6,
        )
        state = {
            "metadata": {
                _FINANCIAL_METADATA_KEY: {
                    "technical_analysis": {
                        "data_confidence": None,
                    }
                }
            }
        }
        assert gate(state) == "limited_technical_disclaimer"


# ---------------------------------------------------------------------------
# Dead-end / disclaimer nodes
# ---------------------------------------------------------------------------


class TestInsufficientDataReportNode:
    async def test_emits_structured_error_report(self) -> None:
        state = {
            "metadata": {
                _FINANCIAL_METADATA_KEY: {
                    "market_snapshot": {
                        "symbol": "AAPL",
                        "missing_fields": ["current_price", "volume_24h"],
                    }
                }
            }
        }
        out = await _insufficient_data_report_node(state)

        assert out["current_agent"] == "insufficient_data_report"
        fin = out["metadata"][_FINANCIAL_METADATA_KEY]
        report = fin["financial_report"]
        assert report["symbol"] == "AAPL"
        assert "Insufficient" in report["executive_summary"]
        assert "Error" in report["sections"]
        assert "current_price" in report["sections"]["Missing Fields"]
        assert fin["insufficient_data"] is True
        # Mandatory legal disclaimer present.
        assert "informational purposes only" in report["disclaimer"]
        # User-facing message.
        assert len(out["messages"]) == 1
        assert "Insufficient" in out["messages"][0].content

    async def test_handles_missing_snapshot_gracefully(self) -> None:
        """When upstream state is malformed the node still produces a report."""
        out = await _insufficient_data_report_node({"metadata": {}})
        fin = out["metadata"][_FINANCIAL_METADATA_KEY]
        assert fin["financial_report"]["symbol"] == "UNKNOWN"
        assert fin["insufficient_data"] is True


class TestLimitedTechnicalDisclaimerNode:
    async def test_flags_state_without_mutating_messages(self) -> None:
        out = await _limited_technical_disclaimer_node({"metadata": {_FINANCIAL_METADATA_KEY: {}}})
        assert out["current_agent"] == "limited_technical_disclaimer"
        # Passthrough — no user-facing message.
        assert "messages" not in out
        fin = out["metadata"][_FINANCIAL_METADATA_KEY]
        assert fin["limited_data_disclaimer"] is True
        assert "limited data" in fin["limited_data_note"]

    async def test_preserves_existing_metadata(self) -> None:
        out = await _limited_technical_disclaimer_node(
            {
                "metadata": {
                    _FINANCIAL_METADATA_KEY: {
                        "technical_analysis": {"data_confidence": 0.3},
                        "market_snapshot": {"symbol": "AAPL"},
                    }
                }
            }
        )
        fin = out["metadata"][_FINANCIAL_METADATA_KEY]
        assert fin["technical_analysis"]["data_confidence"] == 0.3
        assert fin["market_snapshot"]["symbol"] == "AAPL"
        assert fin["limited_data_disclaimer"] is True


# ---------------------------------------------------------------------------
# Topology assertions — _make_definition()
# ---------------------------------------------------------------------------


class TestFinancialTopology:
    def _definition_with_settings(self, *, hitl: bool) -> Any:
        with patch("prismal.agents.subgraphs.financial.builder.get_settings") as mock_settings:
            mock_settings.return_value.financial_hitl_enabled = hitl
            mock_settings.return_value.financial_min_confidence = 0.4
            mock_settings.return_value.financial_technical_min_confidence = 0.6
            return _make_definition()

    def test_baseline_topology_has_quality_nodes(self) -> None:
        d = self._definition_with_settings(hitl=False)
        assert "insufficient_data_report" in d.nodes
        assert "limited_technical_disclaimer" in d.nodes
        # Conditional edges at both quality checkpoints.
        assert "market_data_collector" in d.conditional_edges
        assert "technical_analyst" in d.conditional_edges
        # Without HITL the report generator ends the pipeline directly.
        assert ("report_generator", "__end__") in d.edges
        assert "financial_report_human_approval" not in d.nodes

    def test_full_confidence_pipeline_completes_normally(self) -> None:
        """When every gate passes the pipeline walks to ``report_generator``."""
        d = self._definition_with_settings(hitl=False)

        market_gate = d.conditional_edges["market_data_collector"]
        tech_gate = d.conditional_edges["technical_analyst"]

        assert (
            market_gate(_market_state(missing_fields=[], data_confidence=1.0))
            == "technical_analyst"
        )
        assert tech_gate(_technical_state(data_confidence=1.0)) == "fundamental_analyst"
        # Fundamental → risk_sentiment → report_generator → __end__
        assert ("fundamental_analyst", "risk_sentiment_analyst") in d.edges
        assert ("risk_sentiment_analyst", "report_generator") in d.edges

    def test_hitl_gate_triggered_when_financial_hitl_enabled(self) -> None:
        """AC-041-4: HITL topology adds approval nodes and a gate edge."""
        d = self._definition_with_settings(hitl=True)

        assert "financial_report_approval_seed" in d.nodes
        assert "financial_report_human_approval" in d.nodes
        assert "financial_report_human_approval" in d.conditional_edges
        # Report generator now walks into the approval seed, not END.
        assert (
            "report_generator",
            "financial_report_approval_seed",
        ) in d.edges
        assert (
            "financial_report_approval_seed",
            "financial_report_human_approval",
        ) in d.edges
        # The non-HITL direct edge must NOT be present simultaneously.
        assert ("report_generator", "__end__") not in d.edges

    def test_missing_fields_triggers_end_routing(self) -> None:
        """End-to-end: low-quality snapshot → gate routes to error node → END."""
        d = self._definition_with_settings(hitl=False)
        market_gate = d.conditional_edges["market_data_collector"]

        state = _market_state(
            missing_fields=["current_price", "ohlcv_path"],
            data_confidence=0.15,
        )
        # 1) gate routes to insufficient_data_report
        assert market_gate(state) == "insufficient_data_report"
        # 2) that node has a direct edge to __end__
        assert ("insufficient_data_report", "__end__") in d.edges


# ---------------------------------------------------------------------------
# TechnicalAnalysis artifact integration (scoring → confidence field)
# ---------------------------------------------------------------------------


class TestTechnicalAnalysisArtifact:
    """Verify the scorer output can be stamped into the artifact correctly."""

    def test_confidence_bounds_respected(self) -> None:
        full = {
            "RSI": 50.0,
            "MACD": 0.1,
            "BB_upper": 180.0,
            "SMA_20": 170.0,
            "EMA_20": 172.0,
            "Stoch_K": 60.0,
            "ADX": 22.0,
        }
        score = _score_technical_indicators(full)
        analysis = TechnicalAnalysis(
            symbol="AAPL",
            indicators=full,
        ).model_copy(update={"data_confidence": score})
        assert 0.0 <= analysis.data_confidence <= 1.0
        assert analysis.data_confidence == 1.0

    def test_zero_confidence_valid(self) -> None:
        analysis = TechnicalAnalysis(symbol="AAPL").model_copy(
            update={"data_confidence": _score_technical_indicators({})}
        )
        assert analysis.data_confidence == 0.0

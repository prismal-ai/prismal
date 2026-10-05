"""TKE metrics registered in OTelManager (Phase TKE — SPEC-TKE-OTEL-001)."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from prismal.monitoring.otel import OTelManager

_TKE_COUNTERS = {
    "llm_input_tokens": "prismal.llm_input_tokens_total",
    "llm_reasoning_tokens": "prismal.llm_reasoning_tokens_total",
    "prompt_cache_breakpoints": "prismal.prompt_cache_breakpoints_total",
    "prompt_cache_layout_fallbacks": "prismal.prompt_cache_layout_fallbacks_total",
    "tool_gate_masked_calls": "prismal.tool_gate_masked_calls_total",
    "tool_search_calls": "prismal.tool_search_calls_total",
    "tool_deferred_activated": "prismal.tool_deferred_activated_total",
    "observations_masked": "prismal.observations_masked_total",
    "observations_mask_errors": "prismal.observations_mask_errors_total",
    "artifacts_stored": "prismal.artifacts_stored_total",
    "artifact_reads": "prismal.artifact_reads_total",
    "tool_output_truncated": "prismal.tool_output_truncated_total",
    "toon_encoded": "prismal.toon_encoded_total",
    "handoff_envelopes": "prismal.handoff_envelopes_total",
    "rag_passages_dropped": "prismal.rag_passages_dropped_total",
}
_TKE_HISTOGRAMS = {
    "llm_cache_hit_ratio": "prismal.llm_cache_hit_ratio",
    "rag_context_tokens": "prismal.rag_context_tokens",
}


def _registered() -> tuple[dict[str, Any], dict[str, Any], MagicMock]:
    mgr = object.__new__(OTelManager)
    mgr._counters = {}
    mgr._histograms = {}
    mgr._meter = MagicMock()
    mgr._register_standard_metrics()
    return mgr._counters, mgr._histograms, mgr._meter


def test_tke_counters_registered_with_spec_names() -> None:
    counters, _, meter = _registered()
    created = {c.args[0] for c in meter.create_counter.call_args_list}
    for key, name in _TKE_COUNTERS.items():
        assert key in counters
        assert name in created


def test_tke_histograms_registered_with_spec_names() -> None:
    _, histograms, meter = _registered()
    created = {c.args[0] for c in meter.create_histogram.call_args_list}
    for key, name in _TKE_HISTOGRAMS.items():
        assert key in histograms
        assert name in created

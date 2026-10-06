"""extract_gateway_meta (Phase GW — SPEC-GW-COST-001, GW1-09)."""

from __future__ import annotations

from types import SimpleNamespace

from langchain_core.messages import AIMessage

from prismal.budget.usage import extract_gateway_meta
from prismal.providers.gateways.base import GatewayCallMeta


def test_reads_meta_from_message() -> None:
    meta = GatewayCallMeta(
        gateway="openrouter",
        requested_model="a/b",
        served_model="a/b",
        generation_id="g",
        reported_cost_usd=0.1,
        cost_mode="metered",
    )
    msg = AIMessage(content="x", response_metadata={"prismal_gateway": meta.to_dict()})
    assert extract_gateway_meta(msg) == meta


def test_absent_or_malformed_is_none() -> None:
    assert extract_gateway_meta(AIMessage(content="x")) is None
    assert extract_gateway_meta(SimpleNamespace(response_metadata={"prismal_gateway": 3})) is None
    assert extract_gateway_meta(SimpleNamespace(response_metadata="nope")) is None
    assert extract_gateway_meta(object()) is None
    assert extract_gateway_meta(None) is None

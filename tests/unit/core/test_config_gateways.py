"""Tests for the LLM gateway settings (Phase GW — SPEC-GW-CFG-001)."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from prismal.core.config import Settings


def test_gateway_defaults_match_spec() -> None:
    s = Settings()
    assert s.llm_gateways_enabled is False
    assert s.llm_gateway_agent_map == {}
    assert s.llm_gateway_openai_compatible == {}
    assert s.llm_gateway_tenant_key_fallback is False
    # OpenRouter
    assert s.openrouter_enabled is False
    assert s.openrouter_api_key.get_secret_value() == ""
    assert s.openrouter_base_url == "https://openrouter.ai/api/v1"
    assert s.openrouter_app_url == ""
    assert s.openrouter_app_title == "prismal"
    assert s.openrouter_app_categories == []
    assert s.openrouter_session_stickiness is True
    assert s.openrouter_plugins == []
    assert s.openrouter_auto_allowed_models == []
    assert s.openrouter_auto_tier is None
    assert s.openrouter_require_zdr is True
    assert s.openrouter_provider_sort is None
    assert s.openrouter_max_price == {}
    assert s.openrouter_only == []
    assert s.openrouter_ignore == []
    assert s.openrouter_fallback_models == []
    assert s.openrouter_allowed_variants == ["floor", "nitro", "exacto"]
    assert s.openrouter_allow_auto is False
    assert s.openrouter_systemone_base_url is None
    # OpenCode
    assert s.opencode_zen_enabled is False
    assert s.opencode_go_enabled is False
    assert s.opencode_api_key.get_secret_value() == ""
    assert s.opencode_zen_base_url == "https://opencode.ai/inference/v1"
    assert s.opencode_go_base_url == "https://opencode.ai/inference/go/v1"
    assert s.opencode_model_protocols_path == "config/opencode_models.yaml"
    assert s.opencode_refresh_models is False
    assert s.opencode_go_allowed_agents == ["coder", "codeact_agent", "dev_pipeline", "code_review"]
    assert s.opencode_denied_models == [
        "grok*",
        "muse-spark*",
        "deepseek-v4-flash*",
        "big-pickle",
        "exo-free",
        "mimo-*-free",
        "ling-*-free",
        "nemotron-*-free",
    ]
    assert s.opencode_go_monthly_cap_usd == 0.0
    assert s.opencode_go_window_5h_ratio == 0.20
    assert s.opencode_go_window_week_ratio == 0.50
    assert s.opencode_go_quota_action == "refuse"
    assert s.opencode_go_downgrade_model is None
    # OmniRoute (GW0-07: floor 3.8.51, declared version)
    assert s.omniroute_enabled is False
    assert s.omniroute_api_key.get_secret_value() == ""
    assert s.omniroute_base_url == "http://localhost:20128/v1"
    assert s.omniroute_min_version == "3.8.51"
    assert s.omniroute_allow_unknown_version is False
    assert s.omniroute_declared_version is None
    assert s.omniroute_upstream_attestation is None
    assert s.omniroute_allow_subscription_upstreams is False
    assert s.omniroute_denied_combos == []
    # Reconcile / poll
    assert s.gateway_reconcile_enabled is False
    assert s.gateway_reconcile_delay_s == 2.0
    assert s.gateway_quota_poll_interval_s == 300


def test_secrets_are_secretstr_and_not_in_repr() -> None:
    s = Settings(openrouter_api_key="sk-or-SENTINEL", opencode_api_key="oc-SENTINEL")
    assert "SENTINEL" not in repr(s)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"openrouter_provider_sort": "cheapest"},
        {"opencode_go_quota_action": "explode"},
        {"omniroute_upstream_attestation": "trust_me"},
        {"opencode_go_window_5h_ratio": 0.0},
        {"opencode_go_window_week_ratio": 1.5},
        {"opencode_go_quota_action": "downgrade"},  # no downgrade model
        {"omniroute_min_version": "latest"},
        {"omniroute_declared_version": "3.8"},
        {"llm_gateway_agent_map": {"coder": "openrouter/anthropic/claude"}},
        {"openrouter_auto_tier": "ultra"},
        {"openrouter_plugins": ["web"]},
        {"llm_gateway_openai_compatible": {"Bad Name": {"base_url": "https://x"}}},
        {"llm_gateway_openai_compatible": {"mine": {"auth_ref": "k"}}},  # no base_url
        {"llm_gateway_openai_compatible": {"openrouter": {"base_url": "https://x"}}},
        {"gateway_reconcile_delay_s": -1.0},
        {"opencode_go_monthly_cap_usd": -5.0},
    ],
)
def test_validation_rejects_bad_values(kwargs: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        Settings(**kwargs)


def test_validation_accepts_good_values() -> None:
    s = Settings(
        llm_gateway_agent_map={"coder": "gw:opencode-go/glm-5"},
        llm_gateway_openai_compatible={
            "my-proxy": {"base_url": "https://proxy.local/v1", "auth_ref": "my_proxy_key"}
        },
        openrouter_provider_sort="price",
        openrouter_plugins=["response-healing", "pdf"],
        openrouter_auto_tier="low",
        opencode_go_quota_action="downgrade",
        opencode_go_downgrade_model="gw:opencode-go/glm-5-flash",
        omniroute_upstream_attestation="api_keys_only",
        omniroute_declared_version="3.8.52",
    )
    assert s.llm_gateway_agent_map["coder"] == "gw:opencode-go/glm-5"

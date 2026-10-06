"""Data policy and model reference grammar (Phase GW — SPEC-GW-POL-001)."""

from __future__ import annotations

import pytest

from prismal.core.exceptions import GatewayConfigError
from prismal.providers.gateways.policy import GatewayDataPolicy, ModelRef, parse_model_ref


@pytest.mark.parametrize(
    ("ref", "expected"),
    [
        (
            "gw:openrouter/deepseek/deepseek-chat:floor",
            ModelRef("openrouter", "deepseek/deepseek-chat", "floor"),
        ),
        (
            "gw:openrouter/anthropic/claude-sonnet-4.5",
            ModelRef("openrouter", "anthropic/claude-sonnet-4.5", None),
        ),
        ("gw:opencode-go/glm-5", ModelRef("opencode-go", "glm-5", None)),
        ("gw:omniroute/my-combo", ModelRef("omniroute", "my-combo", None)),
        ("gw:opencode/gpt-5.5", ModelRef("opencode", "gpt-5.5", None)),
        ("gw:my-proxy/qwen3:free", ModelRef("my-proxy", "qwen3", "free")),
    ],
)
def test_parse_valid_refs(ref: str, expected: ModelRef) -> None:
    assert parse_model_ref(ref) == expected


@pytest.mark.parametrize(
    "ref", ["claude-sonnet-4-5", "ollama/llama3", "openrouter/a/b", "", "GW:x/y"]
)
def test_non_gw_strings_return_none(ref: str) -> None:
    assert parse_model_ref(ref) is None


@pytest.mark.parametrize(
    "ref",
    [
        "gw:",
        "gw:openrouter",
        "gw:openrouter/",
        "gw:/model",
        "gw:Bad/model",
        "gw:-lead/model",
        "gw:" + "a" * 33 + "/m",
        "gw:openrouter/model:",
        "gw:openrouter/ spaced",
        "gw:openrouter/a//b",
    ],
)
def test_malformed_gw_refs_raise(ref: str) -> None:
    with pytest.raises(GatewayConfigError):
        parse_model_ref(ref)


def test_policy_defaults() -> None:
    p = GatewayDataPolicy()
    assert p.data_collection == "deny"
    assert p.zdr is True
    assert p.denied_model_patterns == ()
    assert p.require_parameters_for_tools is True


def test_policy_is_denied_fnmatch_case_insensitive() -> None:
    p = GatewayDataPolicy(denied_model_patterns=("grok*", "muse-spark*"))
    assert p.is_denied("Grok-4.7")
    assert p.is_denied("muse-spark-1.3")
    assert not p.is_denied("glm-5")

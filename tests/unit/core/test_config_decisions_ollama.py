"""Ollama decision backend + concrete HITL settings (DEC delta — SPEC-DEC-CFG-001, OLL-001/006/007/010/011/014)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from prismal.core.config import Settings


def test_ollama_defaults_match_the_delta() -> None:
    s = Settings()
    assert s.decision_backend_order == ["rule", "ollama", "jev", "llm"]
    assert s.decision_ollama_base_url is None  # ⇒ settings.ollama_base_url at build time
    assert s.decision_ollama_models == {"fast": "tev1:4b", "accurate": "nimble:9b-q4_K_M"}
    assert s.decision_ollama_keep_alive == "30m"
    assert s.decision_ollama_warmup is False
    assert s.decision_ollama_allow_remote is False
    assert s.decision_ollama_accurate_max_p95_ms == 1000
    assert s.decision_ollama_max_batch == 4
    assert s.decision_ollama_min_version == "0.35.0"


def test_concrete_hitl_defaults() -> None:
    s = Settings()
    assert s.decision_hitl_raise_threshold == 0.5
    assert s.decision_hitl_raise_thresholds == {}
    assert s.decision_hitl_money_limit == 0.0


def test_laya_stays_a_valid_tier() -> None:
    s = Settings(decision_backend_order=["rule", "laya", "ollama", "jev", "llm"])
    assert "laya" in s.decision_backend_order


def test_unknown_backend_still_rejected() -> None:
    with pytest.raises(ValidationError, match="unknown backends"):
        Settings(decision_backend_order=["rule", "llama"])


@pytest.mark.parametrize("tier", ["fast", "accurate"])
def test_empty_ollama_tier_model_rejected(tier: str) -> None:
    with pytest.raises(ValidationError, match="DECISION_OLLAMA_MODELS"):
        Settings(decision_ollama_models={tier: ""})


def test_unknown_ollama_tier_rejected() -> None:
    with pytest.raises(ValidationError, match="DECISION_OLLAMA_MODELS"):
        Settings(decision_ollama_models={"turbo": "tev1:4b"})


def test_ollama_models_may_omit_a_tier() -> None:
    s = Settings(decision_ollama_models={"fast": "tev1:4b"})
    assert s.decision_ollama_models == {"fast": "tev1:4b"}


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:11434",
        "http://127.0.0.1:11434",
        "http://[::1]:11434",
        "http://127.0.0.2:11434",
    ],
)
def test_loopback_ollama_needs_no_opt_in(url: str) -> None:
    s = Settings(decisions_enabled=True, decision_ollama_base_url=url)
    assert s.decision_ollama_base_url == url


@pytest.mark.parametrize(
    "url", ["http://gpu-box.lan:11434", "http://10.0.0.5:11434", "https://ollama.example.com"]
)
def test_remote_ollama_requires_explicit_opt_in(url: str) -> None:
    with pytest.raises(ValidationError, match="ALLOW_REMOTE"):
        Settings(decisions_enabled=True, decision_ollama_base_url=url)
    assert Settings(
        decisions_enabled=True, decision_ollama_base_url=url, decision_ollama_allow_remote=True
    ).decision_ollama_allow_remote


def test_remote_global_llm_ollama_does_not_break_load() -> None:
    """Review M4: an unset decision URL falling back to a remote LLM Ollama must not fail
    Settings load for existing users; the factory refuses to build the tier instead (M1)."""
    s = Settings(decisions_enabled=True, ollama_base_url="http://gpu-box.lan:11434")
    assert s.decision_ollama_base_url is None


@pytest.mark.parametrize(
    ("url", "loopback"),
    [
        ("http://localhost:11434", True),
        ("http://127.0.0.1:11434", True),
        ("http://[::1]:11434", True),
        ("http://0.0.0.0:11434", False),
        ("http://gpu-box.lan:11434", False),
        ("http://169.254.169.254:80", False),
        ("localhost:11434", False),
    ],
)
def test_is_loopback_url(url: str, loopback: bool) -> None:
    from prismal.core.config import is_loopback_url

    assert is_loopback_url(url) is loopback


def test_remote_llm_ollama_is_fine_while_decisions_are_off() -> None:
    """A remote Ollama serving the *LLM* must not break startup when DEC is off."""
    s = Settings(ollama_base_url="http://gpu-box.lan:11434")
    assert s.decisions_enabled is False


def test_remote_check_skipped_when_ollama_not_in_order() -> None:
    s = Settings(
        decisions_enabled=True,
        ollama_base_url="http://gpu-box.lan:11434",
        decision_backend_order=["rule", "jev", "llm"],
    )
    assert s.decision_backend_order == ["rule", "jev", "llm"]


@pytest.mark.parametrize("value", [0.0, 1.0001, -0.1])
def test_raise_threshold_bounds(value: float) -> None:
    with pytest.raises(ValidationError):
        Settings(decision_hitl_raise_threshold=value)


def test_raise_thresholds_values_bounded() -> None:
    with pytest.raises(ValidationError, match="RAISE_THRESHOLDS"):
        Settings(decision_hitl_raise_thresholds={"hitl.destructive": 1.5})


def test_max_batch_bounds() -> None:
    with pytest.raises(ValidationError):
        Settings(decision_ollama_max_batch=0)
    with pytest.raises(ValidationError):
        Settings(decision_ollama_max_batch=65)


def test_raise_thresholds_keys_must_be_concrete_hitl_questions() -> None:
    """Review L7: a typo must not silently fall back to the default threshold."""
    with pytest.raises(ValidationError, match="RAISE_THRESHOLDS"):
        Settings(decision_hitl_raise_thresholds={"hitl.destructiv": 0.3})
    s = Settings(decision_hitl_raise_thresholds={"hitl.destructive": 0.3})
    assert s.decision_hitl_raise_thresholds == {"hitl.destructive": 0.3}


def test_ollama_has_its_own_time_budget() -> None:
    """Review M4: Ollama sub-tiers get their own budget instead of the shared 800 ms."""
    assert Settings().decision_ollama_timeout_ms == 2000
    with pytest.raises(ValidationError):
        Settings(decision_ollama_timeout_ms=0)

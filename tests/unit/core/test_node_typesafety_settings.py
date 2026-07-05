"""Tests for the node-typesafety config fields (Phase NTS — SPEC-NTS-CFG-001)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from prismal.core.config import Settings

# ── Defaults ────────────────────────────────────────────────────────────────


def test_node_typesafety_defaults_match_spec() -> None:
    s = Settings()
    assert s.node_typesafety_enabled is False
    assert s.node_typesafety_mode == "warn"


def test_node_typesafety_disabled_by_default_is_opt_in() -> None:
    assert Settings().node_typesafety_enabled is False


# ── Env parsing (PRISMAL_* prefix) ──────────────────────────────────────────


def test_node_typesafety_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRISMAL_NODE_TYPESAFETY_ENABLED", "true")
    monkeypatch.setenv("PRISMAL_NODE_TYPESAFETY_MODE", "enforce")
    s = Settings()
    assert s.node_typesafety_enabled is True
    assert s.node_typesafety_mode == "enforce"


# ── Validation (_validate_node_typesafety) ──────────────────────────────────


def test_invalid_mode_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(node_typesafety_mode="paranoid")


def test_invalid_mode_message_names_env_var() -> None:
    with pytest.raises(ValidationError, match="PRISMAL_NODE_TYPESAFETY_MODE"):
        Settings(node_typesafety_mode="bogus")


def test_valid_modes_accepted() -> None:
    for mode in ("off", "warn", "enforce"):
        assert Settings(node_typesafety_mode=mode).node_typesafety_mode == mode

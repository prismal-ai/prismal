"""Unit tests for the Skynet S+ role registry (SPEC-SP-REG-001).

``RoleRegistry.resolve`` is best-effort (never raises, falls back to
``DEFAULT_ROLE``); only ``from_yaml`` raises — once, at load time — on a
malformed file.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from prismal.agents.skynet.roles import DEFAULT_ROLE, RoleRegistry, SpecialistRole
from prismal.core.exceptions import SkynetRoleError


def test_resolve_known() -> None:
    researcher = SpecialistRole(
        name="researcher", model="claude-sonnet-4-5", capabilities=["research"]
    )
    registry = RoleRegistry({"researcher": researcher})
    assert registry.resolve("researcher") is researcher
    assert "researcher" in registry.known_roles()


def test_resolve_unknown_falls_back() -> None:
    registry = RoleRegistry({"researcher": SpecialistRole(name="researcher")})
    # Unknown role → DEFAULT_ROLE, never raises (RF-SP-02).
    assert registry.resolve("nonexistent") == DEFAULT_ROLE
    assert registry.resolve("") == DEFAULT_ROLE


def test_default_role_shape() -> None:
    assert DEFAULT_ROLE.name == "worker"
    assert DEFAULT_ROLE.capabilities == ["general"]
    assert DEFAULT_ROLE.model is None
    assert DEFAULT_ROLE.persona == ""
    assert DEFAULT_ROLE.remote_agent is None


def test_from_yaml_loads(tmp_path: Path) -> None:
    yaml_file = tmp_path / "skynet_roles.yaml"
    yaml_file.write_text(
        "roles:\n"
        "  researcher:\n"
        '    model: "claude-sonnet-4-5"\n'
        '    capabilities: ["research", "web"]\n'
        '    persona: "You are a meticulous research specialist."\n'
        "  legal_review:\n"
        '    capabilities: ["legal"]\n'
        '    remote_agent: "https://legal.example.com/.well-known/agent-card.json"\n',
        encoding="utf-8",
    )
    registry = RoleRegistry.from_yaml(yaml_file)

    researcher = registry.resolve("researcher")
    assert researcher.model == "claude-sonnet-4-5"
    assert researcher.capabilities == ["research", "web"]
    assert researcher.persona == "You are a meticulous research specialist."
    assert researcher.remote_agent is None

    legal = registry.resolve("legal_review")
    assert legal.remote_agent == "https://legal.example.com/.well-known/agent-card.json"
    assert legal.model is None

    assert set(registry.known_roles()) == {"researcher", "legal_review"}


def test_from_yaml_missing_file_empty(tmp_path: Path) -> None:
    # A missing file yields an empty registry (all → DEFAULT_ROLE), never raises.
    registry = RoleRegistry.from_yaml(tmp_path / "does_not_exist.yaml")
    assert registry.known_roles() == []
    assert registry.resolve("anything") == DEFAULT_ROLE


def test_from_yaml_malformed_raises(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("roles: [this is not a mapping\n", encoding="utf-8")
    with pytest.raises(SkynetRoleError):
        RoleRegistry.from_yaml(bad)


def test_from_yaml_roles_not_a_mapping_raises(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("roles:\n  - just_a_list_item\n", encoding="utf-8")
    with pytest.raises(SkynetRoleError):
        RoleRegistry.from_yaml(bad)

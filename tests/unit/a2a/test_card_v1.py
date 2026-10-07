"""Agent Card v1 + v0.3 byte-for-byte guard (Phase PRT — SPEC-PRT-A2A-003)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from prismal.a2a import card as card_mod
from prismal.a2a.card import a2a_route_paths, build_agent_card, clear_agent_card_cache
from prismal.a2a.types import AgentCard
from prismal.a2a.v1.types import AgentCardV1
from prismal.core.config import Settings

pytestmark = pytest.mark.unit

_SNAPSHOT = Path(__file__).parents[2] / "fixtures" / "protocols" / "a2a_v03_card_snapshot.json"

REGISTRY = {
    "research": ["general", "research"],
    "coding": ["general", "code_execution"],
    "rag": ["rag", "general"],
}


@pytest.fixture(autouse=True)
def _pinned_version(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(card_mod, "_resolve_version", lambda: "9.9.9")
    clear_agent_card_cache()


def _snapshot_cases() -> dict[str, tuple[Settings, dict[str, Any]]]:
    return {
        "default": (
            Settings(a2a_enabled=True, a2a_base_url="https://prismal.example.com/a2a"),
            {},
        ),
        "tenant": (
            Settings(a2a_enabled=True, a2a_base_url="https://prismal.example.com/a2a/"),
            {"org_id": "acme"},
        ),
        "multimodal_did": (
            Settings(
                a2a_enabled=True,
                a2a_base_url="https://x/a2a",
                multimodal_enabled=True,
                a2a_published_skills=["rag", "nope"],
            ),
            {"did": "did:web:x.example"},
        ),
    }


class TestV03Snapshot:
    """The pre-PRT card, captured before card.py changed, must stay byte-for-byte."""

    @pytest.mark.parametrize("case", ["default", "tenant", "multimodal_did"])
    @pytest.mark.parametrize("explicit", [False, True])
    def test_v03_card_is_byte_for_byte(self, case: str, explicit: bool) -> None:
        expected = json.loads(_SNAPSHOT.read_text())[case]
        settings, kwargs = _snapshot_cases()[case]
        if explicit:
            kwargs = {**kwargs, "protocol_version": "0.3"}
        card = build_agent_card(settings, REGISTRY, **kwargs)
        assert isinstance(card, AgentCard)
        assert card.model_dump_json(by_alias=True) == expected["model_dump_json"]
        assert card.model_dump_json() == expected["model_dump_json_default"]


class TestV1Card:
    def _settings(self, **kw: Any) -> Settings:
        return Settings(a2a_enabled=True, a2a_base_url="https://p.example/a2a", **kw)

    def test_explicit_v1_returns_v1_card(self) -> None:
        card = build_agent_card(self._settings(), REGISTRY, protocol_version="1.0")
        assert isinstance(card, AgentCardV1)
        dumped = card.model_dump(by_alias=True, exclude_none=True)
        assert "url" not in dumped
        assert "protocolVersion" not in dumped
        iface = dumped["supportedInterfaces"][0]
        assert iface == {
            "url": "https://p.example/a2a",
            "protocolBinding": "JSONRPC",
            "protocolVersion": "1.0",
        }
        assert dumped["capabilities"]["streaming"] is True
        assert {s["id"] for s in dumped["skills"]} == set(REGISTRY)
        assert dumped["version"] == "9.9.9"

    def test_setting_selects_v1_when_none(self) -> None:
        card = build_agent_card(self._settings(a2a_protocol_version="1.0"), REGISTRY)
        assert isinstance(card, AgentCardV1)

    def test_tenant_scopes_every_interface(self) -> None:
        card = build_agent_card(self._settings(), REGISTRY, org_id="acme", protocol_version="1.0")
        assert isinstance(card, AgentCardV1)
        assert card.supported_interfaces
        for iface in card.supported_interfaces:
            assert "/acme" in iface.url
            assert iface.tenant == "acme"

    def test_cache_key_includes_version(self) -> None:
        s = self._settings()
        v03 = build_agent_card(s, REGISTRY, protocol_version="0.3")
        v1 = build_agent_card(s, REGISTRY, protocol_version="1.0")
        assert isinstance(v03, AgentCard)
        assert isinstance(v1, AgentCardV1)
        assert build_agent_card(s, REGISTRY, protocol_version="1.0") is v1

    def test_security_schemes_from_settings(self) -> None:
        s = self._settings(a2a_inbound_auth_schemes=["bearer", "mtls", "api_key", "weird"])
        card = build_agent_card(s, REGISTRY, protocol_version="1.0")
        dumped = card.model_dump(by_alias=True, exclude_none=True)
        schemes = dumped["securitySchemes"]
        assert schemes["bearer"] == {"httpAuthSecurityScheme": {"scheme": "Bearer"}}
        assert "mtlsSecurityScheme" in schemes["mtls"]
        assert schemes["api_key"]["apiKeySecurityScheme"]["location"] == "header"
        assert "weird" not in schemes
        assert dumped["securityRequirements"] == [
            {"schemes": {"bearer": {"list": []}}},
            {"schemes": {"mtls": {"list": []}}},
            {"schemes": {"api_key": {"list": []}}},
        ]

    def test_no_schemes_by_default(self) -> None:
        card = build_agent_card(self._settings(), REGISTRY, protocol_version="1.0")
        dumped = card.model_dump(by_alias=True, exclude_none=True)
        assert "securitySchemes" not in dumped

    def test_multimodal_output_modes_and_did(self) -> None:
        s = self._settings(multimodal_enabled=True)
        card = build_agent_card(s, REGISTRY, protocol_version="1.0", did="did:web:p")
        dumped = card.model_dump(by_alias=True, exclude_none=True)
        assert "image/png" in dumped["defaultOutputModes"]
        assert dumped["did"] == "did:web:p"

    def test_v1_card_round_trips_through_its_model(self) -> None:
        card = build_agent_card(self._settings(), REGISTRY, org_id="t1", protocol_version="1.0")
        raw = card.model_dump(by_alias=True, exclude_none=True)
        again = AgentCardV1.model_validate(raw).model_dump(by_alias=True, exclude_none=True)
        assert again == raw


class TestRoutePaths:
    @pytest.mark.parametrize("version", ["0.3", "1.0"])
    @pytest.mark.parametrize("org_id", [None, "acme"])
    def test_base_plus_path_equals_advertised_url(self, version: str, org_id: str | None) -> None:
        s = Settings(a2a_enabled=True, a2a_base_url="https://p.example/a2a/")
        card_path, rpc_path = a2a_route_paths(s, org_id=org_id, protocol_version=version)  # type: ignore[arg-type]
        built = build_agent_card(s, REGISTRY, org_id=org_id, protocol_version=version)  # type: ignore[call-overload]
        advertised = (
            built.url if isinstance(built, AgentCard) else built.supported_interfaces[0].url
        )
        base = s.a2a_base_url.rstrip("/")  # type: ignore[union-attr]
        assert base + rpc_path == advertised
        assert card_path == rpc_path + "/.well-known/agent-card.json"

    def test_no_tenant_paths(self) -> None:
        s = Settings(a2a_enabled=True, a2a_base_url="https://p.example/a2a")
        assert a2a_route_paths(s, org_id=None, protocol_version="0.3") == (
            "/.well-known/agent-card.json",
            "",
        )
        assert a2a_route_paths(s, org_id="acme", protocol_version="1.0") == (
            "/acme/.well-known/agent-card.json",
            "/acme",
        )

"""Agent Card generation (Phase I — SPEC-A2A-002)."""

from __future__ import annotations

import pytest

from prismal.a2a.card import build_agent_card, clear_agent_card_cache
from prismal.a2a.types import AgentCard
from prismal.core.config import Settings

pytestmark = pytest.mark.unit

REGISTRY = {
    "research": ["general", "research"],
    "coding": ["general", "code_execution"],
    "rag": ["rag", "general"],
}


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    clear_agent_card_cache()


class TestBuildAgentCard:
    def test_publishes_all_registry_skills_when_allowlist_empty(self) -> None:
        s = Settings(a2a_enabled=True, a2a_base_url="https://prismal.example.com/a2a")
        card = build_agent_card(s, REGISTRY)
        assert isinstance(card, AgentCard)
        assert {sk.id for sk in card.skills} == set(REGISTRY)
        assert card.url == "https://prismal.example.com/a2a"
        assert card.capabilities["streaming"] is True
        assert card.version  # resolved package version, non-empty

    def test_allowlist_narrows_published_skills(self) -> None:
        s = Settings(
            a2a_enabled=True,
            a2a_base_url="https://x/a2a",
            a2a_published_skills=["research"],
        )
        card = build_agent_card(s, REGISTRY)
        assert [sk.id for sk in card.skills] == ["research"]
        assert card.skills[0].tags == ["general", "research"]
        assert card.skills[0].name == "Research"

    def test_unknown_allowlisted_skill_is_skipped(self) -> None:
        s = Settings(
            a2a_enabled=True,
            a2a_base_url="https://x/a2a",
            a2a_published_skills=["research", "nonexistent"],
        )
        card = build_agent_card(s, REGISTRY)
        assert [sk.id for sk in card.skills] == ["research"]

    def test_org_id_scopes_url(self) -> None:
        s = Settings(a2a_enabled=True, a2a_base_url="https://x/a2a")
        card = build_agent_card(s, REGISTRY, org_id="acme")
        assert card.url == "https://x/a2a/acme"

    def test_multimodal_adds_media_output_modes(self) -> None:
        s = Settings(
            a2a_enabled=True,
            a2a_base_url="https://x/a2a",
            a2a_published_skills=["research"],
            multimodal_enabled=True,
        )
        card = build_agent_card(s, REGISTRY)
        assert "image/png" in card.skills[0].output_modes

    def test_did_web_when_identity_enabled(self) -> None:
        s = Settings(
            a2a_enabled=True,
            a2a_base_url="https://x/a2a",
            identity_enabled=True,
            identity_did_method="web",
            identity_did_web_domain="prismal.example.com",
        )
        card = build_agent_card(s, REGISTRY)
        assert card.did == "did:web:prismal.example.com"

    def test_explicit_did_overrides(self) -> None:
        s = Settings(a2a_enabled=True, a2a_base_url="https://x/a2a")
        card = build_agent_card(s, REGISTRY, did="did:key:zABC")
        assert card.did == "did:key:zABC"

    def test_no_did_by_default(self) -> None:
        s = Settings(a2a_enabled=True, a2a_base_url="https://x/a2a")
        card = build_agent_card(s, REGISTRY)
        assert card.did is None

    def test_card_is_conformant_json(self) -> None:
        s = Settings(
            a2a_enabled=True,
            a2a_base_url="https://x/a2a",
            a2a_published_skills=["coding"],
        )
        card = build_agent_card(s, REGISTRY)
        dumped = card.model_dump(by_alias=True, exclude_none=True)
        assert dumped["protocolVersion"] == "0.3.0"
        assert dumped["skills"][0]["inputModes"] == ["text/plain"]


class TestAgentCardCache:
    def test_same_args_cached(self) -> None:
        s = Settings(a2a_enabled=True, a2a_base_url="https://x/a2a")
        first = build_agent_card(s, REGISTRY, org_id="acme")
        second = build_agent_card(s, REGISTRY, org_id="acme")
        assert first is second

    def test_clear_cache_rebuilds(self) -> None:
        s = Settings(a2a_enabled=True, a2a_base_url="https://x/a2a")
        first = build_agent_card(s, REGISTRY)
        clear_agent_card_cache()
        second = build_agent_card(s, REGISTRY)
        assert first is not second
        assert first == second

    def test_different_settings_not_shared(self) -> None:
        s1 = Settings(a2a_enabled=True, a2a_base_url="https://x/a2a")
        s2 = Settings(a2a_enabled=True, a2a_base_url="https://y/a2a")
        c1 = build_agent_card(s1, REGISTRY)
        c2 = build_agent_card(s2, REGISTRY)
        assert c1.url != c2.url

"""Settings + exceptions for the A2A interop layer (Phase I — SPEC-A2A-007/008)."""

from __future__ import annotations

import pytest

from prismal.core.config import Settings
from prismal.core.exceptions import A2AAgentUnavailable, A2AError, PrismalError

pytestmark = pytest.mark.unit


class TestA2ASettings:
    def test_defaults_off(self) -> None:
        s = Settings()
        assert s.a2a_enabled is False
        assert s.a2a_inbound_enabled is False
        assert s.a2a_outbound_enabled is False
        assert s.a2a_strict is True
        assert s.a2a_base_url is None
        assert s.a2a_published_skills == []
        assert s.a2a_outbound_allowlist == []

    def test_overrides_apply(self) -> None:
        s = Settings(
            a2a_enabled=True,
            a2a_outbound_enabled=True,
            a2a_base_url="https://prismal.example.com/a2a",
            a2a_published_skills=["research", "coding"],
            a2a_outbound_allowlist=["*.trusted.org"],
            a2a_strict=False,
        )
        assert s.a2a_enabled is True
        assert s.a2a_base_url == "https://prismal.example.com/a2a"
        assert s.a2a_published_skills == ["research", "coding"]
        assert s.a2a_outbound_allowlist == ["*.trusted.org"]
        assert s.a2a_strict is False


class TestA2AExceptions:
    def test_error_hierarchy(self) -> None:
        assert issubclass(A2AError, PrismalError)
        assert issubclass(A2AAgentUnavailable, A2AError)

    def test_agent_unavailable_message_and_fields(self) -> None:
        exc = A2AAgentUnavailable("https://billing.acme/a2a", "outside allowlist")
        assert exc.agent == "https://billing.acme/a2a"
        assert exc.reason == "outside allowlist"
        assert "billing.acme" in str(exc)
        assert "outside allowlist" in str(exc)

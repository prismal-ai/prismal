"""A2A version negotiation (SPEC-PRT-A2A-002 as amended by ADR-PRT-001)."""

from __future__ import annotations

from typing import Any

import pytest

from prismal.a2a import negotiation
from prismal.a2a.negotiation import (
    count_a2a_request,
    negotiate_inbound,
    normalize_version,
    select_interface_url,
    select_outbound,
)
from prismal.core.config import Settings

pytestmark = pytest.mark.unit


def _req(method: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": 1, "method": method, "params": {}}


@pytest.fixture
def legacy_ok() -> Settings:
    return Settings(a2a_enabled=True)


@pytest.fixture
def v1_only() -> Settings:
    return Settings(a2a_enabled=True, a2a_protocol_version="1.0", a2a_accept_legacy=False)


class TestNormalize:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("1.0", "1.0"),
            ("1", "1.0"),
            ("1.0.1", "1.0"),
            (" 1.2 ", "1.0"),
            ("0.3", "0.3"),
            ("0.3.0", "0.3"),
            ("0.2", None),
            ("2.0", None),
            ("abc", None),
            ("", None),
            (None, None),
            (3, None),
        ],
    )
    def test_normalize(self, raw: Any, expected: str | None) -> None:
        assert normalize_version(raw) == expected


class TestNegotiateInbound:
    def test_v1_method_without_header_routes_v1(self, legacy_ok: Settings) -> None:
        assert negotiate_inbound(_req("SendMessage"), {}, settings=legacy_ok) == "1.0"

    def test_legacy_method_without_header_routes_legacy(self, legacy_ok: Settings) -> None:
        assert negotiate_inbound(_req("message/send"), {}, settings=legacy_ok) == "0.3"

    def test_header_wins_over_method(self, legacy_ok: Settings) -> None:
        got = negotiate_inbound(_req("message/send"), {"A2A-Version": "1.0"}, settings=legacy_ok)
        assert got == "1.0"
        got = negotiate_inbound(_req("SendMessage"), {"a2a-version": "0.3"}, settings=legacy_ok)
        assert got == "0.3"

    def test_empty_header_is_ignored(self, legacy_ok: Settings) -> None:
        assert negotiate_inbound(_req("GetTask"), {"A2A-Version": ""}, settings=legacy_ok) == "1.0"

    def test_unsupported_header_version_is_refused(self, legacy_ok: Settings) -> None:
        assert (
            negotiate_inbound(_req("GetTask"), {"A2A-Version": "2.0"}, settings=legacy_ok) is None
        )

    def test_unknown_method_defaults_to_legacy_not_setting(self) -> None:
        s = Settings(a2a_enabled=True, a2a_protocol_version="1.0", a2a_accept_legacy=True)
        assert negotiate_inbound(_req("bogus"), {}, settings=s) == "0.3"

    def test_legacy_refused_when_not_accepted(self, v1_only: Settings) -> None:
        assert negotiate_inbound(_req("message/send"), {}, settings=v1_only) is None
        assert negotiate_inbound(_req("bogus"), {}, settings=v1_only) is None
        assert negotiate_inbound(_req("SendMessage"), {}, settings=v1_only) == "1.0"

    def test_none_settings_accepts_legacy(self) -> None:
        assert negotiate_inbound(_req("tasks/get"), {}, settings=None) == "0.3"

    def test_never_raises_on_garbage(self, legacy_ok: Settings) -> None:
        assert negotiate_inbound(None, None, settings=legacy_ok) == "0.3"  # type: ignore[arg-type]
        assert negotiate_inbound({"method": 5}, {"A2A-Version": 7}, settings=legacy_ok) == "0.3"  # type: ignore[dict-item]

    def test_never_raises_when_settings_explode(self) -> None:
        class _Boom:
            @property
            def a2a_accept_legacy(self) -> bool:
                raise RuntimeError("boom")

        assert negotiate_inbound(_req("message/send"), {}, settings=_Boom()) is None  # type: ignore[arg-type]


V1_CARD = {
    "name": "x",
    "supportedInterfaces": [
        {"url": "https://h/rest", "protocolBinding": "HTTP+JSON", "protocolVersion": "1.0"},
        {"url": "https://h/v1", "protocolBinding": "JSONRPC", "protocolVersion": "1.0"},
        {"url": "https://h/legacy", "protocolBinding": "JSONRPC", "protocolVersion": "0.3"},
    ],
}
V03_CARD = {"name": "x", "url": "https://h/a2a", "protocolVersion": "0.3.0"}


class TestSelectOutbound:
    def test_v1_card_selects_v1(self) -> None:
        assert select_outbound(V1_CARD, preferred=None) == "1.0"

    def test_v03_card_selects_legacy(self) -> None:
        assert select_outbound(V03_CARD, preferred=None) == "0.3"

    def test_internal_card_with_v1_marker(self) -> None:
        assert select_outbound({"url": "u", "protocolVersion": "1.0"}, preferred=None) == "1.0"

    def test_preferred_wins_only_when_advertised(self) -> None:
        assert select_outbound(V1_CARD, preferred="0.3") == "0.3"
        assert select_outbound(V03_CARD, preferred="1.0") == "0.3"

    @pytest.mark.parametrize(
        "card", [{}, {"protocolVersion": "zz"}, None, {"supportedInterfaces": 3}]
    )
    def test_missing_or_garbage_defaults_legacy(self, card: Any) -> None:
        assert select_outbound(card, preferred=None) == "0.3"

    def test_grpc_only_v1_interface_is_not_usable(self) -> None:
        card = {
            "url": "https://h/a2a",
            "protocolVersion": "0.3.0",
            "supportedInterfaces": [
                {"url": "g", "protocolBinding": "GRPC", "protocolVersion": "1.0"},
            ],
        }
        assert select_outbound(card, preferred=None) == "0.3"


class TestSelectInterfaceUrl:
    def test_picks_jsonrpc_interface_for_version(self) -> None:
        assert select_interface_url(V1_CARD, "1.0") == ("https://h/v1", None)
        assert select_interface_url(V1_CARD, "0.3") == ("https://h/legacy", None)

    def test_returns_tenant(self) -> None:
        card = {
            "supportedInterfaces": [
                {
                    "url": "https://h/acme",
                    "protocolBinding": "JSONRPC",
                    "protocolVersion": "1.0",
                    "tenant": "acme",
                }
            ]
        }
        assert select_interface_url(card, "1.0") == ("https://h/acme", "acme")

    def test_falls_back_to_top_level_url(self) -> None:
        assert select_interface_url(V03_CARD, "0.3") == ("https://h/a2a", None)
        assert select_interface_url({}, "1.0") == (None, None)


class TestCounter:
    def test_count_uses_otel_with_labels(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[tuple[str, dict[str, Any]]] = []

        class _Otel:
            def increment_counter(self, key: str, value: int = 1, attributes: Any = None) -> None:
                calls.append((key, attributes))

        monkeypatch.setattr(negotiation, "_otel", lambda: _Otel())
        count_a2a_request(
            protocol_version="1.0", direction="inbound", method="SendMessage", outcome="ok"
        )
        assert calls == [
            (
                "a2a_requests",
                {
                    "protocol_version": "1.0",
                    "direction": "inbound",
                    "method": "SendMessage",
                    "outcome": "ok",
                },
            )
        ]

    def test_count_never_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _boom() -> Any:
            raise RuntimeError("x")

        monkeypatch.setattr(negotiation, "_otel", _boom)
        count_a2a_request(protocol_version="0.3", direction="inbound", method="m", outcome="ok")

"""``RuntimeContext.a2a_routes()`` ⇔ Agent Card consistency (SPEC-HRS-A2A-001, HRS1-08)."""

from __future__ import annotations

import pytest

from prismal.a2a.card import build_agent_card, clear_agent_card_cache
from prismal.composition import A2ARouteSpec, build_test_runtime
from prismal.core.config import Settings

_REGISTRY = {"coder": ["code"]}


def _settings(**kw: object) -> Settings:
    return Settings(a2a_enabled=True, a2a_base_url="https://agents.acme.test/a2a/", **kw)


def _advertised(settings: Settings, org_id: str | None, version: str) -> str:
    clear_agent_card_cache()
    if version == "0.3":
        return build_agent_card(settings, _REGISTRY, org_id=org_id, protocol_version="0.3").url
    card = build_agent_card(settings, _REGISTRY, org_id=org_id, protocol_version="1.0")
    return next(i.url for i in card.supported_interfaces if i.protocol_binding == "JSONRPC")


@pytest.mark.parametrize("org_id", [None, "acme"])
@pytest.mark.parametrize("version", ["0.3", "1.0"])
def test_rpc_path_joins_base_url_to_the_advertised_url(org_id: str | None, version: str) -> None:
    settings = _settings()
    ctx = build_test_runtime(settings=settings, org_id=org_id)
    spec = ctx.a2a_routes(protocol_version=version)  # type: ignore[arg-type]
    assert isinstance(spec, A2ARouteSpec)
    assert settings.a2a_base_url.rstrip("/") + spec.rpc_path == _advertised(
        settings, org_id, version
    )


def test_tenant_scoped_paths_and_card_path() -> None:
    scoped = build_test_runtime(settings=_settings(), org_id="acme").a2a_routes()
    assert scoped.tenant_scoped is True
    assert scoped.rpc_path == "/acme"
    assert scoped.card_path == "/acme/.well-known/agent-card.json"
    plain = build_test_runtime(settings=_settings()).a2a_routes()
    assert plain.tenant_scoped is False
    assert plain.card_path == "/.well-known/agent-card.json"


def test_default_version_comes_from_settings() -> None:
    settings = _settings(a2a_protocol_version="1.0")
    ctx = build_test_runtime(settings=settings, org_id="acme")
    assert ctx.a2a_routes() == ctx.a2a_routes(protocol_version="1.0")

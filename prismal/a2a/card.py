"""Agent Card generation — Phase I (SPEC-A2A-002).

``build_agent_card`` derives the Agent Card served at
``/.well-known/agent-card.json`` from the agent/subgraph capability registry
(a ``Mapping[str, list[str]]`` such as
:data:`prismal.agents.tool_registry.DEFAULT_CAPABILITY_MAP`).

The published skills are an **allowlist**: ``settings.a2a_published_skills``
narrows what is exposed; when empty, every registry entry is published. The
result is cached per (settings fingerprint, org_id, protocol version) — see
:func:`clear_agent_card_cache`.

Phase PRT (SPEC-PRT-A2A-003): ``protocol_version="0.3"`` (the default setting)
emits the pre-PRT :class:`AgentCard` byte-for-byte; ``"1.0"`` emits an
:class:`~prismal.a2a.v1.types.AgentCardV1` whose single JSON-RPC interface URL
is tenant-scoped exactly like the v0.3 ``url`` and carries
``AgentInterface.tenant = org_id`` (v1 has no ``multi_tenant`` capability).
:func:`a2a_route_paths` is the shared URL contract with the host routes.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from typing import TYPE_CHECKING, Any, Literal, overload

from prismal.a2a.types import AgentCard, AgentSkill
from prismal.a2a.v1 import constants as v1c
from prismal.a2a.v1.types import (
    AgentCapabilities,
    AgentCardV1,
    AgentInterface,
    AgentSkillV1,
    APIKeySecurityScheme,
    HTTPAuthSecurityScheme,
    MutualTlsSecurityScheme,
    SecurityRequirement,
    SecuritySchemeV1,
    StringList,
)
from prismal.core.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Mapping

    from prismal.core.config import Settings

logger = get_logger(__name__)

# Extra output modalities advertised when the multimodal layer is enabled.
_MEDIA_OUTPUT_MODES = ("image/png", "audio/mpeg", "video/mp4")

_card_cache: dict[Any, AgentCard | AgentCardV1] = {}

# v1 security schemes derivable from a2a_inbound_auth_schemes (no secrets needed).
_V1_SCHEMES: dict[str, SecuritySchemeV1] = {
    "bearer": SecuritySchemeV1(http_auth_security_scheme=HTTPAuthSecurityScheme(scheme="Bearer")),
    "mtls": SecuritySchemeV1(mtls_security_scheme=MutualTlsSecurityScheme()),
    "api_key": SecuritySchemeV1(
        api_key_security_scheme=APIKeySecurityScheme(location="header", name="X-API-Key")
    ),
}


def _resolve_version() -> str:
    """Best-effort prismal package version for the Agent Card."""
    for dist in ("prismal-ai", "prismal", "lightagent-agents"):
        try:
            return _pkg_version(dist)
        except PackageNotFoundError:
            continue
    return "0.0.0"


def clear_agent_card_cache() -> None:
    """Drop every cached Agent Card (call on settings reload)."""
    _card_cache.clear()


def _card_url(base_url: str | None, org_id: str | None) -> str:
    """Tenant-scope the advertised endpoint (mirrors ``collection_for``)."""
    base = (base_url or "").rstrip("/")
    return f"{base}/{org_id}" if org_id else base


def a2a_route_paths(
    settings: Settings,
    *,
    org_id: str | None,
    protocol_version: v1c.A2AProtocolVersion | None = None,
) -> tuple[str, str]:
    """``(card_path, rpc_path)`` relative to ``settings.a2a_base_url``.

    Contract (used by the host's route table): ``a2a_base_url.rstrip("/") +
    rpc_path`` equals the URL the card advertises (v0.3 ``url`` or the v1
    JSON-RPC interface URL). Both stacks share one dual-stack endpoint, so the
    paths do not depend on the version (the argument is accepted for clarity).
    """
    del settings, protocol_version
    rpc_path = f"/{org_id}" if org_id else ""
    return rpc_path + v1c.WELL_KNOWN_CARD_PATH, rpc_path


def _resolve_did(settings: Settings, did: str | None) -> str | None:
    """Resolve the agent DID: explicit override, else identity-derived did:web."""
    if did is not None:
        return did
    if (
        getattr(settings, "identity_enabled", False)
        and getattr(settings, "identity_did_method", "") == "web"
        and getattr(settings, "identity_did_web_domain", "")
    ):
        return f"did:web:{settings.identity_did_web_domain}"
    return None


def _cache_key(
    settings: Settings,
    registry: Mapping[str, list[str]],
    org_id: str | None,
    did: str | None,
    version: str,
) -> tuple[Any, ...]:
    return (
        version,
        tuple(getattr(settings, "a2a_inbound_auth_schemes", ())) if version == "1.0" else (),
        org_id,
        settings.a2a_base_url,
        tuple(settings.a2a_published_skills),
        bool(getattr(settings, "multimodal_enabled", False)),
        _resolve_did(settings, did),
        tuple(sorted(registry)),
    )


def _build_skills(settings: Settings, registry: Mapping[str, list[str]]) -> list[AgentSkill]:
    allowlist = list(settings.a2a_published_skills)
    skill_ids = allowlist if allowlist else sorted(registry)

    output_modes = _output_modes(settings)

    skills: list[AgentSkill] = []
    for sid in skill_ids:
        if sid not in registry:
            logger.warning("a2a.card.skill_not_in_registry", skill=sid)
            continue
        caps = list(registry[sid])
        skills.append(
            AgentSkill(
                id=sid,
                name=sid.replace("_", " ").title(),
                description=f"Prismal '{sid}' capability exposed over A2A.",
                tags=caps,
                output_modes=list(output_modes),
            )
        )
    return skills


def _output_modes(settings: Settings) -> list[str]:
    multimodal = bool(getattr(settings, "multimodal_enabled", False))
    return ["text/plain", *(_MEDIA_OUTPUT_MODES if multimodal else ())]


def _build_v1_card(
    settings: Settings,
    skills: list[AgentSkill],
    *,
    org_id: str | None,
    did: str | None,
) -> AgentCardV1:
    schemes: dict[str, SecuritySchemeV1] = {}
    for name in getattr(settings, "a2a_inbound_auth_schemes", ()):
        scheme = _V1_SCHEMES.get(name)
        if scheme is None:
            logger.warning("a2a.card.unknown_auth_scheme", scheme=name)
            continue
        schemes[name] = scheme
    extra: dict[str, Any] = {"did": did} if did is not None else {}
    return AgentCardV1(
        name="prismal",
        description="Prismal multi-agent framework exposed over A2A.",
        supported_interfaces=[
            AgentInterface(
                url=_card_url(settings.a2a_base_url, org_id),
                protocol_binding=v1c.BINDING_JSONRPC,
                protocol_version=v1c.A2A_V1_PROTOCOL_VERSION,
                tenant=org_id,
            )
        ],
        version=_resolve_version(),
        capabilities=AgentCapabilities(streaming=True, push_notifications=False),
        default_input_modes=["text/plain"],
        default_output_modes=_output_modes(settings),
        skills=[
            AgentSkillV1(
                id=sk.id,
                name=sk.name,
                description=sk.description,
                tags=list(sk.tags),
                input_modes=list(sk.input_modes),
                output_modes=list(sk.output_modes),
            )
            for sk in skills
        ],
        security_schemes=schemes or None,
        security_requirements=[
            SecurityRequirement(schemes={name: StringList()}) for name in schemes
        ]
        or None,
        **extra,
    )


@overload
def build_agent_card(
    settings: Settings,
    registry: Mapping[str, list[str]],
    *,
    org_id: str | None = ...,
    did: str | None = ...,
    protocol_version: Literal["0.3"],
) -> AgentCard:
    """Typed overload: ``protocol_version="0.3"`` returns a v0.3 :class:`AgentCard`."""


@overload
def build_agent_card(
    settings: Settings,
    registry: Mapping[str, list[str]],
    *,
    org_id: str | None = ...,
    did: str | None = ...,
    protocol_version: Literal["1.0"],
) -> AgentCardV1:
    """Typed overload: ``protocol_version="1.0"`` returns an :class:`AgentCardV1`."""


@overload
def build_agent_card(
    settings: Settings,
    registry: Mapping[str, list[str]],
    *,
    org_id: str | None = ...,
    did: str | None = ...,
    protocol_version: None = ...,
) -> AgentCard | AgentCardV1:
    """Typed overload: no explicit version, the ``a2a_protocol_version`` setting decides."""


def build_agent_card(
    settings: Settings,
    registry: Mapping[str, list[str]],
    *,
    org_id: str | None = None,
    did: str | None = None,
    protocol_version: v1c.A2AProtocolVersion | None = None,
) -> AgentCard | AgentCardV1:
    """Build the Agent Card from the capability registry (SPEC-A2A-002, PRT-A2A-003).

    Args:
        settings: Resolved prismal settings (reads the ``a2a_*`` block).
        registry: Mapping of skill/agent id → capability tags.
        org_id: Tenant id; scopes the advertised endpoint URL.
        did: Explicit DID override; otherwise derived from the identity settings.
        protocol_version: ``"0.3"`` (pre-PRT card) or ``"1.0"``; ``None`` ⇒
            ``settings.a2a_protocol_version``.

    Returns:
        A cached :class:`AgentCard` (v0.3) or :class:`AgentCardV1`. Identical
        inputs return the same instance until :func:`clear_agent_card_cache`.
    """
    version: str = protocol_version or str(getattr(settings, "a2a_protocol_version", "0.3"))
    key = _cache_key(settings, registry, org_id, did, version)
    cached = _card_cache.get(key)
    if cached is not None:
        return cached

    skills = _build_skills(settings, registry)
    card: AgentCard | AgentCardV1
    if version == "1.0":
        card = _build_v1_card(settings, skills, org_id=org_id, did=_resolve_did(settings, did))
    else:
        card = AgentCard(
            name="prismal",
            description="Prismal multi-agent framework exposed over A2A.",
            url=_card_url(settings.a2a_base_url, org_id),
            version=_resolve_version(),
            skills=skills,
            did=_resolve_did(settings, did),
        )
    _card_cache[key] = card
    return card


__all__ = ["a2a_route_paths", "build_agent_card", "clear_agent_card_cache"]

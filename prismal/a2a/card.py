"""Agent Card generation — Phase I (SPEC-A2A-002).

``build_agent_card`` derives the Agent Card served at
``/.well-known/agent-card.json`` from the agent/subgraph capability registry
(a ``Mapping[str, list[str]]`` such as
:data:`prismal.agents.tool_registry.DEFAULT_CAPABILITY_MAP`).

The published skills are an **allowlist**: ``settings.a2a_published_skills``
narrows what is exposed; when empty, every registry entry is published. The
result is cached per (settings fingerprint, org_id) — see
:func:`clear_agent_card_cache`.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from typing import TYPE_CHECKING, Any

from prismal.a2a.types import AgentCard, AgentSkill
from prismal.core.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Mapping

    from prismal.core.config import Settings

logger = get_logger(__name__)

# Extra output modalities advertised when the multimodal layer is enabled.
_MEDIA_OUTPUT_MODES = ("image/png", "audio/mpeg", "video/mp4")

_card_cache: dict[Any, AgentCard] = {}


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
) -> tuple[Any, ...]:
    return (
        org_id,
        settings.a2a_base_url,
        tuple(settings.a2a_published_skills),
        bool(getattr(settings, "multimodal_enabled", False)),
        _resolve_did(settings, did),
        tuple(sorted(registry)),
    )


def build_agent_card(
    settings: Settings,
    registry: Mapping[str, list[str]],
    *,
    org_id: str | None = None,
    did: str | None = None,
) -> AgentCard:
    """Build the Agent Card from the capability registry (SPEC-A2A-002).

    Args:
        settings: Resolved prismal settings (reads the ``a2a_*`` block).
        registry: Mapping of skill/agent id → capability tags.
        org_id: Tenant id; scopes the advertised endpoint URL.
        did: Explicit DID override; otherwise derived from the identity settings.

    Returns:
        A cached :class:`AgentCard`. Identical inputs return the same instance
        until :func:`clear_agent_card_cache` is called.
    """
    key = _cache_key(settings, registry, org_id, did)
    cached = _card_cache.get(key)
    if cached is not None:
        return cached

    allowlist = list(settings.a2a_published_skills)
    skill_ids = allowlist if allowlist else sorted(registry)

    multimodal = bool(getattr(settings, "multimodal_enabled", False))
    output_modes = ["text/plain", *(_MEDIA_OUTPUT_MODES if multimodal else ())]

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


__all__ = ["build_agent_card", "clear_agent_card_cache"]

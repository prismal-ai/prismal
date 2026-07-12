"""Specialist role registry for the Skynet swarm (SPEC-SP-REG-001, Phase S+).

A :class:`RoleRegistry` binds a ``SwarmOrder.role`` to a :class:`SpecialistRole`
profile — a per-role model, tool capabilities, system persona, and optional
remote A2A agent.  It is *data only* (DD-SP-001): heterogeneity is a property of
what a worker resolves for its role, not of the graph shape, so the swarm
topology stays fixed and testable.

``resolve`` is best-effort — it **never raises**, falling back to
:data:`DEFAULT_ROLE` for an unknown role (DD-SP-002).  Only ``from_yaml`` can
raise, and only once, at load time, on a malformed file
(:class:`~prismal.core.exceptions.SkynetRoleError`).  The YAML load mirrors the
Phase-H :func:`~prismal.security.tool_policy.load_tool_policies` pattern.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from prismal.core.exceptions import SkynetRoleError
from prismal.core.logging import get_logger

if TYPE_CHECKING:
    from prismal.core.config import Settings

logger = get_logger("prismal.agents.skynet.roles")


@dataclass(frozen=True)
class SpecialistRole:
    """One specialist worker profile, keyed by capability.

    Attributes:
        name: Role key, e.g. ``"researcher"``.
        model: Per-role model override; ``None`` → ``skynet_worker_model``.
        capabilities: Tool filter passed to the ``ToolProviderPort``.
        persona: Role system prompt.  **Trusted** operator config (not
            user-derived) — not sanitized, but never f-string-injected either.
        remote_agent: A2A card URL; when set the order is delegated over A2A
            (Phase S+3) instead of run in-process.
    """

    name: str
    model: str | None = None
    capabilities: list[str] = field(default_factory=list)
    persona: str = ""
    remote_agent: str | None = None


#: The generic fallback used when specialists are disabled or a role is unknown.
#: ``capabilities=["general"]`` mirrors the Phase-S worker's ``[order.role]``
#: resolution for ``role="worker"``.
DEFAULT_ROLE = SpecialistRole(name="worker", capabilities=["general"])


class RoleRegistry:
    """Loads and resolves :class:`SpecialistRole` profiles (never raises at resolve)."""

    def __init__(self, roles: dict[str, SpecialistRole] | None = None) -> None:
        """Initialise the registry with an optional pre-built role map."""
        self._roles: dict[str, SpecialistRole] = dict(roles or {})

    @classmethod
    def from_yaml(
        cls,
        path: str | Path,
        *,
        settings: Settings | None = None,  # noqa: ARG003 — SPEC signature; reserved for per-tenant
    ) -> RoleRegistry:
        """Load roles from ``skynet_roles.yaml`` (mirrors ``load_tool_policies``).

        A missing file yields an empty registry (all → :data:`DEFAULT_ROLE`).
        A malformed file raises :class:`SkynetRoleError` — at load time only.

        Args:
            path: Path to the role YAML file.
            settings: Reserved for future per-tenant resolution (unused today).

        Returns:
            A populated :class:`RoleRegistry`.

        Raises:
            SkynetRoleError: when the YAML is malformed or misshapen.
        """
        resolved = Path(path)
        if not resolved.exists():
            logger.info("skynet.roles_no_file", path=str(resolved))
            return cls({})

        try:
            with resolved.open(encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        except yaml.YAMLError as exc:
            raise SkynetRoleError(f"skynet_roles YAML is malformed: {exc}") from exc

        raw_roles = data.get("roles") or {}
        if not isinstance(raw_roles, dict):
            raise SkynetRoleError("skynet_roles 'roles' must be a mapping")

        roles: dict[str, SpecialistRole] = {}
        for name, raw in raw_roles.items():
            if not isinstance(raw, dict):
                raise SkynetRoleError(f"role {name!r} must be a mapping")
            caps = raw.get("capabilities") or []
            if not isinstance(caps, list):
                raise SkynetRoleError(f"role {name!r} capabilities must be a list")
            key = str(name)
            roles[key] = SpecialistRole(
                name=key,
                model=(str(raw["model"]) if raw.get("model") else None),
                capabilities=[str(c) for c in caps],
                persona=str(raw.get("persona", "")),
                remote_agent=(str(raw["remote_agent"]) if raw.get("remote_agent") else None),
            )
        return cls(roles)

    def resolve(self, role_name: str) -> SpecialistRole:
        """Return the role for *role_name*, or :data:`DEFAULT_ROLE` if unknown.

        Best-effort — never raises (RF-SP-02)."""
        return self._roles.get(role_name, DEFAULT_ROLE)

    def known_roles(self) -> list[str]:
        """Return the registered role names (order-preserving)."""
        return list(self._roles.keys())


__all__ = ["DEFAULT_ROLE", "RoleRegistry", "SpecialistRole"]

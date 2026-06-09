"""Centralized, pure config loaders for the composition root (Phase R — R3).

These functions are **sync and side-effect-free**: they read configuration the
runtime consumes (MCP catalogue, skills layout, vector-store backend, per-tenant
overrides) without opening connections, mutating the filesystem, or touching the
process singletons. ``prismal-dashboard`` reads the same sources to render its
config UI (SPEC-CR-005); :func:`prismal.composition.build_runtime` calls them to
assemble a :class:`~prismal.composition.RuntimeConfig`.

Tenant resolution lives here too: :func:`collection_for` derives a per-``org_id``
collection name applied **identically** to RAG and memory (R4), so a tenant sees
the same isolated collection in both subsystems.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from prismal.core.logging import get_logger

if TYPE_CHECKING:
    from prismal.core.config import Settings

logger = get_logger("prismal.composition.config_sources")

#: Logical base collection used for RAG when the host does not override it.
DEFAULT_COLLECTION_BASE = "default"


def collection_for(base: str, org_id: str | None) -> str:
    """Derive the tenant-scoped collection name (SPEC-CR-005, R4).

    Returns *base* unchanged for the single-tenant case (``org_id is None``) so
    existing collections keep their names; otherwise suffixes the tenant id::

        collection_for("docs", None)    -> "docs"
        collection_for("docs", "acme")  -> "docs_acme"
    """
    return base if org_id is None else f"{base}_{org_id}"


@dataclass(frozen=True)
class McpServerInfo:
    """Summary of one MCP server entry (no secrets, no connection)."""

    name: str
    type: str
    enabled: bool
    capabilities: tuple[str, ...]


@dataclass(frozen=True)
class McpConfig:
    """Parsed view of ``config/mcp_servers.yaml`` (SPEC-CR-005)."""

    path: Path | None
    exists: bool
    servers: tuple[McpServerInfo, ...] = ()

    @property
    def enabled_count(self) -> int:
        """Number of servers flagged ``enabled: true``."""
        return sum(1 for s in self.servers if s.enabled)


def load_mcp_config(path: Path | None = None) -> McpConfig:
    """Parse the MCP server catalogue without connecting (SPEC-CR-005).

    Args:
        path: Override for the YAML location. ``None`` uses the package default
            (``config/mcp_servers.yaml``).

    Returns:
        An :class:`McpConfig` describing the declared servers. A missing file
        yields ``exists=False`` with no servers (not an error — MCP is opt-in).
    """
    from prismal.mcp.client import _DEFAULT_CONFIG_PATH

    resolved = path if path is not None else _DEFAULT_CONFIG_PATH
    if not resolved.exists():
        return McpConfig(path=resolved, exists=False)

    import yaml

    try:
        raw = yaml.safe_load(resolved.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # pragma: no cover - malformed yaml is operator error
        logger.warning("composition.mcp_config_unreadable", path=str(resolved), error=str(exc))
        return McpConfig(path=resolved, exists=True)

    servers: list[McpServerInfo] = []
    for entry in raw.get("servers", []) or []:
        if not isinstance(entry, dict):
            continue
        servers.append(
            McpServerInfo(
                name=str(entry.get("name", "")),
                type=str(entry.get("type", "")),
                enabled=bool(entry.get("enabled", False)),
                capabilities=tuple(entry.get("capabilities", []) or []),
            )
        )
    return McpConfig(path=resolved, exists=True, servers=tuple(servers))


@dataclass(frozen=True)
class SkillsSource:
    """Description of the skills directory layout the runtime reads (SPEC-CR-005)."""

    available_dir: Path
    active_dir: Path
    custom_dir: Path
    external_dirs: tuple[Path, ...] = ()
    available_names: tuple[str, ...] = field(default=())


def resolve_skills_source(settings: Settings) -> SkillsSource:
    """Describe the skills directories and installed skills (SPEC-CR-005).

    Pure: it only inspects directory entries; it does not instantiate
    ``SkillsManager`` (which would restore active symlinks) nor load any skill.

    Args:
        settings: Application settings (read for ``external_skills_dirs``).

    Returns:
        A :class:`SkillsSource` with the three managed dirs, any configured
        external dirs, and the names of installed (``available/``) skills.
    """
    skills_root = Path(__file__).resolve().parent.parent / "skills"
    available = skills_root / "available"

    names: list[str] = []
    if available.is_dir():
        names = sorted(p.name for p in available.iterdir() if p.is_dir())

    external = tuple(Path(d).expanduser().resolve() for d in settings.external_skills_dirs if d)

    return SkillsSource(
        available_dir=available,
        active_dir=skills_root / "active",
        custom_dir=skills_root / "custom",
        external_dirs=external,
        available_names=tuple(names),
    )


def resolve_vector_store(settings: Settings, org_id: str | None) -> tuple[str, str]:
    """Resolve the vector-store backend and tenant collection (SPEC-CR-005, R4).

    Returns:
        ``(backend, collection_name)`` where ``backend`` is
        ``settings.vector_store_backend`` and ``collection_name`` is
        :func:`collection_for` applied to :data:`DEFAULT_COLLECTION_BASE`.
    """
    backend = settings.vector_store_backend
    return backend, collection_for(DEFAULT_COLLECTION_BASE, org_id)


def apply_org_overrides(
    settings: Settings,
    org_id: str | None,
    overrides: dict[str, Any] | None,
) -> Settings:
    """Return effective per-tenant settings without mutating the global (SPEC-CR-005).

    The tenant identity (*org_id*) does not change settings fields directly — it
    drives collection naming via :func:`collection_for`. *overrides* (when given)
    are applied as a validated copy so a tenant can pin, e.g., a different
    ``vector_store_backend`` or model.

    Args:
        settings: Base settings (left untouched).
        org_id: Tenant id, accepted for symmetry / future per-org policy.
        overrides: Field overrides to apply on top of *settings*.

    Returns:
        ``settings`` itself when there are no overrides, otherwise a new
        validated ``Settings`` copy carrying them.
    """
    del org_id  # reserved — collection naming is handled by collection_for
    if not overrides:
        return settings
    # model_copy keeps the global untouched and avoids re-reading env / re-running
    # field resolvers; the host is responsible for passing correctly-typed values.
    return settings.model_copy(update=overrides)


__all__ = [
    "DEFAULT_COLLECTION_BASE",
    "McpConfig",
    "McpServerInfo",
    "SkillsSource",
    "apply_org_overrides",
    "collection_for",
    "load_mcp_config",
    "resolve_skills_source",
    "resolve_vector_store",
]

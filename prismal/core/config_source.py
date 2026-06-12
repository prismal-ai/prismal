"""Configuration source inversion (Phase W — ``ConfigSourcePort``).

The core no longer *reads* its own environment; it *consumes* an injected
:class:`ConfigSourcePort` that *supplies* the raw configuration values, which
:class:`prismal.core.config.Settings` then validates. Producing those values —
from ``os.environ`` / ``.env``, Vault, AWS Secrets Manager, a tenant row, or an
in-memory dict — moves out of the core into the host that owns it.

The change is additive and opt-in: with no source injected, the default
:class:`EnvConfigSource` reproduces today's behaviour byte-for-byte
(``PRISMAL_*`` env + ``.env`` + the legacy ``LIGHTAGENT_`` mirror), so an
existing deployment that does nothing keeps working.

This module is the **only** place in the core (besides the single LiteLLM
``os.environ.setdefault`` write-bridge in ``providers/registry.py``) that may
read ``os.environ`` / the ``.env`` file for configuration.

Mirrors the Phase Y (``ToolProviderPort``) / Phase Z (``VectorStorePort``)
hexagonal-port playbook: Protocol + concrete sources + Fake + a global
injection registry.
"""

from __future__ import annotations

import os
import warnings
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from pydantic import SecretStr

from prismal.core.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Mapping

logger = get_logger(__name__)

ConfigValue = str | SecretStr

_LEGACY_PREFIX = "LIGHTAGENT_"
_NEW_PREFIX = "PRISMAL_"

# Well-known unprefixed provider keys honoured for parity with today's behaviour
# (``Settings`` declares them as validation aliases).
_UNPREFIXED_ALLOWLIST = frozenset(
    {
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "GOOGLE_API_KEY",
        "TAVILY_API_KEY",
        "LANGFUSE_HOST",
        "LANGFUSE_PUBLIC_KEY",
        "LANGFUSE_SECRET_KEY",
    }
)

# Process-level guard so the legacy-alias DeprecationWarning is emitted at most
# once per process (matching the old ``env_compat._applied`` behaviour), even
# though ``load()`` may be called many times across ``Settings`` rebuilds.
_legacy_warning_emitted = False


def _reset_legacy_warning_for_tests() -> None:
    """Reset the once-per-process legacy-warning guard (test seam only)."""
    global _legacy_warning_emitted
    _legacy_warning_emitted = False


@runtime_checkable
class ConfigSourcePort(Protocol):
    """A source of raw configuration values consumed by ``Settings``.

    Conforming shapes: :class:`EnvConfigSource`, :class:`MappingConfigSource`,
    :class:`ChainedConfigSource`, :class:`FakeConfigSource`, and any host source
    (Vault, AWS Secrets Manager, a DB row). The core only ever invokes
    :meth:`load`; it never constructs concrete sources.
    """

    def load(self) -> Mapping[str, ConfigValue]:
        """Return the raw configuration mapping. Sync; must not raise."""
        ...


def _parse_dotenv(path: Path) -> dict[str, str]:
    """Minimal ``.env`` parser (``KEY=VALUE`` lines, ``#`` comments).

    Mirrors ``pydantic-settings`` dotenv semantics closely enough for parity:
    blank lines and comments are skipped, surrounding quotes are stripped, and a
    missing file yields an empty mapping (never raises).
    """
    values: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return values
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key.lower().startswith("export "):
            key = key[len("export ") :].strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        if key:
            values[key] = value
    return values


class EnvConfigSource:
    """Default source — reproduces today's behaviour exactly.

    Reads, in this precedence (highest first):

    1. ``env`` (defaults to ``os.environ``): ``PRISMAL_*`` and the accepted
       unprefixed provider keys.
    2. the dotenv file at ``dotenv_path`` (default ``".env"``), if present.

    and folds in the legacy ``LIGHTAGENT_* → PRISMAL_*`` mirror (only where the
    ``PRISMAL_`` name is unset), emitting a single ``DeprecationWarning`` the
    first time a legacy key is mirrored. The mirror is computed *into the
    returned mapping* — never by mutating ``os.environ`` — so the global
    side effect of the old ``env_compat`` module is removed.
    """

    def __init__(
        self,
        *,
        dotenv_path: str | Path | None = ".env",
        env: Mapping[str, str] | None = None,
        include_legacy_aliases: bool = True,
    ) -> None:
        """Initialize the environment-backed config source."""
        self._dotenv_path = Path(dotenv_path) if dotenv_path is not None else None
        self._env = env
        self._include_legacy_aliases = include_legacy_aliases

    def load(self) -> Mapping[str, ConfigValue]:
        """Resolve env + ``.env`` + legacy mirror into one mapping (never raises)."""
        env = self._env if self._env is not None else os.environ
        result: dict[str, ConfigValue] = {}

        # Lowest precedence: dotenv file.
        if self._dotenv_path is not None:
            result.update(_parse_dotenv(self._dotenv_path))

        # Highest precedence: live environment (prefixed + allowlisted unprefixed).
        for key, value in env.items():
            if key.startswith(_NEW_PREFIX) or key in _UNPREFIXED_ALLOWLIST:
                result[key] = value

        if self._include_legacy_aliases:
            self._apply_legacy_mirror(env, result)

        return result

    def _apply_legacy_mirror(self, env: Mapping[str, str], result: dict[str, ConfigValue]) -> None:
        """Mirror ``LIGHTAGENT_*`` onto ``PRISMAL_*`` where the new name is unset."""
        mirrored: list[str] = []
        for key, value in env.items():
            if not key.startswith(_LEGACY_PREFIX):
                continue
            new_key = _NEW_PREFIX + key[len(_LEGACY_PREFIX) :]
            if new_key not in result:
                result[new_key] = value
                mirrored.append(key)

        if mirrored:
            self._warn_legacy(sorted(mirrored))

    @staticmethod
    def _warn_legacy(mirrored: list[str]) -> None:
        global _legacy_warning_emitted
        logger.warning("config.legacy_alias_mirrored", keys=mirrored)
        if _legacy_warning_emitted:
            return
        _legacy_warning_emitted = True
        warnings.warn(
            "The 'LIGHTAGENT_' environment-variable prefix is deprecated; "
            "rename these to 'PRISMAL_': " + ", ".join(mirrored) + ". "
            "Legacy names still work for now but will be removed in a future "
            "major release.",
            DeprecationWarning,
            stacklevel=3,
        )


class MappingConfigSource:
    """In-memory source from an explicit mapping (dict / tenant row / payload)."""

    def __init__(self, values: Mapping[str, ConfigValue]) -> None:
        """Store a defensive copy of *values*."""
        self._values = dict(values)

    def load(self) -> Mapping[str, ConfigValue]:
        """Return a defensive copy of the stored values. No I/O, never raises."""
        return dict(self._values)


class ChainedConfigSource:
    """Ordered composite: earlier sources win over later ones (first = highest)."""

    def __init__(self, sources: list[ConfigSourcePort]) -> None:
        """Store the ordered list of sub-sources."""
        self._sources = list(sources)

    def load(self) -> Mapping[str, ConfigValue]:
        """Merge each sub-source; earliest source wins on duplicate keys.

        A sub-source whose ``load()`` raises is caught, logged
        (``config_source.subsource_error``), and skipped — parity with Phase Y's
        ``CompositeToolProvider``. Never raises.
        """
        merged: dict[str, ConfigValue] = {}
        for source in self._sources:
            try:
                loaded = source.load()
            except Exception as exc:
                logger.warning(
                    "config_source.subsource_error",
                    source=type(source).__name__,
                    error=str(exc),
                )
                continue
            for key, value in loaded.items():
                merged.setdefault(key, value)  # first (highest-priority) wins
        return merged


class FakeConfigSource:
    """Deterministic test source. No environment access, no ``.env`` discovery."""

    def __init__(self, values: Mapping[str, ConfigValue] | None = None) -> None:
        """Store the test values (defaults to empty)."""
        self._values = dict(values or {})

    def load(self) -> Mapping[str, ConfigValue]:
        """Return the stored values. Pure."""
        return dict(self._values)


# ── Injection registry (variant A — global) ──────────────────────────────────

_source: ConfigSourcePort | None = None


def set_config_source(source: ConfigSourcePort | None) -> None:
    """Inject (or clear) the global config source.

    The host calls this once at startup. Invalidates the ``get_settings()``
    cache so the next read resolves through the new source. Passing ``None``
    clears the injection (the default :class:`EnvConfigSource` resumes).
    """
    global _source
    _source = source
    from prismal.core.config import reload_settings

    reload_settings()
    if source is not None:
        logger.info("config.source_injected", source=type(source).__name__)


def get_config_source() -> ConfigSourcePort | None:
    """Return the injected global source, or ``None`` if none was set."""
    return _source


def _default_source() -> ConfigSourcePort:
    """Return the default source used when none is injected (strict mode off)."""
    return EnvConfigSource()


__all__ = [
    "ChainedConfigSource",
    "ConfigSourcePort",
    "ConfigValue",
    "EnvConfigSource",
    "FakeConfigSource",
    "MappingConfigSource",
    "get_config_source",
    "set_config_source",
]

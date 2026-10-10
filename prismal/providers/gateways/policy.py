"""Gateway data policy and ``gw:`` model references (Phase GW — SPEC-GW-POL-001).

Grammar: ``gw:<gateway>/<upstream-model>[:<variant>]`` where ``<gateway>``
matches ``[a-z0-9][a-z0-9-]{0,31}`` and ``<upstream-model>`` may contain ``/``.
Any string not starting with ``gw:`` is the legacy path (``None``, never raises).
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from typing import Literal

from prismal.core.exceptions import GatewayConfigError

GW_PREFIX = "gw:"
_REF_RE = re.compile(
    r"^gw:(?P<gateway>[a-z0-9][a-z0-9-]{0,31})/"
    r"(?P<model>[A-Za-z0-9._~@+-]+(?:/[A-Za-z0-9._~@+-]+)*)"
    r"(?::(?P<variant>[A-Za-z0-9._-]+))?$"
)


@dataclass(frozen=True)
class GatewayDataPolicy:
    """Upstream data-handling constraints a gateway is asked to enforce."""

    data_collection: Literal["deny", "allow"] = "deny"
    zdr: bool = True
    denied_model_patterns: tuple[str, ...] = ()  # fnmatch, case-insensitive
    require_parameters_for_tools: bool = True

    def is_denied(self, upstream_model: str) -> bool:
        """True when *upstream_model* matches any denied pattern (case-insensitive)."""
        name = upstream_model.lower()
        return any(fnmatch.fnmatchcase(name, p.lower()) for p in self.denied_model_patterns)


@dataclass(frozen=True)
class ModelRef:
    """A parsed ``gw:`` reference."""

    gateway: str
    model: str  # without variant
    variant: str | None


def parse_model_ref(ref: str) -> ModelRef | None:
    """Parse *ref*; ``None`` for a non-``gw:`` string, :class:`GatewayConfigError` if malformed."""
    if not ref.startswith(GW_PREFIX):
        return None
    match = _REF_RE.match(ref)
    if match is None:
        raise GatewayConfigError(f"malformed gateway model reference: {ref!r}")
    return ModelRef(
        gateway=match.group("gateway"),
        model=match.group("model"),
        variant=match.group("variant"),
    )


__all__ = ["GW_PREFIX", "GatewayDataPolicy", "ModelRef", "parse_model_ref"]

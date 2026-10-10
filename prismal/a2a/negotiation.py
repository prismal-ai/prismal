"""A2A protocol-version negotiation (SPEC-PRT-A2A-002, amended by ADR-PRT-001 §2.1).

Deterministic, per request (inbound) or per card (outbound); no trial requests.

- **Inbound** precedence: (1) an explicit, non-empty ``A2A-Version`` header;
  (2) the JSON-RPC method family (PascalCase v1 names vs slash v0.3 names —
  they do not overlap); (3) the header-less protocol default ``0.3``. A v0.3
  outcome with ``a2a_accept_legacy=False`` — or an unsupported header version —
  returns ``None`` (the handler answers ``-32009``).
- **Outbound**: the versions a card advertises over JSON-RPC; ``preferred`` wins
  only when advertised, otherwise the highest advertised, otherwise ``0.3``.

Both helpers are hot-path: they never raise.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from prismal.a2a.v1 import constants as c
from prismal.a2a.v1.constants import A2AProtocolVersion

if TYPE_CHECKING:
    from prismal.core.config import Settings


def normalize_version(raw: object) -> A2AProtocolVersion | None:
    """Map ``"1"``/``"1.x"``/``"1.x.y"`` → ``"1.0"``, ``"0.3[.x]"`` → ``"0.3"``; else ``None``."""
    if not isinstance(raw, str):
        return None
    parts = raw.strip().split(".")
    if not parts or not parts[0].isdigit():
        return None
    if parts[0] == "1":
        return "1.0"
    if parts[0] == "0" and len(parts) > 1 and parts[1] == "3":
        return "0.3"
    return None


def _header(headers: Mapping[str, Any] | None, name: str) -> object:
    if not headers:
        return None
    wanted = name.lower()
    for key, value in headers.items():
        if isinstance(key, str) and key.lower() == wanted:
            return value
    return None


def _accept_legacy(settings: Settings | None) -> bool:
    if settings is None:
        return True
    return bool(settings.a2a_accept_legacy)


def _negotiate(
    payload: Mapping[str, Any] | None, headers: Mapping[str, Any] | None
) -> A2AProtocolVersion | None:
    """Raw negotiation, before the legacy-acceptance gate (may return None)."""
    raw_header = _header(headers, c.VERSION_HEADER)
    if isinstance(raw_header, str) and raw_header.strip():
        return normalize_version(raw_header)
    method = payload.get("method") if isinstance(payload, Mapping) else None
    if isinstance(method, str) and method in c.V1_METHODS:
        return "1.0"
    return "0.3"


def negotiate_inbound(
    payload: Mapping[str, Any],
    headers: Mapping[str, Any] | None,
    *,
    settings: Settings | None,
) -> A2AProtocolVersion | None:
    """Pick the inbound stack; ``None`` ⇒ refuse with ``-32009``. Never raises."""
    try:
        version = _negotiate(payload, headers)
        if version == "0.3" and not _accept_legacy(settings):
            return None
        return version
    except Exception:
        return None


def _advertised(card: Mapping[str, Any]) -> list[tuple[A2AProtocolVersion, str | None, str | None]]:
    """``(version, url, tenant)`` of every JSON-RPC interface a card advertises."""
    found: list[tuple[A2AProtocolVersion, str | None, str | None]] = []
    interfaces = card.get("supportedInterfaces")
    if isinstance(interfaces, list):
        for iface in interfaces:
            if not isinstance(iface, Mapping):
                continue
            binding = iface.get("protocolBinding", c.BINDING_JSONRPC)
            version = normalize_version(iface.get("protocolVersion"))
            if binding != c.BINDING_JSONRPC or version is None:
                continue
            url = iface.get("url")
            tenant = iface.get("tenant")
            found.append(
                (
                    version,
                    url if isinstance(url, str) else None,
                    tenant if isinstance(tenant, str) else None,
                )
            )
    top = normalize_version(card.get("protocolVersion"))
    if top is not None:
        url = card.get("url")
        found.append((top, url if isinstance(url, str) else None, None))
    return found


def select_outbound(
    card: Mapping[str, Any], *, preferred: A2AProtocolVersion | None = None
) -> A2AProtocolVersion:
    """Choose the outbound version for a remote card. Never raises (default ``0.3``)."""
    try:
        versions = {v for v, _, _ in _advertised(card)}
    except Exception:
        return "0.3"
    if preferred is not None and preferred in versions:
        return preferred
    return "1.0" if "1.0" in versions else "0.3"


def select_interface_url(
    card: Mapping[str, Any], version: A2AProtocolVersion
) -> tuple[str | None, str | None]:
    """``(url, tenant)`` of the first JSON-RPC interface for *version*. Never raises."""
    try:
        for v, url, tenant in _advertised(card):
            if v == version and url:
                return url, tenant
        url = card.get("url")
        return (url if isinstance(url, str) else None), None
    except Exception:
        return None, None


def _otel() -> Any:
    from prismal.monitoring.otel import OTelManager

    return OTelManager()


def count_a2a_request(*, protocol_version: str, direction: str, method: str, outcome: str) -> None:
    """Increment ``prismal.a2a_requests_total`` (SPEC-PRT-OTEL-001). Never raises."""
    try:
        _otel().increment_counter(
            "a2a_requests",
            attributes={
                "protocol_version": protocol_version,
                "direction": direction,
                "method": method,
                "outcome": outcome,
            },
        )
    except Exception:
        return


__all__ = [
    "A2AProtocolVersion",
    "count_a2a_request",
    "negotiate_inbound",
    "normalize_version",
    "select_interface_url",
    "select_outbound",
]

"""OmniRoute gateway + compliance guard (Phase GW — SPEC-GW-OMNI-001).

OmniRoute is a self-hosted OpenAI-compatible router. Prismal cannot inspect its
upstream combos, so the guard is layered and fail-closed (DD-GW-007): a
mandatory upstream attestation, a combo deny-list, a mandatory API key and a
CVE-2026-88062 version floor checked once at startup (VERIFY.md GW0-07).
"""

from __future__ import annotations

import ipaddress
import re
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from prismal.core.logging import get_logger
from prismal.providers.gateways.base import GatewayCapabilities, GatewayProfile
from prismal.providers.gateways.openai_compatible import (
    OpenAICompatibleGateway,
    SecretResolver,
    count,
)
from prismal.providers.gateways.policy import GatewayDataPolicy, ModelRef

if TYPE_CHECKING:
    import httpx

    from prismal.core.config import Settings
    from prismal.providers.gateways.base import GatewayPort
    from prismal.providers.gateways.quota import QuotaLedger

logger = get_logger("prismal.providers.gateways.omniroute")

GATEWAY_NAME = "omniroute"
HEALTH_PATH = "/api/monitoring/health"
VERSION_HEADER = "x-omniroute-version"
_SEMVER = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")


def parse_version(value: object) -> tuple[int, int, int] | None:
    """``MAJOR.MINOR.PATCH`` (optional leading ``v``) → int tuple; else ``None``."""
    if not isinstance(value, str):
        return None
    match = _SEMVER.match(value.strip())
    if match is None:
        return None
    major, minor, patch = (int(g) for g in match.groups())
    return major, minor, patch


def _normalize(value: object) -> str | None:
    parsed = parse_version(value)
    return None if parsed is None else ".".join(str(p) for p in parsed)


_PRIVATE_SUFFIXES = (".localhost", ".local", ".internal")


def is_public_plain_http(base_url: str) -> bool:
    """``True`` for ``http://`` to a non-loopback, non-private host (no DNS lookup).

    Non-IP hostnames other than ``localhost`` / ``*.localhost`` / ``*.local`` /
    ``*.internal`` are treated as public.
    """
    parts = urlsplit(base_url)
    if parts.scheme.lower() != "http":
        return False
    host = (parts.hostname or "").lower().rstrip(".")
    if not host or host == "localhost" or host.endswith(_PRIVATE_SUFFIXES):
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return True
    return not (ip.is_loopback or ip.is_private or ip.is_link_local)


class OmniRouteGateway(OpenAICompatibleGateway):
    """OmniRoute adapter: ``gw:omniroute/<model>`` → ``openai/<model>``."""

    def __init__(
        self,
        *,
        settings: Settings,
        secret_resolver: SecretResolver,
        org_id: str | None = None,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        profile = GatewayProfile(
            name=GATEWAY_NAME,
            base_url=settings.omniroute_base_url,
            auth_ref="omniroute_api_key",
            data_policy=GatewayDataPolicy(
                denied_model_patterns=tuple(settings.omniroute_denied_combos)
            ),
            capabilities=GatewayCapabilities(enforces_data_policy=False),
        )
        super().__init__(profile, secret_resolver=secret_resolver, settings=settings, http=http)
        self._omni_settings = settings
        self.org_id = org_id
        self._startup_ok: bool | None = None  # None = not checked yet
        self._startup_reason: str | None = None
        self._version_declared = False
        self._public_http_warned = False

    def __repr__(self) -> str:
        return f"OmniRouteGateway(base_url={self._profile.base_url!r}, org_id={self.org_id!r})"

    # ── pure guards ──────────────────────────────────────────────────────────

    def _attestation_denial(self) -> str | None:
        """Reason the attestation matrix refuses OmniRoute, or ``None`` (pure)."""
        s = self._omni_settings
        if s.omniroute_upstream_attestation is None:
            return "omniroute_attestation_missing"
        if (
            s.omniroute_upstream_attestation == "subscription_oauth"
            and not s.omniroute_allow_subscription_upstreams
        ):
            return "omniroute_subscription_upstreams"
        return None

    def _check_policy(self, ref: ModelRef, *, agent: str | None) -> None:
        # Fail closed until astartup_check() passed (key, attestation, version).
        if self._startup_ok is None:
            self._deny("omniroute_unverified_version", agent=agent)
        if self._startup_ok is False:
            self._deny(self._startup_reason or "omniroute_startup_failed", agent=agent)
        policy = self._profile.data_policy
        if policy.is_denied(ref.model) or policy.is_denied(self._upstream(ref)):
            self._deny("omniroute_denied_combo", agent=agent)

    # ── startup gate (one-shot I/O) ──────────────────────────────────────────

    def _health_url(self) -> str:
        root = self._profile.base_url.rstrip("/")
        root = root.removesuffix("/v1")
        return f"{root}{HEALTH_PATH}"

    async def detect_version(self) -> str | None:
        """Running OmniRoute version (VERIFY.md GW0-07).

        OmniRoute documents no version endpoint: read ``version`` / ``data.version``
        from the health JSON or the ``x-omniroute-version`` header when present,
        else ``omniroute_declared_version``, else ``None``. Fail-open on errors.
        """
        self._version_declared = False
        detected: str | None = None
        try:
            response = await self._client().get(self._health_url(), headers=self._auth_headers())
            response.raise_for_status()
            detected = _normalize(response.headers.get(VERSION_HEADER))
            if detected is None:
                detected = _version_from_body(response.json())
        except Exception as exc:
            count("gateway_errors", gateway=self.name, kind="version_probe")
            logger.info("gateway.omniroute_version_probe_failed", error=type(exc).__name__)
        if detected is not None:
            return detected
        declared = _normalize(self._omni_settings.omniroute_declared_version)
        if declared is not None:
            self._version_declared = True
        return declared

    async def astartup_check(self) -> None:
        """Key, attestation and version gate; a refusal disables the gateway."""
        self._warn_public_http()
        try:
            await self._startup_gate()
        except Exception as exc:
            self._startup_ok = False
            self._startup_reason = getattr(exc, "reason", None) or type(exc).__name__
            raise
        self._startup_ok = True
        self._startup_reason = None

    def _warn_public_http(self) -> None:
        """Warn once per instance on a public plain-HTTP base URL (never blocks)."""
        if self._public_http_warned or not is_public_plain_http(self._profile.base_url):
            return
        self._public_http_warned = True
        count("gateway_policy_denials", gateway=self.name, reason="omniroute_public_http")
        logger.warning(
            "gateway.omniroute_public_http",
            gateway=self.name,
            detail="plain http to a public host; bind OmniRoute to loopback/private network",
        )

    async def _startup_gate(self) -> None:
        s = self._omni_settings
        self._secret()  # mandatory key: GatewayCredentialError propagates
        reason = self._attestation_denial()
        if reason is not None:
            self._deny(reason, agent=None)
        if s.omniroute_upstream_attestation == "subscription_oauth":
            logger.warning(
                "gateway.omniroute_subscription_upstreams",
                gateway=self.name,
                detail="subscription-OAuth upstreams violate provider ToS (account bans)",
            )
        version = await self.detect_version()
        if version is None:
            if not s.omniroute_allow_unknown_version:
                self._deny("omniroute_unknown_version", agent=None)
            logger.warning("gateway.omniroute_version_unknown_allowed", gateway=self.name)
            return
        if self._version_declared:
            logger.warning("gateway.omniroute_version_declared", gateway=self.name, version=version)
        floor = parse_version(s.omniroute_min_version) or (0, 0, 0)
        detected = parse_version(version) or (0, 0, 0)
        if detected < floor:
            self._deny("omniroute_vulnerable_version", agent=None)


def _version_from_body(body: Any) -> str | None:
    if not isinstance(body, dict):
        return None
    found = _normalize(body.get("version"))
    data = body.get("data")
    if found is None and isinstance(data, dict):
        found = _normalize(data.get("version"))
    return found


def build_from_settings(
    settings: Settings,
    *,
    org_id: str | None,
    secret_resolver: SecretResolver,
    ledger: QuotaLedger | None,  # noqa: ARG001 — OmniRoute keeps no quota ledger
) -> dict[str, GatewayPort]:
    """``{"omniroute": OmniRouteGateway}`` for :func:`build_gateway_router`."""
    return {
        GATEWAY_NAME: OmniRouteGateway(
            settings=settings, secret_resolver=secret_resolver, org_id=org_id
        )
    }


__all__ = ["OmniRouteGateway", "build_from_settings", "is_public_plain_http", "parse_version"]

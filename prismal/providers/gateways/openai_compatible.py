"""Generic OpenAI-compatible gateway adapter (Phase GW — SPEC-GW-GEN-001).

The shared base of every gateway adapter: parse the ``gw:`` reference, apply
the fail-closed data policy, resolve the secret lazily through an injected
``SecretResolver`` and return ``ResolvedModel`` kwargs — all without I/O.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from typing import TYPE_CHECKING, Any, NoReturn

from pydantic import SecretStr

from prismal.core.exceptions import GatewayConfigError, GatewayCredentialError, GatewayPolicyError
from prismal.core.logging import get_logger
from prismal.providers.gateways.base import (
    GatewayCapabilities,
    GatewayProfile,
    GenerationStats,
    QuotaSnapshot,
    ReportedCost,
    ResolvedModel,
    WireProtocol,
    read_reported_cost,
)
from prismal.providers.gateways.policy import GatewayDataPolicy, ModelRef, parse_model_ref

if TYPE_CHECKING:
    import httpx

    from prismal.core.config import Settings

logger = get_logger("prismal.providers.gateways")

# auth_ref -> secret (vault-backed, tenant-scoped); None when missing.
SecretResolver = Callable[[str], SecretStr | None]


def count(metric: str, **attributes: Any) -> None:
    """Increment a gateway OTel counter; never raises."""
    with suppress(Exception):
        from prismal.monitoring import otel

        otel.OTelManager().increment_counter(metric, attributes=attributes)


class OpenAICompatibleGateway:
    """Any gateway speaking the OpenAI ``/chat/completions`` API."""

    def __init__(
        self,
        profile: GatewayProfile,
        *,
        secret_resolver: SecretResolver,
        settings: Settings | None = None,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self._profile = profile
        self._secret_resolver = secret_resolver
        self._settings = settings
        self._http = http
        self._owns_http = False

    @property
    def profile(self) -> GatewayProfile:
        return self._profile

    @property
    def name(self) -> str:
        return self._profile.name

    # ── resolution (pure) ────────────────────────────────────────────────────

    def resolve(
        self,
        model_ref: str,
        *,
        agent: str | None,
        session_id: str | None,  # noqa: ARG002 — used by OpenRouter / OpenCode
    ) -> ResolvedModel:
        ref = self._parse(model_ref)
        self._check_policy(ref, agent=agent)
        return ResolvedModel(
            litellm_model=f"openai/{self._upstream(ref)}",
            api_base=self._profile.base_url,
            api_key=self._secret(),
            extra_headers=dict(self._profile.default_headers),
            extra_body={},
            wire_protocol=WireProtocol.OPENAI_CHAT,
            gateway=self.name,
            upstream_model=self._upstream(ref),
            owns_model_fallback=self._profile.capabilities.owns_model_fallback,
        )

    def _parse(self, model_ref: str) -> ModelRef:
        ref = parse_model_ref(model_ref)
        if ref is None or ref.gateway != self.name:
            raise GatewayConfigError(
                f"gateway {self.name!r} cannot resolve reference {model_ref!r}"
            )
        return ref

    @staticmethod
    def _upstream(ref: ModelRef) -> str:
        return f"{ref.model}:{ref.variant}" if ref.variant else ref.model

    def _check_policy(self, ref: ModelRef, *, agent: str | None) -> None:
        if self._profile.data_policy.is_denied(ref.model):
            self._deny("denied_model", agent=agent)

    def _deny(self, reason: str, *, agent: str | None) -> NoReturn:
        """Count, log and raise a fail-closed policy denial (DD-GW-007)."""
        count("gateway_policy_denials", gateway=self.name, reason=reason)
        logger.info("gateway.policy_denied", gateway=self.name, reason=reason, agent=agent)
        raise GatewayPolicyError(reason, gateway=self.name, agent=agent)

    def _secret(self) -> SecretStr:
        auth_ref = self._profile.auth_ref
        if not auth_ref:
            return SecretStr("")
        try:
            secret = self._secret_resolver(auth_ref)
        except Exception:  # a failing vault is a missing key, never a leak
            secret = None
        if secret is None or not secret.get_secret_value():
            raise GatewayCredentialError(auth_ref, gateway=self.name)
        return secret

    # ── telemetry (fail-open) ────────────────────────────────────────────────

    def reported_cost(self, response: object) -> ReportedCost | None:
        return read_reported_cost(response)

    async def quota(self) -> QuotaSnapshot | None:
        return None

    async def fetch_generation(
        self,
        generation_id: str,  # noqa: ARG002 — only gateways with a generation API
    ) -> GenerationStats | None:
        return None

    async def astartup_check(self) -> None:
        return None

    # ── HTTP client lifecycle ────────────────────────────────────────────────

    def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            import httpx

            self._http = httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0))
            self._owns_http = True
        return self._http

    def _auth_headers(self) -> dict[str, str]:
        """``Authorization`` header for gateway REST calls (never a URL parameter)."""
        return {"Authorization": f"Bearer {self._secret().get_secret_value()}"}

    async def aclose(self) -> None:
        if self._owns_http and self._http is not None:
            with suppress(Exception):
                await self._http.aclose()
            self._http = None
            self._owns_http = False


def build_openai_compatible_gateways(
    settings: Settings, *, secret_resolver: SecretResolver
) -> dict[str, OpenAICompatibleGateway]:
    """One adapter per ``llm_gateway_openai_compatible`` entry (validated by settings)."""
    gateways: dict[str, OpenAICompatibleGateway] = {}
    for name, entry in settings.llm_gateway_openai_compatible.items():
        headers = entry.get("headers") or {}
        profile = GatewayProfile(
            name=name,
            base_url=str(entry["base_url"]),
            auth_ref=str(entry.get("auth_ref") or ""),
            default_headers={str(k): str(v) for k, v in dict(headers).items()},
            data_policy=GatewayDataPolicy(
                denied_model_patterns=tuple(entry.get("denied_models") or ())
            ),
            capabilities=GatewayCapabilities(
                owns_model_fallback=bool(entry.get("owns_model_fallback", False))
            ),
        )
        gateways[name] = OpenAICompatibleGateway(
            profile, secret_resolver=secret_resolver, settings=settings
        )
    return gateways


__all__ = [
    "OpenAICompatibleGateway",
    "SecretResolver",
    "build_openai_compatible_gateways",
    "count",
]

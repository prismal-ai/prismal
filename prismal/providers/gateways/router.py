"""Gateway router and composition helpers (Phase GW — SPEC-GW-RTR-001).

``GatewayRouter.resolve`` is pure: it picks the effective ``gw:`` reference
(explicit model first, then ``llm_gateway_agent_map[agent]``) and delegates to
the owning adapter. ``build_gateway_router`` assembles the adapters enabled in
settings; ``get_gateway_router`` reads a per-request ``ContextVar`` first, then
the process global (ARCHITECTURE §10 — RH2 confirms).
"""

from __future__ import annotations

from contextlib import contextmanager, suppress
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

from pydantic import SecretStr

from prismal.core.exceptions import (
    GatewayConfigError,
    GatewayCredentialError,
    GatewayPolicyError,
)
from prismal.core.logging import get_logger
from prismal.providers.gateways.openai_compatible import (
    SecretResolver,
    build_openai_compatible_gateways,
    count,
)
from prismal.providers.gateways.policy import GW_PREFIX, parse_model_ref

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

    from prismal.core.config import Settings
    from prismal.providers.gateways.base import GatewayPort, QuotaSnapshot, ResolvedModel
    from prismal.providers.gateways.poller import GatewayQuotaPoller
    from prismal.providers.gateways.quota import QuotaLedger
    from prismal.providers.gateways.reconcile import CostReconciler

logger = get_logger("prismal.providers.gateways.router")

# Built-in auth_ref → Settings field (global, operator-owned keys).
_SETTINGS_KEYS: dict[str, str] = {
    "openrouter_api_key": "openrouter_api_key",
    "opencode_api_key": "opencode_api_key",
    "omniroute_api_key": "omniroute_api_key",
}


class GatewayRouter:
    """Maps model references to gateway adapters (no I/O in ``resolve``)."""

    def __init__(
        self,
        gateways: Mapping[str, GatewayPort],
        *,
        settings: Settings | None = None,
        org_id: str | None = None,
        ledger: QuotaLedger | None = None,
    ) -> None:
        if settings is None:
            from prismal.core.config import get_settings

            settings = get_settings()
        self._gateways: dict[str, GatewayPort] = dict(gateways)
        self._settings = settings
        self.org_id = org_id
        self.ledger = ledger
        self.disabled: dict[str, str] = {}
        # Set by the composition root when gateway_reconcile_enabled (GW5-04).
        self.reconciler: CostReconciler | None = None
        # Set by the composition root when a gateway exposes quota (GW5-05).
        self.quota_poller: GatewayQuotaPoller | None = None

    @property
    def settings(self) -> Settings:
        """The settings this router was built with."""
        return self._settings

    def target_for(self, model: str | None, *, agent: str | None) -> str | None:
        """Effective ``gw:`` ref or ``None`` (legacy path).

        Precedence: (1) *model* starting with ``gw:``; (2) an explicit non-``gw:``
        model stays legacy; (3) ``llm_gateway_agent_map[agent]``; (4) ``None``.
        """
        if model is not None:
            return model if model.startswith(GW_PREFIX) else None
        if agent is not None:
            return self._settings.llm_gateway_agent_map.get(agent)
        return None

    def resolve(
        self, model: str | None, *, agent: str | None, session_id: str | None
    ) -> ResolvedModel | None:
        """Resolve the gateway target for *model* / *agent*, or ``None`` for the legacy path.

        Raises ``GatewayPolicyError`` for a gateway disabled at startup and
        ``GatewayConfigError`` for an unknown gateway.
        """
        target = self.target_for(model, agent=agent)
        if target is None:
            return None
        ref = parse_model_ref(target)
        if ref is None:  # pragma: no cover — target_for only returns gw: refs
            return None
        if ref.gateway in self.disabled:
            raise GatewayPolicyError(self.disabled[ref.gateway], gateway=ref.gateway, agent=agent)
        gateway = self._gateways.get(ref.gateway)
        if gateway is None:
            raise GatewayConfigError(f"unknown_gateway: {ref.gateway!r}")
        return gateway.resolve(target, agent=agent, session_id=session_id)

    def get(self, name: str) -> GatewayPort | None:
        """The adapter registered as *name*, or ``None``."""
        return self._gateways.get(name)

    def names(self) -> tuple[str, ...]:
        """Sorted names of the active (not disabled) gateways."""
        return tuple(sorted(self._gateways))

    async def astartup(self) -> None:
        """Run each adapter's startup gate; a failing gateway is disabled, not fatal.

        Any failure — policy, credential, config or an unexpected crash — disables
        the gateway (fail-closed, DD-GW-007); later calls raise ``GatewayPolicyError``.
        """
        for name, gateway in list(self._gateways.items()):
            try:
                await gateway.astartup_check()
            except (GatewayPolicyError, GatewayCredentialError, GatewayConfigError) as exc:
                reason = exc.reason if isinstance(exc, GatewayPolicyError) else type(exc).__name__
                self.disabled[name] = reason
                del self._gateways[name]
                logger.warning("gateway.disabled_at_startup", gateway=name, reason=reason)
            except Exception as exc:  # a crashing security gate fails closed
                count("gateway_errors", gateway=name, kind="startup")
                self.disabled[name] = "startup_check_failed"
                del self._gateways[name]
                logger.warning(
                    "gateway.disabled_at_startup", gateway=name, reason=type(exc).__name__
                )

    async def quotas(self) -> list[QuotaSnapshot]:
        """Remote and ledger quota snapshots; failures are skipped (fail-open)."""
        snapshots: list[QuotaSnapshot] = []
        for name, gateway in sorted(self._gateways.items()):
            try:
                snap = await gateway.quota()
            except Exception:
                count("gateway_errors", gateway=name, kind="quota")
                continue
            if snap is not None:
                snapshots.append(snap)
        return snapshots

    async def aclose(self) -> None:
        """Close every adapter that has an ``aclose`` (errors suppressed)."""
        for gateway in self._gateways.values():
            closer = getattr(gateway, "aclose", None)
            if callable(closer):
                with suppress(Exception):
                    await closer()


def settings_secret_resolver(settings: Settings) -> SecretResolver:
    """Global keys from ``Settings`` (built-in refs) or the injected config source."""

    def resolve(auth_ref: str) -> SecretStr | None:
        """Key for *auth_ref* from a settings field or the config source; ``None`` if empty."""
        field = _SETTINGS_KEYS.get(auth_ref)
        if field is not None:
            secret: SecretStr = getattr(settings, field)
            return secret if secret.get_secret_value() else None
        from prismal.core.config_source import EnvConfigSource, get_config_source

        raw = (get_config_source() or EnvConfigSource()).load().get(auth_ref)
        if raw is None:
            return None
        value = raw if isinstance(raw, SecretStr) else SecretStr(str(raw))
        return value if value.get_secret_value() else None

    return resolve


def vault_secret_resolver(vault: Any, *, org_id: str | None, settings: Settings) -> SecretResolver:
    """Tenant-scoped keys ``<auth_ref>@<org>`` from an IDN vault (VERIFY.md GW0-08).

    A missing tenant key is ``None`` (the gateway raises ``GatewayCredentialError``)
    unless ``llm_gateway_tenant_key_fallback`` allows the global key (DD-GW-006).
    """
    fallback = settings_secret_resolver(settings)

    def lookup(ref: str) -> SecretStr | None:
        """Vault value for *ref*, or ``None`` when absent, empty or failing."""
        try:
            value: SecretStr = vault.resolve(ref).value
        except Exception:
            return None
        return value if value.get_secret_value() else None

    def resolve(auth_ref: str) -> SecretStr | None:
        """Tenant key ``<auth_ref>@<org>``; global key only without a tenant or with fallback."""
        if org_id is None:
            return lookup(auth_ref) or fallback(auth_ref)
        scoped = lookup(f"{auth_ref}@{org_id}")
        if scoped is not None:
            return scoped
        if settings.llm_gateway_tenant_key_fallback:
            return lookup(auth_ref) or fallback(auth_ref)
        return None

    return resolve


def build_gateway_router(
    settings: Settings,
    *,
    org_id: str | None = None,
    secret_resolver: SecretResolver | None = None,
) -> GatewayRouter | None:
    """Assemble the enabled adapters; ``None`` when ``llm_gateways_enabled`` is false."""
    if not settings.llm_gateways_enabled:
        return None
    resolver = secret_resolver or settings_secret_resolver(settings)
    gateways: dict[str, GatewayPort] = dict(
        build_openai_compatible_gateways(settings, secret_resolver=resolver)
    )
    ledger: QuotaLedger | None = None
    if settings.opencode_go_enabled or settings.openrouter_enabled:
        from prismal.providers.gateways.quota import QuotaLedger

        ledger = QuotaLedger(settings=settings, org_id=org_id)
    if settings.openrouter_enabled:
        from prismal.providers.gateways import openrouter

        gateways.update(
            openrouter.build_from_settings(
                settings, org_id=org_id, secret_resolver=resolver, ledger=ledger
            )
        )
    if settings.opencode_zen_enabled or settings.opencode_go_enabled:
        from prismal.providers.gateways import opencode

        gateways.update(
            opencode.build_from_settings(
                settings, org_id=org_id, secret_resolver=resolver, ledger=ledger
            )
        )
    if settings.omniroute_enabled:
        from prismal.providers.gateways import omniroute

        gateways.update(
            omniroute.build_from_settings(
                settings, org_id=org_id, secret_resolver=resolver, ledger=ledger
            )
        )
    return GatewayRouter(gateways, settings=settings, org_id=org_id, ledger=ledger)


_CURRENT: ContextVar[GatewayRouter | None] = ContextVar("prismal_gateway_router", default=None)
_GLOBAL: GatewayRouter | None = None


def set_gateway_router(router: GatewayRouter | None) -> None:
    """Install (or clear) the process-global router."""
    global _GLOBAL
    _GLOBAL = router


def get_gateway_router() -> GatewayRouter | None:
    """The per-request router (``ContextVar``) if bound, else the process global."""
    return _CURRENT.get() or _GLOBAL


@contextmanager
def use_gateway_router(router: GatewayRouter | None) -> Iterator[None]:
    """Bind *router* for the current context (per tenant / per request)."""
    token = _CURRENT.set(router)
    try:
        yield
    finally:
        _CURRENT.reset(token)


__all__ = [
    "GatewayRouter",
    "build_gateway_router",
    "get_gateway_router",
    "set_gateway_router",
    "settings_secret_resolver",
    "use_gateway_router",
    "vault_secret_resolver",
]

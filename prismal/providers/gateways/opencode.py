"""OpenCode Zen and Go gateway adapter (Phase GW — SPEC-GW-OC-001; GW3-02..06).

One adapter covers both plans: ``gw:opencode/<id>`` (Zen, pay-as-you-go) and
``gw:opencode-go/<id>`` (Go, flat-fee subscription). OpenCode serves each model
on one of three endpoint families, so the per-model protocol comes from a
configured map (DD-GW-008 — never guessed) and selects the LiteLLM route:
``openai/<id>``, ``anthropic/<id>`` or ``openai/responses/<id>`` (VERIFY.md
GW0-04/05).

Compliance guards are fail-closed and run before anything else (DD-GW-007):
Go is limited to coding agents (ToS, VERIFY.md GW0-05) and retention-risky
upstreams are denied on both plans (GW0-06).
"""

from __future__ import annotations

import importlib.metadata
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import yaml

from prismal.core.exceptions import GatewayConfigError
from prismal.core.logging import get_logger
from prismal.providers.gateways.base import (
    GatewayProfile,
    QuotaSnapshot,
    ResolvedModel,
    WireProtocol,
    session_hash,
)
from prismal.providers.gateways.openai_compatible import (
    OpenAICompatibleGateway,
    SecretResolver,
    count,
)
from prismal.providers.gateways.policy import GatewayDataPolicy, ModelRef, parse_model_ref

if TYPE_CHECKING:
    import httpx

    from prismal.core.config import Settings
    from prismal.providers.gateways.base import GatewayPort
    from prismal.providers.gateways.quota import QuotaLedger

logger = get_logger("prismal.providers.gateways.opencode")

Plan = Literal["zen", "go"]

GATEWAY_NAMES: dict[str, str] = {"zen": "opencode", "go": "opencode-go"}
AUTH_REF = "opencode_api_key"
SESSION_HEADER = "x-opencode-session"
_SESSION_HASH_LEN = 32

_LITELLM_PREFIX: dict[WireProtocol, str] = {
    WireProtocol.OPENAI_CHAT: "openai/",
    WireProtocol.ANTHROPIC_MESSAGES: "anthropic/",
    WireProtocol.OPENAI_RESPONSES: "openai/responses/",
}
# Endpoint path suffix → protocol (used by refresh_protocols).
_ENDPOINT_SUFFIXES: tuple[tuple[str, WireProtocol], ...] = (
    ("/chat/completions", WireProtocol.OPENAI_CHAT),
    ("/messages", WireProtocol.ANTHROPIC_MESSAGES),
    ("/responses", WireProtocol.OPENAI_RESPONSES),
)
_PROTOCOL_FIELDS = ("protocol", "wire_protocol", "endpoint", "api")
_REPO_ROOT = Path(__file__).resolve().parents[3]


def package_version() -> str:
    """Installed ``prismal`` version, ``"0"`` when no distribution metadata exists."""
    try:
        return importlib.metadata.version("prismal")
    except Exception:
        return "0"


# ── protocol map (GW3-02) ────────────────────────────────────────────────────


def _resolve_path(path: str) -> Path:
    """Like ``load_cache_policy``: relative to the cwd; else relative to the repo root."""
    candidate = Path(path)
    if candidate.is_absolute() or candidate.exists():
        return candidate
    return _REPO_ROOT / candidate


def load_opencode_model_protocols(path: str | None = None) -> dict[str, dict[str, WireProtocol]]:
    """Load ``config/opencode_models.yaml`` (``{'zen': {id: protocol}, 'go': {id: protocol}}``).

    *path* ``None`` ⇒ ``settings.opencode_model_protocols_path``. A missing file,
    invalid YAML, a bad shape or an unknown protocol raises ``GatewayConfigError``.
    """
    if path is None:
        from prismal.core.config import get_settings

        path = get_settings().opencode_model_protocols_path
    resolved = _resolve_path(path)
    try:
        raw: object = yaml.safe_load(resolved.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise GatewayConfigError(f"cannot load OpenCode protocol map {str(resolved)!r}") from exc
    if raw is None:
        raw = {}
    if not isinstance(raw, Mapping):
        raise GatewayConfigError("OpenCode protocol map must be a mapping of plan → models")
    unknown_plans = set(raw) - set(GATEWAY_NAMES)
    if unknown_plans:
        raise GatewayConfigError(f"unknown OpenCode plan(s): {sorted(map(str, unknown_plans))}")
    result: dict[str, dict[str, WireProtocol]] = {}
    for plan in GATEWAY_NAMES:
        section: object = raw.get(plan) or {}
        if not isinstance(section, Mapping):
            raise GatewayConfigError(f"OpenCode plan {plan!r} must map model id → protocol")
        models: dict[str, WireProtocol] = {}
        for model_id, protocol in section.items():
            if not isinstance(model_id, str) or not isinstance(protocol, str):
                raise GatewayConfigError(f"bad OpenCode protocol entry {model_id!r}: {protocol!r}")
            try:
                models[model_id] = WireProtocol(protocol)
            except ValueError as exc:
                raise GatewayConfigError(
                    f"unknown protocol {protocol!r} for OpenCode model {model_id!r}"
                ) from exc
        result[plan] = models
    return result


def opencode_profile(settings: Settings, plan: Plan) -> GatewayProfile:
    """The static profile of one OpenCode plan."""
    return GatewayProfile(
        name=GATEWAY_NAMES[plan],
        base_url=settings.opencode_go_base_url if plan == "go" else settings.opencode_zen_base_url,
        auth_ref=AUTH_REF,
        default_headers={"User-Agent": f"prismal/{package_version()}"},
        data_policy=GatewayDataPolicy(denied_model_patterns=tuple(settings.opencode_denied_models)),
    )


def _protocol_of(entry: Mapping[str, Any]) -> WireProtocol | None:
    for field_name in _PROTOCOL_FIELDS:
        value = entry.get(field_name)
        if not isinstance(value, str) or not value:
            continue
        try:
            return WireProtocol(value)
        except ValueError:
            pass
        path = value.rstrip("/")
        for suffix, protocol in _ENDPOINT_SUFFIXES:
            if path.endswith(suffix):
                return protocol
    return None


class OpenCodeGateway(OpenAICompatibleGateway):
    """OpenCode Zen (``opencode``) or Go (``opencode-go``) — SPEC-GW-OC-001."""

    def __init__(
        self,
        profile: GatewayProfile,
        *,
        plan: Plan,
        protocols: Mapping[str, WireProtocol],
        ledger: QuotaLedger | None,
        secret_resolver: SecretResolver,
        settings: Settings | None = None,
        http: httpx.AsyncClient | None = None,
        org_id: str | None = None,
    ) -> None:
        expected = GATEWAY_NAMES.get(plan)
        if expected is None or profile.name != expected:
            raise GatewayConfigError(
                f"OpenCode plan {plan!r} requires gateway name {expected!r}, got {profile.name!r}"
            )
        if settings is None:
            from prismal.core.config import get_settings

            settings = get_settings()
        super().__init__(profile, secret_resolver=secret_resolver, settings=settings, http=http)
        self._plan: Plan = plan
        self._protocols: dict[str, WireProtocol] = dict(protocols)
        self._ledger = ledger
        self._org_id = org_id
        self._cfg: Settings = settings
        self._downgrade = self._compute_downgrade()

    @property
    def plan(self) -> Plan:
        """The OpenCode plan (``go`` subscription or ``zen`` metered)."""
        return self._plan

    def __repr__(self) -> str:
        return f"OpenCodeGateway(name={self.name!r}, plan={self._plan!r})"

    # ── resolution (pure) ────────────────────────────────────────────────────

    def resolve(
        self, model_ref: str, *, agent: str | None, session_id: str | None
    ) -> ResolvedModel:
        """Resolve *model_ref* to the LiteLLM target for its wire protocol; pure, no I/O.

        On the ``go`` plan the ToS agent allowlist is checked first (fail-closed); then the
        retention policy and the protocol map. Adds the hashed session header when
        *session_id* is given.
        """
        # GW3-05: the ToS guard runs before anything else (zero I/O, fail-closed).
        if self._plan == "go" and (agent is None or agent not in self._allowed_agents()):
            self._deny("opencode_go_agent_not_allowed", agent=agent)
        ref = self._parse(model_ref)
        upstream = self._upstream(ref)
        if self._profile.data_policy.is_denied(upstream):
            self._deny("retention_denied_model", agent=agent)
        protocol = self._protocols.get(upstream)
        if protocol is None:
            raise GatewayConfigError(
                f"unknown_opencode_model: {upstream!r} is not in the {self._plan!r} protocol map"
            )
        headers = dict(self._profile.default_headers)
        if session_id:
            headers[SESSION_HEADER] = session_hash(self._org_id, session_id, _SESSION_HASH_LEN)
        return ResolvedModel(
            litellm_model=_LITELLM_PREFIX[protocol] + upstream,
            api_base=self._profile.base_url,
            api_key=self._secret(),
            extra_headers=headers,
            extra_body={},
            wire_protocol=protocol,
            gateway=self.name,
            upstream_model=upstream,
            owns_model_fallback=self._profile.capabilities.owns_model_fallback,
            cost_mode="subscription" if self._plan == "go" else "metered",
            downgrade_model=self._downgrade,
        )

    def _allowed_agents(self) -> frozenset[str]:
        return frozenset(self._cfg.opencode_go_allowed_agents)

    def _compute_downgrade(self) -> str | None:
        """LiteLLM string for ``opencode_go_downgrade_model`` (Go only; None if unusable)."""
        target = self._cfg.opencode_go_downgrade_model
        if self._plan != "go" or not target:
            return None
        try:
            ref: ModelRef | None = parse_model_ref(target)
        except GatewayConfigError:
            ref = None
        upstream = self._upstream(ref) if ref is not None else None
        protocol = self._protocols.get(upstream) if upstream is not None else None
        if (
            ref is None
            or upstream is None
            or ref.gateway != self.name
            or protocol is None
            or self._profile.data_policy.is_denied(upstream)
        ):
            logger.warning("gateway.downgrade_model_unusable", gateway=self.name, target=target)
            return None
        return _LITELLM_PREFIX[protocol] + upstream

    # ── protocol refresh (GW3-06, fail-open) ─────────────────────────────────

    async def refresh_protocols(self) -> Mapping[str, WireProtocol] | None:
        """Merge per-model protocols from ``GET {base}/models``; ``None`` if not exposed."""
        try:
            url = self._profile.base_url.rstrip("/") + "/models"
            response = await self._client().get(url, headers=self._auth_headers())
            response.raise_for_status()
            payload: object = response.json()
            data = payload.get("data") if isinstance(payload, Mapping) else None
            found: dict[str, WireProtocol] = {}
            for entry in data if isinstance(data, list) else ():
                if not isinstance(entry, Mapping) or not isinstance(entry.get("id"), str):
                    continue
                protocol = _protocol_of(entry)
                if protocol is not None:
                    found[str(entry["id"]).rsplit("/", 1)[-1]] = protocol
        except Exception as exc:
            self._refresh_failed(type(exc).__name__)
            return None
        if not found:
            self._refresh_failed("no_protocol_field")
            return None
        self._protocols.update(found)
        self._downgrade = self._compute_downgrade()
        logger.info("gateway.protocols_refreshed", gateway=self.name, models=len(found))
        return found

    def _refresh_failed(self, error: str) -> None:
        count("gateway_errors", gateway=self.name, kind="models_refresh")
        logger.warning("gateway.models_refresh_failed", gateway=self.name, error=error)

    async def astartup_check(self) -> None:
        """Refresh the model protocol map when ``opencode_refresh_models`` (fail-open)."""
        if self._cfg.opencode_refresh_models:
            await self.refresh_protocols()

    # ── quota (ledger view, fail-open) ───────────────────────────────────────

    async def quota(self) -> QuotaSnapshot | None:
        """The tightest ledger window for Go; ``None`` for Zen or without a ledger."""
        if self._plan != "go" or self._ledger is None:
            return None
        snapshots = [s for s in self._ledger.snapshot(self.name) if s.limit]
        if not snapshots:
            return None
        return min(snapshots, key=lambda s: (s.remaining or 0.0) / (s.limit or 1.0))


def build_from_settings(
    settings: Settings,
    *,
    org_id: str | None,
    secret_resolver: SecretResolver,
    ledger: QuotaLedger | None,
) -> dict[str, GatewayPort]:
    """One adapter per enabled OpenCode plan (``opencode`` / ``opencode-go``)."""
    plans: list[Plan] = []
    if settings.opencode_zen_enabled:
        plans.append("zen")
    if settings.opencode_go_enabled:
        plans.append("go")
    if not plans:
        return {}
    protocols = load_opencode_model_protocols(settings.opencode_model_protocols_path)
    gateways: dict[str, GatewayPort] = {}
    for plan in plans:
        gateways[GATEWAY_NAMES[plan]] = OpenCodeGateway(
            opencode_profile(settings, plan),
            plan=plan,
            protocols=protocols[plan],
            ledger=ledger,
            secret_resolver=secret_resolver,
            settings=settings,
            org_id=org_id,
        )
    return gateways


__all__ = [
    "GATEWAY_NAMES",
    "OpenCodeGateway",
    "build_from_settings",
    "load_opencode_model_protocols",
    "opencode_profile",
    "package_version",
]

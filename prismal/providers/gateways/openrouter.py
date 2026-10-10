"""OpenRouter gateway adapter (Phase GW2 — SPEC-GW-OR-001).

Resolves ``gw:openrouter/<vendor>/<model>[:<variant>]`` to LiteLLM's native
``openrouter/…`` route with attribution headers, a hashed sticky ``session_id``
and the fail-closed data policy (``provider.data_collection="deny"`` +
``provider.zdr=true``) in ``extra_body``. ``resolve`` is pure — no I/O.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal, cast

from prismal.providers.gateways.base import (
    GatewayCapabilities,
    GatewayProfile,
    GenerationStats,
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

if TYPE_CHECKING:
    import httpx

    from prismal.core.config import Settings
    from prismal.providers.gateways.base import GatewayPort
    from prismal.providers.gateways.policy import ModelRef

GATEWAY_NAME = "openrouter"
AUTH_REF = "openrouter_api_key"
AUTO_MODEL = "openrouter/auto"  # hands model choice to the gateway (DD-GW-002)
_USAGE_WINDOWS: tuple[tuple[Literal["day", "week", "month"], str], ...] = (
    ("day", "usage_daily"),
    ("week", "usage_weekly"),
    ("month", "usage_monthly"),
)
_HTTP_TIMEOUT_S = 10.0
_HTTP_CONNECT_TIMEOUT_S = 5.0
_SESSION_HASH_LEN = 64  # OpenRouter accepts ≤ 256 chars (verified 2026-10-04)


class OpenRouterGateway(OpenAICompatibleGateway):
    """OpenRouter: prismal picks the model, OpenRouter picks the provider (DD-GW-002)."""

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
            base_url=settings.openrouter_base_url,
            auth_ref=AUTH_REF,
            default_headers=_attribution_headers(settings),
            capabilities=GatewayCapabilities(
                reports_cost=True,
                has_generation_api=True,
                has_quota_api=True,
                supports_prompt_cache=True,
                owns_model_fallback=bool(settings.openrouter_fallback_models),
                enforces_data_policy=True,
            ),
        )
        super().__init__(profile, secret_resolver=secret_resolver, settings=settings, http=http)
        self._or_settings = settings
        self._org_id = org_id

    # ── resolution (pure) ────────────────────────────────────────────────────

    def resolve(
        self,
        model_ref: str,
        *,
        agent: str | None,
        session_id: str | None,
        wants_tools: bool = False,
        wants_structured: bool = False,
    ) -> ResolvedModel:
        """SPEC-GW-OR-001 — pure; raises ``GatewayPolicyError`` / ``GatewayConfigError``."""
        ref = self._parse(model_ref)
        self._check_policy(ref, agent=agent)
        upstream = self._upstream(ref)
        return ResolvedModel(
            litellm_model=f"openrouter/{upstream}",
            api_base=self._profile.base_url,
            api_key=self._secret(),
            extra_headers=dict(self._profile.default_headers),
            extra_body=self.build_extra_body(
                ref,
                wants_tools=wants_tools,
                wants_structured=wants_structured,
                session_id=session_id,
            ),
            wire_protocol=WireProtocol.OPENAI_CHAT,
            gateway=self.name,
            upstream_model=upstream,
            owns_model_fallback=self._profile.capabilities.owns_model_fallback,
            cost_mode="metered",
        )

    def _check_policy(self, ref: ModelRef, *, agent: str | None) -> None:
        """Fail-closed gates (GW2-05), all before the secret is touched."""
        s = self._or_settings
        if ref.variant is not None and ref.variant not in s.openrouter_allowed_variants:
            self._deny("variant_not_allowed", agent=agent)
        if ref.model == AUTO_MODEL and not s.openrouter_allow_auto:
            self._deny("auto_router_disabled", agent=agent)
        super()._check_policy(ref, agent=agent)

    def build_provider_object(self, *, wants_tools: bool, wants_structured: bool) -> dict[str, Any]:
        """The ``provider`` routing object (GW2-03); ``{}`` when nothing applies."""
        s = self._or_settings
        provider: dict[str, Any] = {}
        if s.openrouter_require_zdr:
            provider["data_collection"] = "deny"
            provider["zdr"] = True
        if wants_tools or wants_structured:
            provider["require_parameters"] = True
        if s.openrouter_provider_sort:
            provider["sort"] = s.openrouter_provider_sort
        if s.openrouter_max_price:
            provider["max_price"] = dict(s.openrouter_max_price)
        if s.openrouter_only:
            provider["only"] = list(s.openrouter_only)
        if s.openrouter_ignore:
            provider["ignore"] = list(s.openrouter_ignore)
        return provider

    def build_extra_body(
        self,
        ref: ModelRef,
        *,
        wants_tools: bool,
        wants_structured: bool,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """The OpenRouter-only request fields (``extra_body``).

        ``provider`` and ``plugins`` always travel here — LiteLLM maps only
        ``transforms``, ``models`` and ``route`` natively (SPEC-GW-OR-001).
        """
        s = self._or_settings
        body: dict[str, Any] = {}
        provider = self.build_provider_object(
            wants_tools=wants_tools, wants_structured=wants_structured
        )
        if provider:
            body["provider"] = provider
        if s.openrouter_fallback_models:
            body["models"] = [self._upstream(ref), *s.openrouter_fallback_models]
        plugins: list[dict[str, Any]] = [{"id": p} for p in s.openrouter_plugins]
        if ref.model == AUTO_MODEL:
            auto = self._auto_router_plugin()
            if auto is not None:
                plugins.append(auto)
        if plugins:
            body["plugins"] = plugins
        if s.openrouter_session_stickiness and session_id:
            body["session_id"] = session_hash(self._org_id, session_id, _SESSION_HASH_LEN)
        return body

    def _auto_router_plugin(self) -> dict[str, Any] | None:
        """``openrouter/auto`` constraints as the ``auto-router`` plugin.

        Shape per https://openrouter.ai/docs/features/model-routing (checked
        2026-10-05): ``{"id": "auto-router", "allowed_models": [wildcards],
        "cost_tier": "low|medium|high|xhigh|max"}``.
        """
        s = self._or_settings
        plugin: dict[str, Any] = {"id": "auto-router"}
        if s.openrouter_auto_allowed_models:
            plugin["allowed_models"] = list(s.openrouter_auto_allowed_models)
        if s.openrouter_auto_tier:
            plugin["cost_tier"] = s.openrouter_auto_tier
        return plugin if len(plugin) > 1 else None

    # ── telemetry (fail-open, GW2-07) ────────────────────────────────────────

    async def quota(self) -> QuotaSnapshot | None:
        """The ``credit`` window from ``GET /key``; ``None`` on any failure."""
        snapshots = await self.quotas()
        return snapshots[0] if snapshots else None

    async def quotas(self) -> list[QuotaSnapshot]:
        """Every window ``GET /key`` reports (field names verified 2026-10-04).

        ``credit`` first, then ``day``/``week``/``month`` USD usage and the
        ``free_model_daily_requests`` window when present. ``[]`` on failure.
        """
        try:
            data = await self._get_data("/key")
            snapshots = [
                self._snapshot(
                    "credit",
                    "usd",
                    used=_required_number(data.get("usage")),
                    limit=_number(data.get("limit")),
                    remaining=_number(data.get("limit_remaining")),
                )
            ]
            for window, field in _USAGE_WINDOWS:
                used = _number(data.get(field))
                if used is not None:
                    snapshots.append(self._snapshot(window, "usd", used=used))
            free = data.get("free_model_daily_requests")
            if isinstance(free, dict):
                snapshots.append(
                    self._snapshot(
                        "day",
                        "requests",
                        used=_required_number(free.get("used")),
                        limit=_number(free.get("limit")),
                        remaining=_number(free.get("remaining")),
                    )
                )
        except Exception:  # fail-open — quota is advisory, never raises
            count("gateway_errors", gateway=self.name, kind="quota_lookup")
            return []
        return snapshots

    async def fetch_generation(self, generation_id: str) -> GenerationStats | None:
        """Authoritative per-call stats from ``GET /generation?id=…``.

        Field names per https://openrouter.ai/docs/api-reference/get-a-generation
        (checked 2026-10-05): native token counts (billed) are preferred over the
        normalized ``tokens_*``; ``native_tokens_cached`` / ``native_tokens_reasoning``.
        """
        try:
            data = await self._get_data("/generation", params={"id": generation_id})
            gen_id = data.get("id")
            provider = data.get("provider_name")
            model = data.get("model")
            if not isinstance(model, str):
                raise ValueError("generation without model")
            return GenerationStats(
                generation_id=gen_id if isinstance(gen_id, str) else generation_id,
                total_cost_usd=_required_number(data.get("total_cost")),
                served_model=model,
                provider_name=provider if isinstance(provider, str) else None,
                prompt_tokens=_tokens(data, "native_tokens_prompt", "tokens_prompt"),
                completion_tokens=_tokens(data, "native_tokens_completion", "tokens_completion"),
                cached_tokens=_tokens(data, "native_tokens_cached"),
                reasoning_tokens=_tokens(data, "native_tokens_reasoning"),
            )
        except Exception:  # fail-open — reconciliation is best effort
            count("gateway_errors", gateway=self.name, kind="generation_lookup")
            return None

    async def _get_data(self, path: str, *, params: dict[str, str] | None = None) -> dict[str, Any]:
        """``GET {base_url}{path}`` → the JSON ``data`` object (raises on any problem).

        The key travels only in the ``Authorization`` header, never the URL.
        """
        import httpx

        response = await self._client().get(
            f"{self._profile.base_url.rstrip('/')}{path}",
            params=params,
            headers=self._auth_headers(),
            timeout=httpx.Timeout(_HTTP_TIMEOUT_S, connect=_HTTP_CONNECT_TIMEOUT_S),
        )
        response.raise_for_status()
        data = response.json().get("data")
        if not isinstance(data, dict):
            raise ValueError("response without a data object")
        return cast("dict[str, Any]", data)

    def _snapshot(
        self,
        window: Literal["credit", "day", "week", "month"],
        unit: Literal["usd", "requests"],
        *,
        used: float,
        limit: float | None = None,
        remaining: float | None = None,
    ) -> QuotaSnapshot:
        return QuotaSnapshot(
            gateway=self.name,
            window=window,
            unit=unit,
            limit=limit,
            used=used,
            remaining=remaining,
            resets_at=None,
            source="api",
        )


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _required_number(value: object) -> float:
    number = _number(value)
    if number is None:
        raise ValueError("expected a number")
    return number


def _tokens(data: dict[str, Any], *fields: str) -> int:
    for field in fields:
        number = _number(data.get(field))
        if number is not None:
            return int(number)
    return 0


def _attribution_headers(settings: Settings) -> dict[str, str]:
    """``HTTP-Referer`` / ``X-OpenRouter-Title`` / ``X-OpenRouter-Categories``.

    Only the current header names are sent — never the legacy ``X-Title``.
    """
    headers: dict[str, str] = {}
    if settings.openrouter_app_url:
        headers["HTTP-Referer"] = settings.openrouter_app_url
    headers["X-OpenRouter-Title"] = settings.openrouter_app_title
    if settings.openrouter_app_categories:
        headers["X-OpenRouter-Categories"] = ",".join(settings.openrouter_app_categories)
    return headers


def build_from_settings(
    settings: Settings,
    *,
    org_id: str | None,
    secret_resolver: SecretResolver,
    ledger: object | None = None,  # noqa: ARG001 — the :free day window is GW3-08
) -> dict[str, GatewayPort]:
    """``{"openrouter": OpenRouterGateway}`` for :func:`build_gateway_router`."""
    return {
        GATEWAY_NAME: OpenRouterGateway(
            settings=settings, secret_resolver=secret_resolver, org_id=org_id
        )
    }


__all__ = ["OpenRouterGateway", "build_from_settings"]

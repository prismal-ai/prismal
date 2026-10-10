"""Gateway value objects and port (Phase GW — SPEC-GW-BASE-001..003).

A gateway is an OpenAI-compatible (or Anthropic-compatible) HTTP front for
many upstream models. ``GatewayPort.resolve`` is pure — it turns a ``gw:``
reference into the kwargs a ``ChatLiteLLM`` needs, without network I/O.
Secrets are ``SecretStr`` and excluded from ``repr``.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Literal, Protocol, cast, runtime_checkable

from prismal.providers.gateways.policy import GatewayDataPolicy

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import datetime

    from pydantic import SecretStr

CostMode = Literal["metered", "subscription"]


class WireProtocol(StrEnum):
    """The HTTP API family a gateway model speaks."""

    OPENAI_CHAT = "openai_chat"  # /chat/completions
    ANTHROPIC_MESSAGES = "anthropic_messages"  # /v1/messages
    OPENAI_RESPONSES = "openai_responses"  # /v1/responses


@dataclass(frozen=True)
class GatewayCapabilities:
    """What a gateway can do beyond plain completion."""

    reports_cost: bool = False  # billed cost on every response
    has_generation_api: bool = False  # authoritative per-call stats by id
    has_quota_api: bool = False  # remote credit/quota endpoint
    supports_prompt_cache: bool = False  # cache_control / auto-cache pass-through
    owns_model_fallback: bool = False  # gateway can fall back across models
    enforces_data_policy: bool = False  # data_collection / zdr enforceable upstream


@dataclass(frozen=True)
class GatewayProfile:
    """Static description of one configured gateway."""

    name: str
    base_url: str
    auth_ref: str  # vault key name, e.g. "openrouter_api_key"
    default_headers: Mapping[str, str] = field(default_factory=dict)
    data_policy: GatewayDataPolicy = field(default_factory=GatewayDataPolicy)
    capabilities: GatewayCapabilities = field(default_factory=GatewayCapabilities)


@dataclass(frozen=True)
class ResolvedModel:
    """Everything ``GatewayChatLiteLLM`` needs for one call target."""

    litellm_model: str  # e.g. "openrouter/anthropic/claude-sonnet-4.5"
    api_base: str
    api_key: SecretStr = field(repr=False)
    extra_headers: Mapping[str, str] = field(default_factory=dict)
    extra_body: Mapping[str, Any] = field(default_factory=dict)
    wire_protocol: WireProtocol = WireProtocol.OPENAI_CHAT
    gateway: str = ""
    upstream_model: str = ""  # model id as the gateway knows it
    owns_model_fallback: bool = False  # True ⇒ prismal fallback + LiteLLM retries disabled
    cost_mode: CostMode = "metered"
    downgrade_model: str | None = None  # ready litellm model string for a quota downgrade


@dataclass(frozen=True)
class ReportedCost:
    """A billed cost read from a gateway response or generation lookup."""

    cost_usd: float
    source: Literal["usage.cost", "hidden_params", "generation_api"]
    upstream_cost_usd: float | None = None  # OpenRouter BYOK upstream inference cost
    generation_id: str | None = None


@dataclass(frozen=True)
class QuotaSnapshot:
    """A remote (``api``) or locally tracked (``ledger``) quota window."""

    gateway: str
    window: Literal["credit", "day", "5h", "week", "month"]
    unit: Literal["usd", "requests"]
    limit: float | None  # None = unlimited / unknown
    used: float
    remaining: float | None
    resets_at: datetime | None
    source: Literal["api", "ledger"]


@dataclass(frozen=True)
class GenerationStats:
    """Authoritative per-call statistics from a gateway generation API."""

    generation_id: str
    total_cost_usd: float
    served_model: str
    provider_name: str | None
    prompt_tokens: int
    completion_tokens: int
    cached_tokens: int = 0
    reasoning_tokens: int = 0


@dataclass(frozen=True)
class GatewayCallMeta:
    """Per-call metadata serialized under ``response_metadata["prismal_gateway"]``."""

    gateway: str
    requested_model: str
    served_model: str | None
    generation_id: str | None
    reported_cost_usd: float | None
    cost_mode: CostMode
    fallback_used: bool = False
    quota_downgrade: str | None = None  # exhausted window that forced a downgrade (GW5-05)

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict (the shape stored in message metadata)."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: object) -> GatewayCallMeta | None:
        """Rebuild from :meth:`to_dict` output; ``None`` for anything malformed."""
        if not isinstance(data, dict):
            return None
        data = cast("dict[str, Any]", data)
        try:
            gateway = data["gateway"]
            requested = data["requested_model"]
            if not isinstance(gateway, str) or not isinstance(requested, str):
                return None
            cost = data.get("reported_cost_usd")
            mode = data.get("cost_mode", "metered")
            return cls(
                gateway=gateway,
                requested_model=requested,
                served_model=_opt_str(data.get("served_model")),
                generation_id=_opt_str(data.get("generation_id")),
                reported_cost_usd=float(cost) if isinstance(cost, int | float) else None,
                cost_mode="subscription" if mode == "subscription" else "metered",
                fallback_used=bool(data.get("fallback_used", False)),
                quota_downgrade=_opt_str(data.get("quota_downgrade")),
            )
        except (KeyError, TypeError, ValueError):
            return None


def _opt_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def session_hash(org_id: str | None, session_id: str, length: int) -> str:
    """Stable, tenant-scoped, non-reversible session token (never the raw id)."""
    digest = hashlib.sha256(f"{org_id or ''}:{session_id}".encode()).hexdigest()
    return (digest * (1 + length // len(digest)))[:length]


def read_reported_cost(response: object) -> ReportedCost | None:
    """Billed cost from an ``AIMessage`` (``prismal_gateway`` metadata), a raw
    ``ModelResponse`` (``usage.cost`` / ``_hidden_params``) or a plain dict.

    Never raises; ``None`` when no cost is present.
    """
    try:
        metadata = getattr(response, "response_metadata", None)
        if isinstance(metadata, dict):
            meta = GatewayCallMeta.from_dict(metadata.get("prismal_gateway"))
            if meta is not None and meta.reported_cost_usd is not None:
                return ReportedCost(
                    cost_usd=meta.reported_cost_usd,
                    source="usage.cost",
                    generation_id=meta.generation_id,
                )
            return None
        getter = getattr(response, "get", None)
        if callable(getter):
            usage = getter("usage") or {}
            cost = usage.get("cost") if hasattr(usage, "get") else getattr(usage, "cost", None)
            if isinstance(cost, int | float) and not isinstance(cost, bool):
                details = usage.get("cost_details") if hasattr(usage, "get") else None
                upstream = (
                    details.get("upstream_inference_cost") if isinstance(details, dict) else None
                )
                gen_id = getter("id")
                return ReportedCost(
                    cost_usd=float(cost),
                    source="usage.cost",
                    upstream_cost_usd=float(upstream)
                    if isinstance(upstream, int | float)
                    else None,
                    generation_id=gen_id if isinstance(gen_id, str) else None,
                )
        hidden = getattr(response, "_hidden_params", None)
        if isinstance(hidden, dict):
            cost = hidden.get("response_cost")
            if isinstance(cost, int | float) and not isinstance(cost, bool):
                return ReportedCost(cost_usd=float(cost), source="hidden_params")
    except Exception:  # never raises (SPEC-GW-BASE-003)
        return None
    return None


@runtime_checkable
class GatewayPort(Protocol):
    """Port every gateway adapter implements (SPEC-GW-BASE-003)."""

    @property
    def profile(self) -> GatewayProfile: ...

    def resolve(
        self, model_ref: str, *, agent: str | None, session_id: str | None
    ) -> ResolvedModel:
        """Pure, no I/O. Raises ``GatewayPolicyError`` / ``GatewayConfigError``."""
        ...

    def reported_cost(self, response: object) -> ReportedCost | None:
        """Billed cost from a message or raw response; never raises."""
        ...

    async def quota(self) -> QuotaSnapshot | None:
        """Remote credit/quota or ledger view; fail-open → ``None``."""
        ...

    async def fetch_generation(self, generation_id: str) -> GenerationStats | None:
        """Authoritative per-call stats; fail-open → ``None``."""
        ...

    async def astartup_check(self) -> None:
        """Optional health/version gate run once by the composition root."""
        ...


__all__ = [
    "CostMode",
    "GatewayCallMeta",
    "GatewayCapabilities",
    "GatewayPort",
    "GatewayProfile",
    "GenerationStats",
    "QuotaSnapshot",
    "ReportedCost",
    "ResolvedModel",
    "WireProtocol",
    "read_reported_cost",
    "session_hash",
]

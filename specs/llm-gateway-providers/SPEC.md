# Prismal LLM Gateway Providers — Interface Specification

## Metadata

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `READY` |
| **Version** | 1.0 |
| **Date** | 2026-10-04 |
| **Phase** | GW |
| **Target package version** | `3.14.0` (SemVer minor — new opt-in functionality, not yet started) |
| **PLAN** | `specs/llm-gateway-providers/PLAN.md` |
| **Architecture** | `specs/llm-gateway-providers/ARCHITECTURE.md` |
| **TASKS** | `specs/llm-gateway-providers/TASKS.md` |

## Conventions

- All modules use `from __future__ import annotations`; value objects are frozen dataclasses; constructors accept `settings: Settings | None = None`.
- Everything lives under `prismal/providers/gateways/` (Critical Rule #4: `litellm` and HTTP calls to gateways stay inside `prismal/providers/`). `budget/` only reads plain dicts from message metadata.
- `GatewayRouter.resolve()` is **sync, pure, no I/O**. Network calls (`quota`, `fetch_generation`, startup checks, `/v1/models` refresh) are `async`, use `httpx.AsyncClient` with explicit timeouts, and are **fail-open** (they return `None` and increment a counter). The exception is **policy/security**, which is **fail-closed** and raises `GatewayPolicyError`.
- Secrets are `SecretStr`, resolved lazily from `CredentialVault` / `ConfigSourcePort`. `.get_secret_value()` is called only at the `ChatLiteLLM` constructor boundary. Secrets are never written to `os.environ`, never put in URLs, never logged, and never included in `repr()`.
- Model reference grammar: `gw:<gateway>/<upstream-model>[:<variant>]`, where `<gateway>` matches `[a-z0-9][a-z0-9-]{0,31}` and `<upstream-model>` may contain `/`. Built-in gateway names: `openrouter`, `opencode` (Zen), `opencode-go`, `omniroute`, plus any `openai_compatible` entry.
- Facts tagged **[verify]** are volatile and are confirmed by a GW0 task before the dependent task starts.
- EARS keywords: WHEN / WHILE / WHERE / IF … THEN; THE SYSTEM SHALL.

## Module Summary

| Module | Purpose |
|---|---|
| `providers/gateways/base.py` | `WireProtocol`, `GatewayCapabilities`, `GatewayProfile`, `ResolvedModel`, `ReportedCost`, `QuotaSnapshot`, `GenerationStats`, `GatewayCallMeta`, `GatewayPort` |
| `providers/gateways/policy.py` | `GatewayDataPolicy`, `ModelRef`, `parse_model_ref` |
| `providers/gateways/{openai_compatible,openrouter,opencode,omniroute}.py` | The five adapters (OpenCode covers Zen + Go) |
| `providers/gateways/{router,quota,chat_model,reconcile}.py` | `GatewayRouter`, `QuotaLedger`, `GatewayChatLiteLLM`, `CostReconciler` |
| `providers/registry.py` · `providers/cost.py` · `budget/usage.py` | *(extend)* `get_llm(..., agent=, session_id=)`; `CostEstimate.equivalent_usd`, `"subscription"`; `extract_gateway_meta` |
| `composition/runtime.py` · `core/{config,exceptions}.py` · `monitoring/otel.py` | `RuntimeContext.gateways`; settings; `GatewayError` hierarchy; metrics |

## SPEC-GW-BASE-001: Core value objects (`gateways/base.py`)
```python
class WireProtocol(StrEnum):
    OPENAI_CHAT = "openai_chat"              # /chat/completions
    ANTHROPIC_MESSAGES = "anthropic_messages"  # /v1/messages
    OPENAI_RESPONSES = "openai_responses"    # /v1/responses


@dataclass(frozen=True)
class GatewayCapabilities:
    reports_cost: bool = False            # billed cost on every response
    has_generation_api: bool = False      # authoritative per-call stats by id
    has_quota_api: bool = False           # remote credit/quota endpoint
    supports_prompt_cache: bool = False   # cache_control / auto-cache pass-through
    owns_model_fallback: bool = False     # gateway can fall back across models
    enforces_data_policy: bool = False    # data_collection / zdr enforceable upstream


@dataclass(frozen=True)
class GatewayProfile:
    name: str
    base_url: str
    auth_ref: str                         # vault key name, e.g. "openrouter_api_key"
    default_headers: Mapping[str, str] = field(default_factory=dict)
    data_policy: GatewayDataPolicy = field(default_factory=GatewayDataPolicy)
    capabilities: GatewayCapabilities = field(default_factory=GatewayCapabilities)


@dataclass(frozen=True)
class ResolvedModel:
    litellm_model: str                    # e.g. "openrouter/anthropic/claude-sonnet-4.5"
    api_base: str
    api_key: SecretStr = field(repr=False)
    extra_headers: Mapping[str, str]
    extra_body: Mapping[str, Any]
    wire_protocol: WireProtocol
    gateway: str
    upstream_model: str                   # model id as the gateway knows it
    owns_model_fallback: bool = False     # True ⇒ prismal fallback + LiteLLM retries disabled
    cost_mode: Literal["metered", "subscription"] = "metered"
```
**Acceptance:** `repr(resolved)` and `str(resolved)` never contain the secret value (test with a sentinel key).

## SPEC-GW-BASE-002: Telemetry value objects (`gateways/base.py`)
```python
@dataclass(frozen=True)
class ReportedCost:
    cost_usd: float
    source: Literal["usage.cost", "hidden_params", "generation_api"]
    upstream_cost_usd: float | None = None   # OpenRouter cost_details.upstream_inference_cost (BYOK only)
    generation_id: str | None = None


@dataclass(frozen=True)
class QuotaSnapshot:
    gateway: str
    window: Literal["credit", "day", "5h", "week", "month"]
    unit: Literal["usd", "requests"]
    limit: float | None                       # None = unlimited / unknown
    used: float
    remaining: float | None
    resets_at: datetime | None
    source: Literal["api", "ledger"]


@dataclass(frozen=True)
class GenerationStats:
    generation_id: str
    total_cost_usd: float
    served_model: str
    provider_name: str | None
    prompt_tokens: int
    completion_tokens: int
    cached_tokens: int = 0
    reasoning_tokens: int = 0


@dataclass(frozen=True)
class GatewayCallMeta:                     # serialized under response_metadata["prismal_gateway"]
    gateway: str
    requested_model: str
    served_model: str | None
    generation_id: str | None
    reported_cost_usd: float | None
    cost_mode: Literal["metered", "subscription"]
    fallback_used: bool = False
```

## SPEC-GW-BASE-003: `GatewayPort` (`gateways/base.py`)
```python
@runtime_checkable
class GatewayPort(Protocol):
    @property
    def profile(self) -> GatewayProfile: ...

    def resolve(self, model_ref: str, *, agent: str | None, session_id: str | None) -> ResolvedModel:
        """Pure, no I/O. Raises GatewayPolicyError on a policy/ToS violation, GatewayConfigError on a
        malformed reference. Never performs a network call."""

    def reported_cost(self, response: object) -> ReportedCost | None:
        """Read the billed cost from an AIMessage carrying response_metadata['prismal_gateway'] or
        from a raw LiteLLM ModelResponse. Never raises; None when absent."""

    async def quota(self) -> QuotaSnapshot | None:
        """Remote credit/quota (OpenRouter GET /api/v1/key) or ledger view. Fail-open → None."""

    async def fetch_generation(self, generation_id: str) -> GenerationStats | None:
        """Authoritative per-call stats (OpenRouter GET /api/v1/generation?id=). Fail-open → None."""

    async def astartup_check(self) -> None:
        """Optional health/version gate run once by the composition root. Raises GatewayPolicyError
        only for security failures (e.g. OmniRoute below the minimum version)."""
```
**Acceptance:** `FakeGateway` (tests) and the five adapters satisfy `isinstance(x, GatewayPort)`.

## SPEC-GW-POL-001: Data policy and model references (`gateways/policy.py`)
```python
@dataclass(frozen=True)
class GatewayDataPolicy:
    data_collection: Literal["deny", "allow"] = "deny"
    zdr: bool = True
    denied_model_patterns: tuple[str, ...] = ()          # fnmatch, case-insensitive
    require_parameters_for_tools: bool = True

    def is_denied(self, upstream_model: str) -> bool: ...


@dataclass(frozen=True)
class ModelRef:
    gateway: str
    model: str            # without variant
    variant: str | None   # "free" | "floor" | "nitro" | "exacto" | "batch" | other


def parse_model_ref(ref: str) -> ModelRef | None:
    """Return None for any string not starting with 'gw:' (legacy path). Raise GatewayConfigError
    for a 'gw:' string that does not match the grammar."""
```
- WHEN `parse_model_ref("gw:openrouter/deepseek/deepseek-chat:floor")` is called, THE SYSTEM SHALL return `ModelRef("openrouter", "deepseek/deepseek-chat", "floor")`.
- WHEN a non-`gw:` string is parsed, THE SYSTEM SHALL return `None` without raising.

## SPEC-GW-GEN-001: Generic OpenAI-compatible adapter (`gateways/openai_compatible.py`)
```python
class OpenAICompatibleGateway:
    def __init__(self, profile: GatewayProfile, *, secret_resolver: SecretResolver,
                 settings: Settings | None = None, http: httpx.AsyncClient | None = None) -> None: ...
    # implements GatewayPort; litellm_model = f"openai/{model}", api_base = profile.base_url

SecretResolver = Callable[[str], SecretStr | None]   # auth_ref -> secret (vault-backed, tenant-scoped)
```
- WHEN a gateway listed in `llm_gateway_openai_compatible` resolves a model, THE SYSTEM SHALL produce `litellm_model="openai/<model>"`, `api_base=<base_url>` and the configured headers, with an empty `extra_body`.
- IF the secret resolver returns `None` for a gateway that requires a key, THEN THE SYSTEM SHALL raise `GatewayCredentialError` naming the `auth_ref` only (never a value).

## SPEC-GW-OR-001: OpenRouter adapter (`gateways/openrouter.py`)
```python
class OpenRouterGateway(OpenAICompatibleGateway):
    def build_provider_object(self, *, wants_tools: bool, wants_structured: bool) -> dict[str, Any]: ...
    def build_extra_body(self, ref: ModelRef, *, wants_tools: bool, wants_structured: bool) -> dict[str, Any]: ...
```
- THE SYSTEM SHALL resolve `gw:openrouter/<vendor>/<model>` to `litellm_model="openrouter/<vendor>/<model>[:<variant>]"` with `api_base=openrouter_base_url`.
- THE SYSTEM SHALL send `HTTP-Referer: <openrouter_app_url>` and `X-OpenRouter-Title: <openrouter_app_title>` (OpenRouter still accepts the legacy `X-Title`, so only the current name is sent — verified 2026-10-04); WHERE `openrouter_app_categories` is non-empty, THE SYSTEM SHALL also send `X-OpenRouter-Categories`.
- THE SYSTEM SHALL send a stable `session_id` (body field; `sha256(org_id + ":" + session_id)[:64]`, ≤ 256 chars) on every call of a prismal session so OpenRouter's **sticky provider routing** keeps the same upstream provider and its prompt cache warm (sessions expire after 10 min of inactivity — verified 2026-10-04). This is the gateway half of TKE2's cache strategy.
- WHERE `openrouter_require_zdr` is true (default), THE SYSTEM SHALL put `extra_body["provider"]` containing `"data_collection": "deny"` and `"zdr": true`.
- WHEN tools or a structured output schema are bound to the call, THE SYSTEM SHALL set `provider.require_parameters = true`.
- WHERE `openrouter_provider_sort`, `openrouter_max_price`, `openrouter_only` or `openrouter_ignore` are set, THE SYSTEM SHALL copy them into the `provider` object verbatim. `provider` and `plugins` are always passed via `extra_body` (LiteLLM maps only `transforms`, `models`, `route` natively).
- WHERE `openrouter_fallback_models` is non-empty, THE SYSTEM SHALL set `extra_body["models"] = [primary, *fallbacks]` and `owns_model_fallback=True`.
- IF the variant is not in `openrouter_allowed_variants`, THEN THE SYSTEM SHALL raise `GatewayPolicyError("variant_not_allowed")`.
- IF the model is `openrouter/auto` and `openrouter_allow_auto` is false, THEN THE SYSTEM SHALL raise `GatewayPolicyError("auto_router_disabled")`.
- WHEN a response arrives, THE SYSTEM SHALL record the served `model` in `GatewayCallMeta.served_model` and log it at `debug` when it differs from the requested one.
- WHEN `quota()` is awaited, THE SYSTEM SHALL call `GET /api/v1/key` and return `QuotaSnapshot(window="credit", unit="usd", limit=…, remaining=limit_remaining, source="api")`, plus `usage_daily` / `usage_weekly` / `usage_monthly` and `free_model_daily_requests{used,limit,remaining}` as additional snapshots (field names verified 2026-10-04).
- WHEN an HTTP 402 is returned, THE SYSTEM SHALL read `error.metadata.limit_source` and raise `GatewayPaymentRequired(reason)` with `openrouter_credits → "account_balance"`, `openrouter_key_limit → "key_limit"`, `openrouter_in_flight_budget → "in_flight_budget"`, anything else → `"unknown"`, and SHALL NOT retry it.
- WHERE `openrouter_plugins` lists plugin ids, THE SYSTEM SHALL pass them in `extra_body["plugins"]`; the allowed set is `response-healing` (JSON repair, non-streaming), `context-compression` (middle-out truncation of over-long prompts) and `pdf`; the default is `[]`. `context-compression` is OFF by default because it silently drops the middle of the prompt and breaks TKE's cache-stable layout — TKE4 masking is the preferred mechanism; `web` is deprecated upstream and never sent.
- WHERE a `:free` model is used, THE SYSTEM SHALL count requests per day in the `QuotaLedger` (`window="day"`, `unit="requests"`, limit 50 below 10 purchased credits, 1,000 at 10+; 20 rpm — verified 2026-10-04).

## SPEC-GW-OC-001: OpenCode Zen and Go (`gateways/opencode.py`)
```python
class OpenCodeGateway(OpenAICompatibleGateway):
    def __init__(self, profile: GatewayProfile, *, plan: Literal["zen", "go"],
                 protocols: Mapping[str, WireProtocol], ledger: QuotaLedger | None,
                 secret_resolver: SecretResolver, settings: Settings | None = None,
                 http: httpx.AsyncClient | None = None) -> None: ...
    async def refresh_protocols(self) -> Mapping[str, WireProtocol] | None: ...   # GET /v1/models, fail-open

def load_opencode_model_protocols(path: str | None = None) -> dict[str, dict[str, WireProtocol]]:
    """Load config/opencode_models.yaml ({'zen': {id: protocol}, 'go': {id: protocol}}). A bad shape
    raises GatewayConfigError at load time."""
```
- THE SYSTEM SHALL map `gw:opencode/<id>` to base `opencode_zen_base_url` (default `https://opencode.ai/zen/v1`) and `gw:opencode-go/<id>` to `opencode_go_base_url` (default `https://opencode.ai/zen/go/v1`) **[verify]**.
- WHEN the model's protocol is `openai_chat`, THE SYSTEM SHALL use `litellm_model="openai/<id>"`; WHEN it is `anthropic_messages`, `"anthropic/<id>"`; WHEN it is `openai_responses`, `"openai/responses/<id>"` **[verify LiteLLM support]**. All three use `api_base` = the plan's base URL.
- IF a model id is absent from the protocol map, THEN THE SYSTEM SHALL raise `GatewayConfigError("unknown_opencode_model")`. Protocols are never guessed.
- THE SYSTEM SHALL send `User-Agent: prismal/<package version>` and `x-opencode-session: sha256(org_id + ":" + session_id)[:32]`, which is stable for a session and never the raw id.
- WHERE the plan is `go` and `agent` is not in `opencode_go_allowed_agents` (default `["coder","codeact_agent","dev_pipeline","code_review"]`), THE SYSTEM SHALL raise `GatewayPolicyError("opencode_go_agent_not_allowed")` before any network I/O, and SHALL treat `agent=None` as not allowed.
- IF the upstream model matches `opencode_denied_models` (default `["grok*","muse-spark*"]` **[verify]**), THEN THE SYSTEM SHALL raise `GatewayPolicyError("retention_denied_model")`.
- WHERE the plan is `go`, THE SYSTEM SHALL set `cost_mode="subscription"`.

## SPEC-GW-OMNI-001: OmniRoute (`gateways/omniroute.py`)
```python
class OmniRouteGateway(OpenAICompatibleGateway):
    async def astartup_check(self) -> None: ...
    async def detect_version(self) -> str | None: ...   # GET /health or version header [verify endpoint]
```
- THE SYSTEM SHALL resolve `gw:omniroute/<model>` to `litellm_model="openai/<model>"` with `api_base=omniroute_base_url` (default `http://localhost:20128/v1`).
- IF `omniroute_api_key` resolves empty, THEN THE SYSTEM SHALL raise `GatewayCredentialError` at startup. A key is mandatory.
- IF the detected version is below `omniroute_min_version` (default `3.8.49`, CVE-2026-88062), THEN THE SYSTEM SHALL raise `GatewayPolicyError("omniroute_vulnerable_version")` and SHALL mark the gateway unavailable.
- IF the version cannot be detected and `omniroute_allow_unknown_version` is false (default), THEN THE SYSTEM SHALL refuse the gateway the same way.
- IF `omniroute_upstream_attestation` is unset, or is `"subscription_oauth"` while `omniroute_allow_subscription_upstreams` is false, THEN THE SYSTEM SHALL refuse to enable OmniRoute.
- IF the requested model or combo matches `omniroute_denied_combos`, THEN THE SYSTEM SHALL raise `GatewayPolicyError("omniroute_denied_combo")`.
- WHEN `omniroute_base_url` uses plain `http` to a non-loopback, non-private host, THE SYSTEM SHALL log a `warning` once and increment `prismal.gateway_policy_denials_total{reason="omniroute_public_http"}` (warn only).

## SPEC-GW-RTR-001: `GatewayRouter` (`gateways/router.py`)
```python
class GatewayRouter:
    def __init__(self, gateways: Mapping[str, GatewayPort], *, settings: Settings | None = None,
                 org_id: str | None = None, ledger: QuotaLedger | None = None) -> None: ...
    def target_for(self, model: str | None, *, agent: str | None) -> str | None:
        """Return the effective gw: ref, or None (legacy path). Precedence: (1) model starting with
        'gw:'; (2) llm_gateway_agent_map[agent]; (3) None."""
    def resolve(self, model: str | None, *, agent: str | None, session_id: str | None) -> ResolvedModel | None: ...
    def get(self, name: str) -> GatewayPort | None: ...
    async def astartup(self) -> None: ...          # runs astartup_check on each gateway
    async def quotas(self) -> list[QuotaSnapshot]: ...
    async def aclose(self) -> None: ...

def build_gateway_router(settings: Settings, *, org_id: str | None = None,
                         secret_resolver: SecretResolver | None = None) -> GatewayRouter | None:
    """None when llm_gateways_enabled is false."""

def set_gateway_router(router: GatewayRouter | None) -> None: ...
def get_gateway_router() -> GatewayRouter | None:     # ContextVar first, then process global
```
- WHEN `resolve()` receives a `gw:` ref for an unknown or disabled gateway, THE SYSTEM SHALL raise `GatewayConfigError("unknown_gateway")`.
- THE SYSTEM SHALL complete `resolve()` without network I/O (asserted with a transport that fails on any request).

## SPEC-GW-REG-001: Registry integration (`providers/registry.py`, `gateways/chat_model.py`)
> **Amended 2026-10-05 (M0/X0-03):** see `docs/adr/ADR-WAVE-001-llm-construction-contract.md`. `GatewayChatLiteLLM` is the *innermost* concrete model; when `prompt_cache_enabled` (TKE2) is on, `CacheAwareChatModel` wraps it (pipeline step 6) and `with_fallbacks` is applied on the wrapped objects (step 7). GW owns the `extra_body` keys `provider`, `models`, `plugins`, `session_id`; TKE2/TKE6 keys must stay disjoint (contract-tested). Gateway refs restricted by agent require an explicit `agent=` (no `RunnableConfig` at construction time).
```python
class ProviderRegistry:
    def __init__(self, settings: Settings | None = None, *, gateway_router: GatewayRouter | None = None) -> None: ...
    def get_llm(self, model: str | None = None, streaming: bool = False, temperature: float | None = None,
                *, agent: str | None = None, session_id: str | None = None) -> BaseChatModel: ...
    def get_llm_with_fallback(self, model: str | None = None, streaming: bool = False,
                              temperature: float | None = None, *, agent: str | None = None,
                              session_id: str | None = None) -> BaseChatModel | RunnableWithFallbacks[...]: ...

class GatewayChatLiteLLM(ChatLiteLLM):
    gateway_name: str
    resolved_model_ref: str
    cost_mode: Literal["metered", "subscription"]
    # _create_chat_result override [verify hook]: served model, generation id, usage.cost /
    # _hidden_params["response_cost"] → response_metadata["prismal_gateway"]; _(a)generate runs QuotaLedger.preflight.
```
- WHERE `llm_gateways_enabled` is true and the target resolves to a gateway, THE SYSTEM SHALL return `GatewayChatLiteLLM(model=litellm_model, api_base=…, api_key=…, extra_headers=…, model_kwargs={"extra_body": …})`.
- WHERE `llm_gateways_enabled` is false, or the target is `None`, THE SYSTEM SHALL return a `ChatLiteLLM` built with exactly today's kwargs.
- WHEN `resolved.owns_model_fallback` is true, THE SYSTEM SHALL return the primary from `get_llm_with_fallback` without `with_fallbacks` and SHALL construct it with `max_retries=0`.
- THE SYSTEM SHALL NOT read or write `os.environ` on the gateway path.

## SPEC-GW-QUOTA-001: Flat-fee ledger (`gateways/quota.py`)
```python
@dataclass(frozen=True)
class QuotaWindow:
    name: Literal["5h", "week", "month", "day"]
    limit: float                    # usd-equivalent or requests
    unit: Literal["usd", "requests"]
    rolling: bool

@dataclass(frozen=True)
class QuotaVerdict:
    allowed: bool
    action: Literal["allow", "refuse", "downgrade"]
    exhausted_window: str | None
    downgrade_model: str | None

class QuotaLedger:
    def __init__(self, *, settings: Settings | None = None, clock: Callable[[], datetime] = utcnow,
                 store: QuotaStore | None = None) -> None: ...   # in-memory default; SQLite optional
    def windows_for(self, gateway: str, model: str) -> tuple[QuotaWindow, ...]: ...
    def preflight(self, gateway: str, model: str, *, est_equivalent_usd: float) -> QuotaVerdict: ...
    def record(self, gateway: str, model: str, *, equivalent_usd: float, requests: int = 1) -> None: ...
    def snapshot(self, gateway: str) -> list[QuotaSnapshot]: ...
```
- THE SYSTEM SHALL derive Go windows from config: `month = opencode_go_monthly_cap_usd`, `5h = month × opencode_go_window_5h_ratio` (default 0.20), `week = month × opencode_go_window_week_ratio` (default 0.50) **[verify]**.
- WHEN any window would be exceeded by `est_equivalent_usd`, THE SYSTEM SHALL return `refuse` or `downgrade` according to `opencode_go_quota_action`. On `refuse`, `GatewayChatLiteLLM` raises `GatewayQuotaExhausted` before any network call.
- THE SYSTEM SHALL scope the ledger per `(org_id, gateway)`. Ledger failures are fail-open (allow + counter).

## SPEC-GW-COST-001: Cost precedence (`providers/cost.py`, `budget/usage.py`, `budget/meter.py`)
> **Amended 2026-10-05 (M0/X0-03):** the merged precedence (subscription → gateway → litellm → table → none, plus AMR5 `batch`) is `docs/adr/ADR-WAVE-001-llm-construction-contract.md` §2.3. For subscription calls, `equivalent_usd` = `reported_cost_usd` when present, else the litellm/table price.
```python
@dataclass(frozen=True)
class CostEstimate:
    cost_usd: float
    source: str        # "gateway" | "litellm" | "table" | "subscription" | "none"
    equivalent_usd: float = 0.0     # NEW (GW): list-price equivalent for subscription calls

def compute_cost_usd(model: str, prompt_tokens: int, completion_tokens: int, *,
                     settings: Settings | None = None, cache_read_tokens: int = 0,
                     cache_write_tokens: int = 0, reported_cost_usd: float | None = None,
                     cost_mode: Literal["metered", "subscription"] = "metered") -> CostEstimate: ...

def extract_gateway_meta(message: object) -> GatewayCallMeta | None: ...   # budget/usage.py; never raises
```
- WHEN `cost_mode == "subscription"`, THE SYSTEM SHALL return `CostEstimate(0.0, "subscription", equivalent_usd=<litellm/table price>)` and SHALL record `equivalent_usd` in the `QuotaLedger`.
- WHEN `reported_cost_usd` is not `None`, THE SYSTEM SHALL return `source="gateway"`. Otherwise it follows the TKE1 order `litellm → table → none`.
- WHEN `CostMeter.record_response()` meters a message carrying `prismal_gateway` metadata, THE SYSTEM SHALL pass `reported_cost_usd` and `cost_mode` through and tag OTel with `gateway` and `served_model`.

## SPEC-GW-REC-001: Async reconciliation (`gateways/reconcile.py`)
```python
class CostReconciler:
    def __init__(self, router: GatewayRouter, *, settings: Settings | None = None,
                 max_concurrency: int = 4) -> None: ...
    def schedule(self, meta: GatewayCallMeta) -> None: ...   # non-blocking; drops when queue full
    async def drain(self, timeout_s: float = 5.0) -> None: ...
```
- WHERE `gateway_reconcile_enabled` is true and the gateway `has_generation_api`, WHEN a call completes with a `generation_id`, THE SYSTEM SHALL, after `gateway_reconcile_delay_s`, fetch the generation and record `|authoritative − reported|` in `prismal.gateway_cost_drift_usd`.
- IF the fetch fails, THEN THE SYSTEM SHALL drop the job silently (counter only), never affecting the turn.

## SPEC-GW-COMP-001: Composition root and per-tenant keys (`composition/runtime.py`)
```python
class RuntimeContext:  # existing dataclass, NEW field:
    gateways: GatewayRouter | None = None

def build_runtime(settings: Settings | None = None, *, org_id: str | None = None, ...) -> RuntimeContext: ...
```
- WHEN `build_runtime(org_id="acme")` runs with gateways enabled, THE SYSTEM SHALL resolve each gateway key from `CredentialVault` as `<auth_ref>@acme` (for example `openrouter_api_key@acme`).
- IF the tenant key is missing and `llm_gateway_tenant_key_fallback` is false (default), THEN THE SYSTEM SHALL disable that gateway for the tenant, and calls to it SHALL raise `GatewayCredentialError`. They never use the global key.
- WHEN `RuntimeContext.aclose()` runs, THE SYSTEM SHALL drain the reconciler and close gateway HTTP clients.

## SPEC-GW-CFG-001: Settings (`core/config.py`)

| Setting | Type | Default | Purpose |
|---|---|---|---|
| `llm_gateways_enabled` | `bool` | `False` | Master opt-in |
| `llm_gateway_agent_map` / `llm_gateway_openai_compatible` | `dict[str,str]` / `dict[str,dict]` | `{}` | agent → `gw:` ref; name → `{base_url, auth_ref, headers}` |
| `llm_gateway_tenant_key_fallback` | `bool` | `False` | Allow the global key when a tenant key is missing |
| `openrouter_enabled` / `openrouter_api_key` / `openrouter_base_url` | `bool` / `SecretStr` / `str` | `False` / `""` / `https://openrouter.ai/api/v1` | |
| `openrouter_app_url` / `openrouter_app_title` / `openrouter_app_categories` | `str` / `str` / `list[str]` | `""` / `"prismal"` / `[]` | Attribution headers (`HTTP-Referer`, `X-OpenRouter-Title`, `X-OpenRouter-Categories`) |
| `openrouter_session_stickiness` | `bool` | `True` | Send hashed `session_id` for sticky provider routing (cache hits) |
| `openrouter_plugins` | `list[str]` | `[]` | Allowed: `response-healing`, `context-compression`, `pdf` |
| `openrouter_auto_allowed_models` / `openrouter_auto_tier` | `list[str]` / `str \| None` | `[]` / `None` | `openrouter/auto` wildcards (e.g. `anthropic/*`) and cost tier `low`\|`medium`\|`high`\|`xhigh`\|`max` |
| `openrouter_require_zdr` | `bool` | `True` | `data_collection:"deny"` + `zdr:true` |
| `openrouter_provider_sort` / `openrouter_max_price` | `str \| None` / `dict[str,float]` | `None` / `{}` | `price`\|`throughput`\|`latency`; `{prompt, completion}` USD/M |
| `openrouter_only` / `openrouter_ignore` / `openrouter_fallback_models` | `list[str]` | `[]` | Provider slugs; gateway-owned model fallback |
| `openrouter_allowed_variants` / `openrouter_allow_auto` | `list[str]` / `bool` | `["floor","nitro","exacto"]` / `False` | `free`/`batch` opt-in; `openrouter/auto` gate |
| `openrouter_systemone_base_url` | `str \| None` | `None` | Forward ref for DEC (Jev via `POST /api/v1/systemone`, 32k context, input-only billing — verified 2026-10-04) |
| `opencode_zen_enabled` / `opencode_go_enabled` / `opencode_api_key` | `bool` / `bool` / `SecretStr` | `False` / `False` / `""` | |
| `opencode_zen_base_url` / `opencode_go_base_url` | `str` | see SPEC-GW-OC-001 | |
| `opencode_model_protocols_path` / `opencode_refresh_models` | `str` / `bool` | `config/opencode_models.yaml` / `False` | Protocol map; refresh from `/v1/models` |
| `opencode_go_allowed_agents` / `opencode_denied_models` | `list[str]` | 4 coding agents / `["grok*","muse-spark*"]` | ToS guard; retention deny-list |
| `opencode_go_monthly_cap_usd` | `float` | `0.0` | `0` = ledger disabled |
| `opencode_go_window_5h_ratio` / `_week_ratio` | `float` | `0.20` / `0.50` | |
| `opencode_go_quota_action` / `opencode_go_downgrade_model` | `str` / `str \| None` | `"refuse"` / `None` | `refuse`\|`downgrade` (model required for downgrade) |
| `omniroute_enabled` / `omniroute_api_key` / `omniroute_base_url` | `bool` / `SecretStr` / `str` | `False` / `""` / `http://localhost:20128/v1` | |
| `omniroute_min_version` / `omniroute_allow_unknown_version` | `str` / `bool` | `"3.8.49"` / `False` | CVE-2026-88062 floor |
| `omniroute_upstream_attestation` | `str \| None` | `None` | `api_keys_only`\|`subscription_oauth` |
| `omniroute_allow_subscription_upstreams` / `omniroute_denied_combos` | `bool` / `list[str]` | `False` / `[]` | ToS-violating if true; documented |
| `gateway_reconcile_enabled` / `gateway_reconcile_delay_s` / `gateway_quota_poll_interval_s` | `bool` / `float` / `int` | `False` / `2.0` / `300` | |

`_validate_llm_gateways` rejects an unknown sort, action or attestation, ratios outside (0, 1], `downgrade` without a downgrade model, a non-semver `omniroute_min_version`, and agent-map values that are not `gw:` refs.

## SPEC-GW-ERR-001: Exceptions (`core/exceptions.py`)
```python
class GatewayError(PrismalError): ...
class GatewayConfigError(GatewayError): ...         # load/startup time
class GatewayCredentialError(GatewayError): ...     # carries auth_ref, never the value
class GatewayPolicyError(GatewayError):             # fail-closed ToS / data / security guard
    def __init__(self, reason: str, *, gateway: str, agent: str | None = None) -> None: ...
class GatewayQuotaExhausted(GatewayError):
    def __init__(self, *, gateway: str, window: str) -> None: ...
class GatewayPaymentRequired(GatewayError):         # HTTP 402, non-retryable
    def __init__(self, *, gateway: str, reason: str) -> None: ...
```

## SPEC-GW-OTEL-001: Metrics (`monitoring/otel.py`)

`prismal.gateway_requests_total{gateway,requested_model,served_model}`, `prismal.gateway_fallbacks_total{gateway}`, `prismal.gateway_cost_drift_usd` (histogram), `prismal.gateway_quota_remaining{gateway,window}` (gauge), `prismal.gateway_policy_denials_total{gateway,reason}`, and `prismal.gateway_errors_total{gateway,kind}` for fail-open lookups and 402/429 responses.

## SPEC-GW-SNAP-001: Opt-in invariance

- WHERE `llm_gateways_enabled` is false, THE SYSTEM SHALL compile a supervisor graph byte-for-byte identical to the 3.13.0 baseline and SHALL pass identical kwargs to `ChatLiteLLM` for every existing `get_llm()` call.

## Acceptance Criteria (traceability summary)

| Requirement | Priority | Testable outcome |
|---|---|---|
| BASE-001..003, POL-001, GEN-001 | MUST | Protocol conformance; secret never in `repr`; grammar table; legacy strings → `None`; resolved kwargs captured |
| OR-001 | MUST | Request capture: headers, `provider` object, `models`; 402 typed and not retried |
| OC-001, OMNI-001 | MUST | Protocol → LiteLLM prefix table; guards fail closed with zero requests; `3.8.48` refused, `3.8.49` accepted |
| RTR-001, REG-001, SNAP-001 | MUST | Precedence; no I/O in `resolve`; no double fallback; no `os.environ` writes; snapshot + kwargs contract |
| QUOTA-001, COST-001 | MUST | Window arithmetic with a fake clock; refuse before network; precedence matrix incl. `subscription` |
| COMP-001, CFG-001, ERR-001 | MUST | Per-tenant key isolation, no global fallback by default; validation errors at load |
| REC-001, OTEL-001 | SHOULD | Drift histogram recorded, failure silent; each metric emitted once per event |

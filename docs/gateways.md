# LLM gateways (Phase GW)

Route agents through LLM **gateways**: OpenAI- or Anthropic-compatible HTTP
fronts for many upstream models. The built-in adapters are **OpenRouter**,
**OpenCode Zen** (pay as you go), **OpenCode Go** (flat fee), **OmniRoute**
(self-hosted), and any **generic OpenAI-compatible** endpoint. LiteLLM is still
the only transport (a thin `ChatLiteLLM` subclass, no gateway SDK), and every
gateway module lives under `prismal/providers/gateways/`.

Everything is **opt-in**. With `llm_gateways_enabled=False` (the default) the
supervisor graph and every `ProviderRegistry.get_llm()` call are identical to
v3.13.0; a graph snapshot test and the shared registry contract test
(`tests/unit/providers/test_registry_contract.py`) check this. Each gateway also
has its own `*_enabled` flag.

Verified vendor facts, with sources and dates, are in
[`specs/llm-gateway-providers/VERIFY.md`](../specs/llm-gateway-providers/VERIFY.md).

## Model references

```
gw:<gateway>/<upstream-model>[:<variant>]
gw:openrouter/anthropic/claude-sonnet-4.5
gw:openrouter/deepseek/deepseek-chat:floor
gw:opencode/gpt-5.5
gw:opencode-go/glm-5
gw:omniroute/my-combo
gw:my-proxy/qwen3-coder          # an llm_gateway_openai_compatible entry
```

The target for a call is chosen in this order:

1. an explicit `model` starting with `gw:`;
2. an explicit non-`gw:` model, which always stays on the legacy path;
3. `llm_gateway_agent_map[agent]` when no model is given;
4. otherwise the legacy path (`settings.default_model`, which may itself be a `gw:` ref).

```bash
export PRISMAL_LLM_GATEWAYS_ENABLED=true
export PRISMAL_OPENROUTER_ENABLED=true
export PRISMAL_OPENROUTER_API_KEY=sk-or-...
export PRISMAL_LLM_GATEWAY_AGENT_MAP='{"researcher": "gw:openrouter/anthropic/claude-sonnet-4.5"}'
```

```python
llm = ProviderRegistry().get_llm("gw:openrouter/anthropic/claude-sonnet-4.5",
                                 agent="researcher", session_id=session_id)
```

`agent=` and `session_id=` are new keyword-only arguments on `get_llm` and
`get_llm_with_fallback` (see
[ADR-WAVE-001](adr/ADR-WAVE-001-llm-construction-contract.md)). Gateway refs
restricted by agent, such as OpenCode Go, need an explicit `agent=`.

## Who decides what

**Prismal chooses the model; the gateway chooses the provider** (DD-GW-002).
Prismal decides which model and variant an agent uses: today through the agent
map, later through adaptive routing (AMR). A quota downgrade is also a prismal
decision. The gateway decides which upstream host serves that model and handles
provider failover for the *same* model. Prismal only sends constraints (`only`,
`ignore`, `max_price`, `sort`, data policy). `openrouter/auto` hands the model
choice to the gateway, so it is off by default (`openrouter_allow_auto=false`).

### No double fallback

Exactly one layer owns model fallback for a call (DD-GW-003):

- If the gateway owns it (OpenRouter with `openrouter_fallback_models`, or a
  generic gateway declared with `owns_model_fallback`), then
  `get_llm_with_fallback()` returns the primary model unwrapped and builds it
  with `max_retries=0`.
- Otherwise prismal's `fallback_model` applies as before. When the fallback goes
  to the *same* gateway, `GatewayPaymentRequired` (402) and `GatewayPolicyError`
  are not retried there, because retrying cannot succeed.

## Data retention defaults

| Gateway | Default | Enforced by prismal |
|---|---|---|
| OpenRouter | `provider.data_collection="deny"`, `provider.zdr=true`; `require_parameters` when tools or a schema are bound | Yes (request fields on every call) |
| OpenCode Zen/Go | Deny-list `grok*` (30-day retention), `muse-spark*` (training), `deepseek-v4-flash*` (training since the DeepSeek ZDR ended on 2026-08-31) | Partly (model filter) |
| OmniRoute | Opaque; the operator's responsibility | No |
| Generic | Pass-through headers only | No |

The ZDR default shrinks OpenRouter's provider pool, and some models become
unroutable. That is intentional; relax it with `openrouter_require_zdr=false`.
`:free` and `:batch` are not in the default `openrouter_allowed_variants`,
because their availability under ZDR is undocumented.

## OpenRouter

- **Request shape.** The adapter sends attribution headers (`HTTP-Referer`
  when `openrouter_app_url` is set, `X-OpenRouter-Title`, and
  `X-OpenRouter-Categories` when set). In `extra_body` it puts the `provider`
  object, `models` when gateway fallback is configured, `plugins` from
  `openrouter_plugins` (`response-healing`, `context-compression`, `pdf`;
  default none), and a hashed sticky `session_id`.
- **Sticky routing.** `session_id` is `sha256(org:session)`, never the raw id.
  It keeps the same upstream provider, and so its prompt cache, warm for the
  session (OpenRouter expires idle sessions after 10 min).
- **Plugins.** `context-compression` is off by default: it silently drops the
  middle of the prompt and breaks the TKE cache-stable layout. TKE observation
  masking is the preferred mechanism.
- **Gates.** A variant outside `openrouter_allowed_variants` is refused with
  `variant_not_allowed`. `openrouter/auto` is refused with
  `auto_router_disabled` unless `openrouter_allow_auto=true`; when allowed, it
  is restricted through an `auto-router` plugin entry with
  `openrouter_auto_allowed_models` and `openrouter_auto_tier`.
- **Cost.** The billed `usage.cost` is captured on every call (see Cost below).
  `quota()` reads `GET /api/v1/key`. A 402 raises
  `GatewayPaymentRequired(reason)` with reason `account_balance`, `key_limit`,
  `in_flight_budget` or `unknown`, and is never retried.
- **`:free` models.** These are counted per UTC day in the quota ledger (50
  requests/day below $10 of purchased credits; 1,000/day at $10 or more; 20 rpm).

## OpenCode Zen and Go

- **Endpoints.** `gw:opencode/<id>` uses Zen (`https://opencode.ai/inference/v1`)
  and `gw:opencode-go/<id>` uses Go (`https://opencode.ai/inference/go/v1`), the
  bases documented since 2026-10 (the older `/zen/v1` and `/zen/go/v1` still
  answer but are undocumented; override with `opencode_{zen,go}_base_url`).
- **Protocol map.** Each model speaks one protocol, configured in
  `config/opencode_models.yaml`: `openai_chat` → `openai/<id>`,
  `anthropic_messages` → `anthropic/<id>`, `openai_responses` →
  `openai/responses/<id>`. An id missing from the map raises
  `GatewayConfigError("unknown_opencode_model")`; protocols are never guessed.
  `opencode_refresh_models=true` tries to refresh the map from `/v1/models` at
  startup (fail-open).
- **Headers.** Every call sends `User-Agent: prismal/<version>` and
  `x-opencode-session: sha256(org:session)[:32]`.
- **Go ToS.** OpenCode Go is "designed for OpenCode and other coding agents".
  Prismal restricts it to `opencode_go_allowed_agents` (default `coder`,
  `codeact_agent`, `dev_pipeline`, `code_review`). Any other agent, or
  `agent=None`, is refused with `opencode_go_agent_not_allowed` before any
  request.
- **Go quota.** Go is a flat fee, so calls meter `cost_usd=0` with
  `equivalent_usd` at list price. The ledger tracks the 5 h (20 %), weekly
  (50 %) and monthly windows of `opencode_go_monthly_cap_usd`. An exhausted
  window is refused before the network call (`GatewayQuotaExhausted`), or
  downgraded to `opencode_go_downgrade_model` when
  `opencode_go_quota_action=downgrade`. OpenCode has no quota API, so confirm
  the ratios against the console.
- **Spend risk.** Zen auto-reloads $20 when the balance drops below $5. That is
  an account setting prismal does not control.

## Generic OpenAI-compatible gateways

```bash
export PRISMAL_LLM_GATEWAY_OPENAI_COMPATIBLE='{"my-proxy": {"base_url": "https://proxy.internal/v1", "auth_ref": "my_proxy_key", "headers": {"X-Team": "core"}}}'
```

Each entry resolves to `openai/<model>` with that `api_base`, those headers and
an empty `extra_body`. Optional keys: `owns_model_fallback` and
`denied_models`. An entry with an empty `auth_ref` needs no key.

## Keys and tenants

Keys are resolved lazily, unwrapped only at the `ChatLiteLLM` constructor
boundary, and never written to `os.environ`, logs, exceptions, URLs or `repr`.

- `build_runtime(org_id="acme")` resolves each gateway key from the credential
  vault as `<auth_ref>@acme` (for example `openrouter_api_key@acme`). Pass
  `gateway_vault=` explicitly, or prismal uses the identity vault (when
  `identity_enabled`) or an `EnvVault` over the injected config source.
- A missing tenant key disables that gateway for the tenant (calls raise
  `GatewayCredentialError`). It never silently bills the operator's global key
  unless `llm_gateway_tenant_key_fallback=true`.
- `build_runtime` runs each gateway's startup gate before first use. A gateway
  whose gate fails or crashes is disabled for that runtime (fail-closed).
- In `global` mode the router is installed process-wide (`set_gateway_router`);
  in `context` mode the host binds `ctx.gateways` per request with
  `use_gateway_router(ctx.gateways)`.

## Cost and quotas

- **Precedence.** `compute_cost_usd` (ADR-WAVE-001 §2.3) uses: `subscription`
  (flat fee, `cost_usd=0`, `equivalent_usd` at list price) → `gateway` (billed
  cost) → `litellm` → `table` → `none`.
- **Metadata.** `GatewayChatLiteLLM` stores served model, generation id, billed
  cost and cost mode in `response_metadata["prismal_gateway"]`, for sync,
  async and streaming calls. `CostMeter` reads it, tags OTel with `gateway` and
  `served_model`, and `RunSummary` exposes `gateway`, `served_model` and
  `equivalent_usd`.
- **Reconciliation.** With `gateway_reconcile_enabled`, the reconciler fetches
  OpenRouter's `/api/v1/generation` after `gateway_reconcile_delay_s` and
  records `prismal.gateway_cost_drift_usd`. It is alert-only and never rewrites
  metered usage.
- **Metrics.** `prismal.gateway_requests_total`, `gateway_fallbacks_total`,
  `gateway_cost_drift_usd`, `gateway_quota_remaining`,
  `gateway_policy_denials_total`, `gateway_errors_total`. Served-model labels
  are capped at 200 distinct values; the rest are reported as `other`.

## Rollout

1. Ship with `llm_gateways_enabled=false`.
2. **Shadow:** route one non-critical agent through OpenRouter via the agent
   map, with `gateway_reconcile_enabled=true`, and watch the drift metric.
3. **Warn:** enable OpenCode Go for coding agents with
   `opencode_go_quota_action=downgrade`.
4. **Enforce:** switch to `refuse`, attest OmniRoute as `api_keys_only`, and
   use per-tenant keys in the host.

## OmniRoute compliance

OmniRoute is a self-hosted, OpenAI-compatible router. Prismal cannot see which
upstream accounts OmniRoute uses, so `prismal.providers.gateways.omniroute`
applies fail-closed guards. Every refusal raises `GatewayPolicyError` and
increments `prismal.gateway_policy_denials_total{gateway="omniroute", reason=...}`.

**Startup gate.** `GatewayRouter.astartup()` runs `OmniRouteGateway.astartup_check()`
once. Until that check passes, `resolve()` refuses every model
(`omniroute_unverified_version`). If the check fails, the gateway is disabled and
resolution keeps failing with the recorded reason.

| Check | Refusal reason |
|---|---|
| No `omniroute_api_key` | `GatewayCredentialError` (an API key is mandatory) |
| `omniroute_upstream_attestation` unset | `omniroute_attestation_missing` |
| `subscription_oauth` without `omniroute_allow_subscription_upstreams=true` | `omniroute_subscription_upstreams` |
| Version below `omniroute_min_version` (default `3.8.51`) | `omniroute_vulnerable_version` |
| Version unknown and `omniroute_allow_unknown_version=false` | `omniroute_unknown_version` |
| Model/combo matches `omniroute_denied_combos` (fnmatch, case-insensitive) | `omniroute_denied_combo` |

**Subscription-OAuth upstreams (ToS risk).** Routing traffic through consumer
subscription accounts (Claude Code, Codex, Gemini CLI) violates those providers'
terms, and accounts have been banned for it. You must attest which kind of
upstream you use: `api_keys_only` or `subscription_oauth`. `subscription_oauth`
also needs `omniroute_allow_subscription_upstreams=true`, and prismal logs a
warning at every startup while it is enabled. Put subscription-backed combo
names in `omniroute_denied_combos`.

**CVE-2026-88062 (GHSA-hf57-cqmx-p4gr).** This is an unauthenticated RCE via
`POST /api/acp/agents` when `requireLogin=false`. The advisory was published on
2026-09-03. It lists `omniroute` ≤ 3.8.50 as affected and names no patched
version. Prismal's default floor is `3.8.51`, the first release after the
advisory. Re-check the advisory before you rely on that floor, and raise
`omniroute_min_version` if a later fix is named.

**Version detection.** OmniRoute documents no version endpoint. Prismal calls
`GET <root>/api/monitoring/health` (the base URL without its trailing `/v1`) and
reads a `version` field or an `x-omniroute-version` header if either is present.
Otherwise it uses `omniroute_declared_version`, which you set to the version you
deployed. Prismal logs `gateway.omniroute_version_declared` at startup when it
uses that setting. If neither source gives a version, OmniRoute is refused
unless `omniroute_allow_unknown_version=true`.

**Deployment.**
- Install only from the official sources: the repository `diegosouzapw/OmniRoute`
  and the npm package `omniroute`. Many look-alike forks exist.
- Bind OmniRoute to loopback or a private network. Never expose it publicly.
  A plain `http://` base URL that points at a public host logs a warning once
  and is counted as `omniroute_public_http`. This is a warning only; prismal
  does not block it.
- Require an API key on the OmniRoute side, too.
- OmniRoute cannot enforce upstream data-retention or training policy, and
  prismal cannot enforce it through OmniRoute
  (`capabilities.enforces_data_policy=False`). That is the operator's
  responsibility.

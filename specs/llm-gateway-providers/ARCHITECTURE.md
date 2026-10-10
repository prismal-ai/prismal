# Prismal LLM Gateway Providers — Technical Design Document

## Metadata

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `IMPLEMENTED` |
| **Version** | 1.0 |
| **Date** | 2026-10-04 |
| **Phase** | GW |
| **Target package version** | `3.14.0` (SemVer minor — new opt-in functionality) |
| **PLAN** | `specs/llm-gateway-providers/PLAN.md` |
| **SPEC** | `specs/llm-gateway-providers/SPEC.md` |
| **TASKS** | `specs/llm-gateway-providers/TASKS.md` |

---

## 1. Context

Confirmed against the code (2026-10-04):

- `providers/registry.py::get_llm()` constructs `ChatLiteLLM(model, streaming, temperature, max_tokens, request_timeout, max_retries)` and nothing else. There are no per-call `api_base`, `api_key`, `extra_headers` or `model_kwargs`. `_inject_api_keys()` mirrors three vendor keys and `OLLAMA_API_BASE` into `os.environ` with `setdefault`, and sets `litellm.modify_params = True` globally.
- `get_llm_with_fallback()` wraps the primary in `with_fallbacks([fallback_model])` whenever `fallback_model` is set and differs from the primary.
- `providers/cost.py::compute_cost_usd(model, prompt, completion, *, settings)` → `CostEstimate(cost_usd, source ∈ litellm|table|none)`. TKE1 adds `cache_read_tokens`, `cache_write_tokens`, `reported_cost_usd` and source `"gateway"`. GW is the first *producer* of `reported_cost_usd`.
- `budget/meter.py::CostMeter.record_response(message, model, …)` does extract → price → `record()`. `budget/usage.py::extract_token_usage()` reads only LangChain message metadata and stays free of provider imports.
- `composition/runtime.py::build_runtime(settings, *, org_id, …)` already composes per-tenant ports and exposes them on `RuntimeContext`. IDN's `CredentialVault` (`EnvVault` / `FileVault` / `FakeVault`) supplies scoped secrets, and W's `ConfigSourcePort` is the only sanctioned reader of the environment.

## 2. Feasibility with the existing core

- **LiteLLM already speaks every wire protocol needed.** `openrouter/<vendor>/<model>`, `openai/<id>` + `api_base`, and `anthropic/<id>` + `api_base` are supported. `openai/responses/<id>` against a custom base is **[verify]** (GW0-04). `ChatLiteLLM` accepts `api_base`, `api_key`, `extra_headers` and `model_kwargs`, so no fork is needed.
- **The billed cost reaches the process.** OpenRouter returns `usage.cost`, `prompt_tokens_details.cached_tokens`, `cache_write_tokens` and `reasoning_tokens` on every response in 2026 (`usage:{include:true}` is now a no-op). LiteLLM places the billed cost in `response._hidden_params`. `langchain_litellm` may not copy `_hidden_params` into `AIMessage.response_metadata` **[verify]**, so GW captures it in a thin `ChatLiteLLM` subclass at the raw-response seam (`_create_chat_result`, **[verify hook name]** in GW0-03). The subclass lives in `providers/`, so Rule #4 holds.
- **Composition root and vault exist.** Per-tenant keys need no new mechanism: `build_runtime(org_id)` already resolves scoped config, and GW adds one more port, `RuntimeContext.gateways`.
- **No new runtime dependency.** `httpx` (already pinned) serves `/api/v1/key`, `/api/v1/generation`, `/v1/models` and the OmniRoute health check.

## 3. Proposed Architecture

### 3.1 Modules

| Module | Purpose |
|---|---|
| `providers/gateways/base.py` | Value objects + `GatewayPort` (SPEC-GW-BASE-*) |
| `providers/gateways/policy.py` | `GatewayDataPolicy`, `ModelRef`, `parse_model_ref` |
| `providers/gateways/openai_compatible.py` | Generic adapter; shared base for the other four |
| `providers/gateways/openrouter.py` | Headers, `provider` object, `models` fallback, variants, credit, 402 mapping |
| `providers/gateways/opencode.py` | Zen + Go, per-model protocol map, ToS guard, retention deny-list |
| `providers/gateways/omniroute.py` | Startup version/attestation gate, combo deny-list |
| `providers/gateways/router.py` | `GatewayRouter`, `build_gateway_router`, ContextVar/global accessor |
| `providers/gateways/quota.py` | `QuotaLedger` (flat-fee and `:free` request windows) |
| `providers/gateways/chat_model.py` | `GatewayChatLiteLLM` (metadata capture, quota preflight) |
| `providers/gateways/reconcile.py` | `CostReconciler` (async generation lookups) |
| `config/opencode_models.yaml` | Default per-model wire-protocol map (illustrative ids **[verify]**) |

### 3.2 Resolution and call flow (`llm_gateways_enabled=True`)

```
agent node ──► ProviderRegistry.get_llm(model, agent="coder", session_id=sid)
                      │
                      ▼
        GatewayRouter.target_for(model, agent)        (pure, no I/O)
          model startswith "gw:" ? ── yes ──► ref = model
          agent in llm_gateway_agent_map ? ── yes ──► ref = map[agent]
          else ──► None ──► legacy ChatLiteLLM(...)  (unchanged kwargs)
                      │ ref
                      ▼
        parse_model_ref(ref) ─► ModelRef(gateway, model, variant)
                      │
                      ▼
        GatewayPort.resolve(ref, agent, session_id)
          ├─ policy guards (fail-closed): variant / auto / ToS agent / retention / combo
          ├─ secret = secret_resolver(auth_ref[@org])  (SecretStr, lazy)
          └─ ResolvedModel(litellm_model, api_base, api_key, headers, extra_body,
                           wire_protocol, owns_model_fallback, cost_mode)
                      │
                      ▼
        GatewayChatLiteLLM(model=…, api_base=…, api_key=…, extra_headers=…,
                           model_kwargs={"extra_body": …},
                           max_retries = 0 if owns_model_fallback else retry_attempts)
                      │  per call
                      ▼
        QuotaLedger.preflight ── refuse ──► GatewayQuotaExhausted (no network)
                      │ allow / downgrade(model)
                      ▼
        LiteLLM HTTP ──► gateway ──► upstream provider
                      │
                      ▼
        _create_chat_result: response_metadata["prismal_gateway"] = GatewayCallMeta
                      │
                      ▼
        CostMeter.record_response ─► compute_cost_usd(reported_cost_usd, cost_mode)
                      │                    gateway > litellm > table > none | subscription
                      ├─► QuotaLedger.record(equivalent_usd)
                      └─► CostReconciler.schedule(meta)  (async, fail-open)
```

### 3.3 OpenRouter request shape (defaults)

```
POST {openrouter_base_url}/chat/completions          (via litellm "openrouter/…")
Headers: HTTP-Referer, X-OpenRouter-Title (X-Title still accepted), X-OpenRouter-Categories?
extra_body:
  provider: { data_collection: "deny", zdr: true,
              require_parameters: true            # only when tools / schema bound
              sort?, max_price?, only?, ignore? }
  models?:  [primary, *openrouter_fallback_models]  # ⇒ owns_model_fallback
  session_id: sha256(org:session)[:64]              # sticky provider ⇒ cache hits (10 min idle expiry)
  plugins?: [response-healing | context-compression | pdf]   # default []
```

Default provider load balancing is left to OpenRouter (skip providers with an outage in the last 30 s, weight by 1/price²). Prismal sets `sort` only when the operator asks for it.

### 3.4 Composition and tenancy

```
build_runtime(settings, org_id="acme")
   └─► build_gateway_router(settings, org_id="acme",
                            secret_resolver=lambda ref: vault.resolve(f"{ref}@acme").value)   (verified GW0-08)
          ├─ OpenRouterGateway / OpenCodeGateway(zen|go) / OmniRouteGateway / OpenAICompatibleGateway(*)
          ├─ QuotaLedger scoped (org_id, gateway)
          └─ await router.astartup()   # OmniRoute version + attestation, optional /v1/models refresh
   RuntimeContext.gateways = router ; set_gateway_router(router) in a ContextVar for this tenant
```

## 4. Design Decisions

### DD-GW-001: Gateways are a provider-layer port, not a new agent concept
`GatewayPort` lives under `providers/gateways/`. Agents keep calling `ProviderRegistry.get_llm()`, with two new optional keywords (`agent`, `session_id`). LiteLLM remains the only transport, and no gateway SDK is introduced.

### DD-GW-002: Where routing policy lives — prismal chooses the model, the gateway chooses the provider
- **Prismal owns semantic model choice**: which model (and variant) an agent or role uses, the agent map today, and AMR's tiers, cascades and budget-aware downgrade tomorrow. A quota `downgrade` is also a prismal decision, because it changes the *model*.
- **The gateway owns provider choice**: which upstream host serves the chosen model, load balancing, provider failover for the *same* model, and quantization and region filters. Prismal only expresses constraints (`only`, `ignore`, `max_price`, `sort`, data policy) and never picks the upstream host itself.
- `openrouter/auto` hands *model* choice to the gateway. It therefore contradicts this split and is off by default (`openrouter_allow_auto=False`).
- Consequence: AMR (3.16.0) emits `gw:` refs; it never emits provider slugs.

### DD-GW-003: No double fallback
Exactly one layer owns model fallback for a given call.
- If `ResolvedModel.owns_model_fallback` is true (OpenRouter with `models[]`, or a generic gateway declared with that capability), `get_llm_with_fallback()` returns the primary unwrapped and builds it with `max_retries=0`.
- Otherwise prismal's `fallback_model` applies as today.
- Provider-level failover inside the gateway (same model) is never counted as model fallback.
- `GatewayPaymentRequired` (402) and `GatewayPolicyError` are excluded from `with_fallbacks(exceptions_to_handle=…)` when the fallback resolves to the same gateway and key, because retrying cannot succeed and only adds cost.

### DD-GW-004: Privacy-preserving defaults
OpenRouter defaults to `data_collection:"deny"` and `zdr:true`. This shrinks the provider pool, and some models will become unroutable. That is intentional, and the error message names the setting to relax. `:free` and `:batch` variants are excluded from the default allow-list because their availability under ZDR is **[verify]**. OpenCode applies a retention deny-list. OmniRoute cannot enforce upstream policy (`enforces_data_policy=False`), which is documented and surfaced in `docs/gateways.md`.

### DD-GW-005: Gateway-reported cost is most authoritative; flat-fee is `subscription`
The precedence is `gateway > litellm > table > none`. Flat-fee calls use `source="subscription"` with `cost_usd=0.0` (marginal) and `equivalent_usd` at list price. Budgets in USD therefore measure real spend, while the `QuotaLedger` measures allowance burn. Reconciliation against `/api/v1/generation` measures drift. It does not rewrite already-recorded usage.

### DD-GW-006: Per-tenant keys via the composition root, no global fallback
Secrets are resolved per `org_id` through the IDN vault (`<auth_ref>@<org>`). A missing tenant key disables that gateway for that tenant. Silently billing the operator's global key is opt-in only (`llm_gateway_tenant_key_fallback`). Keys never reach `os.environ`, and `GatewayChatLiteLLM` receives them as constructor arguments.

### DD-GW-007: Compliance guards are fail-closed and evaluated before I/O
The OpenCode Go agent allow-list, the retention deny-list, the variant/auto gates and the OmniRoute version/attestation checks all raise `GatewayPolicyError` before any network call, and each increments `prismal.gateway_policy_denials_total{reason}`. An operator override is possible only through settings, and every override is logged at startup.

### DD-GW-008: Protocols are configured, never guessed
OpenCode endpoints differ per model. The protocol map is YAML and can optionally be refreshed from `/v1/models`. An unknown id raises `GatewayConfigError` instead of falling back to `openai_chat`, because a wrong protocol fails late and confusingly.

### DD-GW-009: Opt-in and snapshot-guaranteed
Every wiring point is gated by `llm_gateways_enabled`, and each gateway also has its own `*_enabled` flag. Two tests prove the flag-off state is identical to today: a snapshot test on the graph and a kwargs contract test on `get_llm()`.

## 5. Security, compliance & cost

### 5.1 ToS / compliance
- **OpenCode Go** is "designed for OpenCode and other coding agents"; validated third-party clients include Claude Code, Codex and Kilo Code CLI **[verify]**. Prismal therefore limits Go to coding-agent roles (`coder`, `codeact_agent`, `dev_pipeline`, `code_review`). Using Go for general chat, RAG or swarm workers is denied by default. Zen (pay-as-you-go) carries no such restriction **[verify]**.
- **OmniRoute subscription-OAuth upstreams** route traffic through consumer subscription accounts (Claude Code, Codex, Gemini CLI). This violates those providers' terms and has led to account bans (issue #4118). Prismal cannot inspect OmniRoute's 4-tier combos, so the guard is layered: a mandatory startup attestation, `omniroute_allow_subscription_upstreams=False`, a combo-name deny-list, and documentation. Choosing `subscription_oauth` requires an explicit flag and logs a warning at every startup.
- **OmniRoute CVE-2026-88062** (GHSA-hf57-cqmx-p4gr): unauthenticated RCE when `requireLogin=false`, listed as affecting ≤ 3.8.50 with no named patch (GW0-07, 2026-10-05); the default floor is 3.8.51. Prismal refuses versions below `omniroute_min_version`, refuses unknown versions by default, requires an API key, and warns on public plain-HTTP base URLs. Docs: install only from the official repository (npm `omniroute`, Docker `diegosouzapw/omniroute`), because many look-alike forks exist. Bind to loopback or a private network, and never expose it publicly. OmniRoute stores keys with AES-256-GCM, but that does not protect a publicly reachable admin surface.

### 5.2 Data retention defaults
| Gateway | Default | Enforceable by prismal |
|---|---|---|
| OpenRouter | `data_collection:"deny"`, `zdr:true`, `require_parameters` with tools | Yes (request field) |
| OpenCode Zen/Go | Deny-list `grok*` (30-day retention), `muse-spark*` (training) **[verify]**; DeepSeek ZDR "through October 2026" re-checked in GW0-06 | Partially (model filter) |
| OmniRoute | Opaque; operator responsibility | No (documented) |
| Generic | Pass-through headers only | No |

### 5.3 Secrets
Keys are `SecretStr` from vault to constructor. They are excluded from `repr`, logs and exceptions, and never appear in URLs (OpenRouter/OpenCode use the `Authorization` header). A log-capture test injects a sentinel key and greps every log record and exception string for it.

### 5.4 Cost
- Fail-open: if reported cost is missing, pricing falls back to litellm/table and `gateway_errors_total{kind="cost_missing"}` increments.
- 402 is surfaced with a reason mapped from `error.metadata.limit_source` (`openrouter_credits`, `openrouter_key_limit`, `openrouter_in_flight_budget`) and is never retried.
- `:free` models are capped at 20 rpm and 50/day (< 10 credits purchased) or 1,000/day; the ledger counts them (verified 2026-10-04).
- OpenCode Zen auto-reload ($20 when the balance drops below $5) is an account setting. It is documented as a spend risk, not controlled by prismal **[verify]**.

## 6. Observability

| Metric | Type | Labels | Emitted when |
|---|---|---|---|
| `prismal.gateway_requests_total` | counter | `gateway, requested_model, served_model` | each completed gateway call |
| `prismal.gateway_fallbacks_total` | counter | `gateway` | served model ≠ requested (gateway-owned fallback) |
| `prismal.gateway_cost_drift_usd` | histogram | `gateway` | reconciliation completes |
| `prismal.gateway_quota_remaining` | gauge | `gateway, window` | quota poll / ledger update |
| `prismal.gateway_policy_denials_total` | counter | `gateway, reason` | any fail-closed guard |
| `prismal.gateway_errors_total` | counter | `gateway, kind` | fail-open lookups, 402, 429 |

Spans: the existing `provider.get_llm` span gains `prismal.gateway` and `prismal.wire_protocol` attributes. `ObservabilityPort` `RunSummary` gains `gateway` and `served_model` per call, building on the cache fields TKE1 added. `requested_model` labels come from config, so cardinality stays bounded. Free-text model ids from responses are bucketed to `other` beyond 200 distinct values.

## 7. Relationship to existing specs

- **TKE (3.13.0)**: TKE1 supplies `Usage` cache/reasoning fields and the `reported_cost_usd` parameter, which GW fills. TKE2 cache detection already recognises `openrouter/anthropic/*`, and GW passes `cache_control` through unchanged.
- **C (cost-budget-governance)**: GW is a producer for `CostMeter`. Quota preflight adds a gateway-side signal next to `BudgetGuard`'s USD and token dimensions, and `downgrade` emits a `Degradation(reduce=True, reason="gateway_quota:<window>")`.
- **IDN / W / R**: scoped secrets via `CredentialVault` and `ConfigSourcePort`, composed in `build_runtime`.
- **DEC (3.15.0)**: may call Jev through `openrouter_systemone_base_url` (forward reference only).
- **AMR (3.16.0)**: consumes `GatewayRouter` and `QuotaSnapshot`, and owns semantic routing per DD-GW-002.
- **prismal-server RH2**: injects per-tenant gateway config, reports `gateway` and `served_model` in the `done` event, and exposes `QuotaSnapshot` via `GET /v1/budget`.

## 8. Testing strategy (detail in `TASKS.md`)

- **Unit, pure:** ref grammar, router precedence, per-adapter `resolve()` tables, policy guards (fail-closed, zero requests via an `httpx.MockTransport` that fails on use), ledger arithmetic with a fake clock, cost precedence matrix, settings validation.
- **Unit, HTTP-faked:** request capture of headers and `extra_body`; 402/429 mapping; `/api/v1/key`, `/api/v1/generation`, `/v1/models` and the OmniRoute health check via `MockTransport`.
- **Contract:** no double fallback; `get_llm()` kwargs unchanged with the flag off; graph snapshot; no `os.environ` mutation; secret never in logs or `repr`.
- **Integration (fakes):** `build_runtime(org_id)` with `FakeVault`, covering two tenants with different keys and a missing-key tenant.
- **Live (`@pytest.mark.live_api`, opt-in, skipped in CI):** one OpenRouter `:free` call checking that usage and cost come back and `served_model` is logged; an optional OpenCode Zen call when a key is present.

## 9. Rollout

1. Ship with `llm_gateways_enabled=False`.
2. **Shadow**: enable OpenRouter for one non-critical agent through `llm_gateway_agent_map` with `gateway_reconcile_enabled=True`. Compare `source="gateway"` cost against litellm estimates and watch `gateway_cost_drift_usd`.
3. **Warn**: enable OpenCode Go for coding agents with the ledger action `downgrade`, and confirm the window ratios against the OpenCode dashboard.
4. **Enforce**: switch the ledger action to `refuse`, set OmniRoute (where used) to `api_keys_only` attestation, and turn on per-tenant keys in prismal-server.

## 10. Open questions

- Should reconciliation *correct* `CostMeter` totals, or only alert? Alert-only is proposed for 3.14.0.
- Should `GatewayRouter` be passed explicitly into every agent's `ProviderRegistry`, or found via a ContextVar set per request by the host? ContextVar + global fallback is proposed, and RH2 needs to confirm.
- Should the OpenCode protocol refresh run at startup by default once `/v1/models` exposes protocol info? It depends on GW0-05.
- Should the `QuotaLedger` persist across restarts (SQLite store)? In-memory is the default; persistence is an optional adapter.

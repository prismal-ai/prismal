# Prismal — LLM Gateway Providers (OpenRouter, OpenCode Zen/Go, OmniRoute, generic OpenAI-compatible)

## Strategic Plan / Product Requirements Document (PLAN)

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `READY` |
| **Version** | 1.0 |
| **Date** | 2026-10-04 |
| **Phase** | GW (LLM Gateway Providers) |
| **Target package version** | `3.14.0` (SemVer minor — new opt-in functionality, not yet started) |
| **Reviewers** | Tech Lead, AI Architect, Security Lead, FinOps |
| **Priority** | P1 (cost control, provider diversity, data-retention guarantees) |
| **Depends on** | W (`ConfigSourcePort`), R (`build_runtime` / `RuntimeContext`), IDN (`CredentialVault`), C (`CostMeter` / `BudgetGuard`), TKE1 (cache/reasoning usage fields + `reported_cost_usd` precedence) |
| **Related** | `docs/gap-analysis-gateways-tokens-decisions-2026-10.md` (§2 rows *Provider access*, *Cost*; §3.1), `specs/token-efficiency/` (TKE1), `specs/cost-budget-governance/` (C), `specs/agent-identity-governance/` (IDN), `specs/typed-decision-models/` (DEC — forward ref, Jev via OpenRouter), `specs/adaptive-model-routing/` (AMR — consumer of `GatewayPort`) |

---

## 1. Executive Summary

Prismal reaches every LLM through `ProviderRegistry.get_llm()` → `ChatLiteLLM(model=…)`, with credentials mirrored into `os.environ` for three vendors and one global `fallback_model`. There is no notion of an **LLM gateway** — an intermediary that serves many upstream models behind one endpoint, applies provider-selection and data-retention policy, reports billed cost, and enforces credit or subscription quotas.

Phase **GW** adds a hexagonal `GatewayPort` with five adapters — **OpenRouter**, **OpenCode Zen** (pay-as-you-go), **OpenCode Go** (flat-fee subscription), **OmniRoute** (self-hosted) and a **generic OpenAI-compatible** adapter (LiteLLM proxy, Portkey, Bifrost, …) — plus a `GatewayRouter` that resolves `gw:<gateway>/<model>` references and a per-agent map. It makes gateway-reported cost the most authoritative cost source, turns gateway credit and flat-fee windows into budget signals, enforces **privacy-preserving defaults** (`data_collection: "deny"`, `zdr: true`) and ships **ToS/compliance guards** (OpenCode Go restricted to coding agents; OmniRoute subscription-OAuth upstreams refused; OmniRoute minimum version for CVE-2026-88062).

Everything is behind `llm_gateways_enabled=False`; with the flag off the compiled supervisor graph and every `get_llm()` output are byte-for-byte unchanged.

---

## 2. Context and Problem

Code facts (verified 2026-10-04, gap analysis §2):

- **No gateway concept.** `providers/registry.py::get_llm()` builds `ChatLiteLLM(model, streaming, temperature, max_tokens, request_timeout, max_retries)` — no per-model `api_base`, `api_key`, `extra_headers` or `extra_body`. OpenRouter works only *by accident* if `OPENROUTER_API_KEY` happens to be in the environment, with no app attribution headers and no provider/data-retention controls.
- **Secrets are mirrored to `os.environ`.** `_inject_api_keys()` writes `ANTHROPIC_/OPENAI_/GOOGLE_API_KEY` with `setdefault`. That is process-global, cannot be per-tenant, and contradicts constitution principle 3 for new code.
- **One global fallback.** `get_llm_with_fallback()` wraps the primary with `with_fallbacks([fallback_model])`. Gateways have their own model/provider fallback. Stacking both multiplies latency and cost on failure, and it can bill a model the operator never chose.
- **Cost ignores the gateway's bill.** `providers/cost.py::compute_cost_usd()` runs `litellm.cost_per_token` → `budget_pricing` table → `0.0` (`source ∈ litellm|table|none`). OpenRouter returns the **actual billed cost** on every response (`usage.cost`), and LiteLLM surfaces it in `response._hidden_params`. Prismal throws that away and re-estimates from list prices, which are wrong for provider routing, BYOK, discounts and cache pricing.
- **Flat-fee plans are invisible.** An OpenCode Go call has zero marginal cost but consumes a monthly/5 h/weekly allowance. `Usage.cost_usd` cannot represent that, and `BudgetGuard` cannot stop a run before the allowance runs out.
- **Usage is prompt/completion only** (`budget/usage.py::extract_token_usage`). TKE1 adds cache and reasoning fields; GW consumes them and adds served-model / gateway attribution.

---

## 3. Target Users

- **Platform Host (`prismal-server`)** — per-tenant gateway keys from a vault, injected through `build_runtime(org_id)`, never shared across tenants and never in `os.environ`.
- **FinOps / Operator** — accurate per-call cost (billed, not estimated), credit remaining, flat-fee window consumption, cost-drift alerts.
- **Security / Compliance Lead** — zero-data-retention by default, retention deny-lists, ToS guards, refusal of vulnerable or ToS-violating gateway setups.
- **AI Engineer / Flow Author** — one model-reference syntax (`gw:openrouter/anthropic/claude-sonnet-4.5`) that works the same in every agent, with free (`:free`) models for development.

---

## 4. Goals and Success Metrics

| Goal | Metric | Target |
|---|---|---|
| Billed-cost accuracy | share of gateway calls whose `CostEstimate.source == "gateway"` (OpenRouter) | ≥ 99 % |
| Cost reconciliation | p95 \|reported − authoritative\| from `GET /api/v1/generation` | ≤ $0.0005 per call; drift alert > 1 % of daily spend |
| Privacy defaults | OpenRouter requests carrying `provider.data_collection="deny"` and `provider.zdr=true` unless explicitly overridden | 100 % (unit + request-capture test) |
| No double fallback | calls where both gateway `models[]` and prismal `with_fallbacks` are active | 0 (contract test) |
| ToS guard | OpenCode Go calls from non-allow-listed agents that reach the network | 0 (fail-closed test) |
| Vulnerable OmniRoute | starts against OmniRoute < `omniroute_min_version` | 0 (refused at startup) |
| Quota protection | OpenCode Go calls issued after a configured window is exhausted (action `refuse`) | 0 |
| Secret hygiene | gateway keys found in `os.environ`, logs, exception text or URLs | 0 (AST + log-capture tests) |
| Overhead | added latency of `GatewayRouter.resolve()` on the hot path | < 1 ms p99 (no I/O) |
| Opt-in safety | `llm_gateways_enabled=False` ⇒ graph snapshot and `get_llm()` kwargs unchanged | 100 % (snapshot test) |

---

## 5. Scope

### In Scope
- **GW1 — Port + generic adapter + router + registry wiring.** `prismal/providers/gateways/` package: `WireProtocol`, `GatewayProfile`, `ResolvedModel`, `GatewayPort`, `GatewayDataPolicy`, the `openai_compatible` adapter, `GatewayRouter` (`gw:` refs + `llm_gateway_agent_map`) and `ProviderRegistry.get_llm()/get_llm_with_fallback()` integration through a `GatewayChatLiteLLM` subclass that captures gateway metadata.
- **GW2 — OpenRouter.** Attribution headers, `extra_body.provider` built from policy (deny/ZDR defaults, `sort`, `max_price`, `only`/`ignore`, `require_parameters`), optional `models` fallback that owns model fallback for that call, variants allow-list, `openrouter/auto` gate (wildcards + cost tier), sticky `session_id` for cache hits, plugin allow-list, billed cost, `GET /api/v1/key` credit snapshot, 402/429 typed errors (`limit_source`).
- **GW3 — OpenCode Zen + Go + `QuotaLedger`.** Per-model wire-protocol map (YAML, optional `/v1/models` refresh), `User-Agent` and `x-opencode-session` headers, coding-agent ToS guard, retention deny-list, flat-fee ledger (monthly cap, 5 h and weekly windows), cost source `"subscription"`.
- **GW4 — OmniRoute + compliance guard.** Mandatory API key, startup version check against `omniroute_min_version` (CVE-2026-88062), subscription-upstream attestation, combo deny-list, deployment hardening notes.
- **GW5 — Cost reconciliation + quota → BudgetGuard + OTel.** Cost precedence `gateway > litellm > table > none` (`subscription` for flat-fee), async generation reconciliation, quota preflight (refuse/downgrade), five `prismal.gateway_*` metrics.
- **GW6 — Integration, live tests, docs.** Per-tenant keys through `build_runtime(..., org_id)` → `RuntimeContext.gateways`, `live_api` tests on a `:free` model, `docs/gateways.md`, `examples/gateways_*.py`, a notebook.

### Out of Scope
- **Semantic model routing** (cascades, tiering, budget-aware model choice, OmniRouter batch assignment) — **AMR** (`adaptive-model-routing/`), which *consumes* `GatewayPort`.
- **Prompt-caching mechanics** — TKE2. GW only passes `cache_control` through and reports cache usage.
- **Jev/Laya decision models** — DEC. GW only exposes `openrouter_systemone_base_url` as a forward reference.
- **Running or shipping a gateway** — prismal never bundles OmniRoute or a LiteLLM proxy; it only connects to them.
- **Rewriting the legacy direct-provider path** — `_inject_api_keys()` stays as is for non-gateway models (deprecation tracked separately).
- **Correcting already-recorded meter entries** after reconciliation — drift is measured and alerted, not retro-applied (see ARCHITECTURE open questions).

---

## 6. Phases Overview

| Phase | Focus | Key deliverables | Est. |
|---|---|---|---|
| GW0 | Verify before build | Every **[verify]** fact re-checked against live docs/APIs; spec amended (OpenRouter facts pre-verified 2026-10-04) | ~1.3 d |
| GW1 | Port, generic adapter, router, registry wiring | `gateways/base.py`, `openai_compatible.py`, `router.py`, `policy.py`, `GatewayChatLiteLLM`, settings, errors, snapshot test | ~5.2 d |
| GW2 | OpenRouter | `openrouter.py`, provider object, sticky session, plugins, fallbacks, variants, credit | ~4.0 d |
| GW3 | OpenCode Zen/Go + QuotaLedger | `opencode.py`, `quota.py`, protocol map, ToS guard | ~3.8 d |
| GW4 | OmniRoute + compliance guard | `omniroute.py`, startup attestation/version check | ~2.1 d |
| GW5 | Cost reconciliation, quota → Budget, OTel | cost precedence, reconciliation worker, preflight, metrics | ~3.2 d |
| GW6 | Integration, live tests, docs | composition root, `live_api` suite, docs/examples/notebook, CHANGELOG | ~3.7 d |

---

## 7. Risks and Mitigations (summary)

| Risk | Mitigation |
|---|---|
| Gateway facts change (endpoints, headers, quotas, model ids, prices) | Nothing volatile is hard-coded: model maps, quota ratios and deny-lists are config; GW0 verify tasks; `[verify]` tags in SPEC |
| Double fallback (gateway `models[]` + prismal `fallback_model` + LiteLLM retries) multiplies cost | DD-GW-003: exactly one layer owns *model* fallback per call; LiteLLM retries off when the gateway owns it |
| OpenCode Go used outside its "coding agents" scope → account termination | `opencode_go_allowed_agents` allow-list, fail-closed `GatewayPolicyError`, operator override logged and counted |
| OmniRoute subscription-OAuth upstreams (Claude Code / Codex / Gemini CLI accounts) → upstream bans, ToS violation | `omniroute_allow_subscription_upstreams=False`, mandatory startup attestation, combo deny-list, docs |
| OmniRoute RCE (CVE-2026-88062, `requireLogin=false`) and look-alike forks | Version floor `3.8.49`, mandatory API key, loopback/private-network warning, "official repo only" docs |
| Data retention by upstream providers | OpenRouter `data_collection:"deny"` + `zdr:true` by default; OpenCode retention deny-list; OmniRoute documented as non-enforceable |
| Tenant key leakage / cross-tenant billing | Keys resolved per `org_id` from `CredentialVault`, `SecretStr` end-to-end, no global fallback unless explicitly enabled |
| Gateway cost lookup failure breaks a turn | Fail-open: falls back to litellm/table pricing, counted; never raises |
| `langchain_litellm` drops `_hidden_params` | `GatewayChatLiteLLM` captures metadata at the raw-response seam; GW0 verify task; fallback to `usage.cost` |
| Behaviour leak with flag off | Every wiring point gated; snapshot + `get_llm()` kwargs contract tests |

---

## 8. Dependencies

- `prismal/providers/registry.py` (`get_llm`, `get_llm_with_fallback`) — integration seam.
- `prismal/providers/cost.py` (`CostEstimate`, `compute_cost_usd`) as extended by TKE1 (`reported_cost_usd`, `source="gateway"`).
- `prismal/budget/{types,usage,meter,guard,resolve}.py` — `Usage` (TKE1 fields), `CostMeter.record_response`, `BudgetGuard`, per-run registry.
- `prismal/core/config_source.py` (`ConfigSourcePort`, W), `prismal/identity/` (`CredentialVault`, IDN), `prismal/composition/runtime.py` (`build_runtime`, `RuntimeContext`, R).
- `prismal/monitoring/otel.py` (`OTelManager`) and `ObservabilityPort` `RunSummary` (OBS).
- Libraries already pinned: `litellm`, `langchain-litellm`, `httpx`. **No new runtime dependency.**

---

## 9. References

- OpenRouter (https://openrouter.ai — "the unified interface for every model": 500+ models, 80+ providers, OpenAI-compatible API, prepaid credits; docs verified 2026-10-04): https://openrouter.ai/docs/api/reference/overview · https://openrouter.ai/docs/guides/routing/provider-routing · https://openrouter.ai/docs/model-fallbacks · https://openrouter.ai/docs/guides/routing/model-variants · https://openrouter.ai/docs/guides/routing/routers/auto · https://openrouter.ai/docs/guides/best-practices/prompt-caching · https://openrouter.ai/docs/guides/guides/usage-accounting · https://openrouter.ai/docs/api/api-reference/generations/get-generation · https://openrouter.ai/docs/limits · https://openrouter.ai/docs/byok · https://openrouter.ai/docs/guides/features/plugins/overview
- LiteLLM: https://docs.litellm.ai/docs/providers/openrouter
- OpenCode: https://opencode.ai/go · https://opencode.ai/docs/go/ · https://opencode.ai/docs/zen/
- OmniRoute: https://github.com/diegosouzapw/OmniRoute · https://github.com/diegosouzapw/OmniRoute/security/advisories/GHSA-hf57-cqmx-p4gr · https://github.com/diegosouzapw/OmniRoute/issues/4118

---

## 10. Next Steps

Review the four SDD documents in this folder, run the GW0 verify tasks, then implement GW1 → GW6 with TDD. This PLAN is a **proposal**, not a build order.

---

## Change History

| Version | Date | Author | Changes |
|---|---|---|---|
| 1.0 | 2026-10-04 | Ernesto Crespo | Initial full SDD from the 2026-10 gap analysis (§2, §3.1) and design brief |

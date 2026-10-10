# GW0 — Verify before build (2026-10-05)

Outcome of the GW0 tasks in `TASKS.md`. Each fact lists its source, the date checked and whether it
**confirmed** or **changed** what SPEC/ARCHITECTURE assumed. Changed facts are amended in `SPEC.md`
(look for "Amended 2026-10-05 (GW0)").

| Task | Fact | Source | Outcome |
|---|---|---|---|
| GW0-01 | `provider` object fields: `order`, `allow_fallbacks`, `require_parameters` (default false), `data_collection` (`allow`\|`deny`, default `allow`), `zdr` (bool), `only`, `ignore`, `quantizations`, `sort` (`price`\|`throughput`\|`latency`), `max_price {prompt, completion}` | openrouter.ai/docs/features/provider-routing | confirmed |
| GW0-01 | Default load balancing weights by inverse-square price; `sort` disables it | same | confirmed |
| GW0-01 | `:free` / `:batch` availability under `zdr:true` is not documented | same | still unknown → both stay out of the default `openrouter_allowed_variants` (DD-GW-004) |
| GW0-02 | `GET /api/v1/key`: `limit`, `limit_remaining`, `usage`, `usage_daily`, `usage_weekly`, `usage_monthly`, `is_free_tier`, `free_model_daily_requests {used, limit, remaining}` | openrouter.ai/docs/api-reference/limits | confirmed |
| GW0-02 | `:free` limits: 20 rpm; 50/day below $10 purchased, 1,000/day at $10+ | same | confirmed |
| GW0-02 | 402 `error.metadata.limit_source` ∈ `openrouter_credits`, `openrouter_key_limit`, `openrouter_in_flight_budget` (the last one sends `Retry-After`); response also carries `remedy_hint` | same | confirmed |
| GW0-03 | `langchain-litellm` 0.6.6: `ChatLiteLLM._create_chat_result(response)` is shared by `_generate` and `_agenerate`, and copies neither `_hidden_params`, the served `model` nor the response `id` | installed source `langchain_litellm/chat_models/litellm.py` | confirmed (capture is needed); re-verified 2026-10-10 on `langchain-litellm` 0.7.1 (hooks unchanged, gateway suite green) |
| GW0-03 | Streaming (`_stream` / `_astream`) **never calls** `_create_chat_result`; raw chunks are only visible inside `completion_with_retry` / `acompletion_with_retry` | same | **changed** → the capture seam is `(a)completion_with_retry` (raw response / chunks), not only `_create_chat_result` |
| GW0-03 | `ChatLiteLLM.api_key` is a plain `Optional[str]` field (appears in pydantic `repr`) | same | **changed** → `GatewayChatLiteLLM` masks `api_key` in `repr` |
| GW0-03 | litellm 1.86.2: `ModelResponse._hidden_params` is a dict (`response_cost` when priced) | installed `litellm.types.utils.ModelResponse` | confirmed |
| GW0-04 | litellm 1.86.2 `get_llm_provider`: `openai/responses/<id>` → provider `openai`, model `responses/<id>`, routed through the Responses bridge in `litellm.main.completion`; `anthropic/<id>` and `openai/<id>` accept a custom `api_base` | installed source `litellm/main.py` | confirmed |
| GW0-05 | Zen base `https://opencode.ai/zen/v1` (`/chat/completions`, `/messages`, `/responses`); Go base `https://opencode.ai/zen/go/v1` with the same three endpoint families; model ids `opencode/<id>` | opencode.ai/docs/zen, opencode.ai/docs/go | confirmed |
| GW0-05 | Zen also serves Gemini through a Google-native `/v1/models/{id}` endpoint | opencode.ai/docs/zen | **new** → not a supported `WireProtocol`; such ids are simply absent from the protocol map (unknown ⇒ `GatewayConfigError`) |
| GW0-05 | Go windows: 5 h = 20 % and weekly = 50 % of the monthly dollar limit (Go $10/month, Go Plus $40/month); no quota API, usage visible only in the console | opencode.ai/docs/go | confirmed |
| GW0-05 | Go ToS: "designed for OpenCode and other coding agents that produce similar types of requests"; clients should send a custom User-Agent and a stable `x-opencode-session`; validated clients: Hermes, Claude Code, Codex, ZCode, Pi, jcode, Kilo Code CLI | opencode.ai/docs/go | confirmed |
| GW0-05 | Zen auto-reload: "$20 when the balance goes below $5", adjustable/disable-able in the account | opencode.ai/docs/zen | confirmed (documented risk only) |
| GW0-06 | Grok and GPT Luna variants: 30-day retention; Muse Spark: used to train future Meta models | opencode.ai/docs/go, opencode.ai/docs/zen | confirmed (deny-list `grok*`, `muse-spark*`) |
| GW0-06 | DeepSeek ZDR was valid **until 2026-08-31**, not "through October 2026"; DeepSeek V4 Flash is now listed as used for training with no retention agreement | github.com/anomalyco/opencode/issues/46870; openveil.app/blog/opencode-go-zero-data-retention-deepseek-v4-flash | **changed** → `deepseek-v4-flash*` added to the default `opencode_denied_models` |
| GW0-07 | CVE-2026-88062 / GHSA-hf57-cqmx-p4gr (published 2026-09-03): RCE via `POST /api/acp/agents`, unauthenticated with `requireLogin=false`; advisory lists `omniroute` **≤ 3.8.50 affected, no patched version** | github.com/advisories/GHSA-hf57-cqmx-p4gr | **changed** → 3.8.49 is *not* a fix. Default `omniroute_min_version` raised to `3.8.51` (released 2026-09-30, after the advisory, with "security hardening"); the advisory does not yet name it as patched, so docs tell operators to re-check |
| GW0-07 | Official distribution: npm `omniroute`, repo `diegosouzapw/OmniRoute` | github.com/diegosouzapw/OmniRoute | confirmed |
| GW0-07 | No documented version endpoint or header; `GET /api/monitoring/health` returns provider counts only | github.com/diegosouzapw/OmniRoute/wiki/API-Reference | **changed** → `detect_version()` reads a `version` field / `x-omniroute-version` header if present, else falls back to the new operator setting `omniroute_declared_version` (logged as declared); otherwise unknown ⇒ refused |
| GW0-08 | IDN vault accessor is `vault.resolve(credential_ref, *, scopes=()) -> Credential` (`.value: SecretStr`), raising `CredentialResolutionError` when missing; there is no `.get` | `prismal/identity/vault.py` | **changed** → `SecretResolver` adapter wraps `resolve(f"{auth_ref}@{org}").value` and maps the error to `None` |
| GW0-08 | `openrouter_systemone_base_url`: the documented SystemOne endpoint found is OpenCode's `https://opencode.ai/zen/v1/systemone` | opencode.ai/docs/zen | noted for DEC (forward ref only, default stays `None`) |

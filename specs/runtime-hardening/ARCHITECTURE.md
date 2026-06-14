# Prismal Runtime Hardening — Technical Design Document

## Metadata

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `IMPLEMENTED` |
| **Version** | 1.0 |
| **Date** | 2026-06-13 |
| **Phase** | H |
| **Target package version** | `3.2.0` (SemVer minor) |
| **PLAN** | `specs/runtime-hardening/PLAN.md` |
| **SPEC** | `specs/runtime-hardening/SPEC.md` |
| **TASKS** | `specs/runtime-hardening/TASKS.md` |

---

## 1. Context

The existing security stack is **input-centric and action-centric**: it sanitizes the user turn (L1–L3), authorizes file/code/tool actions (L4), and audits everything (L5). Two flanks remain open at runtime — **what the model reads back** (untrusted tool/RAG/media content) and **what the model emits** (outputs consumed downstream) — plus a missing **declarative tool policy** and an **explicit loop bound**. This phase adds those four controls as opt-in extensions of `prismal/security/`, hooked at two seams already present in the core: the **`@prismal_node` middleware chain** and the **`react_loop`**.

## 2. Feasibility with the existing core (confirmed)

- The `@prismal_node` middleware chain (`agents/extension/_middleware.py`) already runs *security → audit → retry → timeout* around every node; new checks slot in as additional middleware stages without touching node bodies.
- `ActionInterceptor.check()` is already the single chokepoint before file/code/tool actions → `ToolPolicyEngine` plugs in there (it already exposes `_tool_call_checker` as a monkeypatchable seam used by `security="strict"`).
- `react_loop` already meters Budget per call and supports a `budget_guard` → the same seam carries taint-checking of tool results and the `RunawayGuard`.
- `GuardrailsEngine` already scores arbitrary text → reused verbatim for untrusted content.
- `pii_sanitizer` already redacts text → reused as an output filter.
- `OTelManager` already registers counters → security counters added the same way.

No new LangGraph capability is required.

## 3. Proposed Architecture

### 3.1 New / extended modules

| Module | Purpose |
|---|---|
| `prismal/security/taint.py` | `TaintTag`, `mark_untrusted()`, `is_untrusted()`; provenance metadata for content |
| `prismal/security/indirect_injection.py` | `IndirectInjectionDetector` (heuristic + optional LLM classifier) |
| `prismal/security/output_validator.py` | `OutputValidator` — schema/escape validation of model output |
| `prismal/security/tool_policy.py` | `ToolPolicy`, `ToolPolicyEngine`, YAML loader |
| `prismal/security/runaway.py` | `RunawayGuard` — step + stagnation bounds |
| `prismal/security/pii_sanitizer.py` | *(extend)* `redact_output()` filter wrapper |
| `prismal/security/action_interceptor.py` | *(extend)* consult `ToolPolicyEngine` in `check()` |
| `prismal/core/config.py` | `hardening_*` settings |
| `prismal/core/exceptions.py` | `HardeningError` hierarchy |
| `prismal/monitoring/otel.py` | security counters |
| `config/tool_policies.yaml` | default policy file (example shipped in this spec dir) |

All new state is carried under `state["metadata"]["hardening"]` (taint registry, runaway counters), mirroring the budget/skynet/kokoro isolation convention. **No live objects** (detectors, engines) are ever placed in checkpointed state — they are resolved per-run from a registry keyed by `session_id`, exactly like the Budget meter/guard.

### 3.2 Data flow (with `hardening_enabled=True`)

```
user turn ──► L1–L3 (existing) ──► agent node
                                      │
   tool/RAG/media result ──► mark_untrusted() ──► IndirectInjectionDetector
                                      │                 │ (GuardrailsEngine score)
                                      │            block / sanitize / flag
                                      ▼
                              re-inject into model
                                      │
            model output ──► OutputValidator (schema/escape) ──► PII redact (optional)
                                      │
                 tool call ──► ActionInterceptor.check()
                                      │  └─► ToolPolicyEngine.evaluate(agent, tool, args)
                                      │         allow │ deny │ requires-HITL ─► hitl_gate()
                                      ▼
         each loop turn ──► RunawayGuard.tick(state)  (step cap + stagnation)
                                      │ exceeded
                                      ▼
                       graceful stop + audit + partial answer
```

### 3.3 Middleware ordering

`@prismal_node` chain becomes (outermost→innermost):
`error_mapping → otel → logger → security(L1–L3 + taint-in) → audit → tool_policy(L4) → retry → timeout → user_fn → output_validator → pii_redact`.
The runaway tick lives in `react_loop` (one tick per model call), unified with the Budget check.

## 4. Design Decisions

### DD-HRD-001: Taint as metadata, not a wrapper type
Content stays a plain `str`; provenance is recorded in a per-run taint registry (`metadata.hardening.taint`) keyed by a content hash (xxhash). Avoids invasive type changes across RAG/tools and keeps checkpointed state serializable.

### DD-HRD-002: Reuse `GuardrailsEngine` for untrusted content
`IndirectInjectionDetector` does **not** re-implement scoring; it feeds untrusted text to the existing `GuardrailsEngine` plus a small indirect-injection heuristic pack (imperative-to-the-assistant patterns, tool/role-override phrases, exfiltration intents). The optional LLM classifier lives in `providers/` (rule #4).

### DD-HRD-003: Identity-agnostic tool policy
`ToolPolicyEngine` keys on `(agent_name, tool_name, args)` only. It deliberately does **not** model identities/DIDs — that is `agent-identity-governance`. This keeps Phase H shippable without the identity dependency while remaining forward-compatible (the identity `PolicyEngine` can delegate to or replace it).

### DD-HRD-004: `warn` before `enforce`
Every new control supports a mode: `off | warn | enforce`. `warn` audits + emits a metric but does not block (safe rollout); `enforce` blocks. Default global mode is `warn` when `hardening_enabled=True`, so enabling the layer never breaks a flow on day one.

### DD-HRD-005: RunawayGuard unifies with Budget
`RunawayGuard` shares the per-run registry with the Budget meter/guard. Stagnation = N consecutive turns with no new tool/result signature (xxhash of (node, tool, args)). On breach it raises the same graceful-partial path `react_loop` already uses for a hard budget cap.

### DD-HRD-006: Output validation is schema-first
`OutputValidator` validates structured tool arguments against a Pydantic schema when the tool declares one; for free-form output it applies escaping/format checks before the output is used as a path, command, or rendered. Reuses `filesystem_guard` for path outputs.

### DD-HRD-007: Opt-in, snapshot-guaranteed
Every wiring point is gated on `hardening_enabled`. A snapshot test asserts the compiled graph is byte-for-byte identical when off (mirrors Skynet/Kokoro/Budget).

## 5. Security & cost

- Untrusted content never reaches the model un-scored; soul bodies, OCR, STT, captions, web and RAG are all tainted at their loaders.
- LLM injection-classifier and any judge calls are **metered through Budget** and disabled by default.
- All denials/blocks are hash-first audited via `AuditLogger` (content hash, never content).

## 6. Observability

### 6.1 OTel counters (registered in `OTelManager`)
- `prismal.guardrail_blocks_total{layer}`
- `prismal.injection_detected_total{vector}` (`vector` ∈ direct|tool|rag|media)
- `prismal.output_rejected_total{reason}`
- `prismal.tool_policy_denied_total{agent,tool}`
- `prismal.runaway_stops_total{reason}` (`step_cap`|`stagnation`)

### 6.2 Spans
- `prismal.security.taint_check`, `prismal.security.output_validate`, `prismal.security.tool_policy`.

## 7. Relationship to existing specs

- **`agent-identity-governance/`** — will provide the identity-aware `PolicyEngine`; `ToolPolicyEngine` is its identity-agnostic precursor and integration target.
- **`agent-eval-harness/`** — its adversarial suite is the *proof* that these controls contain injection/tool-abuse/exfiltration over real flows.
- **`cost-budget-governance/`** — `RunawayGuard` reuses the per-run registry and graceful-partial path.

## 8. Testing strategy (summary; detail in `TASKS.md`)

- Unit: taint registry round-trip; injection detector on a curated corpus; output validator on valid/invalid args; policy engine allow/deny/HITL; runaway step + stagnation.
- Integration: `hardening_enabled=False` graph snapshot unchanged; end-to-end flow with fakes where an untrusted tool result carrying an injection is blocked; a high-risk tool routes through `hitl_gate()`.
- Guards: no provider import outside `providers/`; no `mcp`/`skills` import in `agents/**` (reuse existing AST tests).

## 9. Rollout

1. Ship modules behind `hardening_enabled=False` (no wiring change observable).
2. Enable in `warn` mode in staging; tune thresholds from `injection_detected_total` / `tool_policy_denied_total`.
3. Flip to `enforce` per-control once false-positive rate is acceptable.

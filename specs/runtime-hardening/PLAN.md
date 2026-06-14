# Prismal — Runtime Hardening (Indirect-Injection, Output Validation, Tool Policy, Runaway Guard)

## Strategic Plan / Product Requirements Document (PLAN)

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `IMPLEMENTED` (shipped v3.2.0) |
| **Version** | 1.0 |
| **Date** | 2026-06-13 |
| **Phase** | H (Hardening) |
| **Target package version** | `3.2.0` (SemVer **minor** — new opt-in functionality) |
| **Reviewers** | Tech Lead, Security Lead, AI Architect |
| **Priority** | P1 (security) |
| **Related** | `docs/security/hardening-and-harness-engineering.md`, `prismal/security/`, `specs/agent-identity-governance/` (richer identity-based policy), `specs/agent-eval-harness/` (red-team proves these controls) |

---

## 1. Executive Summary

Prismal already ships a strong **5-layer** security stack (`InputSanitizer` → `GuardrailsEngine` (+NeMo) → `ActionInterceptor` → `AuditLogger`, plus `SecurePromptBuilder`, `PermissionManager`, `filesystem_guard`, `MediaValidator`) and a **Budget** autonomy ceiling. The 2025–2026 OWASP **LLM/Agentic Top 10** and harness-engineering literature expose four residual gaps that the current stack does **not** close at runtime:

1. **Indirect prompt injection** — guardrails inspect the *user* turn, but content returning from tools/RAG/web/STT/OCR re-enters the model **untainted** (OWASP LLM01 indirect, Agentic ASI tool-integration).
2. **Improper output handling** — the model's output is consumed by tools / rendering / execution **without validation or escaping** (OWASP LLM05).
3. **Excessive agency at the tool boundary** — capability routing + the 120-tool cap exist, but there is no **declarative per-agent / per-tool / per-argument policy** (allow/deny, rate limit, "requires HITL") (OWASP LLM06 / Agentic).
4. **Unbounded / runaway control loops** — bounded only implicitly by Budget and LangGraph `recursion_limit`; no explicit **step/stagnation** guard (OWASP LLM10).

This feature adds a **Runtime Hardening** layer (`prismal/security/` extensions) that closes these gaps as **opt-in middleware** on the existing `@prismal_node` chain and the `react_loop`, plus PII redaction on outputs and security observability counters. It is **additive and gated** (`hardening_enabled`, default `False`): with the flag off the compiled graph and the 26 agents are byte-for-byte unchanged.

---

## 2. Context and Problem

- **Taint is implicit, not enforced.** `CLAUDE.md` already *advises* treating STT/OCR/captions/soul bodies as user content, but there is no mechanism that **marks** untrusted content and **routes it through guardrails** before re-injection. A poisoned web page or RAG document can carry instructions straight into the model.
- **Outputs are trusted.** A tool argument, a file path, or a code string emitted by the model is used as-is. Improper output handling is the bridge from "the model said X" to "the system did X".
- **Tool authorization is coarse.** `ToolProviderPort` filters tools by capability and caps the total at 120, but cannot express "agent `coder` may call `write_file` only under the workspace, max 20×/run, and `delete_file` needs HITL".
- **No explicit runaway guard.** Budget caps tokens/cost/calls/wall-clock, but a loop can still thrash within budget (repeating the same failing tool, oscillating between two nodes) without a stagnation signal.
- **PII only sanitized in memory.** `pii_sanitizer` protects the long-term store, not arbitrary agent outputs.
- **Security is under-observed.** Budget and tool resolution emit OTel counters; guardrail blocks, injection hits, permission denials and output rejections do not.

> **Scope boundary vs. identity governance.** The richer, identity-aware `PolicyEngine.allow(identity, action, resource)` (W3C DID, OAuth-on-behalf) is owned by [`specs/agent-identity-governance/`](../agent-identity-governance/). This phase ships a **lightweight, identity-agnostic** `ToolPolicyEngine` keyed on `(agent_name, tool_name, args)` that the identity layer can later subsume. The two are complementary, not competing.

---

## 3. Target Users

- **Security Lead:** demonstrable controls for OWASP LLM01/05/06/10; auditable denials; metrics.
- **AI Engineer:** declarative tool policy and output schemas without editing agent code.
- **Platform Host (`prismal-server`):** runtime guardrails on untrusted tool/RAG content; HITL on high-risk tools.
- **Compliance:** mapping of controls to NIST AI RMF / OWASP Agentic Top 10.

---

## 4. Goals and Success Metrics

| Goal | Metric | Target |
|---|---|---|
| Contain indirect injection | Untrusted tool/RAG/media content passes guardrails before re-injection | Enforced |
| Validate outputs | Tool args / structured outputs validated/escaped pre-use | Enforced |
| Least privilege at tools | `(agent, tool, args)` policy evaluated pre-action | Integrated with `ActionInterceptor` |
| Bound the loop | Explicit step + stagnation cap | Enforced |
| PII on outputs | Configurable output redaction | Available |
| Observability | OTel counters for blocks/injections/denials/rejections | Emitted |
| Backward-compat | `hardening_enabled=False` ⇒ graph byte-for-byte unchanged | 100% (snapshot test) |

---

## 5. Scope

### In Scope
- **Taint tracking + `IndirectInjectionDetector`** for untrusted content (tools, RAG, web, STT/OCR/captions); heuristic by default, optional LLM classifier (in `providers/`).
- **`OutputValidator`** — schema/escape validation of model output before tool-call / render / execute.
- **`ToolPolicyEngine`** — declarative `(agent, tool, args)` policy (allow/deny, rate limit, requires-HITL) wired into `ActionInterceptor`; YAML config.
- **`RunawayGuard`** — explicit step cap + stagnation detection; unifies with Budget.
- **PII on outputs** — reuse `pii_sanitizer` as an output filter.
- **Security OTel counters** — registered in `OTelManager`.
- **`hardening_*` settings** + opt-in wiring (no behavior change when off).
- Docs (`docs/security/...`) + example (`examples/runtime_hardening.py`).

### Out of Scope
- W3C DID / OAuth-on-behalf identity, per-agent credentials, IdP integration → [`agent-identity-governance/`](../agent-identity-governance/).
- The adversarial **test corpus / runner** that exercises these controls → [`agent-eval-harness/`](../agent-eval-harness/) (this phase only *exposes* the controls; the harness proves them).
- New sandbox backends (already covered by `SandboxExecutor`).
- Network/infra hardening (mTLS, seccomp) — host responsibility.

---

## 6. Functional Requirements (summary)

| ID | Requirement | Priority |
|---|---|---|
| RF-HRD-001 | `TaintTag`/provenance on content from tools, RAG, web, media | `MUST` |
| RF-HRD-002 | `IndirectInjectionDetector` runs untrusted content through guardrails before re-injection | `MUST` |
| RF-HRD-003 | `OutputValidator` validates/escapes model output (schema per tool) pre-use | `MUST` |
| RF-HRD-004 | `ToolPolicyEngine.evaluate(agent, tool, args)` → allow/deny/requires-HITL, integrated with `ActionInterceptor` | `MUST` |
| RF-HRD-005 | YAML policy config (`tool_policies.yaml`) with safe defaults + `warn`/`enforce` modes | `MUST` |
| RF-HRD-006 | `RunawayGuard`: step cap + stagnation detection (repeated state/tool) | `MUST` |
| RF-HRD-007 | PII redaction as a configurable output filter | `SHOULD` |
| RF-HRD-008 | OTel security counters (blocks/injections/denials/rejections/runaway) | `SHOULD` |
| RF-HRD-009 | `hardening_enabled=False` ⇒ compiled graph byte-for-byte unchanged | `MUST` |
| RF-HRD-010 | No provider SDK import outside `providers/`; no `mcp`/`skills` import in `agents/**` | `MUST` |

---

## 7. Risks and Mitigations (summary)

| Risk | Mitigation |
|---|---|
| Injection classifier adds latency/cost | Heuristic default; LLM classifier opt-in + metered via Budget |
| Over-strict policy blocks legitimate flows | `warn` mode before `enforce`; safe permissive defaults; per-tool override |
| False positives in injection detection | Tunable thresholds; audit + metric, fail-open in `warn`, fail-closed in `enforce` |
| Behavior leak when disabled | Gate every wiring point on `hardening_enabled`; snapshot test |
| Duplicating identity governance | Keep policy identity-agnostic; defer DID/OAuth to identity spec |

---

## 8. Dependencies

- `prismal/security/` (`sanitizer`, `guardrails`, `action_interceptor`, `audit`, `pii_sanitizer`, `prompt_builder`).
- `prismal/providers/` (optional LLM injection classifier — provider isolation rule #4).
- `prismal/monitoring/otel.py` (counters).
- `prismal/agents/` `react_loop` seam (taint + runaway integration) and the `@prismal_node` middleware chain (`agents/extension/_middleware.py`).
- `prismal/budget/` (RunawayGuard unifies with `budget_max_calls`/`wall_clock`).
- Proven by [`agent-eval-harness/`](../agent-eval-harness/) red-team suite.

---

## 9. Next Steps

Implement per `TASKS.md` (phases H1–H6). Ship behind `hardening_enabled`. Then wire the `agent-eval-harness` adversarial suite to assert each control contains its corresponding attack class.

---

## Change History

| Version | Date | Author | Changes |
|---|---|---|---|
| 1.0 | 2026-06-13 | Ernesto Crespo | Full SDD seeded from `docs/security/hardening-and-harness-engineering.md` research |

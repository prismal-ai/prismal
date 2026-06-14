# Runtime Hardening (Phase H)

Runtime Hardening adds a **defense-in-depth layer** on top of Prismal's existing
5-layer security stack, closing four residual runtime gaps from the 2025–2026
OWASP **LLM / Agentic Top 10**. It is **opt-in** (`hardening_enabled`, default
`False`) and **additive**: with the flag off, the compiled supervisor graph and
the 26 agents are byte-for-byte unchanged (snapshot-tested).

| Control | Module | OWASP |
|---|---|---|
| Indirect prompt-injection containment | `security/indirect_injection.py` | LLM01 |
| Improper output handling | `security/output_validator.py` | LLM05 |
| Excessive agency at the tool boundary | `security/tool_policy.py` | LLM06 |
| Unbounded / runaway loops | `security/runaway.py` | LLM10 |
| Taint tracking (provenance) | `security/taint.py` | LLM01 |
| PII on outputs | `security/pii_sanitizer.py::redact_output` | — |

## Quick start

```bash
export PRISMAL_HARDENING_ENABLED=true
export PRISMAL_HARDENING_MODE=warn        # off | warn | enforce
```

Ship in `warn` first (audits + metrics, never blocks), tune thresholds from the
OTel counters, then flip to `enforce`. See `examples/runtime_hardening.py`.

## Controls

### 1. Taint tracking (`security/taint.py`)

Content from tools, RAG, web, media (STT/OCR/captions), and Kokoro `SOUL.md`
bodies is **untrusted**. Loaders tag it at their boundary via a per-run
`TaintRegistry` (exposed to deep loaders through a `ContextVar`; no-op when no run
is active). The registry stores only hashes + provenance enums, so it is safe in
checkpointed state.

### 2. Indirect injection detector (`security/indirect_injection.py`)

Before untrusted content is re-injected into the model, `IndirectInjectionDetector`
scores it by reusing `GuardrailsEngine` **plus** an indirect-injection heuristic
pack (instruction override, role/tool override, exfiltration intent). An optional
LLM classifier (`providers/injection_classifier.py`, off by default, metered via
Budget) is max-combined. In `react_loop`, tool results are screened: `enforce`
blocks (replaces with a notice), `warn` neutralizes the directives.

### 3. Output validator (`security/output_validator.py`)

`validate_tool_args` checks structured tool arguments against a Pydantic schema;
`validate_freeform` escapes/format-checks output used as a path (delegated to
`filesystem_guard`), shell command, or HTML. `redact_output` redacts PII from
agent outputs when `hardening_pii_output=True`.

### 4. Tool policy engine (`security/tool_policy.py`)

A declarative, **identity-agnostic** `(agent, tool, args)` policy
(`config/tool_policies.yaml`): `allow` / `deny` / `require_hitl`, per-argument
regex constraints, and `rate_limit_per_run`. Evaluation is first-match /
most-specific-wins. `DENY` blocks the call; `REQUIRE_HITL` routes through
`hitl_gate()` at the graph-node level (and is a safe deny-until-approved skip in
the core `react_loop`). The richer identity-aware `PolicyEngine` in
`agent-identity-governance` will later subsume it.

### 5. Runaway guard (`security/runaway.py`)

`RunawayGuard.tick()` adds an explicit step cap plus stagnation detection (N
consecutive turns with an identical action signature). On a breach `react_loop`
takes its graceful-partial path — the same one used for a hard budget cap.

## Modes

Every control honours `mode ∈ {off, warn, enforce}` (global default
`hardening_mode`):

- **off** — control disabled.
- **warn** — fail-open: audits + emits a metric + sanitizes, but does not block.
- **enforce** — fail-closed: blocks.

## Settings (`PRISMAL_*`)

| Setting | Default | Purpose |
|---|---|---|
| `hardening_enabled` | `False` | Master opt-in toggle |
| `hardening_mode` | `warn` | Global control mode |
| `taint_tracking_enabled` | `True` | Mark untrusted content |
| `hardening_injection_threshold` | `0.7` | Risk threshold to flag/block (0–1) |
| `hardening_injection_classifier` | `False` | Use the optional LLM classifier |
| `output_validation_enabled` | `True` | Validate/escape outputs |
| `tool_policy_path` | `config/tool_policies.yaml` | Policy file |
| `hardening_tool_policy_default` | `allow` | Default effect (`allow`/`deny`) |
| `hardening_runaway_max_steps` | `40` | Step cap per run (0 = unlimited) |
| `hardening_runaway_stagnation_window` | `4` | Identical-signature window |
| `hardening_pii_output` | `False` | Redact PII from outputs |

## Observability (OTel counters)

- `prismal.guardrail_blocks_total{layer}`
- `prismal.injection_detected_total{vector}` (`direct`|`tool`|`rag`|`media`)
- `prismal.output_rejected_total{reason}`
- `prismal.tool_policy_denied_total{agent,tool}`
- `prismal.runaway_stops_total{reason}` (`step_cap`|`stagnation`)

All blocks/denials are hash-first audited via `AuditLogger` (content hash, never
content).

## Architecture notes

- Per-run **live** engines (detector, runaway guard, tool-policy enforcer, taint
  registry) live in an in-process registry keyed by `session_id`
  (`security/hardening_run.py`), seeded once per turn from the supervisor — never
  in checkpointed state (mirrors the Budget meter/guard). State carries only a
  serializable marker under `state["metadata"]["hardening"]`.
- The `@prismal_node` middleware chain gains an innermost `hardening_middleware`
  stage (taint-in + PII-on-output), a complete passthrough when disabled.

## Relationship to other phases

- **`agent-identity-governance`** provides the identity-aware `PolicyEngine`;
  the `ToolPolicyEngine` here is its identity-agnostic precursor.
- **`agent-eval-harness`** is the executable proof: its adversarial red-team
  suite asserts each control contains its attack class.
- **`cost-budget-governance`** — `RunawayGuard` reuses the per-run registry and
  graceful-partial path.

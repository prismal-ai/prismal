# AI Hardening & Harness Engineering for Prismal

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Date** | 2026-06-13 |
| **Status** | `RESEARCH / ANALYSIS` |
| **Scope** | Whether AI hardening and harness-engineering practices can be implemented in the `prismal-ai` core, and how |
| **Focus** | Practical / actionable |
| **Companion note** | Obsidian `Documentacion/Prismal/Prismal-Hardening-IA-y-Harness-Engineering` |

> **Verdict in one line.** Yes — and prismal is *already* a hardened harness. The core covers ~60–70% of established harness-engineering and AI-hardening practice (5-layer security, sandbox, budget governance, observability, HITL). The highest-ROI gaps are: (1) **indirect prompt-injection detection / taint tracking** of untrusted content, (2) **output validation and per-policy tool authorization**, and (3) a **first-class evaluation harness** (trajectory scoring, red-team in CI, replay). All of it fits **additively and opt-in** on the existing hexagonal architecture.

> Related: [`../extension.md`](../extension.md) · [`../budget.md`](../budget.md) · [`../tool-providers.md`](../tool-providers.md) · [`../composition-root.md`](../composition-root.md)

---

## 1. Background

### 1.1 AI hardening
Hardening an AI system means shrinking its attack surface and **forcing** safe behavior through **external controls** — not relying on model alignment alone. Practices that matter in 2025–2026:

- **Prompt hardening** — security-aware system prompts: explicit role, allow/deny lists, never reveal internal rules or tool details.
- **Defense in depth** — multiple layers (input filtering, guardrails, action authorization, audit) because multi-turn jailbreaks, token smuggling and **indirect prompt injection** can bypass a single layer.
- **AI Control vs AI Alignment** — enforce safety with **runtime guardrails (external constraints)**, do not assume the model "wants" to be safe.
- **Least privilege / Excessive Agency** — constrain autonomy, permissions and tool scope.
- **Infrastructure hardening** — containers with dropped capabilities, non-root, seccomp, segmented networks/mTLS; map controls to NIST AI RMF (Govern/Map/Measure/Manage), EU AI Act, SOC 2.
- **Continuous red teaming** and monitoring.

### 1.2 Harness engineering
> **Agent = Model + Harness.** The *harness* is everything around the model: instructions, tools, memory, sandbox, verification, cost limits, logging, and the control loop.

- "A decent model with a great harness beats a great model with a bad one."
- The harness is the **deterministic runtime layer** that validates, authorizes, executes and logs every action the model proposes.
- **Evaluating an agent = evaluating harness + model together**: score the **trajectory** (which agent/tool was called, the plan, step count, cost, termination), not just the final answer. Detect hallucinations, infinite loops and runaway.

**Conclusion.** Prismal *is* a harness: the LangGraph SUPERVISOR pattern + the security layer + sandbox + budget + observability are the canonical harness components. The work is not "build a harness from scratch" but **close the gaps** and **add the evaluation subsystem**.

---

## 2. What prismal already provides

| Harness / hardening component | In prismal core | Status |
|---|---|---|
| User-input isolation | `security/prompt_builder.py` — `SecurePromptBuilder` (canary tokens); rule "never f-string user input" | ✅ Strong |
| Input sanitization | `InputSanitizer` (L1): control chars, unicode, `MAX_INPUT_LENGTH` | ✅ |
| Guardrails / risk scoring | `GuardrailsEngine` (L2) + `nemo_rails.py` NeMo Guardrails (L3) | ✅ |
| Pre-tool action authorization | `ActionInterceptor` (L4) `.check()` / `.check_media_op()` | ✅ |
| Immutable audit | `AuditLogger` (L5) append-only JSONL, `xxhash` chain | ✅ Strong |
| TTL permission grants | `PermissionManager` (SQLite) | ✅ |
| Filesystem confinement | `filesystem_guard.py` (`resolve().is_relative_to()`) | ✅ |
| Media validation | `MediaValidator` (magic bytes, size/duration) | ✅ |
| Execution sandbox | `SandboxExecutor` (docker/podman/nsjail/bwrap/firejail) + AST denylist in `codeact_agent` | ✅ Strong |
| Autonomy ceiling (cost) | **Budget Governance** (`prismal/budget/`): tokens/USD/calls/wall-clock, soft/hard | ✅ Strong |
| Global tool cap | `_MAX_TOTAL_TOOLS = 120`, capability routing | ✅ |
| Least-privilege tools | `ToolProviderPort` + per-agent `capabilities` filtering | 🟡 Partial |
| Human-in-the-loop | `subgraphs/gates.py::hitl_gate()` with `interrupt()` | ✅ |
| Self-verification / critique | `critic`, `patterns/reflection.py::reflection_loop()`, `patterns/constitutional.py` | 🟡 Partial (not a mandatory gate) |
| Observability | Langfuse + OpenTelemetry (spans/counters) + structlog | ✅ |
| Config without reading env secrets | `ConfigSourcePort` (no `os.environ` in core) | ✅ |
| PII handling | `pii_sanitizer` in long-term memory | 🟡 Memory only |
| Plugin supply chain | `plugins_allowlist`/`denylist`, `bandit` in skill validation | 🟡 Partial |
| **Evaluation harness (trajectories)** | unit/integration tests; `FakeToolProvider`, `build_test_runtime` | 🔴 **Missing as a subsystem** |
| **Indirect-injection detection** (tool output / RAG / OCR) | — (guardrails see user input, not taint of external content) | 🔴 **Gap** |
| **Output validation** (Improper Output Handling) | — | 🔴 **Gap** |
| Explicit runaway/loop guard | implicit via budget + LangGraph `recursion_limit` | 🟡 Partial |
| Continuous red team in CI | — | 🔴 Gap |

---

## 3. OWASP Top 10 mapping (LLM 2025 + Agentic 2026)

| Risk | prismal coverage | Recommended action |
|---|---|---|
| LLM01 Prompt Injection (direct) | ✅ L1–L3 + `SecurePromptBuilder` | Keep |
| **Indirect injection** (tool/RAG/web/OCR content) | 🔴 | **Taint tracking** + guardrail over untrusted content |
| LLM02 Sensitive info disclosure | 🟡 (PII in memory only) | Extend `pii_sanitizer` to **outputs** and prompts |
| LLM03 Supply chain | 🟡 (allowlist + bandit) | Sign / provenance for plugins & skills; pin model versions |
| LLM04 Data & model poisoning | 🟡 | RAG source validation + `MediaValidator` already helps |
| LLM05 Improper Output Handling | 🔴 | **OutputValidator** (schema/escape) before using output |
| LLM06 Excessive Agency | ✅ Budget + cap + caps | **Tool policy** per agent/arg + HITL on high-risk |
| LLM07 System-prompt leakage | ✅ `SecurePromptBuilder` | Add leak test to red-team suite |
| LLM08 Vector/embeddings weaknesses | 🟡 | Per-tenant isolation (`collection_for`) exists; validate RAG inputs |
| LLM09 Misinformation / grounding | 🟡 (critic/self-RAG) | **Faithfulness check** in eval harness |
| LLM10 Unbounded consumption | ✅ Budget (soft/hard) | Add explicit **RunawayGuard** (steps/stagnation) |
| ASI (Agentic) — tool misuse / inter-agent | 🟡 | Tool policy + inter-agent audit (Skynet/Swarm) |

---

## 4. Implementation plan (additive, opt-in, `specs/`-style)

Two new modules following the repo's **Fase** convention (port + settings gate + graph snapshot test when off).

### Fase H — "Hardening++" (extends `prismal/security/`)
Hooks into the **`@prismal_node` middleware chain** and the existing 5-layer stack.

1. **Taint / provenance of untrusted content** — *HIGH priority*
   - Tag everything coming from tools, RAG, web, STT/OCR/captions as `untrusted` (the repo already warns to treat transcripts/OCR/captions as user content).
   - Route it through `GuardrailsEngine` **before** re-injecting it into the model; new `IndirectInjectionDetector` (heuristics + optional LLM classifier via `providers/`).
   - Location: `security/taint.py`, integrated in `react_loop` and the RAG/multimodal loaders.
2. **`OutputValidator`** — *HIGH* — `security/output_validator.py`: validate/escape model output before (a) calling a tool, (b) rendering, (c) executing. Pydantic schema per tool.
3. **`ToolPolicyEngine`** — *HIGH* — declarative policy per agent/tool/argument (allow/deny, rate limits, "requires HITL"). Builds on `ActionInterceptor` + `PermissionManager` + `hitl_gate`. Config in `config/tool_policies.yaml`.
4. **`RunawayGuard`** — *MEDIUM* — explicit supervisor step limits, stagnation detection (repeated state/tool), depth cap; unifies with Budget (`budget_max_calls`/`wall_clock`).
5. **PII on outputs** — *MEDIUM* — reuse `pii_sanitizer` as a configurable output filter.
6. **Security OTel metrics** — *LOW, quick win* — counters: `prismal.guardrail_blocks_total`, `prismal.injection_detected_total`, `prismal.permission_denied_total`, `prismal.output_rejected_total`.
7. **Supply chain** — *MEDIUM* — sign / provenance for AI-generated plugins & skills; pin model versions.

**Settings:** `hardening_enabled`, `taint_tracking_enabled`, `output_validation_enabled`, `tool_policy_path`, `runaway_max_steps`, `runaway_stagnation_window`.

### Fase E — "Evaluation Harness" (new package `prismal/eval/`) — *HIGH priority, the biggest gap*
Reuses what already exists: `build_test_runtime`, `FakeToolProvider`, the checkpointer (replay), `providers/` (LLM-as-judge), Langfuse.

- `eval/runner.py` — run scenarios over `get_async_compiled_graph()` with deterministic fakes.
- `eval/trajectory.py` — **trajectory** evaluators: right agent routed? right tool with right args? step count, cost, did it terminate (no runaway)?
- `eval/judges.py` — LLM-as-judge + **grounding/faithfulness** for RAG.
- `eval/redteam/` — corpus of **direct/indirect injection, jailbreaks, tool-misuse, system-prompt leakage**; runs in CI (`-m redteam`).
- `eval/regression.py` — **golden transcripts** + replay from checkpoints; detects behavioral regressions.
- `eval/report.py` — report + export to Langfuse evals.
- CI integration: new `eval`/`redteam` markers in `pyproject.toml`; a job that fails if score drops or a corpus attack passes.

**CLI:** `python -m prismal.eval run --suite redteam|regression|trajectory`.

> **Why this is low-risk.** The **hexagonal** design (`ToolProvider`/`VectorStore`/`ConfigSource`/`Composition` ports) + the **Extension API** (`@prismal_node`, `PrismalStateGraphBuilder`) + the **deterministic fakes** (`FakeToolProvider`, `build_test_runtime`) let both hardening and the evaluation harness enter as **middleware/ports/subpackages** without touching the 26 agents. Same "flag off ⇒ graph byte-for-byte identical (snapshot-tested)" invariant already used by Multimodal/Kokoro/Skynet/Budget.

---

## 5. Suggested roadmap (impact / effort)

**Quick wins (1–2 weeks)**
- [ ] Security OTel metrics (guardrail/permission/output counters).
- [ ] Explicit `RunawayGuard` (reuses budget + `recursion_limit`).
- [ ] PII as an output filter (reuses `pii_sanitizer`).
- [ ] `prismal/eval/` skeleton + 5–10 trajectory scenarios with fakes.

**Mid term (Fase H core)**
- [ ] Taint tracking + `IndirectInjectionDetector` (indirect injection).
- [ ] `OutputValidator` with per-tool schemas.
- [ ] `ToolPolicyEngine` (least privilege + HITL on high-risk).

**Strategic (Fase E full)**
- [ ] Red-team corpus + CI gate.
- [ ] Regression harness with golden transcripts + replay.
- [ ] Langfuse evals integration + report.
- [ ] Control mapping to NIST AI RMF / OWASP Agentic (compliance document).

---

## 6. Risks & notes
- **Do not reinvent.** Repo rule #4 (provider isolation) requires any LLM classifier (injection detector, judge) to live in `prismal/providers/`.
- **Cost.** LLM classifiers and LLM-as-judge add latency/cost → make them opt-in and metered through the existing Budget layer.
- **Eval determinism.** Use fakes by default; LLM judges go in `live_api`/`slow` suites.
- **Graph snapshot.** Preserve the "flag off ⇒ identical graph" invariant so the 26 agents are never disturbed.

---

## 7. Sources
- OWASP GenAI — Top 10 for Agentic Applications 2026 — https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/
- OWASP GenAI — Top 10 Agentic release (Dec 2025) — https://genai.owasp.org/2025/12/09/owasp-genai-security-project-releases-top-10-risks-and-mitigations-for-agentic-ai-security/
- OWASP — LLM Top 10 (archive) — https://genai.owasp.org/llm-top-10/
- Hardening Your AI: A Leader's Guide to Agent Security (A. Masood) — https://medium.com/@adnanmasood/hardening-your-ai-a-leaders-guide-to-agent-security-security-challenges-and-future-directions-f227003d590c
- Radware — LLM Security in 2026 — https://www.radware.com/cyberpedia/llm-security/
- Living Security — AI Agent Vulnerability 2026 — https://www.livingsecurity.com/blog/human-ai-agent-security-risks
- Awesome Harness Engineering — https://github.com/ai-boost/awesome-harness-engineering
- Martin Fowler — Harness engineering for coding agent users — https://martinfowler.com/articles/harness-engineering.html
- Harness Engineering for AI Agents (DEV) — https://dev.to/akki907/harness-engineering-for-ai-agents-16a0
- Agent Harness Engineering Guide 2026 (QubitTool) — https://qubittool.com/blog/agent-harness-evaluation-guide
- LLM Guardrails — Complete Guide 2026 — https://aisecurityandsafety.org/en/guides/llm-guardrails/
- Operationalizing NIST AI RMF for LLMs (IntechOpen) — https://www.intechopen.com/online-first/1242753
- Architecting Trust: NIST-Based Security Governance for AI Agents (Microsoft) — https://techcommunity.microsoft.com/blog/microsoftdefendercloudblog/architecting-trust-a-nist-based-security-governance-framework-for-ai-agents/4490556
- OpenClaw PRISM — Defense-in-Depth Runtime for Tool-Augmented LLM Agents (arXiv) — https://arxiv.org/pdf/2603.11853
- SoK: The Attack Surface of Agentic AI (arXiv) — https://arxiv.org/pdf/2603.22928

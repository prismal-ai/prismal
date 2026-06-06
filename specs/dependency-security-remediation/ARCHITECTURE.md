# Prismal Dependency Security Remediation — Triage and Remediation Methodology

## Metadata

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `IMPLEMENTED` |
| **Version** | 1.0 |
| **Date** | 2026-06-05 |
| **PLAN** | `specs/dependency-security-remediation/PLAN.md` |
| **SPEC** | `specs/dependency-security-remediation/SPEC.md` |
| **TASKS** | `specs/dependency-security-remediation/TASKS.md` |
| **Reviewers** | Tech Lead, Security Lead |

---

## 1. Context

Dependabot reported 18 alerts against `uv.lock` and `.github/workflows/ci.yml`. Unlike a *feature* with an API surface, this is a **dependency remediation effort**: no new code is designed; for each CVE, one of four actions is decided and executed (close / update / mitigate / respond to supply chain). This document defines the **triage methodology** that produces the matrix in `SPEC.md` and the plan in `TASKS.md`, so that the decisions are reproducible and auditable, not ad-hoc.

The repo already operates an SCA (Software Composition Analysis) process: `pip-audit` (pre-commit + CI), `trivy` (`.trivyignore`), `bandit`, and triage via `prismal doctor security-check`. This methodology is built on top of that process.

---

## 2. Technical Goals

- **OT-1:** Classify each alert deterministically (resolved / upgrade / mitigate / supply-chain).
- **OT-2:** Distinguish **nominal risk** (by version, what Dependabot sees) from **effective risk** (by prismal's real *surface*).
- **OT-3:** Introduce no regressions: every upgrade passes through `uv lock` + test suite + SCA.
- **OT-4:** Leave versioned traceability (this spec) and synchronize the three ignore-lists.
- **OT-5:** Treat the supply-chain incident as a separate flow (verification + rotation), not as a simple bump.

---

## 3. Decision Model

### 3.1 Per-alert classification tree

```
For each alert:
  Is the version in uv.lock ≥ patched version (GHSA)?
    ├─ YES → RESOLVED  → push the lock + verify + close the alert
    └─ NO  → Is there a patched version compatible with the stack?
              ├─ YES → UPGRADE → bump constraint + uv lock + validate
              └─ NO  → Is it a won't-fix or unpatched?
                        ├─ Yes → MITIGATE → surface analysis + documented ignore + trigger
                        └─ (CI case) → SUPPLY-CHAIN → verify reference + pin SHA + rotate secrets
```

### 3.2 Exposure analysis (nominal vs effective risk)

The key step Dependabot does **not** perform: is the vulnerable path in prismal's *surface*?

| Surface | Definition | Examples in this report | Effect on priority |
|---|---|---|---|
| `runtime` | Code that prismal runs in production | urllib3, aiohttp, langsmith, idna, ecdsa | Priority according to real severity |
| `server` | Vulnerability in a server that prismal **does not run** (uses the lib as client/embedded) | LiteLLM Proxy (SQLi, SSTI, MCP stdio, guardrail), ChromaDB FastAPI (pre-auth RCE), Starlette (BadHost) | Low effective risk; remediate for hygiene |
| `dev/docs` | Development or documentation toolchain | pymdown-extensions | Low risk; build-time only |
| `ci` | Continuous integration workflow | trivy-action | Build-chain risk; treat as P0 |

**Principle:** Dependabot's severity sets the *review order*; the surface analysis adjusts the *effective risk* and the urgency of the action. A `server` Critical that prismal does not expose (chromadb) has lower effective risk than a `runtime` Moderate that is actually exercised (aiohttp).

### 3.3 Action matrix

```
                 fix exists       no fix
              ┌───────────────┬──────────────────┐
 lock ≥ fix   │  RESOLVED     │   (n/a)          │
              ├───────────────┼──────────────────┤
 lock < fix   │  UPGRADE      │   MITIGATE       │
              └───────────────┴──────────────────┘
   CI case / actions  →  SUPPLY-CHAIN (its own flow)
```

---

## 4. Remediation Flows

### Flow A — RESOLVED (push the lock + close)
```
1. Confirm uv.lock >= fix:  uv pip show <pkg>
2. Ensure the uv.lock is committed and pushed to the branch Dependabot scans.
3. Dependabot re-scans and auto-closes; if not, close manually citing "fixed in <ver>".
4. Remove the corresponding ignore from .trivyignore/CI if it existed.
```
Applies to: litellm×4, urllib3×2, langsmith×2, idna, starlette (≈11 alerts).

### Flow B — UPGRADE (bump + validation)
```
1. Edit pyproject.toml: raise the minimum constraint to the fix version.
2. uv lock  (resolve) ; review the uv.lock diff (transitive effects).
3. uv sync ; run the affected sub-suite (e.g. MCP integration for aiohttp).
4. pip-audit + trivy + bandit clean.
5. Isolated and revertible commit.
```
Applies to: aiohttp (≥3.14.0), prefect, possibly pymdown, transformers (via torch).

### Flow C — MITIGATE (no fix)
```
1. Surface analysis: is the vulnerable path reachable in prismal?
2. Apply a compensating mitigation (config, isolation, indirect constraint e.g. torch>=2.6).
3. Keep/add the ignore in .trivyignore + ci.yml + pre-commit, with:
   - CVE/GHSA, package, reason (won't-fix / no patch yet),
   - reference to this spec,
   - re-evaluation TRIGGER (condition to remove the ignore).
```
Applies to: chromadb (unpatched), ecdsa (won't-fix), transformers (mitigation via torch).

### Flow D — SUPPLY-CHAIN (trivy-action incident)
```
1. grep .github/workflows/** for aquasecurity/trivy-action and aquasecurity/setup-trivy.
2. Determine whether any run used compromised tags/binaries during the windows (Mar 19–20, 2026).
3. If the action was used: pin to an immutable SHA of a safe version (trivy-action 0.35.0 / setup-trivy 0.2.6).
   If the binary is downloaded via curl: pin TRIVY_VERSION=0.69.3 + verify checksum/signature.
4. Rotate runner secrets if there was execution during the compromised window (P0).
5. Policy: pin ALL actions to SHA (follow-up debt).
```

---

## 5. Change Structure (what gets touched)

```
prismal/
├── pyproject.toml                 # constraint bumps: aiohttp>=3.14.0, (torch>=2.6), prefect, pymdown
├── uv.lock                        # re-resolved by `uv lock`
├── .trivyignore                   # sync: remove resolved, keep chromadb/ecdsa + new no-fix
├── .pre-commit-config.yaml        # pip-audit hook: mirror of .trivyignore
├── .github/workflows/ci.yml       # security-pip-audit: mirror + pin actions to SHA
├── CHANGELOG.md                   # security entry
└── specs/dependency-security-remediation/
    ├── PLAN.md
    ├── ARCHITECTURE.md  (this)
    ├── SPEC.md          (matrix)
    └── TASKS.md         (execution)
```

No code under `prismal/**` is modified: the 18 alerts are in dependencies, not in our own code.

---

## 6. Design Decisions

### DD-SEC-001: Prioritize by effective risk, not just nominal severity
The execution order weighs severity **and** surface. The server-surface Criticals that prismal does not expose (chromadb, litellm proxy) are documented but do not block; the runtime Moderates that are actually exercised (aiohttp) are remediated with a real upgrade.

### DD-SEC-002: "Push the lock first"
Since ~11 alerts are already resolved in the lock, the first action (P0) is to ensure that `uv.lock` is pushed. This closes most of the noise before touching anything, and clarifies the remaining real work.

### DD-SEC-003: Mitigation via indirect constraint before a major bump
For `transformers` (CVE-2026-1839) it is preferred to force `torch>=2.6` (neutralizes the vector) instead of bumping to `transformers` 5.x (breaking for `sentence-transformers`). Smaller blast radius, same security effect.

### DD-SEC-004: Ignore-lists as a triplicated single source of truth
`.trivyignore`, the `pip-audit` hook, and `ci.yml` must remain mirrored. A consistency test/script (or `prismal doctor security-check`) verifies that all three list the same set, each entry with justification and trigger.

### DD-SEC-005: Supply chain = P0 flow with rotation
The `trivy-action` incident is not treated as a version bump but as incident response: exposure verification + secret rotation. Pin to SHA, not to tag.

### DD-SEC-006: Dated per-CVE traceability
The patched versions are based on GHSA as of 2026-06-05 and are re-verified at execution time. Each decision is recorded in `SPEC.md` with CVE, version, action, and date — auditable.

---

## 7. Validation and Observability

### 7.1 Validation gates
- `uv lock` resolvable without conflicts.
- `pip-audit` (with justified ignores) without unexpected findings.
- `trivy fs --ignorefile .trivyignore .` clean.
- `bandit -r prismal` clean.
- `pytest -m "not live_api"` 100%.

### 7.2 Closure evidence
- Per resolved alert: capture of `uv pip show <pkg>` ≥ fix + absence in `pip-audit`.
- Per mitigation: entry in `.trivyignore` with trigger + surface note.
- Per supply-chain: workflow diff (pin SHA) + rotation checklist.

### 7.3 Process metric
- `n_alerts_terminal / 18` (target 18/18).
- `n_ignores_without_justification` (target 0).
- `n_actions_without_pin_sha` (target 0 — follow-up debt).

---

## 8. Rollout Plan

1. **P0:** push the lock (closes ~11) + trivy-action incident + document chromadb/ecdsa.
2. **P1:** real upgrades (aiohttp, transformers via torch, prefect, pymdown) in isolated commits.
3. **P2:** synchronize ignore-lists, final validation, closure of remaining alerts, entry in `CHANGELOG.md`.

Backout: each upgrade is a revertible commit; `uv.lock` allows deterministic rollback.

---

## 9. Open Questions

- **PA-1:** Has `ci.yml` already migrated 100% from `trivy-action` to `curl`? (Verify in P0; conditions whether secret rotation is needed.)
- **PA-2:** Is `torch>=2.6` resolvable with the rest of the lock without problematic downgrades? (Validate in P1.)
- **PA-3:** Exact version of `prefect` with PR #21591 and the GHSA for `pymdown`? (Confirm against GHSA at execution time.)
- **PA-4:** Do we adopt the policy of pinning all actions to SHA now, or leave it as debt? (Recommended: adopt.)

---

## Change History

| Version | Date | Author | Changes |
|---|---|---|---|
| 1.0 | 2026-06-05 | Ernesto Crespo | Triage methodology + remediation flows |

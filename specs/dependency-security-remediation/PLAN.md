# Prismal — Dependency Vulnerability Remediation (Dependabot)

## Strategic Plan / Product Requirements Document (PLAN)

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `IMPLEMENTED` |
| **Version** | 1.0 |
| **Date** | 2026-06-05 |
| **Reviewers** | Tech Lead, Security Lead, AI Architect |
| **Related documents** | `ARCHITECTURE.md`, `SPEC.md`, `TASKS.md` |
| **Source** | GitHub Dependabot — `prismal-ai/prismal/security/dependabot` (18 open alerts) |
| **Priority** | **HIGH** |

---

## 1. Executive Summary

The Dependabot report lists **18 open alerts** against `uv.lock` and one CI workflow: **3 Critical, 8 High, 6 Moderate, 1 Low**. This plan analyzes them one by one, cross-references them with the version actually pinned today in `uv.lock` and with prismal's real surface, and defines a risk-prioritized remediation.

**Main finding:** most of the alerts **are already resolved in the current `uv.lock`** — Dependabot scanned a previous `uv.lock`. Of the 18:

- **~11 already resolved in the current lock** (the pinned version is already ≥ the patched one): the 4 `litellm` alerts (1.86.2 ≥ 1.83.10), the 2 `urllib3` alerts (2.7.0), the 2 `langsmith` alerts (0.8.7 ≥ 0.8.0), `idna` (3.17 ≥ 3.15), `starlette` (1.2.0 ≥ 1.0.1). → **Action: push the lock + verify/close the alert.**
- **~3 require a real upgrade**: `aiohttp` (2 alerts → ≥ 3.14.0), `transformers` (CVE-2026-1839), `prefect` (SSRF), and `pymdown-extensions` (snippets regression, to be verified).
- **2 with no upstream fix → mitigation**: `chromadb` (CVE-2026-45829, unpatched) and `ecdsa` (CVE-2024-23342, *won't-fix*). Both already documented in `.trivyignore` with justification.
- **1 supply-chain incident**: `aquasecurity/trivy-action` (GHSA-69fq-xp46-6x23) in `.github/workflows/ci.yml`.

**Decisive exposure context:** prismal is a **framework/library**, not a deployment that runs LiteLLM's *proxy server* or ChromaDB's *HTTP server*. Therefore several Critical/High CVEs in the server surface (LiteLLM proxy SQLi, SSTI in `/prompts/test`, MCP stdio test endpoints, *sandbox escape* of the *custom-code guardrail*, ChromaDB pre-auth RCE) **are not in prismal's execution surface**. Dependabot alerts by version, not by usage; the real risk is substantially lower, but it is still remediated by bumping to patched versions to keep the tree clean.

---

## 2. Context and Problem

### 2.1 Current Situation

- The repo already has a mature CVE management practice: `.trivyignore`, `pip-audit --ignore-vuln` in `.pre-commit-config.yaml` and `.github/workflows/ci.yml`, and triage via `prismal doctor security-check` (Phase 30).
- However, **the `.trivyignore` and the CI ignores do not cover all 18 new alerts**: missing are `aiohttp` (CVE-2026-34993, CVE-2026-47265), `transformers` (CVE-2026-1839), `urllib3` (CVE-2026-21441, GHSA-qccp-gfcp-xxvc), `idna` (CVE-2026-45409), `starlette` (CVE-2026-48710), `langsmith` (CVE-2026-45134), `pymdown-extensions`, `prefect` (SSRF), and most of the new `litellm` CVEs.
- The `uv.lock` was already bumped above several fixes (litellm 1.86.2, urllib3 2.7.0, langsmith 0.8.7, idna 3.17, starlette 1.2.0), but **the Dependabot alerts remain open** because they reflect a previous scan or are waiting for the lock to be pushed.

### 2.2 Problem

1. **Noise vs signal:** 18 open alerts obscure which ones require real action (≈5) versus those that only need the lock pushed (≈11) or mitigation without a fix (2).
2. **Incomplete ignore-list coverage:** the CVEs without a fix (chromadb, ecdsa) must remain documented; the resolved ones must be removed from the ignore-lists once the lock surpasses them.
3. **Active supply-chain risk:** the `trivy-action` incident (Mar 2026) requires verifying that the workflow does not reference compromised tags and, if it ran during the compromise window, rotating CI secrets.
4. **No versioned triage artifact:** there is no document that records, per alert, the decision (upgrade / mitigate / accept) and its exposure justification.

### 2.3 Opportunity

Turn the Dependabot report into a **versioned remediation artifact** (this spec) that: (a) quickly closes the ~11 already resolved, (b) executes the ~5 real upgrades with validation, (c) documents the 2 no-fix mitigations, and (d) closes the supply-chain incident. Low-to-medium effort, reduces the surface, and leaves a security audit trail.

---

## 3. Target Users

### Persona 1: Security Lead / Maintainer
- **Need:** A decision per alert with exposure justification and closure criteria, not just "bump the version".
- **Frequency:** Per report / security sprint.

### Persona 2: Release Engineer
- **Need:** Concrete `uv` commands and validation (`pip-audit`, `trivy`, test suite) to apply and verify each change without breaking the stack.
- **Frequency:** Per release.

### Persona 3: Downstream Consumer (prismal-sdk / prismal-web)
- **Need:** To know which CVEs actually affect the runtime and which are noise from an unused library (LiteLLM proxy, ChromaDB server).
- **Frequency:** Per dependency upgrade.

---

## 4. Goals and Success Metrics

### 4.1 Goals

| Goal | Metric | Target | Timeframe |
|---|---|---|---|
| Close alerts already resolved in the lock | Dependabot alerts closed after pushing the lock | 11/11 | P0 (days) |
| Remediate real upgrades | aiohttp, transformers, prefect, pymdown remediated or mitigated | 100% | P1 (1 week) |
| Document no-fix mitigations | chromadb + ecdsa with justification in `.trivyignore` + this spec | 2/2 | P0 |
| Close supply-chain incident | trivy-action verified/pinned to SHA; secrets rotated if applicable | Done | P0 |
| Ignore-list hygiene | `.trivyignore`/CI without obsolete ignores; new ones documented | Synced | P1 |
| No regressions | `uv run pytest -m "not live_api"` | 100% | Global |

### 4.2 Non-goals (this cycle)

- Migrate `python-jose` → `PyJWT` (mitigation for `ecdsa`): documented as debt, not executed here.
- Major bump of `transformers` to 5.x: mitigation (`torch≥2.6`) is preferred unless the stable 5.x is validated.

---

## 5. Scope

### 5.1 In Scope

- Triage and decision for each of the 18 alerts (full matrix in `SPEC.md`).
- Upgrades in `pyproject.toml` / `uv.lock` for the alerts with an available and compatible fix.
- Documented mitigations for the 2 without a fix.
- Verification and pinning of the CI workflow affected by the `trivy-action` incident.
- Synchronization of `.trivyignore`, `.pre-commit-config.yaml`, and `.github/workflows/ci.yml`.
- Validation: `pip-audit`, `trivy`, `bandit`, test suite.

### 5.2 Out of Scope

- Rewriting application code (the CVEs are in dependencies, not in prismal code).
- Security audit of prismal's own code (the L1–L5 layers already exist; out of scope for this cycle).
- `python-jose`→`PyJWT` migration (recorded as debt).
- Hardening of `prismal-web` deployments (host's responsibility).

### 5.3 Future Considerations

- Automate the "Dependabot → matrix → uv upgrade → pip-audit" flow in `prismal doctor security-check`.
- Pin ALL GitHub Actions to an immutable SHA (not just trivy).
- SLA policy by severity (Critical: 48 h, High: 7 days, Moderate: 30 days).

---

## 6. Functional Requirements (Summary — detail in `SPEC.md`)

| ID | Requirement | Priority |
|---|---|---|
| RF-SEC-001 | Each alert has a documented decision: resolved / upgrade / mitigate / supply-chain | `MUST` |
| RF-SEC-002 | The ~11 alerts already resolved in the lock are verified and closed after the push | `MUST` |
| RF-SEC-003 | `aiohttp` is bumped to ≥ 3.14.0 (CVE-2026-34993, CVE-2026-47265) | `MUST` |
| RF-SEC-004 | `transformers` is mitigated (`torch≥2.6`) or bumped to stable 5.x (CVE-2026-1839) | `MUST` |
| RF-SEC-005 | `prefect` is bumped to the version with the SSRF DNS-rebinding fix | `SHOULD` |
| RF-SEC-006 | `pymdown-extensions` is verified and remediated (snippets regression) | `SHOULD` |
| RF-SEC-007 | `chromadb` (no fix) documented as mitigation: embedded use, no HTTP server | `MUST` |
| RF-SEC-008 | `ecdsa` (won't-fix) documented: transitive, debt to migrate to PyJWT | `MUST` |
| RF-SEC-009 | CI workflow verified/pinned for the `trivy-action` incident; secrets rotated if applicable | `MUST` |
| RF-SEC-010 | `.trivyignore`/CI synchronized; obsolete ignores removed, new ones justified | `MUST` |
| RF-SEC-011 | Final validation: `pip-audit` + `trivy` + `bandit` + green tests | `MUST` |
| RF-SEC-012 | Exposure note (library vs server surface) per server-surface CVE | `SHOULD` |

---

## 7. Non-Functional Requirements

### Security
- No Critical/High CVE without an explicit and justified decision.
- CVEs without a fix must have a documented mitigation and a re-evaluation *trigger* (when to remove the ignore).
- The supply-chain incident is treated as P0 (potential exfiltration of CI secrets).

### Compatibility
- No upgrade may break the pinned stack (Python 3.13+, resolvable `uv.lock`).
- `filterwarnings=error` in tests: upgrades must not introduce our own `DeprecationWarning`s.

### Traceability
- Each decision is recorded in `SPEC.md` with CVE, version, action, and date.
- `.trivyignore` references this spec for the new ignores.

### Reversibility
- Each upgrade is an isolated, revertible commit; `uv.lock` allows rollback.

---

## 8. Constraints and Dependencies

- `uv` as the manager; all resolution goes through `uv lock` / `uv sync`.
- Some fixes have no upstream fix (chromadb, ecdsa) → mitigation, not upgrade.
- `transformers` 5.x is a major (breaking) bump → prefer mitigation via `torch≥2.6`.
- Verification of patched versions against the GitHub Advisory Database (GHSA) at remediation time (the versions here are based on the state as of 2026-06-05).

---

## 9. User Stories

**US-SEC-001:** As a Security Lead, I want to know which of the 18 alerts require real action so I don't waste time on the ones already resolved.
- [ ] The matrix separates "resolved in lock" from "upgrade" from "mitigate".

**US-SEC-002:** As a Release Engineer, I want the exact `uv` commands and validation for each upgrade.
- [ ] `TASKS.md` lists the command and the verification criterion per alert.

**US-SEC-003:** As a Maintainer, I want to close the `trivy-action` incident with certainty that there was no exfiltration or, if there was, with secret rotation.
- [ ] Workflow verification + rotation checklist.

**US-SEC-004:** As a Downstream Consumer, I want to know which CVEs are noise from an unused library.
- [ ] Exposure note per server-surface CVE (LiteLLM proxy, ChromaDB server).

---

## 10. Risks and Mitigations

| Risk | Probability | Impact | Mitigation |
|---|---|---|---|
| `aiohttp` 3.14 upgrade breaks the MCP SSE transport | Low | Medium | MCP integration tests; isolated and revertible change |
| `transformers` 5.x bump breaks `sentence-transformers` | Medium | High | Prefer `torch≥2.6` mitigation; do not bump to 5.x without validation |
| CI secrets compromised by `trivy-action` (Mar 2026) | Medium | Critical | Verify execution window; rotate tokens/secrets; pin to SHA |
| `chromadb`/`ecdsa` without a fix get "forgotten" in the ignore-list | Medium | Medium | Documented re-evaluation trigger + `prismal doctor security-check` |
| Close resolved alerts but the lock is not pushed | Medium | Low | Explicit P0: push the current `uv.lock` first |
| Patched versions change after this spec | Medium | Low | Verify GHSA at execution time; SPEC is dated |

---

## 11. Estimated Timeline (risk-prioritized)

| Wave | Duration | Deliverable |
|---|---|---|
| **P0 — Critical/Supply-chain** | 1–2 days | Push the lock (closes ~11), trivy-action incident closed, chromadb/ecdsa documented |
| **P1 — Real upgrades** | 3–5 days | aiohttp ≥3.14, transformers mitigated, prefect, pymdown |
| **P2 — Hygiene** | 1–2 days | Ignore-list sync, final validation, closure of remaining alerts |
| **Total** | **~1.5 weeks** | 18/18 alerts resolved or mitigated with traceability |

---

## 12. Definition of Done (Global)

- [ ] The 18 alerts have a terminal state: closed (resolved), remediated (upgrade), or mitigated (no fix, documented).
- [ ] `uv.lock` updated and pushed; already-resolved alerts closed in Dependabot.
- [ ] `aiohttp ≥ 3.14.0`; `transformers` mitigated or ≥ safe version; `prefect`/`pymdown` remediated or with a documented decision.
- [ ] `chromadb` and `ecdsa` with mitigation and re-evaluation trigger in `.trivyignore` + `SPEC.md`.
- [ ] CI workflow verified/pinned for the `trivy-action` incident; secret rotation executed if applicable.
- [ ] `.trivyignore`, `.pre-commit-config.yaml`, `.github/workflows/ci.yml` synchronized.
- [ ] `uv run pip-audit` (with justified ignores) clean; `trivy` and `bandit` clean; `pytest -m "not live_api"` 100%.
- [ ] `CHANGELOG.md` with a security entry; this spec marked `IMPLEMENTED`.

---

## Change History

| Version | Date | Author | Changes |
|---|---|---|---|
| 1.0 | 2026-06-05 | Ernesto Crespo | Initial plan — remediation of 18 Dependabot alerts |

## Approvals

| Role | Name | Date | Status |
|---|---|---|---|
| Tech Lead | — | | ☐ Pending |
| Security Lead | — | | ☐ Pending |
| AI Architect | — | | ☐ Pending |

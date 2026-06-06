# Prismal Dependency Security Remediation — Execution Plan (TASKS)

## Metadata

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `IMPLEMENTED` |
| **Version** | 1.0 |
| **Date** | 2026-06-05 |
| **PLAN** | `specs/dependency-security-remediation/PLAN.md` |
| **Architecture** | `specs/dependency-security-remediation/ARCHITECTURE.md` |
| **SPEC** | `specs/dependency-security-remediation/SPEC.md` |
| **Priority** | **HIGH** |

---

## 1. Execution Summary

18 Dependabot alerts → 3 risk-prioritized waves. **P0** closes the bulk of the noise (the ~11 already resolved in the lock) and handles what is truly urgent (supply chain + documenting no-fix). **P1** executes the real upgrades. **P2** synchronizes ignore-lists and validates.

> The fix versions are based on GHSA as of 2026-06-05. **Re-verify against the GitHub Advisory Database at execution time.**

---

## 2. Prerequisites

- [ ] Access to the branch Dependabot scans (for pushing `uv.lock`).
- [ ] `uv`, `pip-audit`, `trivy`, `bandit` available in the environment.
- [ ] Permissions to rotate CI secrets (if the `trivy-action` incident requires it).
- [ ] Clean snapshot/commit of the current `uv.lock` as a rollback point.

---

## 3. Execution Phases

### WAVE P0 — Critical, supply-chain, and quick closure (1–2 days)

#### P0-01 — Push `uv.lock` and close the already-resolved ones (≈11 alerts)
- [ ] Confirm versions: `uv pip show litellm urllib3 langsmith idna starlette` (litellm ≥1.83.10, urllib3 ≥2.7.0, langsmith ≥0.8.0, idna ≥3.15, starlette ≥1.0.1).
- [ ] Ensure `uv.lock` is committed and pushed to the scanned branch.
- [ ] Close (or let auto-close) alerts #3, #4, #5, #6, #7, #8, #9, #10, #11, #18 citing "fixed in <ver>".
- **Done:** the 10–11 alerts marked as resolved in Dependabot.

#### P0-02 — Supply-chain incident `trivy-action` (#14)
- [ ] `grep -rn "aquasecurity/trivy-action\|aquasecurity/setup-trivy" .github/workflows/`.
- [ ] If it appears: pin to an **immutable SHA** of a safe version (trivy-action 0.35.0 / setup-trivy 0.2.6).
- [ ] If the binary is downloaded via `curl`: pin `TRIVY_VERSION=0.69.3` + verify checksum/signature; confirm 0.69.4 was NOT used.
- [ ] Review CI runs between Mar 19–20, 2026; if the compromised action/binary was executed → **rotate all runner secrets** (GH tokens, registry keys, deployment secrets).
- [ ] Close #14 documenting the verification.
- **Done:** workflow without mutable references to compromised tags; rotation done if it applied.

#### P0-03 — Document no-fix mitigations (chromadb #15, ecdsa #1)
- [ ] Confirm that `chromadb` is used only embedded (no `chroma run` / HTTP server).
- [ ] Keep `CVE-2026-45829` and `CVE-2024-23342` in `.trivyignore` with: reason, surface, reference to `SPEC.md`, and re-evaluation **trigger**.
- [ ] Record debt: `python-jose` → `PyJWT` migration (follow-up issue).
- **Done:** both alerts with documented mitigation and trigger; #15 and #1 marked "mitigated/won't-fix".

---

### WAVE P1 — Real upgrades (3–5 days)

#### P1-01 — `aiohttp` ≥ 3.14.0 (#16, #17)
- [ ] `pyproject.toml`: `aiohttp>=3.14.0`.
- [ ] `uv lock` → review the transitive diff.
- [ ] `uv sync` → run MCP integration tests (SSE transport).
- [ ] `pip-audit` no longer reports CVE-2026-34993 / CVE-2026-47265.
- **Done:** aiohttp ≥3.14.0 + green MCP tests; #16 #17 closed.

#### P1-02 — `transformers` / mitigation via `torch≥2.6` (#2)
- [ ] Check the `torch` version in the lock; if < 2.6, bump `torch>=2.6` in `pyproject.toml`.
- [ ] `uv lock` → confirm resolution without problematic `sentence-transformers` downgrades.
- [ ] If the torch mitigation is not viable: evaluate `transformers>=5.0.0` (validate `sentence-transformers`).
- [ ] Document in `.trivyignore` (mitigated via torch≥2.6) with a trigger, or remove if bumped to 5.x.
- **Done:** CVE-2026-1839 vector neutralized (torch≥2.6) or transformers on 5.x; #2 closed/mitigated.

#### P1-03 — `prefect` SSRF (#13)
- [ ] Confirm against GHSA the first `prefect` version that includes PR #21591 (OSS-7874).
- [ ] Bump `prefect>=<fix_ver>` in `pyproject.toml`; `uv lock` + `uv sync`.
- [ ] Run scheduler tests (`prismal/scheduler/`) — APScheduler/Prefect flows.
- [ ] If there is no stable version with the fix: document it (do not use webhooks to untrusted destinations) + ignore with trigger.
- **Done:** prefect on a version with the fix or documented mitigation; #13 closed/mitigated.

#### P1-04 — `pymdown-extensions` snippets (#12)
- [ ] Confirm the exact GHSA/CVE of the regression and its fix version.
- [ ] Bump `pymdown-extensions>=<fix_ver>` (docs dep); `uv lock`.
- [ ] Verify the docs build (mkdocs) is still OK.
- [ ] If there is no fix: document it (snippets only over trusted repo sources) + ignore.
- **Done:** pymdown remediated or decision documented; #12 closed/mitigated.

---

### WAVE P2 — Hygiene and validation (1–2 days)

#### P2-01 — Synchronize ignore-lists
- [ ] `.trivyignore`: remove IDs of CVEs already resolved by the lock; keep only no-fix ones (chromadb, ecdsa, + new) with justification + trigger.
- [ ] `.pre-commit-config.yaml` (pip-audit hook): exact mirror of `.trivyignore`.
- [ ] `.github/workflows/ci.yml` (security-pip-audit): exact mirror + pin actions to SHA.
- [ ] (Optional) consistency test/script that verifies all three list the same set.
- **Done:** three mirrored sources; 0 ignores without justification.

#### P2-02 — Pin GitHub Actions to SHA (P0-02 debt → policy)
- [ ] Pin all actions in `.github/workflows/**` to an immutable SHA (not just trivy).
- **Done:** 0 actions with a mutable tag.

#### P2-03 — Final validation
- [ ] `uv run pip-audit ${PIP_AUDIT_IGNORES} --skip-editable` clean.
- [ ] `trivy fs --ignorefile .trivyignore .` clean.
- [ ] `uv run bandit -r prismal -c pyproject.toml` clean.
- [ ] `uv run pytest -m "not live_api"` 100%.
- [ ] `uv run mypy prismal` + `uv run ruff check .` without regressions.
- **Done:** all gates green.

#### P2-04 — Documentation and closure
- [ ] Security entry in `CHANGELOG.md` (remediated + mitigated CVEs).
- [ ] Mark this spec `IMPLEMENTED`; update the per-alert status table in `SPEC.md`.
- [ ] Confirm 18/18 alerts in terminal state in Dependabot.
- **Done:** complete traceability.

---

## 4. Inter-Task Dependencies

```
P0-01 (push lock)         → closes ~11 alerts, unblocks clarity of the real work
P0-02 (trivy-action)      → independent; P0 due to exfiltration risk
P0-03 (chromadb/ecdsa)    → independent
P1-01..04 (upgrades)      → after P0-01; each isolated and revertible
P2-01 (sync ignores)      → after P1 (depends on the final version state)
P2-02 (pin SHA)           → after/alongside P0-02
P2-03 (validation)        → after P1 + P2-01
P2-04 (closure)           → last
```

Risk critical path: **P0-02 (supply-chain)** → secret rotation if applicable. Volume critical path: **P0-01 (push lock)**.

---

## 5. Tasks ↔ Alerts Matrix

| Task | Alerts | Target state |
|---|---|---|
| P0-01 | #3 #4 #5 #6 #7 #8 #9 #10 #11 #18 | RESOLVED (closed) |
| P0-02 | #14 | SUPPLY-CHAIN (closed) |
| P0-03 | #15 #1 | MITIGATE (documented) |
| P1-01 | #16 #17 | UPGRADE (closed) |
| P1-02 | #2 | MITIGATE/UPGRADE |
| P1-03 | #13 | UPGRADE/mitigate |
| P1-04 | #12 | UPGRADE/verify |

Coverage: 18/18 alerts assigned to a task.

---

## 6. Risk Matrix

| Risk | Mitigation | Task |
|---|---|---|
| `aiohttp` 3.14 breaks the MCP transport | MCP integration tests; revertible commit | P1-01 |
| `torch≥2.6` does not resolve with the lock | Fall back to documenting + evaluate transformers 5.x | P1-02 |
| Compromised CI secrets | P0 rotation if there was execution during the window | P0-02 |
| Obsolete ignores persist | Triplicated sync + consistency script | P2-01 |
| Fix version changed after the spec | Re-verify GHSA at execution time | all |
| Lock is not pushed and alerts stay open | Explicit P0-01 as the first step | P0-01 |

---

## 7. Definition of Done (Global)

- [ ] 18/18 alerts in terminal state (closed / remediated / mitigated-documented).
- [ ] `uv.lock` pushed; ~11 resolved ones closed in Dependabot.
- [ ] `aiohttp ≥ 3.14.0`; CVE-2026-1839 neutralized (torch≥2.6) or transformers 5.x; prefect and pymdown remediated or documented.
- [ ] chromadb + ecdsa with mitigation + trigger in `.trivyignore`.
- [ ] trivy-action incident closed; actions pinned to SHA; secrets rotated if it applied.
- [ ] `.trivyignore` / pip-audit / ci.yml synchronized; 0 ignores without justification.
- [ ] `pip-audit` + `trivy` + `bandit` + `pytest -m "not live_api"` green.
- [ ] `CHANGELOG.md` updated; spec `IMPLEMENTED`.

---

## 8. Reference Commands

```bash
# Check current versions
uv pip show aiohttp transformers prefect pymdown-extensions torch litellm urllib3 langsmith idna starlette

# Apply upgrades (aiohttp example)
#   edit pyproject.toml -> aiohttp>=3.14.0
uv lock && uv sync

# SCA
uv run pip-audit ${PIP_AUDIT_IGNORES} --skip-editable
trivy fs --ignorefile .trivyignore .

# Security lint + tests
uv run bandit -r prismal -c pyproject.toml
uv run pytest -m "not live_api"

# Supply-chain check
grep -rn "aquasecurity/trivy-action\|aquasecurity/setup-trivy" .github/workflows/
```

---

## Change History

| Version | Date | Author | Changes |
|---|---|---|---|
| 1.0 | 2026-06-05 | Ernesto Crespo | Initial execution plan — 3 waves, 18 alerts |

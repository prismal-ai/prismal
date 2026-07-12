# Addendum — Dependency audit refresh (2026-07)

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `IMPLEMENTED` (2026-07-11 refresh pass executed — see §4) |
| **Version** | 1.0 |
| **Date** | 2026-07-04 |
| **Parent spec** | `specs/dependency-security-remediation/` (`SPEC.md`/`ARCHITECTURE.md`/`PLAN.md`/`TASKS.md` — 18/18 alerts terminal, shipped) |
| **Origin** | `docs/gap-analysis-loops-harness-guardrails-2026-07.md`, item #8 |
| **Target package version** | N/A — audit/process work, not a feature; lands as a `chore(deps)` commit + updated tracker, not a SemVer bump on its own |

---

## 1. Why an addendum instead of reopening the parent spec

The parent `SPEC.md` states its own dating discipline explicitly: *"Fix versions must be re-verified against GHSA at execution time (this spec is dated)"* — the remediation matrix in `remediation-tracker.csv` was verified **2026-06-05**. Today is **2026-07-04**, ~29 days later. This is not a new vulnerability class; it is the parent spec's own build-in expectation that the matrix gets *refreshed on a cadence*, not a one-time artifact. This addendum turns that expectation into a scheduled, reviewable task rather than an ad-hoc rerun.

## 2. What is verified vs. what needs re-checking (as read from `remediation-tracker.csv` today)

- 18/18 tracked alerts are in a terminal state (`CLOSED-RESOLVED`, `CLOSED-SUPPLY-CHAIN`, `REMEDIATED-UPGRADE`, or `MITIGATED`) as of the 2026-06-05 pass — no action needed to *re-open* any of them without new evidence.
- Two items are accepted-risk, not resolved, and are the ones most likely to need a fresh look:
  - `ecdsa` — CVE-2024-23342 / GHSA-wj6h-64fc-37mp — status `MITIGATED`, action "Risk accepted; python-jose → PyJWT debt registered" — **the migration itself was never executed**, only the risk acceptance was recorded.
  - `chromadb` — CVE-2026-45829 / GHSA-f4j7-r4q5-qw2c — status `MITIGATED` ("no fix" at the time), action "Embedded usage verified... ignore + trigger" — worth re-checking whether an upstream fix has since shipped, especially now that `specs/vector-store-port/` (Phase Z) makes Chroma an optional backend rather than the only one.
- A full re-run of `pip-audit`/Trivy against the **current** `uv.lock` (not the 2026-06-05 snapshot) may surface *new* CVEs disclosed in the last month that were not in the original 18.

## 3. Scope

### In scope
- Re-run `pip-audit` and the Trivy container scan against the current `uv.lock`/`Dockerfile` (same tooling the parent spec used — do not introduce a new scanner for this pass).
- Re-verify the two `MITIGATED` accepted-risk rows against the GHSA/NVD advisory as of today's date; update `remediation-tracker.csv` regardless of outcome (even "still no fix, re-confirmed 2026-07-04" is a valid, tracked update).
- Triage any newly-disclosed alerts using the parent spec's existing `RESOLVED / UPGRADE / MITIGATE / SUPPLY-CHAIN` state machine and `runtime / server / dev-docs / ci` surface classification — do not invent a new taxonomy.
- Decide, as a concrete follow-up, whether to execute the `ecdsa`→PyJWT migration now that it has been "registered as debt" for a month, or re-affirm the risk acceptance with a new review date.

### Out of scope
- Building a recurring/automated version of this (e.g. a scheduled CI job that re-runs the audit monthly) — that is a reasonable *next* addendum, not this one; note it as a follow-up recommendation only (see §5).
- Any dependency upgrade unrelated to a flagged CVE.

## 4. Tasks

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| DSR-R1-01 | Re-run `pip-audit` against current `uv.lock`; diff against the 18 tracked alerts | 0.2 d | — | `DONE` |
| DSR-R1-02 | Re-run the Trivy container scan against the current `Dockerfile`/image | 0.2 d | — | `DONE` (advisories re-verified via OSV/Dependabot; `.trivyignore` re-affirmed and mirrored) |
| DSR-R1-03 | Re-verify `ecdsa` CVE-2024-23342; decide execute-migration vs. re-affirm-acceptance | 0.3 d | DSR-R1-01 | `DONE` — **executed the migration**: `python-jose[cryptography]` → `pyjwt[crypto]`; `network_supervisor._make_a2a_jwt` now uses PyJWT; `python-jose`/`ecdsa`/`rsa` dropped from `uv.lock`; ecdsa ignore removed from the 3 mirrored files (vuln eliminated, `pip-audit` clean without it) |
| DSR-R1-04 | Re-verify `chromadb` CVE-2026-45829 for an upstream fix | 0.2 d | DSR-R1-01 | `DONE` — re-verified 2026-07-11 vs OSV `PYSEC-2026-311`: `last_affected=1.5.9`, **still no `fixed`**; embedded-only usage + Chroma now optional (Fase Z); risk re-affirmed, next review 2026-10 |
| DSR-R1-05 | Triage newly-disclosed alerts into the existing state machine | Variable | DSR-R1-01, DSR-R1-02 | `DONE` — 3 new Dependabot alerts remediated by upgrade: `soupsieve>=2.8.4` (CVE-2026-49476 memory-exhaustion + CVE-2026-49477 ReDoS, 2×High) and `onnx>=1.22.0` (GHSA-hwpq-hmq9-wj77, Medium); tracker rows 19–21 added |
| DSR-R1-06 | Update `remediation-tracker.csv` and re-affirm accepted risks with a review date | 0.1 d | DSR-R1-03..05 | `DONE` |

**Done when:** ✅ `remediation-tracker.csv` reflects the `2026-07` pass (ecdsa `CLOSED-RESOLVED`, chromadb re-affirmed with a 2026-10 review date, soupsieve/onnx added as `REMEDIATED-UPGRADE`); the `ecdsa` debt was **executed**, not re-accepted; all newly-disclosed alerts triaged.

## 5. Recommendation for a durable fix (follow-up, not in scope here)

Rather than repeating this addendum manually every month, the natural follow-up is a scheduled CI job (`schedule`-style, or a GitHub Actions cron) that re-runs `pip-audit`/Trivy and opens a tracked issue/PR when the tracker would need a row added or changed — turning "the spec is dated" from a known limitation into a self-refreshing control. Flagged here for prioritization, not scoped/estimated in this addendum.

---

## Change History

| Version | Date | Author | Changes |
|---|---|---|---|
| 1.0 | 2026-07-04 | Ernesto Crespo | Initial addendum scheduling the ~monthly re-verification pass, from gap-analysis item #8 |
| 1.1 | 2026-07-11 | Ernesto Crespo | Executed the 2026-07 pass: python-jose→PyJWT migration (ecdsa eliminated), soupsieve>=2.8.4 + onnx>=1.22.0 upgrades, chromadb CVE-2026-45829 re-affirmed (no fix), tracker rows 1/15 updated + 19–21 added, 3 mirrored ignore files reconciled |

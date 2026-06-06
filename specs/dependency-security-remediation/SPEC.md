# Prismal Dependency Security Remediation — Remediation Matrix (SPEC)

## Metadata

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `IMPLEMENTED` |
| **Version** | 1.0 |
| **Date** | 2026-06-05 |
| **PLAN** | `specs/dependency-security-remediation/PLAN.md` |
| **Architecture** | `specs/dependency-security-remediation/ARCHITECTURE.md` |
| **TASKS** | `specs/dependency-security-remediation/TASKS.md` |

---

## Conventions

- **Current version** = the one pinned today in `uv.lock` (verified 2026-06-05).
- **Fix version** = first patched version per the GitHub Advisory Database (GHSA) / GLAD as of 2026-06-05.
- **State**: `RESOLVED` (lock ≥ fix) · `UPGRADE` (lock < fix, fix available) · `MITIGATE` (no upstream fix) · `SUPPLY-CHAIN`.
- **Surface**: `runtime` (affects prismal's actual usage) · `server` (only if a server that prismal does NOT run is executed) · `dev/docs` (toolchain) · `ci` (workflow).
- Fix versions must be **re-verified against GHSA at execution time** (this spec is dated).

---

## Summary by severity

| Severity | # | Dominant state |
|---|---|---|
| Critical | 3 | 1 resolved (litellm), 1 mitigate (chromadb), 1 supply-chain (trivy) |
| High | 8 | 6 resolved (litellm×3, urllib3×2, langsmith×1*), 1 mitigate (ecdsa), 1 resolved (langsmith/langchain-classic) |
| Moderate | 6 | 2 upgrade (aiohttp×2), 1 mitigate/upgrade (transformers), 1 resolved (idna), 1 resolved (starlette), 1 verify (pymdown) |
| Low | 1 | 1 upgrade/verify (prefect) |

\* The two LangSmith alerts (#9 langsmith, #10 langchain-classic) are the same CVE.

---

## Complete matrix (18 alerts)

| # | Package | Sev | CVE / GHSA | Current | Fix | State | Surface | Action |
|---|---|---|---|---|---|---|---|---|
| 4 | litellm | Critical | CVE-2026-42208 / GHSA-r75f-5x8p-qvmc | 1.86.2 | 1.83.7 | RESOLVED | server | Verify + close |
| 15 | chromadb | Critical | CVE-2026-45829 / GHSA-f4j7-r4q5-qw2c | 1.5.8 | — (no fix) | MITIGATE | server | Embedded use; do not run HTTP server; monitor |
| 14 | aquasecurity/trivy-action | Critical | GHSA-69fq-xp46-6x23 | (ci.yml) | trivy-action 0.35.0 / setup-trivy 0.2.6 / trivy 0.69.2–0.69.3 | SUPPLY-CHAIN | ci | Verify reference + pin SHA + rotate secrets if applicable |
| 5 | litellm | High | (cluster X41 / 1.83.x — confirm GHSA) | 1.86.2 | ~1.83.x | RESOLVED | server | Verify + close |
| 3 | litellm | High | (SSTI /prompts/test — confirm GHSA) | 1.86.2 | ~1.83.x | RESOLVED | server | Verify + close |
| 8 | litellm | High | CVE-2026-40217 (X41-2026-001) | 1.86.2 | 1.83.10 | RESOLVED | server | Verify + close |
| 7 | urllib3 | High | CVE-2026-21441 / GHSA-38jv-5279-wg99 | 2.7.0 | 2.6.3 | RESOLVED | runtime | Verify + close |
| 6 | urllib3 | High | GHSA-qccp-gfcp-xxvc | 2.7.0 | 2.7.0 | RESOLVED | runtime | Verify + close |
| 1 | ecdsa | High | CVE-2024-23342 / GHSA-wj6h-64fc-37mp | 0.19.2 | — (won't-fix) | MITIGATE | runtime (transitive) | Accept + PyJWT migration debt |
| 9 | langsmith | High | CVE-2026-45134 | 0.8.7 | 0.8.0 | RESOLVED | runtime | Verify + close |
| 10 | langchain-classic→langsmith | High | CVE-2026-45134 | 1.0.7 / 0.8.7 | langsmith 0.8.0 | RESOLVED | runtime | Verify + close (same CVE as #9) |
| 11 | idna | Moderate | CVE-2026-45409 (bypass of CVE-2024-3651) | 3.17 | 3.15 | RESOLVED | runtime (transitive) | Verify + close |
| 18 | starlette | Moderate | CVE-2026-48710 (BadHost) | 1.2.0 | 1.0.1 | RESOLVED | server (transitive) | Verify + close |
| 17 | aiohttp | Moderate | CVE-2026-47265 | 3.13.5 | 3.14.0 | UPGRADE | runtime | Bump to ≥ 3.14.0 |
| 16 | aiohttp | Moderate | CVE-2026-34993 | 3.13.5 | 3.14.0 | UPGRADE | runtime | Bump to ≥ 3.14.0 |
| 2 | transformers | Moderate | CVE-2026-1839 | 4.57.6 | 5.0.0rc3 | MITIGATE/UPGRADE | runtime (does not use Trainer) | Mitigate `torch≥2.6`; or bump to stable 5.x |
| 12 | pymdown-extensions | Moderate | (regression of GHSA-jh85-wwv9-24hv / CVE-2023-32309) | 10.21.3 | (confirm GHSA) | VERIFY | dev/docs | Confirm GHSA and bump; if it's a docs dep, low risk |
| 13 | prefect | Low | SSRF DNS-rebinding (PR #21591 / OSS-7874) | 3.6.27 | (confirm version with PR #21591) | UPGRADE | runtime (webhooks) | Bump to version with fix; only affects webhooks with `allow_private_urls=False` |

---

## Detail per alert

### SEC-A01 · litellm (#4 Critical, #3 #5 #8 High) — RESOLVED in lock
- **Confirmed CVEs:** CVE-2026-42208 (proxy API key SQLi, fix 1.83.7), CVE-2026-40217 (sandbox escape custom-code guardrail, fix 1.83.10). #3 (SSTI `/prompts/test`) and #5 (MCP stdio test endpoints) belong to the same *cluster* of proxy-surface disclosures, patched in the 1.83.x series.
- **State:** `uv.lock` pins **litellm 1.86.2 ≥ 1.83.10** → all four are resolved.
- **Surface:** `server`. All affect the **LiteLLM Proxy Server** (`/v1/*`, `/prompts/test`, `/guardrails/test_custom_code`, MCP stdio test endpoints). Prismal uses litellm as a **client library** (`providers/` wrapper), **does not run the proxy**, so they are not in its execution surface.
- **Closure validation:** `uv pip show litellm` ≥ 1.83.10; `pip-audit` does not report the CVEs; confirm the exact GHSA for #3/#5 and record it.

### SEC-A02 · chromadb (#15 Critical) — MITIGATE (no fix)
- **CVE:** CVE-2026-45829 / GHSA-f4j7-r4q5-qw2c, CVSS 10.0. Pre-auth RCE in ChromaDB's **FastAPI server** (`/api/v2/.../collections` with `trust_remote_code=true` loading HuggingFace repos).
- **State:** affects 1.0.0–1.5.8; **there is no patched version** (1.5.9 is still affected). Lock pins 1.5.8.
- **Surface:** `server`. Prismal uses ChromaDB as an **embedded vector store (SQLite + local Chroma)**, **does not expose the HTTP server** → the pre-auth path is not reachable.
- **Mitigation:** (1) do not run `chroma run` / HTTP server; (2) if it is exposed in the future, disable `trust_remote_code` and add auth + an isolated network; (3) keep the ignore in `.trivyignore`/CI with this spec as reference. **Re-evaluation trigger:** when chromadb publishes a fix → remove the ignore and bump.

### SEC-A03 · trivy-action (#14 Critical) — SUPPLY-CHAIN
- **Advisory:** GHSA-69fq-xp46-6x23. On 2026-03-19, 76/77 tags of `aquasecurity/trivy-action` and the 7 of `aquasecurity/setup-trivy` were compromised with CI/CD secret-stealing malware; trivy v0.69.4 was trojanized.
- **Safe versions:** trivy-action **0.35.0**, setup-trivy **0.2.6**, trivy binary **0.69.2 / 0.69.3**.
- **Repo state:** `ci.yml` appears to **download the trivy binary via `curl`** (lines ~203–213), avoiding the action — verify that NO reference to `aquasecurity/trivy-action@<tag>` or `setup-trivy` remains.
- **Action:**
  1. `grep` in `.github/workflows/**` for `aquasecurity/trivy-action` and `aquasecurity/setup-trivy`.
  2. If used: pin to an **immutable SHA** of a safe version (not a mutable tag).
  3. If the binary is downloaded via `curl`, pin `TRIVY_VERSION` to 0.69.3 and verify the checksum.
  4. **Secret rotation:** if any CI run executed the action/binary during the compromised windows (Mar 19–20, 2026), rotate all tokens/secrets exposed to the runner.
- **Surface:** `ci`. Does not affect prismal's runtime but does affect the build chain.

### SEC-A04 · urllib3 (#6 #7 High) — RESOLVED in lock
- **CVEs:** CVE-2026-21441 / GHSA-38jv-5279-wg99 (decompression-bomb in streaming redirects, fix **2.6.3**); GHSA-qccp-gfcp-xxvc (sensitive headers forwarded cross-origin in ProxyManager redirects, fix **2.7.0**).
- **State:** lock pins **urllib3 2.7.0** → both resolved.
- **Surface:** `runtime` (transitive HTTP client). Validate and close.

### SEC-A05 · ecdsa (#1 High) — MITIGATE (won't-fix)
- **CVE:** CVE-2024-23342 / GHSA-wj6h-64fc-37mp (Minerva timing attack on P-256). The maintainer publicly stated that **there will be no fix** (it requires crypto in C).
- **State:** transitive (`python-jose → ecdsa`). Already in `.trivyignore` with justification.
- **Mitigation:** prismal does not perform sensitive hot-path ECDSA P-256 signatures; accept the residual risk. **Debt:** evaluate migrating `python-jose` → `PyJWT` to eliminate the `ecdsa` dependency (out of scope this cycle). Keep the ignore + trigger.

### SEC-A06 · langsmith (#9 #10 High) — RESOLVED in lock
- **CVE:** CVE-2026-45134 (public prompt pull deserializes untrusted manifests as executable config). Fix **langsmith 0.8.0** (JS 0.6.0).
- **State:** lock pins **langsmith 0.8.7 ≥ 0.8.0** → resolved. Alert #10 ("langchain-classic 1.0.7") is the same CVE via the transitive dependency on langsmith.
- **Surface:** `runtime`. Additional defense-in-depth mitigation: treat prompts pulled from the public Hub as untrusted content (already aligned with L1/`SecurePromptBuilder`). Validate and close both.

### SEC-A07 · idna (#11 Moderate) — RESOLVED in lock
- **CVE:** CVE-2026-45409 (ReDoS in `valid_contexto`, *bypass* of the incomplete fix for CVE-2024-3651). Fix **idna 3.15**.
- **State:** lock pins **idna 3.17 ≥ 3.15** → resolved. Complementary mitigation: 253-char limit before `idna.encode()` (defense in depth). Validate and close.

### SEC-A08 · starlette (#18 Moderate) — RESOLVED in lock
- **CVE:** CVE-2026-48710 ("BadHost": missing Host header validation poisons `request.url.path`). Fix **starlette 1.0.1**.
- **State:** lock pins **starlette 1.2.0 ≥ 1.0.1** → resolved. Transitive (via FastAPI/Prefect). Validate and close.

### SEC-A09 · aiohttp (#16 #17 Moderate) — UPGRADE
- **CVEs:** CVE-2026-34993 (RCE via `CookieJar.load()` with untrusted pickle) and CVE-2026-47265 (per-request cookies forwarded on cross-origin redirect). Both fix **aiohttp 3.14.0**.
- **State:** lock pins **aiohttp 3.13.5 < 3.14.0** → **requires upgrade**.
- **Surface:** `runtime` (MCP SSE transport, `aiohttp>=3.11.10` line in pyproject). #16 is only exploitable if the app calls `CookieJar.load()` with untrusted input (prismal does not); #17 applies to client redirects.
- **Action:** bump the constraint to `aiohttp>=3.14.0`, `uv lock`, run MCP integration tests. **Closure criterion:** `uv pip show aiohttp` ≥ 3.14.0 + green MCP tests.

### SEC-A10 · transformers (#2 Moderate) — MITIGATE/UPGRADE
- **CVE:** CVE-2026-1839 (RCE in `Trainer._load_rng_state()` via `torch.load()` without `weights_only=True`). Affects `torch>=2.2` with **PyTorch < 2.6**; fix in **transformers 5.0.0rc3**.
- **State:** lock pins **transformers 4.57.6**; the fix is in the 5.x series (major bump, breaking for `sentence-transformers`).
- **Surface:** `runtime` but **prismal does not use the `Trainer` class** (inference/embeddings only via `sentence-transformers`); the attack vector (loading a malicious `rng_state.pth`) is not exercised.
- **Preferred action (mitigation):** ensure **`torch>=2.6`** in the lock — with PyTorch ≥ 2.6, `safe_globals()` neutralizes the vector and the CVE is no longer exploitable, without a major transformers bump. **Alternative:** bump to `transformers>=5.0.0` once there is a stable release and `sentence-transformers` supports it. Document the mitigation in `.trivyignore` with a trigger.

### SEC-A11 · pymdown-extensions (#12 Moderate) — VERIFY
- **Lineage:** regression of the snippets *path traversal* (`GHSA-jh85-wwv9-24hv` / CVE-2023-32309) — `restrict_base_path` reintroduces the sibling-prefix bypass.
- **State:** lock pins **10.21.3**; confirm the exact new GHSA/CVE and its fix version against GHSA.
- **Surface:** `dev/docs`. It is a dependency of the documentation toolchain (mkdocs), not of prismal's runtime → low execution risk (only docs builds with snippets from untrusted sources).
- **Action:** confirm GHSA, bump to the patched version; if there is no fix yet, document it (snippets only over trusted repo sources).

### SEC-A12 · prefect (#13 Low) — UPGRADE/VERIFY
- **Advisory:** SSRF bypass via DNS-rebinding (TOCTOU) in `validate_restricted_url`; fix in PR #21591 (OSS-7874) which adds `SSRFProtected*HTTPTransport` and uses `getaddrinfo`.
- **State:** lock pins **prefect 3.6.27**; confirm the version that includes PR #21591 and bump.
- **Surface:** `runtime` but only affects **webhooks / `CustomWebhookNotificationBlock` with `allow_private_urls=False`**; prismal uses prefect as a flow orchestrator, not necessarily those notification blocks → low risk.
- **Action:** bump to the version with the fix; if there is no stable version yet, document it and do not use webhooks toward untrusted destinations.

---

## Ignore-list synchronization (expected result)

After remediation, the three sources of truth must remain consistent:

| File | Action |
|---|---|
| `.trivyignore` | Remove ignores for CVEs already resolved in the lock; keep only `chromadb` (CVE-2026-45829) and `ecdsa` (CVE-2024-23342) + any new no-fix entries, all with justification and trigger |
| `.pre-commit-config.yaml` (pip-audit hook) | Mirror of `.trivyignore` |
| `.github/workflows/ci.yml` (security-pip-audit) | Mirror of `.trivyignore`; + pin actions to SHA |

Golden rule (already documented in the repo): **any new ignore must be documented in `.trivyignore` AND in `ci.yml`/`pip-audit`**, with a reference to this spec.

---

## Global validation criteria

```bash
# 1. Version state after upgrades
uv sync && uv pip list | grep -E "aiohttp|transformers|prefect|pymdown|torch"

# 2. Clean SCA (with justified ignores)
uv run pip-audit ${PIP_AUDIT_IGNORES} --skip-editable
trivy fs --ignorefile .trivyignore .

# 3. Security lint of our own code
uv run bandit -r prismal -c pyproject.toml

# 4. No regressions
uv run pytest -m "not live_api"
```

Per-alert closure criterion: the CVE stops appearing in `pip-audit`/`trivy` **without** being in the ignore-list (for the remediated ones), or appears **with** a justified ignore + trigger (for the no-fix ones).

---

## Execution Result (2026-06-05/06)

The 18 alerts reached a terminal state — per-alert detail with evidence in
`remediation-tracker.csv`:

| Result | # | Alerts |
|---|---|---|
| CLOSED-RESOLVED (lock ≥ fix; closes with the lock push to main) | 12 | #3 #4 #5 #6 #7 #8 #9 #10 #11 #12 #18 |
| REMEDIATED-UPGRADE (aiohttp 3.14.0, prefect 3.7.4) | 3 | #16 #17 #13 |
| MITIGATED (no fix; documented ignore + trigger) | 3 | #15 chromadb, #1 ecdsa, #2 transformers (torch≥2.6) |
| CLOSED-SUPPLY-CHAIN (no exposure; checksum + pin SHA) | 1 | #14 |

Additional findings corrected during execution (pip-audit DB more recent than
Dependabot): pip 26.1.2 (PYSEC-2026-196), pyjwt 2.13.0
(PYSEC-2026-175/177/178/179).

GHSA confirmed at execution time: #8 = GHSA-wxxx-gvqv-xp7p; #9/#10 =
GHSA-3644-q5cj-c5c7; #11 = GHSA-65pc-fj4g-8rjx; #18 = GHSA-86qp-5c8j-p5mr;
#16 = GHSA-jg22-mg44-37j8; #17 = GHSA-hg6j-4rv6-33pg; #2 =
GHSA-69w3-r845-3855; #12 = CVE-2026-46338 / GHSA-62q4-447f-wv8h (fix =
10.21.3, exactly the lock version → reclassified RESOLVED); #13 =
CVE-2026-7724 / GHSA-p3pq-hxmr-vqqr (fix 3.6.28.dev2, lock → 3.7.4).

Final gates: `pip-audit` clean (4 no-fix ignores); `trivy fs uv.lock` = 0
non-ignored findings; `bandit` 0 medium/high; suite 2786 passed (19 pre-existing
failures verified identical with prefect 3.6.27 — unrelated to this
remediation).

Incident #14: no secret rotation — the GitHub Actions workflows have existed
since 2026-05-22, after the compromised window (Mar 19–20, 2026); verified via
git history.

## Change History

| Version | Date | Author | Changes |
|---|---|---|---|
| 1.0 | 2026-06-05 | Ernesto Crespo | Initial matrix of 18 alerts with CVE, fix version, and action |
| 1.1 | 2026-06-06 | Ernesto Crespo + Claude | Execution completed — 18/18 in terminal state; spec `IMPLEMENTED` |

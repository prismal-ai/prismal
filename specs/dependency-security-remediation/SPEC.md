# Prismal Dependency Security Remediation — Matriz de Remediación (SPEC)

## Metadata

| Campo | Valor |
|---|---|
| **Autor** | Ernesto Crespo |
| **Estado** | `IMPLEMENTED` |
| **Versión** | 1.0 |
| **Fecha** | 2026-06-05 |
| **PLAN** | `specs/dependency-security-remediation/PLAN.md` |
| **Architecture** | `specs/dependency-security-remediation/ARCHITECTURE.md` |
| **TASKS** | `specs/dependency-security-remediation/TASKS.md` |

---

## Convenciones

- **Versión actual** = la fijada hoy en `uv.lock` (verificada 2026-06-05).
- **Versión fix** = primera versión parcheada según GitHub Advisory Database (GHSA) / GLAD a 2026-06-05.
- **Estado**: `RESUELTA` (lock ≥ fix) · `UPGRADE` (lock < fix, hay fix) · `MITIGAR` (sin fix upstream) · `SUPPLY-CHAIN`.
- **Surface**: `runtime` (afecta el uso real de prismal) · `server` (solo si se ejecuta un servidor que prismal NO levanta) · `dev/docs` (toolchain) · `ci` (workflow).
- Las versiones fix deben **re-verificarse contra GHSA al ejecutar** (este spec está fechado).

---

## Resumen por severidad

| Severidad | # | Estado dominante |
|---|---|---|
| Critical | 3 | 1 resuelta (litellm), 1 mitigar (chromadb), 1 supply-chain (trivy) |
| High | 8 | 6 resueltas (litellm×3, urllib3×2, langsmith×1*), 1 mitigar (ecdsa), 1 resuelta (langsmith/langchain-classic) |
| Moderate | 6 | 2 upgrade (aiohttp×2), 1 mitigar/upgrade (transformers), 1 resuelta (idna), 1 resuelta (starlette), 1 verificar (pymdown) |
| Low | 1 | 1 upgrade/verificar (prefect) |

\* Las dos alertas de LangSmith (#9 langsmith, #10 langchain-classic) son la misma CVE.

---

## Matriz completa (18 alertas)

| # | Paquete | Sev | CVE / GHSA | Actual | Fix | Estado | Surface | Acción |
|---|---|---|---|---|---|---|---|---|
| 4 | litellm | Critical | CVE-2026-42208 / GHSA-r75f-5x8p-qvmc | 1.86.2 | 1.83.7 | RESUELTA | server | Verificar + cerrar |
| 15 | chromadb | Critical | CVE-2026-45829 / GHSA-f4j7-r4q5-qw2c | 1.5.8 | — (sin fix) | MITIGAR | server | Uso embebido; no levantar server HTTP; monitor |
| 14 | aquasecurity/trivy-action | Critical | GHSA-69fq-xp46-6x23 | (ci.yml) | trivy-action 0.35.0 / setup-trivy 0.2.6 / trivy 0.69.2–0.69.3 | SUPPLY-CHAIN | ci | Verificar referencia + pin SHA + rotar secretos si aplica |
| 5 | litellm | High | (cluster X41 / 1.83.x — confirmar GHSA) | 1.86.2 | ~1.83.x | RESUELTA | server | Verificar + cerrar |
| 3 | litellm | High | (SSTI /prompts/test — confirmar GHSA) | 1.86.2 | ~1.83.x | RESUELTA | server | Verificar + cerrar |
| 8 | litellm | High | CVE-2026-40217 (X41-2026-001) | 1.86.2 | 1.83.10 | RESUELTA | server | Verificar + cerrar |
| 7 | urllib3 | High | CVE-2026-21441 / GHSA-38jv-5279-wg99 | 2.7.0 | 2.6.3 | RESUELTA | runtime | Verificar + cerrar |
| 6 | urllib3 | High | GHSA-qccp-gfcp-xxvc | 2.7.0 | 2.7.0 | RESUELTA | runtime | Verificar + cerrar |
| 1 | ecdsa | High | CVE-2024-23342 / GHSA-wj6h-64fc-37mp | 0.19.2 | — (won't-fix) | MITIGAR | runtime (transitiva) | Aceptar + deuda migración PyJWT |
| 9 | langsmith | High | CVE-2026-45134 | 0.8.7 | 0.8.0 | RESUELTA | runtime | Verificar + cerrar |
| 10 | langchain-classic→langsmith | High | CVE-2026-45134 | 1.0.7 / 0.8.7 | langsmith 0.8.0 | RESUELTA | runtime | Verificar + cerrar (misma CVE que #9) |
| 11 | idna | Moderate | CVE-2026-45409 (bypass de CVE-2024-3651) | 3.17 | 3.15 | RESUELTA | runtime (transitiva) | Verificar + cerrar |
| 18 | starlette | Moderate | CVE-2026-48710 (BadHost) | 1.2.0 | 1.0.1 | RESUELTA | server (transitiva) | Verificar + cerrar |
| 17 | aiohttp | Moderate | CVE-2026-47265 | 3.13.5 | 3.14.0 | UPGRADE | runtime | Subir a ≥ 3.14.0 |
| 16 | aiohttp | Moderate | CVE-2026-34993 | 3.13.5 | 3.14.0 | UPGRADE | runtime | Subir a ≥ 3.14.0 |
| 2 | transformers | Moderate | CVE-2026-1839 | 4.57.6 | 5.0.0rc3 | MITIGAR/UPGRADE | runtime (no usa Trainer) | Mitigar `torch≥2.6`; o subir a 5.x estable |
| 12 | pymdown-extensions | Moderate | (regresión de GHSA-jh85-wwv9-24hv / CVE-2023-32309) | 10.21.3 | (confirmar GHSA) | VERIFICAR | dev/docs | Confirmar GHSA y subir; si es dep de docs, riesgo bajo |
| 13 | prefect | Low | SSRF DNS-rebinding (PR #21591 / OSS-7874) | 3.6.27 | (confirmar versión con PR #21591) | UPGRADE | runtime (webhooks) | Subir a versión con fix; solo afecta webhooks con `allow_private_urls=False` |

---

## Detalle por alerta

### SEC-A01 · litellm (#4 Critical, #3 #5 #8 High) — RESUELTAS en lock
- **CVE confirmadas:** CVE-2026-42208 (SQLi proxy API key, fix 1.83.7), CVE-2026-40217 (sandbox escape custom-code guardrail, fix 1.83.10). #3 (SSTI `/prompts/test`) y #5 (MCP stdio test endpoints) pertenecen al mismo *cluster* de disclosures de superficie de proxy, parcheado en la serie 1.83.x.
- **Estado:** `uv.lock` fija **litellm 1.86.2 ≥ 1.83.10** → las cuatro están resueltas.
- **Surface:** `server`. Todas afectan el **LiteLLM Proxy Server** (`/v1/*`, `/prompts/test`, `/guardrails/test_custom_code`, endpoints MCP stdio de test). Prismal usa litellm como **librería cliente** (`providers/` wrapper), **no levanta el proxy**, por lo que no están en su surface de ejecución.
- **Validación de cierre:** `uv pip show litellm` ≥ 1.83.10; `pip-audit` no reporta las CVE; confirmar GHSA exacto de #3/#5 y registrar.

### SEC-A02 · chromadb (#15 Critical) — MITIGAR (sin fix)
- **CVE:** CVE-2026-45829 / GHSA-f4j7-r4q5-qw2c, CVSS 10.0. RCE pre-auth en el **servidor FastAPI** de ChromaDB (`/api/v2/.../collections` con `trust_remote_code=true` cargando repos HuggingFace).
- **Estado:** afecta 1.0.0–1.5.8; **no hay versión parcheada** (1.5.9 sigue afectada). Lock fija 1.5.8.
- **Surface:** `server`. Prismal usa ChromaDB como **vector store embebido (SQLite + Chroma local)**, **no expone el servidor HTTP** → la ruta pre-auth no es alcanzable.
- **Mitigación:** (1) no ejecutar `chroma run` / servidor HTTP; (2) si en el futuro se expone, deshabilitar `trust_remote_code` y poner auth + red aislada; (3) mantener el ignore en `.trivyignore`/CI con este spec como referencia. **Trigger de re-evaluación:** cuando chromadb publique fix → quitar ignore y subir.

### SEC-A03 · trivy-action (#14 Critical) — SUPPLY-CHAIN
- **Advisory:** GHSA-69fq-xp46-6x23. El 19-mar-2026 se comprometieron 76/77 tags de `aquasecurity/trivy-action` y los 7 de `aquasecurity/setup-trivy` con malware ladrón de secretos de CI/CD; trivy v0.69.4 fue troyanizado.
- **Versiones seguras:** trivy-action **0.35.0**, setup-trivy **0.2.6**, binario trivy **0.69.2 / 0.69.3**.
- **Estado del repo:** `ci.yml` parece **descargar el binario de trivy vía `curl`** (líneas ~203–213), evitando la action — verificar que NO quede ninguna referencia a `aquasecurity/trivy-action@<tag>` ni `setup-trivy`.
- **Acción:**
  1. `grep` en `.github/workflows/**` por `aquasecurity/trivy-action` y `aquasecurity/setup-trivy`.
  2. Si se usa: pinear a **SHA inmutable** de una versión segura (no tag mutable).
  3. Si el binario se descarga por `curl`, fijar `TRIVY_VERSION` a 0.69.3 y verificar checksum.
  4. **Rotación de secretos:** si algún run de CI ejecutó la action/binario en las ventanas comprometidas (19–20 mar 2026), rotar todos los tokens/secretos expuestos al runner.
- **Surface:** `ci`. No afecta el runtime de prismal pero sí la cadena de build.

### SEC-A04 · urllib3 (#6 #7 High) — RESUELTAS en lock
- **CVEs:** CVE-2026-21441 / GHSA-38jv-5279-wg99 (decompression-bomb en redirects streaming, fix **2.6.3**); GHSA-qccp-gfcp-xxvc (headers sensibles reenviados cross-origin en redirects de ProxyManager, fix **2.7.0**).
- **Estado:** lock fija **urllib3 2.7.0** → ambas resueltas.
- **Surface:** `runtime` (cliente HTTP transitivo). Validar y cerrar.

### SEC-A05 · ecdsa (#1 High) — MITIGAR (won't-fix)
- **CVE:** CVE-2024-23342 / GHSA-wj6h-64fc-37mp (Minerva timing attack en P-256). El mantenedor declaró públicamente que **no habrá fix** (requiere cripto en C).
- **Estado:** transitiva (`python-jose → ecdsa`). Ya en `.trivyignore` con justificación.
- **Mitigación:** prismal no realiza firmas ECDSA P-256 sensibles en caliente; aceptar el riesgo residual. **Deuda:** evaluar migrar `python-jose` → `PyJWT` para eliminar la dependencia de `ecdsa` (fuera de alcance este ciclo). Mantener ignore + trigger.

### SEC-A06 · langsmith (#9 #10 High) — RESUELTAS en lock
- **CVE:** CVE-2026-45134 (pull público de prompts deserializa manifiestos no confiables como config ejecutable). Fix **langsmith 0.8.0** (JS 0.6.0).
- **Estado:** lock fija **langsmith 0.8.7 ≥ 0.8.0** → resuelta. La alerta #10 ("langchain-classic 1.0.7") es la misma CVE vía la dependencia transitiva a langsmith.
- **Surface:** `runtime`. Mitigación adicional de defensa en profundidad: tratar prompts traídos de Hub público como contenido no confiable (ya alineado con L1/`SecurePromptBuilder`). Validar y cerrar ambas.

### SEC-A07 · idna (#11 Moderate) — RESUELTA en lock
- **CVE:** CVE-2026-45409 (ReDoS en `valid_contexto`, *bypass* del fix incompleto de CVE-2024-3651). Fix **idna 3.15**.
- **Estado:** lock fija **idna 3.17 ≥ 3.15** → resuelta. Mitigación complementaria: límite de 253 chars antes de `idna.encode()` (defensa en profundidad). Validar y cerrar.

### SEC-A08 · starlette (#18 Moderate) — RESUELTA en lock
- **CVE:** CVE-2026-48710 ("BadHost": falta validación del header Host envenena `request.url.path`). Fix **starlette 1.0.1**.
- **Estado:** lock fija **starlette 1.2.0 ≥ 1.0.1** → resuelta. Transitiva (vía FastAPI/Prefect). Validar y cerrar.

### SEC-A09 · aiohttp (#16 #17 Moderate) — UPGRADE
- **CVEs:** CVE-2026-34993 (RCE vía `CookieJar.load()` con pickle no confiable) y CVE-2026-47265 (cookies per-request reenviadas en redirect cross-origin). Ambas fix **aiohttp 3.14.0**.
- **Estado:** lock fija **aiohttp 3.13.5 < 3.14.0** → **requiere upgrade**.
- **Surface:** `runtime` (transporte MCP SSE, línea `aiohttp>=3.11.10` en pyproject). #16 solo es explotable si la app llama `CookieJar.load()` con input no confiable (prismal no lo hace); #17 aplica a redirects de cliente.
- **Acción:** subir constraint a `aiohttp>=3.14.0`, `uv lock`, correr tests de integración MCP. **Criterio de cierre:** `uv pip show aiohttp` ≥ 3.14.0 + tests MCP verdes.

### SEC-A10 · transformers (#2 Moderate) — MITIGAR/UPGRADE
- **CVE:** CVE-2026-1839 (RCE en `Trainer._load_rng_state()` vía `torch.load()` sin `weights_only=True`). Afecta `torch>=2.2` con **PyTorch < 2.6**; fix en **transformers 5.0.0rc3**.
- **Estado:** lock fija **transformers 4.57.6**; el fix está en la serie 5.x (bump mayor, breaking para `sentence-transformers`).
- **Surface:** `runtime` pero **prismal no usa la clase `Trainer`** (solo inferencia/embeddings vía `sentence-transformers`); el vector de ataque (cargar `rng_state.pth` malicioso) no se ejercita.
- **Acción preferida (mitigación):** garantizar **`torch>=2.6`** en el lock — con PyTorch ≥ 2.6 el `safe_globals()` neutraliza el vector y la CVE deja de ser explotable, sin bump mayor de transformers. **Alternativa:** subir a `transformers>=5.0.0` cuando haya release estable y `sentence-transformers` lo soporte. Documentar la mitigación en `.trivyignore` con trigger.

### SEC-A11 · pymdown-extensions (#12 Moderate) — VERIFICAR
- **Lineage:** regresión del *path traversal* de snippets (`GHSA-jh85-wwv9-24hv` / CVE-2023-32309) — `restrict_base_path` reintroduce el bypass de prefijo hermano.
- **Estado:** lock fija **10.21.3**; confirmar el GHSA/CVE nuevo exacto y su versión fix contra GHSA.
- **Surface:** `dev/docs`. Es dependencia del toolchain de documentación (mkdocs), no del runtime de prismal → riesgo de ejecución bajo (solo build de docs con snippets de fuentes no confiables).
- **Acción:** confirmar GHSA, subir a la versión parcheada; si no hay fix aún, documentar (snippets solo sobre fuentes confiables del repo).

### SEC-A12 · prefect (#13 Low) — UPGRADE/VERIFICAR
- **Advisory:** SSRF bypass por DNS-rebinding (TOCTOU) en `validate_restricted_url`; fix en PR #21591 (OSS-7874) que añade `SSRFProtected*HTTPTransport` y usa `getaddrinfo`.
- **Estado:** lock fija **prefect 3.6.27**; confirmar la versión que incluye el PR #21591 y subir.
- **Surface:** `runtime` pero solo afecta **webhooks / `CustomWebhookNotificationBlock` con `allow_private_urls=False`**; prismal usa prefect como orquestador de flows, no necesariamente esos bloques de notificación → riesgo bajo.
- **Acción:** subir a la versión con fix; si no hay versión estable aún, documentar y no usar webhooks hacia destinos no confiables.

---

## Sincronización de ignore-lists (resultado esperado)

Tras la remediación, los tres puntos de verdad deben quedar coherentes:

| Archivo | Acción |
|---|---|
| `.trivyignore` | Quitar ignores de CVEs ya resueltas en el lock; mantener solo `chromadb` (CVE-2026-45829) y `ecdsa` (CVE-2024-23342) + cualquier sin-fix nuevo, todos con justificación y trigger |
| `.pre-commit-config.yaml` (hook pip-audit) | Espejo del `.trivyignore` |
| `.github/workflows/ci.yml` (security-pip-audit) | Espejo del `.trivyignore`; + pin de actions a SHA |

Regla de oro (ya documentada en el repo): **cualquier ignore nuevo debe documentarse en `.trivyignore` Y en `ci.yml`/`pip-audit`**, con referencia a este spec.

---

## Criterios de validación globales

```bash
# 1. Estado de versiones tras upgrades
uv sync && uv pip list | grep -E "aiohttp|transformers|prefect|pymdown|torch"

# 2. SCA limpio (con ignores justificados)
uv run pip-audit ${PIP_AUDIT_IGNORES} --skip-editable
trivy fs --ignorefile .trivyignore .

# 3. Lint de seguridad del código propio
uv run bandit -r prismal -c pyproject.toml

# 4. Sin regresiones
uv run pytest -m "not live_api"
```

Criterio de cierre por alerta: la CVE deja de aparecer en `pip-audit`/`trivy` **sin** estar en el ignore-list (para las remediadas), o aparece **con** ignore justificado + trigger (para las sin-fix).

---

## Resultado de Ejecución (2026-06-05/06)

Las 18 alertas alcanzaron estado terminal — detalle por alerta con evidencia en
`remediation-tracker.csv`:

| Resultado | # | Alertas |
|---|---|---|
| CERRADA-RESUELTA (lock ≥ fix; cierra con el push del lock a main) | 12 | #3 #4 #5 #6 #7 #8 #9 #10 #11 #12 #18 |
| REMEDIADA-UPGRADE (aiohttp 3.14.0, prefect 3.7.4) | 3 | #16 #17 #13 |
| MITIGADA (sin fix; ignore documentado + trigger) | 3 | #15 chromadb, #1 ecdsa, #2 transformers (torch≥2.6) |
| CERRADA-SUPPLY-CHAIN (sin exposición; checksum + pin SHA) | 1 | #14 |

Hallazgos adicionales corregidos durante la ejecución (DB de pip-audit más
reciente que Dependabot): pip 26.1.2 (PYSEC-2026-196), pyjwt 2.13.0
(PYSEC-2026-175/177/178/179).

GHSA confirmados al ejecutar: #8 = GHSA-wxxx-gvqv-xp7p; #9/#10 =
GHSA-3644-q5cj-c5c7; #11 = GHSA-65pc-fj4g-8rjx; #18 = GHSA-86qp-5c8j-p5mr;
#16 = GHSA-jg22-mg44-37j8; #17 = GHSA-hg6j-4rv6-33pg; #2 =
GHSA-69w3-r845-3855; #12 = CVE-2026-46338 / GHSA-62q4-447f-wv8h (fix =
10.21.3, exactamente la versión del lock → reclasificada RESUELTA); #13 =
CVE-2026-7724 / GHSA-p3pq-hxmr-vqqr (fix 3.6.28.dev2, lock → 3.7.4).

Gates finales: `pip-audit` limpio (4 ignores sin-fix); `trivy fs uv.lock` = 0
hallazgos no ignorados; `bandit` 0 medium/high; suite 2786 passed (19 fallos
preexistentes verificados idénticos con prefect 3.6.27 — ajenos a esta
remediación).

Incidente #14: sin rotación de secretos — los workflows de GitHub Actions
existen desde 2026-05-22, posterior a la ventana comprometida (19–20 mar 2026);
verificado vía historial git.

## Historial de Cambios

| Versión | Fecha | Autor | Cambios |
|---|---|---|---|
| 1.0 | 2026-06-05 | Ernesto Crespo | Matriz inicial de 18 alertas con CVE, versión fix y acción |
| 1.1 | 2026-06-06 | Ernesto Crespo + Claude | Ejecución completada — 18/18 en estado terminal; spec `IMPLEMENTED` |

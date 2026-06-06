# Prismal Dependency Security Remediation — Plan de Ejecución (TASKS)

## Metadata

| Campo | Valor |
|---|---|
| **Autor** | Ernesto Crespo |
| **Estado** | `DRAFT` |
| **Versión** | 1.0 |
| **Fecha** | 2026-06-05 |
| **PLAN** | `specs/dependency-security-remediation/PLAN.md` |
| **Architecture** | `specs/dependency-security-remediation/ARCHITECTURE.md` |
| **SPEC** | `specs/dependency-security-remediation/SPEC.md` |
| **Prioridad** | **ALTA** |

---

## 1. Resumen de Ejecución

18 alertas Dependabot → 3 olas priorizadas por riesgo. **P0** cierra el grueso del ruido (las ~11 ya resueltas en el lock) y atiende lo realmente urgente (cadena de suministro + documentar sin-fix). **P1** ejecuta los upgrades reales. **P2** sincroniza ignore-lists y valida.

> Las versiones fix se basan en GHSA a 2026-06-05. **Re-verificar contra GitHub Advisory Database al ejecutar.**

---

## 2. Pre-requisitos

- [ ] Acceso a la rama que Dependabot escanea (para push del `uv.lock`).
- [ ] `uv`, `pip-audit`, `trivy`, `bandit` disponibles en el entorno.
- [ ] Permisos para rotar secretos de CI (si el incidente `trivy-action` lo requiere).
- [ ] Snapshot/commit limpio del `uv.lock` actual como punto de rollback.

---

## 3. Fases de Ejecución

### OLA P0 — Crítico, supply-chain y cierre rápido (1–2 días)

#### P0-01 — Push del `uv.lock` y cierre de las ya resueltas (≈11 alertas)
- [ ] Confirmar versiones: `uv pip show litellm urllib3 langsmith idna starlette` (litellm ≥1.83.10, urllib3 ≥2.7.0, langsmith ≥0.8.0, idna ≥3.15, starlette ≥1.0.1).
- [ ] Asegurar `uv.lock` commiteado y pusheado a la rama escaneada.
- [ ] Cerrar (o dejar auto-cerrar) las alertas #3, #4, #5, #6, #7, #8, #9, #10, #11, #18 citando "fixed in <ver>".
- **Done:** las 10–11 alertas marcadas como resueltas en Dependabot.

#### P0-02 — Incidente cadena de suministro `trivy-action` (#14)
- [ ] `grep -rn "aquasecurity/trivy-action\|aquasecurity/setup-trivy" .github/workflows/`.
- [ ] Si aparece: pinear a **SHA inmutable** de versión segura (trivy-action 0.35.0 / setup-trivy 0.2.6).
- [ ] Si se descarga binario por `curl`: fijar `TRIVY_VERSION=0.69.3` + verificar checksum/firma; confirmar que NO se usó 0.69.4.
- [ ] Revisar runs de CI entre 19–20 mar 2026; si hubo ejecución de la action/binario comprometido → **rotar todos los secretos del runner** (tokens GH, claves de registry, secretos de despliegue).
- [ ] Cerrar #14 documentando la verificación.
- **Done:** workflow sin referencias mutables a tags comprometidos; rotación hecha si aplicaba.

#### P0-03 — Documentar mitigaciones sin-fix (chromadb #15, ecdsa #1)
- [ ] Confirmar que `chromadb` se usa solo embebido (sin `chroma run` / server HTTP).
- [ ] Mantener `CVE-2026-45829` y `CVE-2024-23342` en `.trivyignore` con: razón, surface, referencia a `SPEC.md`, y **trigger** de re-evaluación.
- [ ] Registrar deuda: migración `python-jose` → `PyJWT` (issue de seguimiento).
- **Done:** ambas alertas con mitigación documentada y trigger; #15 y #1 marcadas "mitigated/won't-fix".

---

### OLA P1 — Upgrades reales (3–5 días)

#### P1-01 — `aiohttp` ≥ 3.14.0 (#16, #17)
- [ ] `pyproject.toml`: `aiohttp>=3.14.0`.
- [ ] `uv lock` → revisar diff de transitivas.
- [ ] `uv sync` → correr tests de integración MCP (transporte SSE).
- [ ] `pip-audit` ya no reporta CVE-2026-34993 / CVE-2026-47265.
- **Done:** aiohttp ≥3.14.0 + tests MCP verdes; #16 #17 cerradas.

#### P1-02 — `transformers` / mitigación por `torch≥2.6` (#2)
- [ ] Verificar versión de `torch` en el lock; si < 2.6, subir `torch>=2.6` en `pyproject.toml`.
- [ ] `uv lock` → confirmar resolución sin downgrades problemáticos de `sentence-transformers`.
- [ ] Si la mitigación por torch no es viable: evaluar `transformers>=5.0.0` (validar `sentence-transformers`).
- [ ] Documentar en `.trivyignore` (mitigado por torch≥2.6) con trigger, o quitar si se subió a 5.x.
- **Done:** vector CVE-2026-1839 neutralizado (torch≥2.6) o transformers en 5.x; #2 cerrada/mitigada.

#### P1-03 — `prefect` SSRF (#13)
- [ ] Confirmar contra GHSA la primera versión de `prefect` que incluye el PR #21591 (OSS-7874).
- [ ] Subir `prefect>=<ver_fix>` en `pyproject.toml`; `uv lock` + `uv sync`.
- [ ] Correr tests del scheduler (`prismal/scheduler/`) — APScheduler/Prefect flows.
- [ ] Si no hay versión estable con el fix: documentar (no usar webhooks a destinos no confiables) + ignore con trigger.
- **Done:** prefect en versión con fix o mitigación documentada; #13 cerrada/mitigada.

#### P1-04 — `pymdown-extensions` snippets (#12)
- [ ] Confirmar el GHSA/CVE exacto de la regresión y su versión fix.
- [ ] Subir `pymdown-extensions>=<ver_fix>` (dep de docs); `uv lock`.
- [ ] Verificar que el build de docs (mkdocs) sigue OK.
- [ ] Si no hay fix: documentar (snippets solo sobre fuentes confiables del repo) + ignore.
- **Done:** pymdown remediado o decisión documentada; #12 cerrada/mitigada.

---

### OLA P2 — Higiene y validación (1–2 días)

#### P2-01 — Sincronizar ignore-lists
- [ ] `.trivyignore`: quitar IDs de CVEs ya resueltas por el lock; conservar solo sin-fix (chromadb, ecdsa, + nuevos) con justificación + trigger.
- [ ] `.pre-commit-config.yaml` (hook pip-audit): espejo exacto del `.trivyignore`.
- [ ] `.github/workflows/ci.yml` (security-pip-audit): espejo exacto + pin de actions a SHA.
- [ ] (Opcional) test/script de consistencia que verifique que los tres listan el mismo set.
- **Done:** tres fuentes espejadas; 0 ignores sin justificación.

#### P2-02 — Pin de GitHub Actions a SHA (deuda P0-02 → política)
- [ ] Pinear todas las actions de `.github/workflows/**` a SHA inmutable (no solo trivy).
- **Done:** 0 actions con tag mutable.

#### P2-03 — Validación final
- [ ] `uv run pip-audit ${PIP_AUDIT_IGNORES} --skip-editable` limpio.
- [ ] `trivy fs --ignorefile .trivyignore .` limpio.
- [ ] `uv run bandit -r prismal -c pyproject.toml` limpio.
- [ ] `uv run pytest -m "not live_api"` 100%.
- [ ] `uv run mypy prismal` + `uv run ruff check .` sin regresiones.
- **Done:** todos los gates verdes.

#### P2-04 — Documentación y cierre
- [ ] Entrada de seguridad en `CHANGELOG.md` (CVEs remediadas + mitigadas).
- [ ] Marcar este spec `IMPLEMENTED`; actualizar la tabla de estado por alerta en `SPEC.md`.
- [ ] Confirmar 18/18 alertas en estado terminal en Dependabot.
- **Done:** trazabilidad completa.

---

## 4. Dependencias Inter-Tareas

```
P0-01 (push lock)         → cierra ~11 alertas, desbloquea claridad del trabajo real
P0-02 (trivy-action)      → independiente; P0 por riesgo de exfiltración
P0-03 (chromadb/ecdsa)    → independiente
P1-01..04 (upgrades)      → tras P0-01; cada uno aislado y revertible
P2-01 (sync ignores)      → tras P1 (depende del estado final de versiones)
P2-02 (pin SHA)           → tras/junto P0-02
P2-03 (validación)        → tras P1 + P2-01
P2-04 (cierre)            → último
```

Ruta crítica de riesgo: **P0-02 (supply-chain)** → rotación de secretos si aplica. Ruta crítica de volumen: **P0-01 (push lock)**.

---

## 5. Matriz de Tareas ↔ Alertas

| Tarea | Alertas | Estado objetivo |
|---|---|---|
| P0-01 | #3 #4 #5 #6 #7 #8 #9 #10 #11 #18 | RESUELTA (cerrada) |
| P0-02 | #14 | SUPPLY-CHAIN (cerrada) |
| P0-03 | #15 #1 | MITIGAR (documentada) |
| P1-01 | #16 #17 | UPGRADE (cerrada) |
| P1-02 | #2 | MITIGAR/UPGRADE |
| P1-03 | #13 | UPGRADE/mitigar |
| P1-04 | #12 | UPGRADE/verificar |

Cobertura: 18/18 alertas asignadas a una tarea.

---

## 6. Matriz de Riesgos

| Riesgo | Mitigación | Tarea |
|---|---|---|
| `aiohttp` 3.14 rompe transporte MCP | Tests integración MCP; commit revertible | P1-01 |
| `torch≥2.6` no resuelve con el lock | Fallback a documentar + evaluar transformers 5.x | P1-02 |
| Secretos CI comprometidos | Rotación P0 si hubo ejecución en ventana | P0-02 |
| Ignores obsoletos persisten | Sync triplicado + script de consistencia | P2-01 |
| Versión fix cambió tras el spec | Re-verificar GHSA al ejecutar | todas |
| Lock no se empuja y alertas siguen abiertas | P0-01 explícito como primer paso | P0-01 |

---

## 7. Definición de Done (Global)

- [ ] 18/18 alertas en estado terminal (cerrada / remediada / mitigada-documentada).
- [ ] `uv.lock` empujado; ~11 resueltas cerradas en Dependabot.
- [ ] `aiohttp ≥ 3.14.0`; CVE-2026-1839 neutralizada (torch≥2.6) o transformers 5.x; prefect y pymdown remediados o documentados.
- [ ] chromadb + ecdsa con mitigación + trigger en `.trivyignore`.
- [ ] Incidente trivy-action cerrado; actions pineadas a SHA; secretos rotados si aplicó.
- [ ] `.trivyignore` / pip-audit / ci.yml sincronizados; 0 ignores sin justificación.
- [ ] `pip-audit` + `trivy` + `bandit` + `pytest -m "not live_api"` verdes.
- [ ] `CHANGELOG.md` actualizado; spec `IMPLEMENTED`.

---

## 8. Comandos de Referencia

```bash
# Verificar versiones actuales
uv pip show aiohttp transformers prefect pymdown-extensions torch litellm urllib3 langsmith idna starlette

# Aplicar upgrades (ejemplo aiohttp)
#   editar pyproject.toml -> aiohttp>=3.14.0
uv lock && uv sync

# SCA
uv run pip-audit ${PIP_AUDIT_IGNORES} --skip-editable
trivy fs --ignorefile .trivyignore .

# Lint de seguridad + tests
uv run bandit -r prismal -c pyproject.toml
uv run pytest -m "not live_api"

# Supply-chain check
grep -rn "aquasecurity/trivy-action\|aquasecurity/setup-trivy" .github/workflows/
```

---

## Historial de Cambios

| Versión | Fecha | Autor | Cambios |
|---|---|---|---|
| 1.0 | 2026-06-05 | Ernesto Crespo | Plan de ejecución inicial — 3 olas, 18 alertas |

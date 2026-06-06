# Prismal Dependency Security Remediation — Metodología de Triage y Remediación

## Metadata

| Campo | Valor |
|---|---|
| **Autor** | Ernesto Crespo |
| **Estado** | `DRAFT` |
| **Versión** | 1.0 |
| **Fecha** | 2026-06-05 |
| **PLAN** | `specs/dependency-security-remediation/PLAN.md` |
| **SPEC** | `specs/dependency-security-remediation/SPEC.md` |
| **TASKS** | `specs/dependency-security-remediation/TASKS.md` |
| **Reviewers** | Tech Lead, Security Lead |

---

## 1. Contexto

Dependabot reportó 18 alertas contra `uv.lock` y `.github/workflows/ci.yml`. A diferencia de un *feature* con superficie de API, esto es un **esfuerzo de remediación de dependencias**: no se diseña código nuevo, se decide y ejecuta por cada CVE una de cuatro acciones (cerrar / actualizar / mitigar / responder a cadena de suministro). Este documento define la **metodología de triage** que produce la matriz de `SPEC.md` y el plan de `TASKS.md`, de modo que las decisiones sean reproducibles y auditables, no ad-hoc.

El repo ya opera un proceso de SCA (Software Composition Analysis): `pip-audit` (pre-commit + CI), `trivy` (`.trivyignore`), `bandit`, y triage vía `prismal doctor security-check`. Esta metodología se monta sobre ese proceso.

---

## 2. Objetivos Técnicos

- **OT-1:** Clasificar cada alerta de forma determinista (resuelta / upgrade / mitigar / supply-chain).
- **OT-2:** Distinguir **riesgo nominal** (por versión, lo que ve Dependabot) de **riesgo efectivo** (por *surface* real de prismal).
- **OT-3:** No introducir regresiones: todo upgrade pasa por `uv lock` + suite de tests + SCA.
- **OT-4:** Dejar trazabilidad versionada (este spec) y sincronizar los tres ignore-lists.
- **OT-5:** Tratar el incidente de cadena de suministro como un flujo aparte (verificación + rotación), no como un simple bump.

---

## 3. Modelo de Decisión

### 3.1 Árbol de clasificación por alerta

```
Para cada alerta:
  ¿La versión en uv.lock ≥ versión parcheada (GHSA)?
    ├─ SÍ  → RESUELTA  → push del lock + verificar + cerrar alerta
    └─ NO  → ¿Existe versión parcheada compatible con el stack?
              ├─ SÍ  → UPGRADE → bump constraint + uv lock + validar
              └─ NO  → ¿Es un won't-fix o sin parche?
                        ├─ Sí → MITIGAR → análisis de surface + ignore documentado + trigger
                        └─ (caso CI) → SUPPLY-CHAIN → verificar referencia + pin SHA + rotar secretos
```

### 3.2 Análisis de exposición (riesgo nominal vs efectivo)

El paso clave que Dependabot **no** hace: ¿la ruta vulnerable está en el *surface* de prismal?

| Surface | Definición | Ejemplos en este reporte | Efecto en prioridad |
|---|---|---|---|
| `runtime` | Código que prismal ejecuta en producción | urllib3, aiohttp, langsmith, idna, ecdsa | Prioridad según severidad real |
| `server` | Vulnerabilidad en un servidor que prismal **no levanta** (usa la lib como cliente/embebido) | LiteLLM Proxy (SQLi, SSTI, MCP stdio, guardrail), ChromaDB FastAPI (RCE pre-auth), Starlette (BadHost) | Riesgo efectivo bajo; remediar por higiene |
| `dev/docs` | Toolchain de desarrollo o documentación | pymdown-extensions | Riesgo bajo; build-time only |
| `ci` | Workflow de integración continua | trivy-action | Riesgo de cadena de build; tratar como P0 |

**Principio:** la severidad de Dependabot fija el *orden de revisión*; el análisis de surface ajusta el *riesgo efectivo* y la urgencia de la acción. Una Critical de `server` que prismal no expone (chromadb) tiene menor riesgo efectivo que una Moderate de `runtime` que sí se ejercita (aiohttp).

### 3.3 Matriz de acción

```
                 hay fix          sin fix
              ┌───────────────┬──────────────────┐
 lock ≥ fix   │  RESUELTA     │   (n/a)          │
              ├───────────────┼──────────────────┤
 lock < fix   │  UPGRADE      │   MITIGAR        │
              └───────────────┴──────────────────┘
   caso CI / actions  →  SUPPLY-CHAIN (flujo propio)
```

---

## 4. Flujos de Remediación

### Flujo A — RESUELTA (push del lock + cierre)
```
1. Confirmar uv.lock >= fix:  uv pip show <pkg>
2. Asegurar que el uv.lock está commiteado y pusheado a la rama que Dependabot escanea.
3. Dependabot re-escanea y auto-cierra; si no, cerrar manualmente citando "fixed in <ver>".
4. Quitar el ignore correspondiente de .trivyignore/CI si existía.
```
Aplica a: litellm×4, urllib3×2, langsmith×2, idna, starlette (≈11 alertas).

### Flujo B — UPGRADE (bump + validación)
```
1. Editar pyproject.toml: subir el constraint mínimo a la versión fix.
2. uv lock  (resolver) ; revisar el diff de uv.lock (efectos transitivos).
3. uv sync ; correr la sub-suite afectada (p.ej. integración MCP para aiohttp).
4. pip-audit + trivy + bandit limpios.
5. Commit aislado y revertible.
```
Aplica a: aiohttp (≥3.14.0), prefect, posiblemente pymdown, transformers (vía torch).

### Flujo C — MITIGAR (sin fix)
```
1. Análisis de surface: ¿la ruta vulnerable es alcanzable en prismal?
2. Aplicar mitigación compensatoria (config, aislamiento, constraint indirecto p.ej. torch>=2.6).
3. Mantener/añadir el ignore en .trivyignore + ci.yml + pre-commit, con:
   - CVE/GHSA, paquete, razón (won't-fix / no patch yet),
   - referencia a este spec,
   - TRIGGER de re-evaluación (condición para quitar el ignore).
```
Aplica a: chromadb (sin parche), ecdsa (won't-fix), transformers (mitigación por torch).

### Flujo D — SUPPLY-CHAIN (incidente trivy-action)
```
1. grep .github/workflows/** por aquasecurity/trivy-action y aquasecurity/setup-trivy.
2. Determinar si algún run usó tags/binarios en las ventanas comprometidas (19–20 mar 2026).
3. Si se usó la action: pinear a SHA inmutable de versión segura (trivy-action 0.35.0 / setup-trivy 0.2.6).
   Si se descarga binario por curl: fijar TRIVY_VERSION=0.69.3 + verificar checksum/firma.
4. Rotación de secretos del runner si hubo ejecución en ventana comprometida (P0).
5. Política: pinear TODAS las actions a SHA (deuda de seguimiento).
```

---

## 5. Estructura de Cambios (qué se toca)

```
prismal/
├── pyproject.toml                 # bump de constraints: aiohttp>=3.14.0, (torch>=2.6), prefect, pymdown
├── uv.lock                        # re-resuelto por `uv lock`
├── .trivyignore                   # sync: quitar resueltas, mantener chromadb/ecdsa + nuevos sin-fix
├── .pre-commit-config.yaml        # hook pip-audit: espejo de .trivyignore
├── .github/workflows/ci.yml       # security-pip-audit: espejo + pin de actions a SHA
├── CHANGELOG.md                   # entrada de seguridad
└── specs/dependency-security-remediation/
    ├── PLAN.md
    ├── ARCHITECTURE.md  (este)
    ├── SPEC.md          (matriz)
    └── TASKS.md         (ejecución)
```

No se modifica código de `prismal/**`: las 18 alertas son de dependencias, no de código propio.

---

## 6. Decisiones de Diseño

### DD-SEC-001: Priorizar por riesgo efectivo, no solo por severidad nominal
El orden de ejecución pondera severidad **y** surface. Las Critical de superficie de servidor que prismal no expone (chromadb, litellm proxy) se documentan pero no bloquean; las Moderate de runtime que sí se ejercitan (aiohttp) se remedian con upgrade real.

### DD-SEC-002: "Push del lock primero"
Como ~11 alertas ya están resueltas en el lock, la primera acción (P0) es asegurar que `uv.lock` está empujado. Esto cierra la mayoría del ruido antes de tocar nada, y aclara el trabajo real restante.

### DD-SEC-003: Mitigación por constraint indirecto antes que bump mayor
Para `transformers` (CVE-2026-1839) se prefiere forzar `torch>=2.6` (neutraliza el vector) en lugar de subir a `transformers` 5.x (breaking para `sentence-transformers`). Menor blast radius, mismo efecto de seguridad.

### DD-SEC-004: Ignore-lists como verdad única triplicada
`.trivyignore`, el hook `pip-audit` y `ci.yml` deben permanecer espejados. Un test/script de consistencia (o `prismal doctor security-check`) verifica que los tres listan el mismo set, cada entrada con justificación y trigger.

### DD-SEC-005: Cadena de suministro = flujo P0 con rotación
El incidente de `trivy-action` no se trata como un bump de versión sino como respuesta a incidente: verificación de exposición + rotación de secretos. Pinear a SHA, no a tag.

### DD-SEC-006: Trazabilidad por CVE fechada
Las versiones parcheadas se basan en GHSA a 2026-06-05 y se re-verifican al ejecutar. Cada decisión queda en `SPEC.md` con CVE, versión, acción y fecha — auditable.

---

## 7. Validación y Observabilidad

### 7.1 Gates de validación
- `uv lock` resoluble sin conflictos.
- `pip-audit` (con ignores justificados) sin hallazgos no esperados.
- `trivy fs --ignorefile .trivyignore .` limpio.
- `bandit -r prismal` limpio.
- `pytest -m "not live_api"` 100%.

### 7.2 Evidencia de cierre
- Por alerta resuelta: captura de `uv pip show <pkg>` ≥ fix + ausencia en `pip-audit`.
- Por mitigación: entrada en `.trivyignore` con trigger + nota de surface.
- Por supply-chain: diff del workflow (pin SHA) + checklist de rotación.

### 7.3 Métrica de proceso
- `n_alertas_terminal / 18` (objetivo 18/18).
- `n_ignores_sin_justificacion` (objetivo 0).
- `n_actions_sin_pin_sha` (objetivo 0 — deuda de seguimiento).

---

## 8. Plan de Rollout

1. **P0:** push del lock (cierra ~11) + incidente trivy-action + documentar chromadb/ecdsa.
2. **P1:** upgrades reales (aiohttp, transformers vía torch, prefect, pymdown) en commits aislados.
3. **P2:** sincronizar ignore-lists, validación final, cierre de alertas restantes, entrada en `CHANGELOG.md`.

Backout: cada upgrade es un commit revertible; `uv.lock` permite rollback determinista.

---

## 9. Preguntas Abiertas

- **PA-1:** ¿`ci.yml` ya migró 100% de `trivy-action` a `curl`? (Verificar en P0; condiciona si hay rotación de secretos.)
- **PA-2:** ¿`torch>=2.6` es resoluble con el resto del lock sin downgrades problemáticos? (Validar en P1.)
- **PA-3:** ¿Versión exacta de `prefect` con el PR #21591 y GHSA de `pymdown`? (Confirmar contra GHSA al ejecutar.)
- **PA-4:** ¿Se adopta ya la política de pinear todas las actions a SHA, o queda como deuda? (Recomendado: adoptar.)

---

## Historial de Cambios

| Versión | Fecha | Autor | Cambios |
|---|---|---|---|
| 1.0 | 2026-06-05 | Ernesto Crespo | Metodología de triage + flujos de remediación |

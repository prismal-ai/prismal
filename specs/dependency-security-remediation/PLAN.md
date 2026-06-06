# Prismal — Remediación de Vulnerabilidades de Dependencias (Dependabot)

## Strategic Plan / Product Requirements Document (PLAN)

| Campo | Valor |
|---|---|
| **Autor** | Ernesto Crespo |
| **Estado** | `IMPLEMENTED` |
| **Versión** | 1.0 |
| **Fecha** | 2026-06-05 |
| **Reviewers** | Tech Lead, Security Lead, AI Architect |
| **Documentos relacionados** | `ARCHITECTURE.md`, `SPEC.md`, `TASKS.md` |
| **Fuente** | GitHub Dependabot — `prismal-ai/prismal/security/dependabot` (18 alertas abiertas) |
| **Prioridad** | **ALTA** |

---

## 1. Resumen Ejecutivo

El reporte de Dependabot lista **18 alertas abiertas** sobre el `uv.lock` y un workflow de CI: **3 Critical, 8 High, 6 Moderate, 1 Low**. Este plan las analiza una por una, las cruza con la versión efectivamente fijada hoy en `uv.lock` y con el *surface* real de prismal, y define una remediación priorizada por riesgo.

**Hallazgo principal:** la mayoría de las alertas **ya están resueltas en el `uv.lock` actual** — Dependabot escaneó un `uv.lock` anterior. De las 18:

- **~11 ya resueltas en el lock actual** (la versión fijada ya es ≥ la parcheada): los 4 alerts de `litellm` (1.86.2 ≥ 1.83.10), los 2 de `urllib3` (2.7.0), los 2 de `langsmith` (0.8.7 ≥ 0.8.0), `idna` (3.17 ≥ 3.15), `starlette` (1.2.0 ≥ 1.0.1). → **Acción: push del lock + verificar/cerrar la alerta.**
- **~3 requieren upgrade real**: `aiohttp` (2 alertas → ≥ 3.14.0), `transformers` (CVE-2026-1839), `prefect` (SSRF), y `pymdown-extensions` (regresión snippets, a verificar).
- **2 sin fix upstream → mitigación**: `chromadb` (CVE-2026-45829, sin parche) y `ecdsa` (CVE-2024-23342, *won't-fix*). Ambas ya documentadas en `.trivyignore` con justificación.
- **1 incidente de cadena de suministro**: `aquasecurity/trivy-action` (GHSA-69fq-xp46-6x23) en `.github/workflows/ci.yml`.

**Contexto de exposición decisivo:** prismal es un **framework/librería**, no un despliegue que ejecute el *proxy server* de LiteLLM ni el *servidor HTTP* de ChromaDB. Por eso varias CVEs Critical/High de superficie de servidor (SQLi del proxy LiteLLM, SSTI en `/prompts/test`, endpoints MCP stdio de test, *sandbox escape* del *custom-code guardrail*, RCE pre-auth de ChromaDB) **no están en el surface de ejecución de prismal**. Dependabot alerta por versión, no por uso; el riesgo real es sustancialmente menor, pero igual se remedia subiendo a versiones parcheadas para mantener el árbol limpio.

---

## 2. Contexto y Problema

### 2.1 Situación Actual

- El repo ya tiene una práctica de gestión de CVEs madura: `.trivyignore`, `pip-audit --ignore-vuln` en `.pre-commit-config.yaml` y `.github/workflows/ci.yml`, y triage vía `prismal doctor security-check` (Phase 30).
- Sin embargo, **el `.trivyignore` y los ignores de CI no cubren todas las 18 alertas nuevas**: faltan `aiohttp` (CVE-2026-34993, CVE-2026-47265), `transformers` (CVE-2026-1839), `urllib3` (CVE-2026-21441, GHSA-qccp-gfcp-xxvc), `idna` (CVE-2026-45409), `starlette` (CVE-2026-48710), `langsmith` (CVE-2026-45134), `pymdown-extensions`, `prefect` (SSRF), y la mayoría de las CVEs nuevas de `litellm`.
- El `uv.lock` ya fue bumpeado por encima de varias correcciones (litellm 1.86.2, urllib3 2.7.0, langsmith 0.8.7, idna 3.17, starlette 1.2.0), pero **las alertas de Dependabot siguen abiertas** porque reflejan un escaneo previo o esperan que se empuje el lock.

### 2.2 Problema

1. **Ruido vs señal:** 18 alertas abiertas ocultan cuáles requieren acción real (≈5) frente a las que solo necesitan empujar el lock (≈11) o mitigar sin fix (2).
2. **Cobertura incompleta de los ignore-lists:** las CVEs sin fix (chromadb, ecdsa) deben quedar documentadas; las resueltas deben quitarse de los ignore-lists cuando el lock las supere.
3. **Riesgo de cadena de suministro activo:** el incidente de `trivy-action` (mar-2026) exige verificar que el workflow no referencia tags comprometidos y, si se ejecutó en la ventana de compromiso, rotar secretos de CI.
4. **Sin un artefacto de triage versionado:** no hay un documento que registre, por alerta, la decisión (upgrade / mitigar / aceptar) y su justificación de exposición.

### 2.3 Oportunidad

Convertir el reporte de Dependabot en un **artefacto de remediación versionado** (este spec) que: (a) cierre rápido las ~11 ya resueltas, (b) ejecute los ~5 upgrades reales con validación, (c) documente las 2 mitigaciones sin-fix, y (d) cierre el incidente de cadena de suministro. Esfuerzo bajo-medio, reduce el surface y deja trazabilidad de seguridad.

---

## 3. Usuarios Objetivo

### Persona 1: Security Lead / Maintainer
- **Necesidad:** Una decisión por alerta con justificación de exposición y criterio de cierre, no solo "subir versión".
- **Frecuencia:** Por reporte / sprint de seguridad.

### Persona 2: Release Engineer
- **Necesidad:** Comandos `uv` concretos y validación (`pip-audit`, `trivy`, suite de tests) para aplicar y verificar cada cambio sin romper el stack.
- **Frecuencia:** Por release.

### Persona 3: Downstream Consumer (prismal-sdk / prismal-web)
- **Necesidad:** Saber qué CVEs afectan realmente el runtime y cuáles son ruido de librería no usada (proxy LiteLLM, server ChromaDB).
- **Frecuencia:** Por upgrade de dependencia.

---

## 4. Objetivos y Métricas de Éxito

### 4.1 Objetivos

| Objetivo | Métrica | Target | Plazo |
|---|---|---|---|
| Cerrar alertas ya resueltas en el lock | Alertas Dependabot cerradas tras push del lock | 11/11 | P0 (días) |
| Remediar upgrades reales | aiohttp, transformers, prefect, pymdown remediadas o mitigadas | 100% | P1 (1 semana) |
| Documentar mitigaciones sin-fix | chromadb + ecdsa con justificación en `.trivyignore` + este spec | 2/2 | P0 |
| Cerrar incidente cadena de suministro | trivy-action verificado/pineado a SHA; secretos rotados si aplica | Hecho | P0 |
| Higiene de ignore-lists | `.trivyignore`/CI sin ignores obsoletos; nuevos documentados | Sincronizado | P1 |
| Sin regresiones | `uv run pytest -m "not live_api"` | 100% | Global |

### 4.2 No-objetivos (este ciclo)

- Migrar `python-jose` → `PyJWT` (mitigación de `ecdsa`): se documenta como deuda, no se ejecuta aquí.
- Bump mayor de `transformers` a 5.x: se prefiere mitigación (`torch≥2.6`) salvo que se valide la 5.x estable.

---

## 5. Alcance

### 5.1 In Scope

- Triage y decisión por cada una de las 18 alertas (matriz completa en `SPEC.md`).
- Upgrades en `pyproject.toml` / `uv.lock` para las alertas con fix disponible y compatible.
- Mitigaciones documentadas para las 2 sin fix.
- Verificación y pin del workflow de CI afectado por el incidente de `trivy-action`.
- Sincronización de `.trivyignore`, `.pre-commit-config.yaml` y `.github/workflows/ci.yml`.
- Validación: `pip-audit`, `trivy`, `bandit`, suite de tests.

### 5.2 Out of Scope

- Reescritura de código de aplicación (las CVEs son de dependencias, no de código prismal).
- Auditoría de seguridad del propio código de prismal (las capas L1–L5 ya existen; fuera de este ciclo).
- Migración `python-jose`→`PyJWT` (deuda registrada).
- Endurecimiento de despliegues de `prismal-web` (responsabilidad del host).

### 5.3 Futuras Consideraciones

- Automatizar el flujo "Dependabot → matriz → uv upgrade → pip-audit" en `prismal doctor security-check`.
- Pin de TODAS las GitHub Actions a SHA inmutable (no solo trivy).
- Política de SLA por severidad (Critical: 48 h, High: 7 días, Moderate: 30 días).

---

## 6. Requisitos Funcionales (Resumen — detalle en `SPEC.md`)

| ID | Requisito | Prioridad |
|---|---|---|
| RF-SEC-001 | Cada alerta tiene decisión documentada: resuelta / upgrade / mitigar / supply-chain | `MUST` |
| RF-SEC-002 | Las ~11 alertas ya resueltas en el lock se verifican y cierran tras push | `MUST` |
| RF-SEC-003 | `aiohttp` se sube a ≥ 3.14.0 (CVE-2026-34993, CVE-2026-47265) | `MUST` |
| RF-SEC-004 | `transformers` se mitiga (`torch≥2.6`) o se sube a 5.x estable (CVE-2026-1839) | `MUST` |
| RF-SEC-005 | `prefect` se sube a la versión con fix del SSRF DNS-rebinding | `SHOULD` |
| RF-SEC-006 | `pymdown-extensions` se verifica y remedia (regresión snippets) | `SHOULD` |
| RF-SEC-007 | `chromadb` (sin fix) documentada como mitigación: uso embebido, sin server HTTP | `MUST` |
| RF-SEC-008 | `ecdsa` (won't-fix) documentada: transitiva, deuda de migración a PyJWT | `MUST` |
| RF-SEC-009 | Workflow CI verificado/pineado por el incidente `trivy-action`; secretos rotados si aplica | `MUST` |
| RF-SEC-010 | `.trivyignore`/CI sincronizados; ignores obsoletos removidos, nuevos justificados | `MUST` |
| RF-SEC-011 | Validación final: `pip-audit` + `trivy` + `bandit` + tests verdes | `MUST` |
| RF-SEC-012 | Nota de exposición (library vs server surface) por CVE de superficie de servidor | `SHOULD` |

---

## 7. Requisitos No Funcionales

### Seguridad
- Ninguna CVE Critical/High sin decisión explícita y justificada.
- Las CVEs sin fix deben tener mitigación documentada y un *trigger* de re-evaluación (cuándo quitar el ignore).
- El incidente de cadena de suministro se trata como P0 (potencial exfiltración de secretos de CI).

### Compatibilidad
- Ningún upgrade puede romper el stack pineado (Python 3.13+, `uv.lock` resoluble).
- `filterwarnings=error` en tests: los upgrades no deben introducir `DeprecationWarning` propios.

### Trazabilidad
- Cada decisión queda en `SPEC.md` con CVE, versión, acción y fecha.
- `.trivyignore` referencia este spec para los ignores nuevos.

### Reversibilidad
- Cada upgrade es un commit aislado y revertible; `uv.lock` permite rollback.

---

## 8. Restricciones y Dependencias

- `uv` como gestor; toda resolución pasa por `uv lock` / `uv sync`.
- Algunas correcciones no tienen fix upstream (chromadb, ecdsa) → mitigación, no upgrade.
- `transformers` 5.x es un bump mayor (breaking) → preferir mitigación por `torch≥2.6`.
- Verificación de versiones parcheadas contra GitHub Advisory Database (GHSA) en el momento de remediar (las versiones aquí se basan en el estado a 2026-06-05).

---

## 9. User Stories

**US-SEC-001:** Como Security Lead, quiero saber cuáles de las 18 alertas requieren acción real para no perder tiempo en las ya resueltas.
- [ ] La matriz separa "resuelta en lock" de "upgrade" de "mitigar".

**US-SEC-002:** Como Release Engineer, quiero los comandos `uv` exactos y la validación para cada upgrade.
- [ ] `TASKS.md` lista el comando y el criterio de verificación por alerta.

**US-SEC-003:** Como Maintainer, quiero cerrar el incidente de `trivy-action` con certeza de que no hubo exfiltración o, si la hubo, con rotación de secretos.
- [ ] Verificación del workflow + checklist de rotación.

**US-SEC-004:** Como Downstream Consumer, quiero saber qué CVEs son ruido de librería no usada.
- [ ] Nota de exposición por CVE de superficie de servidor (LiteLLM proxy, ChromaDB server).

---

## 10. Riesgos y Mitigaciones

| Riesgo | Probabilidad | Impacto | Mitigación |
|---|---|---|---|
| Upgrade de `aiohttp` 3.14 rompe transporte MCP SSE | Baja | Medio | Tests de integración MCP; cambio aislado y revertible |
| Bump de `transformers` 5.x rompe `sentence-transformers` | Media | Alto | Preferir mitigación `torch≥2.6`; no subir a 5.x salvo validación |
| Secretos de CI comprometidos por `trivy-action` (mar-2026) | Media | Crítico | Verificar ventana de ejecución; rotar tokens/secretos; pin a SHA |
| `chromadb`/`ecdsa` sin fix se "olvidan" en el ignore-list | Media | Medio | Trigger de re-evaluación documentado + `prismal doctor security-check` |
| Cerrar alertas resueltas pero el lock no se empuja | Media | Bajo | P0 explícito: push del `uv.lock` actual primero |
| Versiones parcheadas cambian tras este spec | Media | Bajo | Verificar GHSA al ejecutar; SPEC fechado |

---

## 11. Timeline Estimado (priorizado por riesgo)

| Ola | Duración | Entregable |
|---|---|---|
| **P0 — Crítico/Supply-chain** | 1–2 días | Push del lock (cierra ~11), incidente trivy-action cerrado, chromadb/ecdsa documentadas |
| **P1 — Upgrades reales** | 3–5 días | aiohttp ≥3.14, transformers mitigado, prefect, pymdown |
| **P2 — Higiene** | 1–2 días | Sync de ignore-lists, validación final, cierre de alertas restantes |
| **Total** | **~1.5 semanas** | 18/18 alertas resueltas o mitigadas con trazabilidad |

---

## 12. Definición de Done (Global)

- [ ] Las 18 alertas tienen estado terminal: cerrada (resuelta), remediada (upgrade), o mitigada (sin fix, documentada).
- [ ] `uv.lock` actualizado y empujado; alertas ya resueltas cerradas en Dependabot.
- [ ] `aiohttp ≥ 3.14.0`; `transformers` mitigado o ≥ versión segura; `prefect`/`pymdown` remediados o con decisión documentada.
- [ ] `chromadb` y `ecdsa` con mitigación y trigger de re-evaluación en `.trivyignore` + `SPEC.md`.
- [ ] Workflow CI verificado/pineado por el incidente `trivy-action`; rotación de secretos ejecutada si aplica.
- [ ] `.trivyignore`, `.pre-commit-config.yaml`, `.github/workflows/ci.yml` sincronizados.
- [ ] `uv run pip-audit` (con ignores justificados) limpio; `trivy` y `bandit` limpios; `pytest -m "not live_api"` 100%.
- [ ] `CHANGELOG.md` con entrada de seguridad; este spec marcado `IMPLEMENTED`.

---

## Historial de Cambios

| Versión | Fecha | Autor | Cambios |
|---|---|---|---|
| 1.0 | 2026-06-05 | Ernesto Crespo | Plan inicial — remediación de 18 alertas Dependabot |

## Aprobaciones

| Rol | Nombre | Fecha | Estado |
|---|---|---|---|
| Tech Lead | — | | ☐ Pendiente |
| Security Lead | — | | ☐ Pendiente |
| AI Architect | — | | ☐ Pendiente |

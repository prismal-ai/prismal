# Prismal — Agent Identity & Access Governance

## Strategic Plan / Product Requirements Document (PLAN) — *PRD semilla*

| Campo | Valor |
|---|---|
| **Autor** | Ernesto Crespo |
| **Estado** | `DRAFT` (PRD semilla; faltan ARCHITECTURE/SPEC/TASKS) |
| **Versión** | 0.1 |
| **Fecha** | 2026-06-06 |
| **Reviewers** | Tech Lead, Security Lead, AI Architect |
| **Prioridad** | P1 (production blocker enterprise) |
| **Relacionado** | `specs/a2a-interop/` (consume DID), `prismal/security/permissions.py`, `audit.py` |

---

## 1. Resumen Ejecutivo

La brecha de gobernanza más citada en 2026 es la **identidad y el control de acceso de agentes autónomos**: equipos comparten credenciales humanas con agentes por falta de alternativas. Prismal tiene `PermissionManager` (grants TTL) y `AuditLogger`, pero **no tiene identidad por agente** (DID), **credenciales propias por agente** ni **delegación tipo OAuth-on-behalf**. Esta feature añade una capa de **identidad y gobernanza de acceso** para que cada agente (interno o expuesto vía A2A) tenga identidad verificable, credenciales acotadas y políticas de acceso auditable — base de confianza para multi-tenant (Fase R) y A2A (Fase I).

---

## 2. Contexto y Problema

- **Sin identidad de agente:** no hay forma estándar de afirmar "este agente es X" ni de verificar la identidad de un agente remoto (A2A usa **W3C DID** — prismal no lo emite ni valida).
- **Credenciales compartidas:** los agentes usan las API keys globales del proceso; no hay credenciales por agente/tenant con *scopes* mínimos.
- **Sin delegación on-behalf:** un agente que actúa por un usuario no porta un token acotado del usuario (OAuth on-behalf-of), lo que impide auditoría y revocación finas.
- **`PermissionManager` es coarse:** grants TTL por capacidad, pero no por identidad de agente ni por recurso/acción con política declarativa.
- **Runtime governance emergente** ("policies on paths"): falta un motor de política que decida, por identidad+acción+recurso, si se permite.

---

## 3. Usuarios Objetivo

- **Security/Compliance Lead:** identidad verificable por agente, scopes mínimos, revocación, auditoría por identidad.
- **Platform Host (`prismal-server`):** emitir/rotar credenciales por agente/tenant; integrar con IdP corporativo (OIDC/Entra/Okta).
- **A2A Integrator:** DID para el Agent Card y verificación de DIDs remotos.
- **Operator:** políticas declarativas (quién puede hacer qué sobre qué).

---

## 4. Objetivos y Métricas de Éxito

| Objetivo | Métrica | Target |
|---|---|---|
| Identidad por agente | Cada agente/tenant tiene un `AgentIdentity` (DID) verificable | 100% |
| Credenciales acotadas | Scopes mínimos por agente; sin claves globales compartidas | 0 claves globales en agentes |
| Delegación on-behalf | Token acotado del usuario propagado y auditado | Soportado |
| Política declarativa | Motor `allow(identity, action, resource)` evaluado pre-acción | Integrado con `ActionInterceptor` |
| Auditoría por identidad | Toda acción atribuible a una identidad | 100% |
| Backward-compat | Sin habilitar, comportamiento actual | 100% |

---

## 5. Alcance (propuesto)

### In Scope
- **`AgentIdentity`** (DID + metadatos) y un `IdentityProvider` (emisión/rotación/verificación), con backend pluggable (local; OIDC/Entra/Okta como adaptadores).
- **Credenciales por agente/tenant** con scopes; bóveda de secretos pluggable (no en claro en estado/logs).
- **OAuth on-behalf-of**: propagar y acotar el token del usuario por la cadena de delegación.
- **Motor de política** `PolicyEngine.allow(identity, action, resource, context)`; integración con `ActionInterceptor` (pre-tool/pre-acción) y con A2A (in/out).
- **DID para A2A**: emitir el DID del Agent Card y verificar DIDs remotos.
- **Auditoría por identidad** (extiende `AuditLogger`).
- Settings `identity_*`; integración con Fase R (identidad por `org_id`).

### Out of Scope
- IdP propio completo (se integra con IdPs existentes).
- PKI/CA propia (se usa la del entorno).
- Revocación distribuida en tiempo real entre tenants (fase posterior).

---

## 6. Requisitos Funcionales (resumen)

| ID | Requisito | Prioridad |
|---|---|---|
| RF-IDN-001 | `AgentIdentity` con DID verificable por agente/tenant | `MUST` |
| RF-IDN-002 | `IdentityProvider` pluggable (local + OIDC/Entra/Okta) | `MUST` |
| RF-IDN-003 | Credenciales por agente con scopes; bóveda pluggable | `MUST` |
| RF-IDN-004 | OAuth on-behalf-of por cadena de delegación | `SHOULD` |
| RF-IDN-005 | `PolicyEngine.allow(...)` integrado con `ActionInterceptor` | `MUST` |
| RF-IDN-006 | Emitir/verificar DID para Agent Cards (A2A) | `MUST` |
| RF-IDN-007 | Auditoría por identidad (extiende `AuditLogger`) | `MUST` |
| RF-IDN-008 | Settings + integración Fase R (por `org_id`) | `SHOULD` |

---

## 7. Riesgos y Mitigaciones (resumen)

| Riesgo | Mitigación |
|---|---|
| Secretos en logs/estado | Bóveda + redacción; nunca en `AgentState`/logs |
| Complejidad DID/PKI | Backend pluggable; empezar con DID local + OIDC |
| Política mal configurada bloquea todo | Modo `warn` antes de `enforce`; defaults seguros |
| Acople con A2A e identidad simultáneo | Definir el subset mínimo de DID que A2A necesita primero |

---

## 8. Dependencias

- `prismal/security/permissions.py`, `action_interceptor.py`, `audit.py` (extensión).
- `specs/a2a-interop/` (consumidor del DID).
- `specs/composition-root/` (identidad por tenant).
- IdP externo (OIDC) — responsabilidad del host.

---

## 9. Próximos Pasos

Expandir este PRD a set SDD completo (ARCHITECTURE/SPEC/TASKS) con: modelo `AgentIdentity`/`IdentityProvider`/`PolicyEngine`, formato de credenciales y scopes, integración exacta con `ActionInterceptor` y A2A, y plan por fases.

---

## Historial de Cambios

| Versión | Fecha | Autor | Cambios |
|---|---|---|---|
| 0.1 | 2026-06-06 | Ernesto Crespo | PRD semilla — identidad y gobernanza de acceso de agentes |

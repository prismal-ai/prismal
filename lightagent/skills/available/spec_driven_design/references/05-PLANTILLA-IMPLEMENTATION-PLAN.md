# [NOMBRE DEL PROYECTO/FEATURE] — Implementation Plan

## Metadata

| Campo | Valor |
|---|---|
| **Autor** | [Nombre] |
| **Estado** | `DRAFT` / `IN_REVIEW` / `APPROVED` / `IN_PROGRESS` / `COMPLETED` |
| **Versión** | 1.0 |
| **Fecha** | YYYY-MM-DD |
| **PRD** | [Link al PRD] |
| **Tech Design** | [Link al Tech Design] |
| **Data Model** | [Link al Data Model] |
| **API Spec** | [Link al API Spec] |

---

## 1. Resumen de Implementación

[Párrafo describiendo la estrategia: cuántas fases, enfoque general, duración estimada total.]

**Duración total estimada:** [X semanas/sprints]
**Equipo requerido:** [X backend, X frontend, X QA]
**Fecha objetivo de producción:** [YYYY-MM-DD]

## 2. Pre-requisitos

| Pre-requisito | Owner | Estado | Fecha Límite |
|---|---|---|---|
| [Ej: Specs aprobados] | Tech Lead | ☐ Pendiente | YYYY-MM-DD |
| [Ej: Acceso a API externa] | DevOps | ☐ Pendiente | YYYY-MM-DD |
| [Ej: Ambiente de staging] | DevOps | ☐ Pendiente | YYYY-MM-DD |

## 3. Fases de Implementación

---

### Fase 1: Foundation & Infrastructure

**Duración:** [X días/sprints]
**Objetivo:** Establecer la base técnica.

#### Tareas

| ID | Tarea | Responsable | Estimación | Dependencia | Estado |
|---|---|---|---|---|---|
| F1-01 | Crear estructura de proyecto y scaffolding | [Nombre] | 2d | — | ☐ |
| F1-02 | Configurar CI/CD pipeline | [Nombre] | 1d | F1-01 | ☐ |
| F1-03 | Setup base de datos + migraciones | [Nombre] | 2d | F1-01 | ☐ |
| F1-04 | Health checks y configuración | [Nombre] | 1d | F1-01 | ☐ |
| F1-05 | Logging y métricas base | [Nombre] | 1d | F1-04 | ☐ |
| F1-06 | Setup testing framework | [Nombre] | 1d | F1-01 | ☐ |

#### Criterios de "Done"
- Pipeline CI verde con tests pasando
- Deploy exitoso a staging
- Métricas y logs visibles en dashboard

---

### Fase 2: Core Domain Logic

**Duración:** [X días/sprints]
**Objetivo:** Implementar lógica de negocio sin integraciones externas.

#### Tareas

| ID | Tarea | Responsable | Estimación | Dependencia | Estado |
|---|---|---|---|---|---|
| F2-01 | Modelos de dominio + validaciones | [Nombre] | 2d | F1-03 | ☐ |
| F2-02 | Repositorios (CRUD base) | [Nombre] | 2d | F2-01 | ☐ |
| F2-03 | Service layer + reglas de negocio | [Nombre] | 3d | F2-02 | ☐ |
| F2-04 | Máquina de estados | [Nombre] | 2d | F2-03 | ☐ |
| F2-05 | Unit tests dominio y servicios | [Nombre] | 2d | F2-04 | ☐ |
| F2-06 | Integration tests con DB real | [Nombre] | 2d | F2-05 | ☐ |

#### Criterios de "Done"
- Todos los requisitos MUST cubiertos
- Tests unitarios e integración pasando
- Code review aprobado

---

### Fase 3: API Layer

**Duración:** [X días/sprints]
**Objetivo:** Exponer funcionalidad como API REST según el API Spec.

#### Tareas

| ID | Tarea | Responsable | Estimación | Dependencia | Estado |
|---|---|---|---|---|---|
| F3-01 | Endpoints CRUD | [Nombre] | 2d | F2-03 | ☐ |
| F3-02 | Autenticación y autorización | [Nombre] | 2d | F3-01 | ☐ |
| F3-03 | Paginación, filtros y búsqueda | [Nombre] | 1d | F3-01 | ☐ |
| F3-04 | Endpoint de acciones (estados) | [Nombre] | 2d | F2-04, F3-01 | ☐ |
| F3-05 | Rate limiting | [Nombre] | 1d | F3-02 | ☐ |
| F3-06 | Error handling global | [Nombre] | 1d | F3-01 | ☐ |
| F3-07 | E2E tests del API | [Nombre] | 2d | F3-06 | ☐ |
| F3-08 | Documentación OpenAPI | [Nombre] | 1d | F3-07 | ☐ |

#### Criterios de "Done"
- API Spec cumplido al 100%
- Swagger/OpenAPI docs accesibles

---

### Fase 4: Integraciones Externas

**Duración:** [X días/sprints]
**Objetivo:** Conectar con servicios externos.

#### Tareas

| ID | Tarea | Responsable | Estimación | Dependencia | Estado |
|---|---|---|---|---|---|
| F4-01 | Cliente de servicio externo | [Nombre] | 3d | F3-04 | ☐ |
| F4-02 | Circuit breaker + retry logic | [Nombre] | 2d | F4-01 | ☐ |
| F4-03 | Publisher de eventos a cola | [Nombre] | 1d | F3-04 | ☐ |
| F4-04 | Worker/consumer de cola | [Nombre] | 2d | F4-03 | ☐ |
| F4-05 | Webhooks de notificación | [Nombre] | 2d | F4-04 | ☐ |
| F4-06 | Integration tests con mocks | [Nombre] | 2d | F4-05 | ☐ |

#### Criterios de "Done"
- Integración end-to-end funcional en staging
- Retry logic probado con fallas simuladas

---

### Fase 5: Hardening & Production Readiness

**Duración:** [X días/sprints]
**Objetivo:** Preparar para producción.

#### Tareas

| ID | Tarea | Responsable | Estimación | Dependencia | Estado |
|---|---|---|---|---|---|
| F5-01 | Security audit | [Nombre] | 2d | F4-05 | ☐ |
| F5-02 | Load testing | [Nombre] | 2d | F4-05 | ☐ |
| F5-03 | Alertas de producción | [Nombre] | 1d | F4-05 | ☐ |
| F5-04 | Runbooks operacionales | [Nombre] | 1d | F5-03 | ☐ |
| F5-05 | UAT con stakeholders | [Nombre] | 3d | F5-02 | ☐ |
| F5-06 | Documentación final | [Nombre] | 1d | F5-05 | ☐ |

#### Criterios de "Done"
- Load test pasa targets de rendimiento
- Sin vulnerabilidades critical/high
- Stakeholders firman UAT

---

## 4. Mapa de Dependencias

```
Fase 1: Foundation
  │
  ├──▶ Fase 2: Core Domain ──▶ Fase 3: API Layer ──▶ Fase 4: Integraciones
  │                                                         │
  │                                                         ▼
  │                                                   Fase 5: Hardening
  │
  └──▶ [Frontend puede iniciar con mock API desde Fase 1]
```

## 5. Riesgos de Implementación

| Riesgo | Probabilidad | Impacto | Mitigación | Owner |
|---|---|---|---|---|
| Retraso en acceso a API externa | Media | Alto | Empezar con mocks | DevOps |
| Cambio de requisitos mid-sprint | Media | Medio | Spec aprobado como gate | PM |
| Complejidad de edge cases | Alta | Alto | Spike técnico, pair programming | Backend Lead |

## 6. Comunicación y Seguimiento

### Ceremonias

| Ceremonia | Frecuencia | Participantes | Propósito |
|---|---|---|---|
| Daily standup | Diario | Equipo dev | Progreso y bloqueos |
| Sprint review | Fin de cada fase | Equipo + PM | Demo de entregables |
| Retrospectiva | Fin de cada fase | Equipo | Mejora continua |

### Reportes de Progreso

Reporte semanal cada viernes con: tareas completadas, planificadas, bloqueos activos, cambios en estimaciones.

## 7. Definición de Done (Global)

- [ ] Código implementado y mergeado a main
- [ ] Tests pasando (unit + integration + e2e)
- [ ] Code review aprobado por al menos 1 peer
- [ ] Documentación actualizada (API docs, README)
- [ ] Deployed a staging y verificado
- [ ] Métricas y alertas configuradas
- [ ] Spec actualizado si hubo cambios durante implementación
- [ ] Sin deuda técnica known sin ticket correspondiente

---

## Historial de Cambios

| Versión | Fecha | Autor | Cambios |
|---|---|---|---|
| 1.0 | YYYY-MM-DD | [Nombre] | Versión inicial |

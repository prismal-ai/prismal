# [NOMBRE DEL FEATURE/PRODUCTO]

## Product Requirements Document (PRD)

| Campo | Valor |
|---|---|
| **Autor** | [Nombre] |
| **Estado** | `DRAFT` / `IN_REVIEW` / `APPROVED` / `DEPRECATED` |
| **Versión** | 1.0 |
| **Fecha** | YYYY-MM-DD |
| **Reviewers** | [Nombres de revisores] |
| **Última actualización** | YYYY-MM-DD |

---

## 1. Resumen Ejecutivo

<!-- 
  2-3 párrafos máximo. Debe responder:
  - ¿Qué vamos a construir?
  - ¿Para quién?
  - ¿Qué problema resuelve?
  Un stakeholder que solo lea esta sección debe entender el alcance.
-->

[Descripción concisa del feature o producto propuesto]

## 2. Contexto y Problema

### 2.1 Situación Actual
[Descripción del estado actual]

### 2.2 Problema
[Descripción del problema con evidencia]

### 2.3 Oportunidad
[Descripción de la oportunidad]

## 3. Usuarios Objetivo

### Persona 1: [Nombre del Rol]
- **Descripción:** [Quién es, qué hace]
- **Necesidad principal:** [Qué necesita lograr]
- **Frecuencia de uso:** [Diario / Semanal / Mensual / Eventual]
- **Nivel técnico:** [Bajo / Medio / Alto]

## 4. Objetivos y Métricas de Éxito

### 4.1 Objetivos del Negocio

| Objetivo | Métrica | Target | Plazo |
|---|---|---|---|
| [Objetivo] | [Métrica] | [Target] | [Plazo] |

### 4.2 Objetivos de Usuario

| Objetivo del Usuario | Indicador |
|---|---|
| [Objetivo] | [Indicador] |

## 5. Alcance

### 5.1 In Scope (Incluido)
- [ ] [Funcionalidad 1]
- [ ] [Funcionalidad 2]

### 5.2 Out of Scope (Excluido)
- [Funcionalidad excluida 1 — razón breve]

### 5.3 Futuras Consideraciones
- [Consideración futura 1]

## 6. Requisitos Funcionales

### RF-001: [Nombre del Requisito]
- **Descripción:** El sistema debe [acción específica]
- **Actor:** [Quién inicia la acción]
- **Precondiciones:** [Qué debe ser verdad antes]
- **Flujo principal:**
  1. [Paso 1]
  2. [Paso 2]
  3. [Paso 3]
- **Flujo alternativo:** [Qué pasa si algo sale diferente]
- **Postcondiciones:** [Qué es verdad después]
- **Prioridad:** `MUST` / `SHOULD` / `COULD` / `WONT`

## 7. Requisitos No Funcionales

### Rendimiento
- [Ej: Tiempo de respuesta API < 200ms en p95]

### Seguridad
- [Ej: Autenticación via JWT con refresh tokens]

### Disponibilidad
- [Ej: SLA 99.9% uptime]

### Escalabilidad
- [Ej: Diseño horizontal-scalable para 10x crecimiento]

### Observabilidad
- [Ej: Logs estructurados, métricas en Prometheus, tracing distribuido]

## 8. Restricciones y Dependencias

### Restricciones Técnicas
- [Ej: Debe integrarse con MongoDB existente v6.0]

### Restricciones de Negocio
- [Ej: Regulación bancaria requiere X]

### Dependencias Externas

| Dependencia | Tipo | Owner | Estado | Riesgo |
|---|---|---|---|---|
| [Dependencia] | [Tipo] | [Owner] | [Estado] | [Riesgo] |

## 9. User Stories

### Épica: [Nombre de la Épica]

**US-001:** Como [persona], quiero [acción], para [beneficio].
- Criterios de aceptación:
  - [ ] [Criterio verificable 1]
  - [ ] [Criterio verificable 2]

## 10. Wireframes / Mockups
- [Enlace a Figma / imagen / descripción textual del flujo]

## 11. Riesgos y Mitigaciones

| Riesgo | Probabilidad | Impacto | Mitigación |
|---|---|---|---|
| [Riesgo] | [P] | [I] | [Mitigación] |

## 12. Timeline Estimado

| Fase | Duración Estimada | Entregable |
|---|---|---|
| Spec & Design | [X semanas] | Specs aprobados |
| Implementación MVP | [X semanas] | Feature funcional |
| Testing & QA | [X semanas] | Release candidate |
| Rollout | [X semanas] | Producción |

---

## Historial de Cambios

| Versión | Fecha | Autor | Cambios |
|---|---|---|---|
| 1.0 | YYYY-MM-DD | [Nombre] | Versión inicial |

## Aprobaciones

| Rol | Nombre | Fecha | Estado |
|---|---|---|---|
| Product Manager | [Nombre] | | ☐ Pendiente |
| Tech Lead | [Nombre] | | ☐ Pendiente |
| Stakeholder | [Nombre] | | ☐ Pendiente |

# Guía de Llenado de Plantillas SDD

## Índice

1. Cómo llenar el PRD
2. Cómo llenar el API Spec
3. Cómo llenar el Technical Design
4. Cómo llenar el Data Model
5. Cómo llenar el Implementation Plan
6. Flujo de trabajo completo
7. Usando AI para acelerar el proceso

---

## 1. Cómo llenar el PRD

### ¿Cuándo escribir un PRD?

Escribe un PRD cuando vayas a construir algo nuevo o hacer un cambio significativo. No necesitas PRD para bug fixes, refactors, o mejoras internas pequeñas.

### Tips por sección

**Resumen Ejecutivo:** Escríbelo al final. Usa: "Vamos a construir [qué], para [quién], que resuelve [problema], medido por [métrica]."

**Contexto y Problema:** Usa datos concretos, no opiniones. Mal: "El sistema es malo". Bien: "El 65% de los pagos se procesan manualmente via call center con 2,300 llamadas mensuales."

**Usuarios Objetivo:** Máximo 3 personas. Si tienes más, tu scope es demasiado amplio.

**Objetivos y Métricas:** Cada objetivo debe ser SMART. Mal: "Mejorar la experiencia". Bien: "Reducir el tiempo de pago de 8 min a menos de 2 min en p50, dentro de 3 meses post-launch."

**Alcance:** La sección más importante para evitar scope creep. Sé explícito sobre lo que NO incluye y por qué.

**Requisitos Funcionales:** Usa "El sistema DEBE..." (obligatorio) y "El sistema DEBERÍA..." (deseable). Cada requisito debe ser verificable con una prueba concreta.

**Prioridad MoSCoW:**
- MUST: Sin esto, no tiene sentido. Es el MVP.
- SHOULD: Importante pero no bloqueante.
- COULD: Nice to have si hay tiempo.
- WONT: Excluido de esta iteración.

### Errores comunes

| Error | Consecuencia | Solución |
|---|---|---|
| Requisitos ambiguos | Interpretaciones diferentes | Criterios verificables |
| Sin métricas de éxito | No se puede evaluar | Definir números antes |
| Scope demasiado amplio | Nunca se termina | MoSCoW agresivo |
| Sin "Out of Scope" | Scope creep | Listar exclusiones |

---

## 2. Cómo llenar el API Spec

### ¿Cuándo escribir un API Spec?

Siempre que construyas endpoints consumidos por otro equipo, servicio, o frontend.

### Tips por sección

**Autenticación:** Incluye cómo obtener tokens, duración, y refresh. Un dev debe poder autenticarse solo leyendo esta sección.

**Convenciones:** Define una vez para todo el API: formato de respuesta, formato de errores (con códigos de dominio como `PAYMENT_INSUFFICIENT_FUNDS`), y paginación.

**Endpoints:** Cada endpoint documenta: Método+Path, Descripción, Roles requeridos, Request (body/params con tipos y validaciones), Responses (happy path Y todos los errores), Ejemplo cURL copy-pasteable.

**Diagrama de Estados:** Obligatorio si tu recurso tiene ciclo de vida. Documenta cada transición, qué la activa, y qué rol la ejecuta.

### Errores comunes

| Error | Consecuencia | Solución |
|---|---|---|
| No documentar errores | Frontend no sabe qué manejar | Tabla de errores por endpoint |
| Campos sin tipos claros | Bugs por type mismatch | Tipos explícitos con validaciones |
| Sin rate limiting docs | Polling agresivo de clientes | Documentar limits y headers |
| Sin versionado | Breaking changes | Versionar desde día 1 |

---

## 3. Cómo llenar el Technical Design

### ¿Cuándo escribir un Technical Design?

Cuando hay decisiones arquitectónicas no triviales: nueva infraestructura, integración compleja, cambio de patrones.

### Tips por sección

**Decisiones de Diseño (sección más valiosa):** Para cada decisión documenta: Contexto, Opciones evaluadas (mínimo 2), Criterios de evaluación, Decisión final, Consecuencias y trade-offs.

**Flujo de Datos:** Numera los pasos. Incluye happy path y flujos de error. Esto permite que en code review alguien diga "el step 4 no está cubierto por tests".

**Seguridad:** No dejar vacía. Para pagos: inyección, token hijacking, replay attacks, etc.

### Errores comunes

| Error | Consecuencia | Solución |
|---|---|---|
| Solo documentar decisión final | Se repiten discusiones | Documentar alternativas descartadas |
| Sobre-diseñar | Tiempo perdido | Diseñar para requisitos actuales + 1 iteración |
| Ignorar flujos de error | Bugs en producción | Documentar flujo de compensación |

---

## 4. Cómo llenar el Data Model

### ¿Cuándo escribir un Data Model Spec?

Siempre que crees colecciones/tablas nuevas o modifiques schemas existentes.

### Tips clave

- **Decimal128 para montos financieros**, nunca float/double
- **Incluir `created_at` y `updated_at`** en toda colección
- **Soft delete (`deleted_at`)** para datos que necesitan auditoría
- **Documentar enum values** explícitamente

### Índices

Cada índice debe tener justificación vinculada a una query real. Cómo decidir:
1. Identifica queries más frecuentes
2. Verifica que hay índice que la cubra
3. Verifica con `explain()` que se usa
4. No crear índices "por si acaso"

**Orden en compound index:** Igualdad primero, luego rango, luego sort.

```javascript
// Bueno: igualdad primero
{ user_id: 1, status: 1, created_at: -1 }

// Malo: sort antes de igualdad
{ created_at: -1, user_id: 1, status: 1 }
```

### Errores comunes

| Error | Consecuencia | Solución |
|---|---|---|
| Float para dinero | Errores de precisión | Siempre Decimal128 |
| Índices sin justificación | Escrituras lentas | Vincular a query |
| Sin plan de migración | Downtime al cambiar schema | Scripts up/down |

---

## 5. Cómo llenar el Implementation Plan

### ¿Cuándo escribir un Implementation Plan?

Cuando la implementación dura más de 1 sprint o involucra más de 1 persona.

### Tips por sección

**Fases:** Cada fase produce algo deployable y verificable. Estructura recomendada:
1. Foundation (infra, CI/CD)
2. Core Domain (lógica pura, testeable)
3. API Layer (exponer funcionalidad)
4. Integraciones (servicios externos, mayor riesgo)
5. Hardening (seguridad, performance, producción)

**Estimaciones:** Multiplica estimación optimista por 1.5. Si no puedes estimar → spike técnico de 2-4 horas.

| Estimación del dev | Estimación real |
|---|---|
| "1 día" | 1.5-2 días |
| "3 días" | 4-5 días |
| "1 semana" | 1.5-2 semanas |
| "No sé" | Spike técnico primero |

### Errores comunes

| Error | Consecuencia | Solución |
|---|---|---|
| Tareas > 3 días | Difícil trackear | Desglosar en 0.5-2 días |
| Sin dependencias | Bloqueos | Grafo explícito |
| Sin criterios de done | "¿Ya terminamos?" | Checklist por fase |

---

## 6. Flujo de Trabajo Completo

```
Día 1-2:  Escribir PRD (PM + Tech Lead)
Día 3:    Review del PRD con stakeholders
Día 4-5:  Escribir API Spec + Data Model (Backend Lead)
Día 6:    Review del API Spec (Backend + Frontend)
          → Frontend puede empezar con mock API
Día 7-8:  Escribir Technical Design (Tech Lead + Senior Devs)
Día 9:    Review técnico del design
Día 10:   Escribir Implementation Plan (Tech Lead)
Día 11:   Review del plan + ajuste de estimaciones
Día 12+:  Implementación según el plan
```

### ¿Cuándo es overkill?

| Tipo de trabajo | Documentos recomendados |
|---|---|
| Bug fix | Ninguno (ticket suficiente) |
| Refactor interno | Tech Design lite |
| CRUD simple + API | API Spec + Data Model |
| Feature medio (1-2 sprints) | PRD + API Spec + Data Model |
| Feature complejo (3+ sprints) | Los 5 documentos |
| Nuevo servicio | Los 5 documentos |

---

## 7. Usando AI para acelerar el proceso

### Generar draft de PRD

```
Actúa como Product Manager. Dado este contexto:
- Problema: [describe el problema]
- Usuarios: [describe los usuarios]
- Restricciones: [lista restricciones]

Genera un draft de PRD siguiendo la plantilla de Spec-Driven Design.
```

### Generar API Spec desde PRD

```
Dado este PRD:
[pega el PRD completado]

Genera un API Spec que cubra todos los requisitos funcionales RF-001 a RF-00N del PRD.
```

### Generar tests desde API Spec

```
Dado este API Spec:
[pega el API Spec]

Genera tests de integración en Python usando pytest y httpx que verifiquen:
1. Happy path de cada endpoint
2. Todos los errores documentados
3. Validaciones de campos
4. Transiciones de estado válidas e inválidas
```

### Generar Data Model desde Tech Design

```
Dado este Technical Design y este API Spec:
[pega ambos]

Genera un Data Model Spec para MongoDB.
Asegúrate de que los índices cubran todas las queries implícitas en los endpoints del API.
```

### Validar coherencia entre specs

```
Tengo estos specs para el mismo feature:
- PRD: [contenido]
- API Spec: [contenido]
- Tech Design: [contenido]
- Data Model: [contenido]

Identifica inconsistencias:
1. ¿Hay requisitos del PRD no cubiertos por el API Spec?
2. ¿Hay campos en el API que no están en el Data Model?
3. ¿Las transiciones de estado del API coinciden con el Tech Design?
4. ¿Los índices del Data Model cubren las queries del API?
```

---

## Checklist Final

Antes de implementar, verifica:

- [ ] PRD: Requisitos MUST con criterios verificables
- [ ] PRD: Out of Scope definido
- [ ] API Spec: Frontend puede integrar sin preguntas
- [ ] API Spec: Todos los errores con códigos
- [ ] Tech Design: Decisiones con alternativas documentadas
- [ ] Tech Design: Flujos de error con compensación
- [ ] Data Model: Índices vinculados a queries
- [ ] Data Model: Financieros con Decimal128
- [ ] Plan: Fases con criterios de Done verificables
- [ ] Plan: Dependencias mapeadas
- [ ] General: Specs en el repositorio bajo `/specs`
- [ ] General: Equipo ha revieweado y aprobado

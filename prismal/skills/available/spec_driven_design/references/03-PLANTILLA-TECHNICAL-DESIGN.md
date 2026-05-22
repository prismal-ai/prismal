# [NOMBRE DEL COMPONENTE/FEATURE] — Technical Design Document

## Metadata

| Campo | Valor |
|---|---|
| **Autor** | [Nombre] |
| **Estado** | `DRAFT` / `IN_REVIEW` / `APPROVED` / `DEPRECATED` |
| **Versión** | 1.0 |
| **Fecha** | YYYY-MM-DD |
| **PRD Relacionado** | [Link al PRD] |
| **API Spec Relacionado** | [Link al API Spec] |
| **Reviewers** | [Nombres] |

---

## 1. Contexto

[Descripción del contexto técnico en 2-3 párrafos. Qué se va a construir desde el punto de vista de ingeniería y por qué las decisiones técnicas son relevantes.]

## 2. Objetivos Técnicos

- **Correctitud:** [Ej: Las transacciones deben ser atómicas y consistentes]
- **Rendimiento:** [Ej: p95 < 200ms para queries principales]
- **Mantenibilidad:** [Ej: Cobertura de tests > 80%, código modular]
- **Operabilidad:** [Ej: Alertas, dashboards, runbooks]

## 3. Arquitectura Propuesta

### 3.1 Diagrama de Alto Nivel

```
┌─────────────┐     ┌──────────────┐     ┌──────────────┐
│   Client     │────▶│  API Gateway │────▶│  Service A   │
│  (React)     │     │  (FastAPI)   │     │  (Worker)    │
└─────────────┘     └──────┬───────┘     └──────┬───────┘
                           │                     │
                    ┌──────▼───────┐     ┌───────▼──────┐
                    │   MongoDB    │     │  Message     │
                    │   (Primary)  │     │  Queue       │
                    └──────────────┘     └──────────────┘
```

### 3.2 Componentes

| Componente | Tecnología | Responsabilidad |
|---|---|---|
| API Gateway | FastAPI / Python 3.13 | Routing, auth, validación |
| Service A | Python / Worker | [Lógica de negocio específica] |
| Base de Datos | MongoDB 7.x | Persistencia principal |
| Cola de Mensajes | [RabbitMQ / Redis Streams] | Procesamiento asíncrono |
| Cache | Redis | [Sesiones / rate limiting / cache] |

### 3.3 Flujo de Datos

**Flujo: [Nombre de la operación principal]**

```
1. Cliente envía POST /resources con payload
2. API Gateway valida JWT y permisos
3. FastAPI valida schema con Pydantic
4. Service crea documento en MongoDB con status=PENDING
5. Service publica evento en cola: resource.created
6. Worker consume evento y ejecuta lógica de negocio
7. Worker actualiza status en MongoDB
8. Webhook notifica al cliente del cambio de estado
```

**Flujo de error / compensación:**

```
1. Si paso 4 falla → retorna 400/500 al cliente
2. Si paso 6 falla → retry con backoff exponencial (3 intentos)
3. Si paso 7 falla después de retry → marcar como FAILED, alerta
4. Dead letter queue captura mensajes no procesables
```

## 4. Decisiones de Diseño

### DD-001: [Nombre de la decisión]

- **Decisión:** [Qué se decidió]
- **Contexto:** [Qué motivó esta decisión]
- **Alternativas evaluadas:**

| Opción | Pros | Contras |
|---|---|---|
| **Opción A (elegida)** | [Pro 1, Pro 2] | [Contra 1] |
| Opción B | [Pro 1] | [Contra 1, Contra 2] |
| Opción C | [Pro 1] | [Contra 1, Contra 2] |

- **Justificación:** [Por qué se eligió la Opción A]
- **Consecuencias:** [Qué implica esta decisión a futuro]

## 5. Patrones y Convenciones

### 5.1 Estructura del Código

```
src/
├── api/
│   ├── routes/          # Definición de endpoints
│   ├── schemas/         # Pydantic models (request/response)
│   ├── dependencies.py  # Inyección de dependencias
│   └── middleware.py     # Auth, logging, error handling
├── core/
│   ├── config.py        # Settings con Pydantic BaseSettings
│   └── exceptions.py    # Custom exceptions del dominio
├── domain/
│   ├── models/          # Entidades de dominio
│   └── services/        # Lógica de negocio
├── infrastructure/
│   ├── database/
│   │   ├── connection.py
│   │   └── repositories/
│   ├── messaging/       # Queue publisher
│   └── external/        # Integraciones externas
├── workers/             # Consumers de cola
└── tests/
    ├── unit/
    ├── integration/
    └── e2e/
```

### 5.2 Patrones Aplicados

| Patrón | Dónde | Por qué |
|---|---|---|
| Repository Pattern | `infrastructure/database/` | Desacoplar lógica de persistencia |
| Service Layer | `domain/services/` | Centralizar lógica de negocio |
| Circuit Breaker | `infrastructure/external/` | Resiliencia ante servicios externos |

### 5.3 Manejo de Errores

```python
# Jerarquía de excepciones del dominio
class DomainError(Exception): ...
class ValidationError(DomainError): ...      # → 400
class NotFoundError(DomainError): ...        # → 404
class ConflictError(DomainError): ...        # → 409
class ExternalServiceError(DomainError): ... # → 502
```

## 6. Seguridad

### 6.1 Superficie de Ataque

| Vector | Mitigación |
|---|---|
| Inyección en queries MongoDB | Validación estricta con Pydantic |
| JWT comprometido | Tokens de corta duración (15min), refresh rotados |
| Rate limiting bypass | Rate limit por IP + por usuario, sliding window |
| Datos sensibles en logs | Sanitización automática de campos sensibles |

### 6.2 Datos Sensibles

| Dato | Clasificación | Almacenamiento | Acceso |
|---|---|---|---|
| [Dato] | [Clasificación] | [Cómo se almacena] | [Quién accede] |

## 7. Observabilidad

### 7.1 Logging

```json
{
  "timestamp": "ISO-8601",
  "level": "INFO|WARN|ERROR",
  "service": "service-name",
  "request_id": "uuid",
  "user_id": "string",
  "action": "action_name",
  "duration_ms": 45,
  "status": "success|failure",
  "metadata": {}
}
```

### 7.2 Métricas

| Métrica | Tipo | Descripción |
|---|---|---|
| `api_request_duration_seconds` | Histogram | Latencia por endpoint |
| `api_requests_total` | Counter | Total de requests por status code |
| `queue_messages_processed` | Counter | Mensajes procesados por tipo |
| `external_service_errors` | Counter | Errores de servicios externos |

### 7.3 Alertas

| Alerta | Condición | Severidad |
|---|---|---|
| Alta latencia | p95 > 500ms por 5 min | Warning |
| Error rate elevado | > 5% errores 5xx por 5 min | Critical |
| Queue atrasada | > 1000 mensajes pendientes | Warning |
| Servicio externo caído | Circuit breaker abierto | Critical |

## 8. Testing Strategy

| Nivel | Cobertura Target | Herramientas | Qué cubre |
|---|---|---|---|
| Unit | > 80% | pytest, unittest.mock | Lógica de negocio, validaciones |
| Integration | Flujos críticos | pytest, testcontainers | DB, cola, servicios |
| E2E | Happy paths | pytest, httpx | API completa end-to-end |
| Load | Benchmarks | locust / k6 | Rendimiento bajo carga |

## 9. Plan de Migración / Rollout

### 9.1 Estrategia de Deployment
- [ ] Feature flag para activación gradual
- [ ] Canary deployment (10% → 50% → 100%)
- [ ] Rollback automático si error rate > X%

### 9.2 Backward Compatibility
- [Ej: Los endpoints v1 existentes se mantienen por 6 meses]
- [Ej: Nuevos campos son opcionales en el request]

## 10. Preguntas Abiertas
- [ ] [Pregunta 1 — Owner, Deadline]
- [ ] [Pregunta 2 — Owner, Deadline]

---

## Historial de Cambios

| Versión | Fecha | Autor | Cambios |
|---|---|---|---|
| 1.0 | YYYY-MM-DD | [Nombre] | Versión inicial |

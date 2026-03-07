# [NOMBRE DEL API] — API Specification

## Metadata

| Campo               | Valor                                             |
| ------------------- | ------------------------------------------------- |
| **Autor**           | [Nombre]                                          |
| **Estado**          | `DRAFT` / `IN_REVIEW` / `APPROVED` / `DEPRECATED` |
| **Versión API**     | v1.0                                              |
| **Fecha**           | YYYY-MM-DD                                        |
| **PRD Relacionado** | [Link al PRD]                                     |
| **Base URL**        | `https://api.ejemplo.com/v1`                      |

---

## 1. Visión General

[Descripción del propósito del API y sus consumidores principales]

## 2. Autenticación y Autorización

### Método de Autenticación

```
Authorization: Bearer <token>
```

### Roles y Permisos

| Rol        | Descripción   | Endpoints Permitidos |
| ---------- | ------------- | -------------------- |
| `admin`    | [Descripción] | Todos                |
| `operator` | [Descripción] | [Lista de endpoints] |
| `viewer`   | [Descripción] | Solo GET             |

### Obtención de Token

```http
POST /auth/token
Content-Type: application/json

{
  "client_id": "string",
  "client_secret": "string",
  "grant_type": "client_credentials"
}
```

**Respuesta exitosa (200):**

```json
{
  "access_token": "eyJhbG...",
  "token_type": "Bearer",
  "expires_in": 3600
}
```

## 3. Convenciones Generales

### Formato de Respuesta

```json
{
  "success": true,
  "data": { },
  "meta": {
    "request_id": "uuid-v4",
    "timestamp": "ISO-8601"
  }
}
```

### Formato de Error

```json
{
  "success": false,
  "error": {
    "code": "ERROR_CODE_DOMAIN",
    "message": "Descripción legible para el desarrollador",
    "details": [
      {
        "field": "campo",
        "issue": "Descripción del problema"
      }
    ]
  },
  "meta": {
    "request_id": "uuid-v4",
    "timestamp": "ISO-8601"
  }
}
```

### Códigos de Error del Dominio

| Código               | HTTP Status | Descripción                             |
| -------------------- | ----------- | --------------------------------------- |
| `VALIDATION_ERROR`   | 400         | Datos de entrada inválidos              |
| `UNAUTHORIZED`       | 401         | Token inválido o expirado               |
| `FORBIDDEN`          | 403         | Permisos insuficientes                  |
| `RESOURCE_NOT_FOUND` | 404         | Recurso no encontrado                   |
| `CONFLICT`           | 409         | Estado conflictivo (ej: pago duplicado) |
| `RATE_LIMITED`       | 429         | Demasiadas solicitudes                  |
| `INTERNAL_ERROR`     | 500         | Error interno del servidor              |

### Paginación

```http
GET /resources?page=1&page_size=20&sort_by=created_at&sort_order=desc
```

**Respuesta:**

```json
{
  "data": [...],
  "pagination": {
    "page": 1,
    "page_size": 20,
    "total_items": 150,
    "total_pages": 8,
    "has_next": true,
    "has_prev": false
  }
}
```

### Headers Requeridos

| Header             | Valor                  | Requerido   |
| ------------------ | ---------------------- | ----------- |
| `Content-Type`     | `application/json`     | Sí          |
| `Authorization`    | `Bearer <token>`       | Sí          |
| `X-Request-ID`     | UUID v4 (idempotencia) | Recomendado |
| `X-Client-Version` | Versión del cliente    | Opcional    |

## 4. Endpoints

---

### 4.1 `POST /resources`

**Descripción:** Crea un nuevo recurso.

**Roles requeridos:** `admin`, `operator`

**Request Body:**

```json
{
  "name": "string (requerido, 3-100 chars)",
  "type": "string (enum: 'TYPE_A' | 'TYPE_B' | 'TYPE_C')",
  "amount": "number (requerido, > 0, max 2 decimales)",
  "metadata": {
    "key": "string (opcional)"
  }
}
```

**Validaciones:**

| Campo | Regla | Error si falla |
|---|---|---|
| `name` | Requerido, 3-100 caracteres | `VALIDATION_ERROR` |
| `type` | Debe ser uno de los enum definidos | `VALIDATION_ERROR` |
| `amount` | Requerido, positivo, máximo 2 decimales | `VALIDATION_ERROR` |

**Respuesta exitosa (201):**

```json
{
  "success": true,
  "data": {
    "id": "res_abc123",
    "name": "Ejemplo",
    "type": "TYPE_A",
    "amount": 150.50,
    "status": "PENDING",
    "created_at": "2026-03-01T10:00:00Z",
    "updated_at": "2026-03-01T10:00:00Z"
  }
}
```

**Errores posibles:**

| Status | Código | Cuándo |
|---|---|---|
| 400 | `VALIDATION_ERROR` | Campos inválidos |
| 401 | `UNAUTHORIZED` | Token inválido |
| 409 | `CONFLICT` | Recurso duplicado |

**Ejemplo cURL:**

```bash
curl -X POST https://api.ejemplo.com/v1/resources \
  -H "Authorization: Bearer eyJhbG..." \
  -H "Content-Type: application/json" \
  -H "X-Request-ID: 550e8400-e29b-41d4-a716-446655440000" \
  -d '{
    "name": "Ejemplo",
    "type": "TYPE_A",
    "amount": 150.50
  }'
```

---

### 4.2 `GET /resources`

**Descripción:** Lista recursos con filtros y paginación.

**Roles requeridos:** `admin`, `operator`, `viewer`

**Query Parameters:**

| Parámetro | Tipo | Default | Descripción |
|---|---|---|---|
| `page` | integer | 1 | Número de página |
| `page_size` | integer | 20 | Items por página (máx 100) |
| `status` | string | — | Filtrar por status |
| `type` | string | — | Filtrar por tipo |
| `created_after` | ISO-8601 | — | Filtrar por fecha de creación |
| `created_before` | ISO-8601 | — | Filtrar por fecha de creación |
| `sort_by` | string | `created_at` | Campo de ordenamiento |
| `sort_order` | string | `desc` | `asc` o `desc` |
| `search` | string | — | Búsqueda por texto en name |

---

### 4.3 `GET /resources/{id}`

**Descripción:** Obtiene un recurso por su ID.

**Path Parameters:**

| Parámetro | Tipo | Descripción |
|---|---|---|
| `id` | string | ID único del recurso (formato: `res_xxxxx`) |

---

### 4.4 `PATCH /resources/{id}`

**Descripción:** Actualiza parcialmente un recurso.

**Nota:** Los campos `type`, `amount`, `status` no son editables directamente. El status se cambia mediante acciones específicas.

---

### 4.5 `POST /resources/{id}/actions/{action}`

**Descripción:** Ejecuta una acción que cambia el estado del recurso.

**Acciones disponibles:**

| Acción | Descripción | Status Requerido | Status Resultante | Rol Mínimo |
|---|---|---|---|---|
| `approve` | Aprueba el recurso | `PENDING` | `APPROVED` | `admin` |
| `reject` | Rechaza el recurso | `PENDING` | `REJECTED` | `admin` |
| `process` | Inicia procesamiento | `APPROVED` | `PROCESSING` | `operator` |
| `complete` | Marca como completado | `PROCESSING` | `COMPLETED` | `system` |
| `cancel` | Cancela el recurso | `PENDING`, `APPROVED` | `CANCELLED` | `operator` |

**Diagrama de Estados:**

```
                 ┌──────────┐
                 │ PENDING  │
                 └────┬─────┘
              ┌───────┼───────┐
              ▼       │       ▼
        ┌──────────┐  │  ┌──────────┐
        │ APPROVED │  │  │ REJECTED │
        └────┬─────┘  │  └──────────┘
             │        │
             ▼        ▼
       ┌───────────┐ ┌───────────┐
       │PROCESSING │ │ CANCELLED │
       └─────┬─────┘ └───────────┘
             │
             ▼
       ┌───────────┐
       │ COMPLETED │
       └───────────┘
```

---

## 5. Webhooks (si aplica)

| Evento | Descripción | Payload |
|---|---|---|
| `resource.created` | Recurso creado | Objeto recurso completo |
| `resource.status_changed` | Cambio de estado | Objeto con old/new status |
| `resource.completed` | Recurso completado | Objeto recurso completo |

## 6. Rate Limiting

| Tier | Límite | Ventana |
|---|---|---|
| Standard | 100 requests | Por minuto |
| Premium | 1000 requests | Por minuto |
| Burst | 10 requests | Por segundo |

## 7. Versionado

El API usa versionado por URL path: `/v1/`, `/v2/`.

Política de deprecación: las versiones anteriores se mantienen por 12 meses después de publicar una nueva versión major.

---

## Historial de Cambios

| Versión | Fecha | Cambios |
|---|---|---|
| 1.0 | YYYY-MM-DD | Versión inicial |

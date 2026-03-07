# [NOMBRE DEL DOMINIO] — Data Model Specification

## Metadata

| Campo | Valor |
|---|---|
| **Autor** | [Nombre] |
| **Estado** | `DRAFT` / `IN_REVIEW` / `APPROVED` / `DEPRECATED` |
| **Versión** | 1.0 |
| **Fecha** | YYYY-MM-DD |
| **Base de Datos** | [MongoDB 7.x / PostgreSQL 16 / etc.] |
| **Tech Design Relacionado** | [Link al Tech Design] |

---

## 1. Visión General del Modelo

[Descripción del dominio de datos: qué entidades existen, cómo se relacionan, y qué patrones de acceso son prioritarios.]

### Diagrama de Relaciones

```
┌──────────────┐       ┌──────────────┐       ┌──────────────┐
│    User      │──1:N─▶│   Resource   │──1:N─▶│  Transaction │
└──────────────┘       └──────┬───────┘       └──────────────┘
                              │ N:1
                       ┌──────▼───────┐
                       │   Category   │
                       └──────────────┘
```

## 2. Colecciones / Tablas

### 2.1 `resources`

**Propósito:** [Descripción]
**Volumen estimado:** [Ej: ~100K documentos iniciales, crecimiento ~5K/mes]
**Patrón de acceso principal:** [Ej: Lectura frecuente por ID y por user_id]

#### Schema

```json
{
  "_id": "ObjectId",
  "resource_id": "string (unique, formato: res_xxxxx)",
  "user_id": "string (referencia a users)",
  "category_id": "string (referencia a categories)",
  "name": "string (requerido, 3-100 chars)",
  "type": "string (enum: 'TYPE_A' | 'TYPE_B' | 'TYPE_C')",
  "status": "string (enum: 'PENDING' | 'APPROVED' | 'PROCESSING' | 'COMPLETED' | 'REJECTED' | 'CANCELLED')",
  "amount": {
    "value": "Decimal128 (requerido, > 0)",
    "currency": "string (ISO 4217, default: 'VES')"
  },
  "metadata": {
    "source": "string (opcional)",
    "reference_number": "string (opcional)",
    "custom_fields": "object (flexible, máx 10 keys)"
  },
  "status_history": [
    {
      "status": "string",
      "changed_at": "ISODate",
      "changed_by": "string (user_id o 'system')",
      "reason": "string (opcional)"
    }
  ],
  "created_at": "ISODate",
  "updated_at": "ISODate",
  "deleted_at": "ISODate | null (soft delete)"
}
```

#### Campos: Detalle

| Campo | Tipo | Requerido | Default | Descripción |
|---|---|---|---|---|
| `_id` | ObjectId | Auto | Auto | ID interno de MongoDB |
| `resource_id` | string | Sí | Generado | ID público, formato `res_` + nanoid(12) |
| `user_id` | string | Sí | — | Referencia al usuario propietario |
| `name` | string | Sí | — | Nombre legible, 3-100 caracteres |
| `type` | string | Sí | — | Tipo del recurso (enum definido) |
| `status` | string | Sí | `PENDING` | Estado actual en el ciclo de vida |
| `amount.value` | Decimal128 | Sí | — | Monto numérico con precisión decimal |
| `amount.currency` | string | Sí | `VES` | Código ISO 4217 de moneda |

#### Índices

| Nombre | Campos | Tipo | Justificación |
|---|---|---|---|
| `idx_resource_id` | `{ resource_id: 1 }` | Unique | Lookup por ID público |
| `idx_user_status` | `{ user_id: 1, status: 1 }` | Compound | Query: "recursos de usuario X con status Y" |
| `idx_created_at` | `{ created_at: -1 }` | Single | Paginación y ordenamiento por fecha |
| `idx_type_status` | `{ type: 1, status: 1, created_at: -1 }` | Compound | Dashboard: filtros por tipo + status |

**Nota sobre índices:** El orden de campos en compound index importa. Pon primero campos de igualdad, luego rango, luego sort.

#### Validaciones a Nivel de Base de Datos

```javascript
db.createCollection("resources", {
  validator: {
    $jsonSchema: {
      bsonType: "object",
      required: ["resource_id", "user_id", "name", "type", "status", "amount", "created_at"],
      properties: {
        resource_id: { bsonType: "string", pattern: "^res_[a-zA-Z0-9]{12}$" },
        status: { enum: ["PENDING", "APPROVED", "PROCESSING", "COMPLETED", "REJECTED", "CANCELLED"] },
        "amount.value": { bsonType: "decimal" },
        "amount.currency": { bsonType: "string", minLength: 3, maxLength: 3 }
      }
    }
  }
});
```

---

## 3. Relaciones entre Entidades

| Desde | Hacia | Tipo | Campo FK | Descripción |
|---|---|---|---|---|
| `resources` | `users` | N:1 | `user_id` | Cada recurso pertenece a un usuario |
| `transactions` | `resources` | N:1 | `resource_id` | Cada transacción opera sobre un recurso |

**Nota:** MongoDB no enforce foreign keys nativamente. La integridad se garantiza a nivel de aplicación.

## 4. Queries Críticas

### Q1: Obtener recurso por ID público

```javascript
db.resources.findOne({ resource_id: "res_abc123", deleted_at: null })
// Índice: idx_resource_id | Frecuencia: ~10K/día | Target: < 5ms
```

### Q2: Listar recursos de un usuario con filtros

```javascript
db.resources.find({ user_id: "usr_xyz", status: "PENDING", deleted_at: null })
  .sort({ created_at: -1 }).limit(20)
// Índice: idx_user_status | Frecuencia: ~5K/día | Target: < 20ms
```

### Q3: Dashboard — agregación por tipo y status

```javascript
db.resources.aggregate([
  { $match: { deleted_at: null, created_at: { $gte: ISODate("2026-03-01") } } },
  { $group: { _id: { type: "$type", status: "$status" }, count: { $sum: 1 }, total: { $sum: "$amount.value" } } }
])
// Índice: idx_type_status | Frecuencia: ~100/día | Target: < 500ms
```

## 5. Migración de Datos

### 5.1 Scripts de Migración

Los scripts viven en `scripts/migrations/` con formato: `YYYY-MM-DD_description.js`

Cada script tiene funciones `up()` y `down()`.

### 5.2 Rollback de Migración

Siempre documentar el script inverso para cada migración.

## 6. Estrategias de Archivado

| Colección | Criterio de Archivado | Destino | Frecuencia |
|---|---|---|---|
| `transactions` | `completed_at` > 90 días | `transactions_archive` | Mensual |
| `resources` | COMPLETED + `updated_at` > 1 año | Cold storage | Trimestral |

## 7. Backup y Recuperación

| Aspecto | Configuración |
|---|---|
| **Backup completo** | [Ej: Diario, mongodump a S3] |
| **Backup incremental** | [Ej: Oplog continuo] |
| **Retención** | [Ej: 30 días completos, 7 días incrementales] |
| **RPO** | [Ej: < 1 hora] |
| **RTO** | [Ej: < 4 horas] |

---

## Historial de Cambios

| Versión | Fecha | Autor | Cambios |
|---|---|---|---|
| 1.0 | YYYY-MM-DD | [Nombre] | Versión inicial |

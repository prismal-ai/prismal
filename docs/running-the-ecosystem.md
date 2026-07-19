# Correr el ecosistema completo

Cómo levantar todos los componentes de `prismal-ai` interconectados: **1 motor embebido + 1 host con estado + N faces sin estado**.

```
usuario ──► face (tui / dashboard / webchat / chatbot)
                │  prismal-sdk (REST + SSE, tipado)
                ▼
        prismal-server (:8000, REST · SSE · A2A)
                │  build_runtime() / get_async_compiled_graph()
                ▼
        prismal (motor: LangGraph SUPERVISOR, 30 rutas, RAG, seguridad)
```

Principios: un solo proceso posee el motor y el estado (checkpointer); las faces son intercambiables y sin estado; una conversación = un `thread_id`, compartible entre faces.

## 1. Motor — `prismal` (librería, no proceso)

El motor no abre puertos: lo embebe el host.

```bash
pip install prismal-ai            # import: prismal.*
# o contenedor: ghcr.io/prismal-ai/prismal
```

Las capacidades opt-in se activan por `Settings`/env antes de arrancar el host: `multimodal_enabled`, `kokoro_enabled`, `skynet_enabled` (y S+: `skynet_specialists_enabled`, `skynet_remote_workers_enabled`), `blind_review_pipeline_enabled`, `budget_enabled`, `a2a_enabled`. Con todos los flags off, el grafo compilado es byte-for-byte el mismo.

## 2. Host — `prismal-server` (único proceso con estado)

```bash
cd prismal-server
uv pip install -e ".[dev]"        # pin del motor: prismal-ai>=3.10.2,<4
uvicorn prismal_server.app:app    # :8000
```

El lifespan llama `build_runtime()` **una vez** (tool provider, vector store, embeddings, checkpointer, audit; `org_id` para multi-tenancy) y sirve:

| Endpoint | Función |
|---|---|
| `GET /healthz` · `/readyz` | liveness / readiness |
| `POST /threads` | crear conversación |
| `POST /threads/{id}/messages` | turno con streaming SSE (`token` / `tool_call` / `state` / `done` / `error`, heartbeat 15 s) |
| `GET /.well-known/agent-card.json` | Agent Card A2A |
| `POST /a2a` | JSON-RPC / SSE A2A inbound |

Al apagar, cada `RuntimeContext` se cierra con `aclose()`.

## 3. Faces — los cuatro front-ends

Todos hablan **solo** `prismal-sdk` (`AsyncPrismalClient`); ninguno importa `prismal` ni `prismal_server`. Cada uno apunta al host por env (default `http://localhost:8000`) más su token (`SecretStr`, server-side).

| Componente | Arranque | Env clave | Notas |
|---|---|---|---|
| `prismal-tui` | `prismal-tui` | `PRISMAL_TUI_SERVER_URL`, `PRISMAL_TUI_TOKEN` | Textual en la terminal; `Esc` cancela el turno |
| `prismal-webchat` | `reflex run` | `PRISMAL_WEBCHAT_SERVER_URL`, `PRISMAL_WEBCHAT_EMBED_ORIGINS` | BFF; `<script src=".../embed.js">` en el sitio; CSP `frame-ancestors` |
| `prismal-dashboard` | `reflex run` | `PRISMAL_DASHBOARD_SERVER_URL`, `PRISMAL_DASHBOARD_ADMIN_PASSPHRASE` | Login del operador; status + consola en `http://localhost:3000` |
| `prismal-chatbot` | `python -m prismal_chatbot` | `PRISMAL_CHATBOT_SERVER_URL`, tokens Slack/Discord | Socket Mode / Gateway; un asyncio task por adapter |

## 4. Verificación end-to-end

```bash
curl localhost:8000/healthz && curl localhost:8000/readyz
curl localhost:8000/.well-known/agent-card.json        # A2A activo
curl -N -X POST localhost:8000/threads/t-1/messages \
     -d '{"content":"Hola","role":"user"}'             # stream SSE de prueba
```

Con eso: la TUI conversa desde la terminal, el webchat embebido desde cualquier página, el dashboard monitorea salud y Agent Card, los bots responden menciones en Slack/Discord — todos contra el mismo motor y el mismo historial por `thread_id`. Otros agentes A2A externos pueden descubrir el Agent Card y delegar tareas por `POST /a2a`.

## Orden de arranque y apagado

1. Arrancar `prismal-server`; esperar `readyz`.
2. Arrancar las faces necesarias (independientes entre sí; una face caída no afecta al resto).
3. Apagar: faces primero (opcional), luego el server (cierra `RuntimeContext` y el checkpointer limpiamente).

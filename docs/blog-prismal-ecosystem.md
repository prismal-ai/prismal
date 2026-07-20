# prismal: anatomía de un ecosistema de agentes — del motor a las cuatro caras

*Estado del proyecto a julio de 2026 — prismal v3.12.0*

Hace unos meses `prismal` era un paquete dentro de un monorepo. Hoy es una organización de 8 repositorios con una idea central muy simple: **la inteligencia vive en un solo lugar, y todo lo demás son caras intercambiables**. Este artículo recorre la arquitectura completa: el motor, su modelo de agentes, RAG, seguridad, las fases más recientes (v3.11 y v3.12), el host de referencia, el SDK y los cuatro front-ends recién especificados.

## Qué es prismal

`prismal` (PyPI: `prismal-ai`, import `prismal.*`) es un framework de orquestación multi-agente construido sobre LangGraph. Es una librería pura — sin servidor web, sin dashboard, sin CLI — pensada para ser embebida. Sus señas de identidad: security-first (5 capas de defensa más 18 módulos de hardening), provider-agnostic vía LiteLLM, completamente composable (patterns + subgraphs + extension surface) y empaquetada como namespace package PEP 420, sin `__init__.py`. A la fecha: 21 specs SDD implementadas, 30 rutas de agentes, 7 motores RAG y cero deuda de especificación en el motor.

## El corazón: un supervisor LangGraph con 30 rutas

El núcleo es un `StateGraph[AgentState]` con un `supervisor_node` central que enruta cada turno a una de 30 rutas: 15 agentes base siempre activos (researcher, coder, codeact, cua, rag_agent, planner, critic, data_analyst, file_manager, skill_manager, cron_manager, dev_pipeline, ml_pipeline, financial_analyst, parallel_researcher), 6 patterns avanzados y 5 subgraphs de dominio opt-in (`enable_subgraphs`), y 4 rutas especiales con flag propio: `multimodal_pipeline`, `kokoro`, `skynet` y — la más nueva — `blind_review_pipeline`. Antes del LLM actúa un router de intents determinista por regex; un modo jerárquico opcional reduce el ruteo raíz a 3 orquestadores de dominio.

Los 7 patterns de razonamiento (Tree of Thoughts, Debate, Constitutional AI, LATS/MCTS, LLM-Compiler, Mixture of Agents, Swarm/Handoff) y los 15 subgraph pipelines comparten un mismo principio de diseño: la lógica de negocio acepta callables inyectables (`generate_fn`, `evaluate_fn`, `tool_executor`…), de modo que todo se testea sin backend LLM.

En RAG, el motor ofrece 7 engines (HyDE, RAG-Fusion, Hybrid BM25+semántico, Self-RAG, Hierarchical, Multi-Vector y un facade Adaptive) más CRAG, RAG multimodal y búsqueda federada — todos tipados contra `VectorStorePort`, con Chroma por defecto y adapters opt-in para LanceDB, sqlite-vec, Qdrant y pgvector.

## Arquitectura hexagonal por dentro

Una serie de fases invirtió las dependencias del motor en puertos formales: las tools llegan por `ToolProviderPort` (los agentes ya no importan MCP ni skills), el vector store por `VectorStorePort`, la configuración por `ConfigSourcePort`, y un composition root (`build_runtime()` → `RuntimeContext`) ensambla todos los puertos por tenant (`org_id`) con teardown coordinado. Encima corren capas opt-in que no alteran el grafo cuando están apagadas (verificado con snapshot tests): multimodal (visión/audio/video), Kokoro (deliberación de tres "almas" y un juez), Skynet (swarm map-reduce), gobernanza de presupuesto (`CostMeter` + `BudgetGuard`), identidad DID e interoperabilidad A2A bidireccional.

La seguridad es defensa en profundidad: `InputSanitizer` → `GuardrailsEngine` → NeMo Rails → `ActionInterceptor` → `AuditLogger` (JSONL con hash-chain), con `SecurePromptBuilder` aislando todo input de usuario con canary tokens, y módulos de hardening como taint tracking, detección de inyección indirecta, `RunawayGuard`, sanitización de PII y validación de outputs, cada uno en modo off/warn/enforce.

## Lo nuevo en v3.11 y v3.12

**Blind Review Pipeline (v3.11.0).** Un subgraph de desarrollo con revisión ciega: un spec agent produce la especificación, un implementer construye el artefacto, y dos reviewers independientes lo evalúan viendo únicamente `(spec, artifact)` — jamás el historial de mensajes ni el razonamiento del implementer. La ceguera se garantiza por triple vía: contrato de entrada estrecho, un AST guard bloqueante en CI y un `BlindnessGuard` en runtime. Una síntesis determinista fusiona veredictos y aprueba (con HITL opcional) o devuelve correcciones en un loop acotado. Cada rol puede usar su propio LLM y su propio alcance de tools.

**Skynet S+ (v3.12.0).** El swarm de Fase S se vuelve heterogéneo, medido y remoto — componiendo primitivas que ya existían. Cada worker puede ser un especialista con modelo, persona y tools propios (un `RoleRegistry` cargado de YAML); un único `CostMeter` compartido hace que `skynet_token_budget` acote *todo* el swarm de forma veraz; y un rol puede ser un agente A2A remoto, con allowlist, sanitización L1 de todo lo que vuelve y auditoría hash-first. Sin topología nueva: mismo plan → fan-out → reduce → evaluate.

**Higiene de dependencias.** Una spec dedicada analizó las 18 alertas de Dependabot una a una contra el `uv.lock` real y la superficie de ejecución real (prismal es librería: los CVEs del proxy de LiteLLM o del servidor HTTP de ChromaDB no le aplican). Resultado: ~11 ya resueltas en el lock, 4 upgrades reales, 2 mitigaciones documentadas en `.trivyignore` y un incidente de supply chain en CI remediado.

## El host y el SDK

`prismal-server` es el host de referencia: un proceso FastAPI/ASGI que arranca el motor una vez y lo sirve por REST + SSE + A2A, con exactamente cuatro seams hacia el motor (composición, ejecución, A2A inbound, identidad) y una regla de oro: *orquestar, no reimplementar*. Su v0.1 está completa: threads, streaming SSE con eventos nombrados y heartbeat, Agent Card en `/.well-known/agent-card.json`, auth y tenancy.

`prismal-sdk` es el cliente Python tipado de ese contrato: `PrismalClient` y `AsyncPrismalClient` espejados sobre `httpx`, eventos pydantic por frame SSE, soporte A2A, y cero dependencia del motor o del host — se verifica contra el contrato documentado, nunca contra el código fuente.

## Las cuatro caras: "a face, not a brain"

La novedad más reciente del ecosistema son las SDD completas (PLAN + ARCHITECTURE + SPEC + TASKS, 2026-07-19) de los cuatro front-ends. Todos comparten el mismo patrón: cero imports del motor o del host, todo el tráfico por `prismal-sdk`, texto del usuario sin modificar, historial solo en el checkpointer del motor (localmente solo el mapeo conversación↔`thread_id`), secretos `SecretStr` server-side, errores traducidos a texto amigable y TDD offline contra `FakeAsyncPrismalClient` — con un AST boundary-guard en CI vigilando la frontera en cada repo.

Cada cara resuelve su propio problema: **prismal-tui** (Textual) es el cliente de terminal del desarrollador, con render coalescido por frames y cancelación con `Esc`; **prismal-dashboard** (Reflex) es la consola del operador — login por passphrase con comparación constant-time, panel de salud y Agent Card, consola de conversaciones; **prismal-webchat** (Reflex) es el widget embebible vía `embed.js` + iframe con CSP estricta y sin ningún secreto en el navegador; y **prismal-chatbot** puentea Slack (Socket Mode) y Discord (Gateway) con un bridge platform-agnostic, mapeo de threads persistido *antes* del primer envío y streaming throttled para respetar los rate limits de las plataformas.

## Correrlo todo junto

El despliegue completo es deliberadamente aburrido: `pip install prismal-ai` (el motor no abre puertos), `uvicorn prismal_server.app:app` (el único proceso con estado, `build_runtime()` una vez en el lifespan), y luego cada cara apuntada por env `PRISMAL_<APP>_SERVER_URL` — `prismal-tui`, `reflex run` para webchat y dashboard, `python -m prismal_chatbot`. La verificación es un `curl` a `/healthz`, el Agent Card y un mensaje SSE de prueba. Una conversación es un `thread_id`, y como el estado vive en el motor, cualquier cara puede retomarla. Guía paso a paso en `docs/running-the-ecosystem.md`.

## Dónde estamos y qué sigue

El motor está listo: v3.12.0, 21/21 specs, cero cambios al grafo con los flags off. El server v0.1 está completo y el SDK funcional a falta de su contract suite y release. Los próximos pasos son publicar `prismal-server` v0.1.0 y cerrar `prismal-sdk` v0.1.0, e implementar los cuatro front-ends sobre sus SDD — cada fase futura del motor nacerá, como todas las anteriores, como una SDD nueva antes de escribir una línea de código.

---

*prismal-ai · github.com/prismal-ai/prismal · pypi.org/project/prismal-ai*

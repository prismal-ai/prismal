"""
RAG Federado — Búsqueda multi-nodo con fusión y re-ranking
==========================================================
Arquitectura: SPEC-021 / T-213 / prismal.rag.federated

Dataset: corpus sintético multi-nodo (estilo base de conocimiento corporativa)
  • 3 nodos simulados: local (conocimiento general), nodo-ingenieria y
    nodo-investigacion, más un nodo-archivado caído (tolerancia a fallos)
  • 9 documentos deterministas repartidos por dominio, con una réplica
    compartida (mismo chunk_id en dos nodos) para demostrar la deduplicación
  • Por qué: la federación RAG está pensada para organizaciones con el
    conocimiento distribuido en varios nodos Prismal; cada nodo indexa su
    dominio y las consultas se resuelven en paralelo sobre todos ellos.

Descripción de la arquitectura federada:
  1. FederatedRAGEngine consulta el motor RAG local (búsqueda síncrona)
  2. Lanza en paralelo (asyncio.gather) una consulta por nodo remoto
  3. Los nodos caídos se omiten — se devuelven resultados parciales
     en lugar de propagar la excepción
  4. _merge_and_rerank deduplica por chunk_id (conserva el score más alto)
     y ordena por relevance_score descendente

  En producción cada nodo remoto expone GET /api/v1/rag/search con auth
  JWT (SPEC-018 RBAC); aquí se sobreescribe _query_remote_node con corpus
  en memoria para ejecutar el ejemplo 100% offline y sin LLM.

Uso:
    uv run python examples/rag/10_federated_rag.py
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

from prismal.rag.crag import RetrievedChunk
from prismal.rag.federated import FederatedRAGEngine, _merge_and_rerank

# ── Dataset: corpus determinista por nodo ─────────────────────────────────────
# El chunk "kb-rag-100" está replicado en local y nodo-investigacion para
# demostrar la deduplicación por chunk_id (se conserva el score más alto).
LOCAL_CORPUS = [
    {
        "chunk_id": "kb-despliegue-001",
        "text": (
            "El despliegue a producción requiere la aprobación del equipo de "
            "plataforma, pruebas de regresión completas y una ventana de "
            "mantenimiento anunciada con 24 horas de antelación."
        ),
    },
    {
        "chunk_id": "kb-onboarding-002",
        "text": (
            "El proceso de onboarding de nuevos ingenieros incluye acceso al "
            "repositorio, configuración del entorno local y una sesión de "
            "arquitectura durante la primera semana."
        ),
    },
    {
        "chunk_id": "kb-rag-100",
        "text": (
            "La recuperación federada en arquitecturas RAG combina resultados "
            "de múltiples nodos y los reordena por relevancia para responder "
            "con conocimiento distribuido."
        ),
    },
]

REMOTE_NODES: list[dict[str, Any]] = [
    {
        "name": "nodo-ingenieria",
        "url": "https://ingenieria.example.internal",
        "corpus": [
            {
                "chunk_id": "ing-latencia-010",
                "text": (
                    "Para optimizar la latencia de inferencia del modelo se "
                    "recomienda cuantización INT8, batching dinámico y caché "
                    "de prefijos en el servidor de inferencia."
                ),
            },
            {
                "chunk_id": "ing-observabilidad-011",
                "text": (
                    "La observabilidad del clúster usa OpenTelemetry para "
                    "trazas distribuidas y Prometheus para métricas de "
                    "latencia, saturación y errores."
                ),
            },
            {
                "chunk_id": "ing-ci-012",
                "text": (
                    "El pipeline de integración continua ejecuta linters, "
                    "pruebas unitarias y análisis de seguridad en cada commit "
                    "antes de permitir el merge."
                ),
            },
        ],
    },
    {
        "name": "nodo-investigacion",
        "url": "https://investigacion.example.internal",
        "corpus": [
            {
                "chunk_id": "inv-agentes-020",
                "text": (
                    "Los agentes con arquitectura supervisor delegan tareas en "
                    "especialistas y devuelven el control tras cada turno, lo "
                    "que facilita la trazabilidad de las decisiones."
                ),
            },
            {
                # Réplica de kb-rag-100 (mismo chunk_id, texto ligeramente
                # distinto) → la fusión conserva la versión con mayor score.
                "chunk_id": "kb-rag-100",
                "text": (
                    "La recuperación federada en arquitecturas RAG permite "
                    "consultar en paralelo nodos con conocimiento distribuido "
                    "y fusionar los rankings resultantes."
                ),
            },
            {
                "chunk_id": "inv-eval-021",
                "text": (
                    "La evaluación de sistemas RAG usa métricas de recall, "
                    "precisión de contexto y fidelidad de la respuesta "
                    "generada frente a las fuentes recuperadas."
                ),
            },
        ],
    },
    {
        # Nodo caído: demuestra que la federación devuelve resultados
        # parciales en lugar de fallar (SPEC-021 criterio 3).
        "name": "nodo-archivado",
        "url": "https://archivado.example.internal",
        "corpus": [],
        "offline": True,
    },
]

FEDERATED_QUERIES = [
    {
        "id": "FQ1",
        "query": "¿Qué aprobación requiere el despliegue a producción?",
        "expected_chunk": "kb-despliegue-001",
        "expected_node": "local",
    },
    {
        "id": "FQ2",
        "query": "¿Cómo optimizar la latencia de inferencia del modelo?",
        "expected_chunk": "ing-latencia-010",
        "expected_node": "nodo-ingenieria",
    },
    {
        "id": "FQ3",
        "query": "recuperación federada de conocimiento distribuido en arquitecturas RAG",
        "expected_chunk": "kb-rag-100",
        "expected_node": "*",  # réplica: puede ganar cualquiera de los dos nodos
    },
]


# ── Scoring determinista por solapamiento de tokens ───────────────────────────


def _tokens(text: str) -> set[str]:
    """Tokeniza en minúsculas y descarta palabras de ≤2 caracteres."""
    return {t for t in re.findall(r"\w+", text.lower()) if len(t) > 2}


def _keyword_search(
    corpus: list[dict[str, str]],
    node_name: str,
    query: str,
    k: int,
) -> list[RetrievedChunk]:
    """Búsqueda determinista: score = |query ∩ doc| / |query| en [0, 1]."""
    q_tokens = _tokens(query)
    if not q_tokens:
        return []

    scored: list[RetrievedChunk] = []
    for doc in corpus:
        overlap = len(q_tokens & _tokens(doc["text"]))
        score = round(overlap / len(q_tokens), 4)
        if score > 0.0:
            scored.append(
                RetrievedChunk(
                    source=node_name,
                    chunk_id=doc["chunk_id"],
                    relevance_score=score,
                    content=doc["text"],
                )
            )

    scored.sort(key=lambda c: c.relevance_score, reverse=True)
    return scored[:k]


# ── Motores offline ───────────────────────────────────────────────────────────


class InMemoryRAGEngine:
    """Sustituto offline del RAGEngine local (misma interfaz search())."""

    def __init__(self, name: str, corpus: list[dict[str, str]]) -> None:
        self._name = name
        self._corpus = corpus

    def search(self, query: str, k: int = 5) -> list[RetrievedChunk]:
        """Búsqueda síncrona sobre el corpus local en memoria."""
        return _keyword_search(self._corpus, self._name, query, k)


class OfflineFederatedRAGEngine(FederatedRAGEngine):
    """FederatedRAGEngine con los nodos remotos servidos desde memoria.

    Sobreescribe únicamente ``_query_remote_node`` (la capa HTTP+JWT);
    el resto del flujo — asyncio.gather, tolerancia a fallos por nodo y
    _merge_and_rerank — es el código real de producción.
    """

    async def _query_remote_node(
        self,
        node: dict[str, Any],
        query: str,
        k: int,
    ) -> list[RetrievedChunk]:
        """Simula GET /api/v1/rag/search contra el corpus del nodo."""
        if node.get("offline"):
            msg = f"nodo '{node['name']}' no responde (connection refused)"
            raise ConnectionError(msg)
        return _keyword_search(node["corpus"], node["name"], query, k)


# ── Demos ─────────────────────────────────────────────────────────────────────


def demo_merge_and_rerank() -> None:
    """Demuestra la fusión: dedup por chunk_id + orden por score descendente."""
    print("\n[Fusión — _merge_and_rerank]")
    print("  Dedup por chunk_id (conserva el score más alto) + orden descendente")
    print()

    chunks = [
        RetrievedChunk("nodo-a", "doc-1", 0.62, "réplica en nodo-a"),
        RetrievedChunk("nodo-b", "doc-1", 0.91, "réplica en nodo-b"),
        RetrievedChunk("local", "doc-2", 0.75, "chunk único local"),
        RetrievedChunk("nodo-a", "doc-3", 0.40, "chunk único remoto"),
    ]

    print(f"  Entrada ({len(chunks)} chunks, doc-1 duplicado):")
    for c in chunks:
        print(f"    [{c.relevance_score:.2f}] {c.chunk_id:<8} ← {c.source}")

    merged = _merge_and_rerank(chunks)
    print(f"\n  Salida ({len(merged)} chunks tras dedup):")
    for c in merged:
        print(f"    [{c.relevance_score:.2f}] {c.chunk_id:<8} ← {c.source}")

    print("\n  Observación: doc-1 sobrevive con 0.91 (nodo-b); la réplica 0.62 se descarta.")


def print_federated_result(query_info: dict, results: list[RetrievedChunk]) -> None:
    """Muestra el ranking fusionado con la procedencia (nodo) de cada chunk."""
    print(f"\n[{query_info['id']}] Query:")
    print(f"  '{query_info['query']}'")

    print("\n  Ranking fusionado (score · chunk_id · nodo):")
    for chunk in results[:5]:
        is_expected = chunk.chunk_id == query_info["expected_chunk"]
        mark = "→ " if is_expected else "  "
        print(
            f"    {mark}[{chunk.relevance_score:.4f}] "
            f"{chunk.chunk_id:<22} ← {chunk.source}"
        )

    top_ids = [c.chunk_id for c in results[:3]]
    found = query_info["expected_chunk"] in top_ids
    print(f"\n  Chunk esperado en top-3: {'✓' if found else '✗'}")
    print("─" * 70)


async def main() -> None:
    print("=" * 70)
    print("  RAG Federado — corpus sintético multi-nodo (offline)")
    print("=" * 70)

    # 1. Fusión y re-ranking en aislamiento
    demo_merge_and_rerank()

    # 2. Configurar el motor federado: local + 2 nodos remotos + 1 caído
    print("\n[Inicialización del motor federado]")
    local = InMemoryRAGEngine("local", LOCAL_CORPUS)
    engine = OfflineFederatedRAGEngine(
        local_engine=local,  # type: ignore[arg-type]  # duck-typed search()
        remote_nodes=REMOTE_NODES,
        timeout_seconds=5,
    )
    total_docs = len(LOCAL_CORPUS) + sum(len(n["corpus"]) for n in REMOTE_NODES)
    print(f"  ✓ Nodo local: {len(LOCAL_CORPUS)} documentos")
    for node in REMOTE_NODES:
        state = "OFFLINE" if node.get("offline") else "activo"
        print(f"  ✓ Remoto '{node['name']}': {len(node['corpus'])} docs ({state})")
    print(f"  ✓ Total federado: {total_docs} documentos en {1 + len(REMOTE_NODES)} nodos")

    # 3. Ejecutar las queries federadas
    print(f"\n[Ejecutando {len(FEDERATED_QUERIES)} queries federadas]")

    correct = 0
    for query_info in FEDERATED_QUERIES:
        results = await engine.search(query_info["query"], k=3)
        print_federated_result(query_info, results)
        if query_info["expected_chunk"] in [c.chunk_id for c in results[:3]]:
            correct += 1

    # 4. Resumen
    print("\n[Resumen]")
    recall_at_3 = correct / len(FEDERATED_QUERIES)
    print(f"  Recall@3: {correct}/{len(FEDERATED_QUERIES)} ({recall_at_3:.0%})")
    print("  El nodo-archivado falló en todas las queries → resultados parciales, sin excepción")

    print("\n[Ventajas de la federación RAG]")
    print("  ✓ Conocimiento distribuido: cada nodo indexa solo su dominio")
    print("  ✓ Paralelismo: todas las consultas remotas via asyncio.gather")
    print("  ✓ Tolerancia a fallos: nodos caídos se omiten (resultados parciales)")
    print("  ✓ Dedup por chunk_id: las réplicas conservan su mejor score")
    print("  ✗ Latencia: acotada por el nodo remoto más lento (timeout configurable)")
    print("  ✗ Requiere auth JWT entre nodos en producción (SPEC-018 RBAC)")


if __name__ == "__main__":
    asyncio.run(main())

"""
Document Generation Subgraph — Generación de documentos técnicos
================================================================
Subgraph: prismal.agents.subgraphs.document_generation

Dataset: Wikipedia Technical Docs + OpenAPI Specifications
  • Temas: API REST Design, Machine Learning Explainability, Zero-Trust Security
  • Referencia: https://en.wikipedia.org/wiki/REST y papers de XAI
  • Por qué: El subgraph document_generation tiene 5 nodos en pipeline lineal
    (planner → researcher → writer → editor → formatter). Los temas técnicos
    tienen estructura predecible (introducción, conceptos clave, ejemplos,
    mejores prácticas, referencias) que permite validar cada nodo del pipeline.
    Cada nodo transforma el documento incrementalmente, lo que hace visible
    el valor de cada etapa.

Descripción del subgraph Document Generation:
  planner → researcher → writer → editor → formatter

  Nodos:
  1. planner    — define estructura del documento (secciones, audiencia, longitud)
  2. researcher — recopila información relevante (LLM + RAG opcional)
  3. writer     — redacta el borrador completo según el plan
  4. editor     — revisa claridad, coherencia, consistencia técnica
  5. formatter  — aplica formato final (markdown/plain/html) y metadatos

Uso:
    uv run python examples/subgraphs/07_document_generation.py
"""

from __future__ import annotations

import asyncio

# Importar con manejo de error
try:
    from prismal.agents.subgraphs.document_generation.builder import (
        build_document_generation_subgraph,
        register_document_generation,
    )

    DOC_GEN_AVAILABLE = True
except ImportError:
    DOC_GEN_AVAILABLE = False

# ── Dataset: solicitudes de documentos técnicos ───────────────────────────────
DOCUMENT_REQUESTS = [
    {
        "id": "DOC-001",
        "title": "Guía de Diseño de APIs REST",
        "topic": "REST API design best practices",
        "audience": "developers intermediate",
        "format": "markdown",
        "sections": [
            "Introducción",
            "Recursos y URIs",
            "Métodos HTTP",
            "Códigos de estado",
            "Versionado",
            "Autenticación",
            "Paginación",
            "Mejores prácticas",
        ],
        "target_length": "1500 words",
        "context": (
            "Guía práctica para desarrolladores backend que quieren diseñar APIs REST "
            "robustas y mantenibles. Debe incluir ejemplos de URIs bien y mal diseñadas."
        ),
    },
    {
        "id": "DOC-002",
        "title": "Introducción a la Explicabilidad en ML (XAI)",
        "topic": "Machine Learning Explainability (SHAP, LIME, feature importance)",
        "audience": "data scientists intermediate",
        "format": "markdown",
        "sections": [
            "¿Por qué XAI?",
            "SHAP Values",
            "LIME",
            "Importancia de features",
            "Casos de uso",
            "Limitaciones",
            "Herramientas",
        ],
        "target_length": "1200 words",
        "context": (
            "Documento para data scientists que entrenan modelos en producción y "
            "necesitan explicar sus predicciones a stakeholders no técnicos."
        ),
    },
    {
        "id": "DOC-003",
        "title": "Zero-Trust Security Architecture",
        "topic": "Zero-trust network security model",
        "audience": "security engineers advanced",
        "format": "markdown",
        "sections": [
            "Core Principles",
            "Identity Verification",
            "Least Privilege",
            "Microsegmentation",
            "Continuous Monitoring",
            "Implementation Roadmap",
        ],
        "target_length": "2000 words",
        "context": (
            "Technical reference for security engineers migrating from perimeter-based "
            "to zero-trust architecture. Includes practical implementation steps."
        ),
    },
]

# ── Conocimiento base para cada tema (simula lo que extrae el researcher) ─────
RESEARCH_KNOWLEDGE = {
    "REST API design best practices": {
        "key_concepts": [
            "Resources are nouns, not verbs: /users not /getUsers",
            "Use HTTP methods semantically: GET (read), POST (create), PUT/PATCH (update), DELETE",
            "HTTP status codes: 200 OK, 201 Created, 400 Bad Request, 401 Unauthorized, "
            "404 Not Found, 422 Unprocessable Entity, 500 Internal Server Error",
            "Versioning strategies: URI (/v1/users), Header (Accept: application/vnd.api.v1+json), "
            "Query param (?version=1)",
            "Pagination: cursor-based (more scalable) vs offset-based (simpler)",
            "Authentication: OAuth 2.0 + JWT for stateless auth",
            "HATEOAS: Hypermedia as the Engine of Application State",
        ],
        "good_examples": [
            "GET /users → list users",
            "GET /users/123 → get user 123",
            "POST /users → create user",
            "PATCH /users/123 → update user 123",
            "DELETE /users/123 → delete user 123",
            "GET /users/123/orders → user's orders",
        ],
        "bad_examples": [
            "GET /getUsers ← verb in URI",
            "POST /deleteUser ← wrong method",
            "GET /users?action=delete ← action as query param",
        ],
        "references": ["Roy Fielding's REST dissertation (2000)", "RFC 7230-7235", "OpenAPI 3.1"],
    },
    "Machine Learning Explainability (SHAP, LIME, feature importance)": {
        "key_concepts": [
            "SHAP (SHapley Additive exPlanations): game-theory based, global + local explanations",
            "LIME (Local Interpretable Model-agnostic Explanations): local surrogate models",
            "Feature importance: permutation importance (model-agnostic), gain importance (tree-based)",
            "Global explainability: understand model overall behavior",
            "Local explainability: explain single prediction",
            "GDPR Article 22: right to explanation for automated decisions",
        ],
        "formulas": [
            "SHAP value φᵢ = Σ [|S|!(|F|-|S|-1)!/|F|!] × [f(S∪{i}) - f(S)]",
            "LIME: arg min L(f, g, πₓ) + Ω(g)",
        ],
        "use_cases": [
            "Credit scoring: why was a loan rejected?",
            "Medical diagnosis: which features drove the prediction?",
            "Fraud detection: explain flagged transaction",
        ],
        "tools": ["shap library", "lime library", "eli5", "Captum (PyTorch)", "InterpretML"],
    },
    "Zero-trust network security model": {
        "key_concepts": [
            "Never trust, always verify — no implicit trust based on network location",
            "Verify explicitly: authenticate and authorize every request",
            "Use least privilege access: minimal necessary permissions",
            "Assume breach: design as if the network is already compromised",
            "Microsegmentation: divide network into small zones",
            "Continuous monitoring: log, inspect, and analyze all traffic",
        ],
        "principles": [
            "Identity is the new perimeter",
            "Device health verification",
            "Application-layer encryption (mTLS)",
            "Just-in-time (JIT) access provisioning",
        ],
        "frameworks": ["NIST SP 800-207", "BeyondCorp (Google)", "Forrester ZTX", "CISA ZTA"],
        "implementation_steps": [
            "1. Identify sensitive data and workflows",
            "2. Map transaction flows",
            "3. Build a Zero Trust architecture",
            "4. Create Zero Trust policy",
            "5. Monitor and maintain",
        ],
    },
}


# ── Simulador del pipeline ────────────────────────────────────────────────────


def simulate_planner(request: dict) -> dict:
    """Simula el nodo planner: genera el plan del documento."""
    return {
        "title": request["title"],
        "audience": request["audience"],
        "sections": request["sections"],
        "estimated_words_per_section": int(request["target_length"].split()[0])
        // len(request["sections"]),
        "format": request["format"],
        "tone": "technical" if "advanced" in request["audience"] else "approachable-technical",
    }


def simulate_researcher(request: dict, plan: dict) -> dict:
    """Simula el nodo researcher: recopila información relevante."""
    kb = RESEARCH_KNOWLEDGE.get(request["topic"], {})
    return {
        "key_concepts": kb.get("key_concepts", []),
        "examples": kb.get("good_examples", []) + kb.get("bad_examples", []),
        "formulas": kb.get("formulas", []),
        "use_cases": kb.get("use_cases", []),
        "tools": kb.get("tools", []),
        "frameworks": kb.get("frameworks", []),
        "references": kb.get("references", []),
        "sources_count": len(kb),
    }


def simulate_writer(request: dict, plan: dict, research: dict) -> str:
    """Simula el nodo writer: redacta el borrador."""
    sections = plan["sections"]
    concepts = research.get("key_concepts", [])
    examples = research.get("examples", [])

    doc = f"# {request['title']}\n\n"
    doc += f"*Audiencia: {plan['audience']} | Formato: {plan['format']}*\n\n"

    for i, section in enumerate(sections):
        doc += f"## {section}\n\n"
        # Añadir contenido simulado por sección
        if i < len(concepts):
            doc += f"{concepts[i]}\n\n"
        if i == 0:
            doc += f"{request['context']}\n\n"
        if examples and i == len(sections) // 2:
            doc += "**Ejemplos:**\n"
            for ex in examples[:3]:
                doc += f"- `{ex}`\n"
            doc += "\n"

    if research.get("references"):
        doc += "## Referencias\n\n"
        for ref in research["references"]:
            doc += f"- {ref}\n"

    return doc


def simulate_editor(draft: str, request: dict) -> tuple[str, list[str]]:
    """Simula el nodo editor: revisa y mejora el borrador."""
    edits = []
    edited = draft

    # Verificar que hay secciones H2
    h2_count = draft.count("\n## ")
    if h2_count < len(request["sections"]) - 1:
        edits.append(f"Añadidas {len(request['sections']) - h2_count} secciones faltantes")

    # Verificar longitud mínima
    word_count = len(draft.split())
    target = int(request["target_length"].split()[0])
    if word_count < target * 0.5:
        edits.append(f"Documento demasiado corto ({word_count} palabras vs {target} objetivo)")

    # Añadir nota de conclusión si falta
    if "Conclusión" not in draft and "Conclusion" not in draft:
        edited += "\n## Conclusión\n\nEste documento cubre los aspectos fundamentales del tema. "
        edited += "Se recomienda complementarlo con la documentación oficial referenciada.\n"
        edits.append("Añadida sección de Conclusión")

    # Verificar coherencia de tono
    if request["audience"].endswith("advanced") and "¿" in draft[:200]:
        edits.append("Ajustado tono: más técnico para audiencia avanzada")

    return edited, edits


def simulate_formatter(document: str, format_type: str) -> str:
    """Simula el nodo formatter: aplica formato final."""
    if format_type == "markdown":
        # Ya está en Markdown, añadir metadatos YAML frontmatter
        frontmatter = "---\nformat: markdown\ngenerator: prismal-document-generation\n---\n\n"
        return frontmatter + document
    if format_type == "html":
        # Conversión básica a HTML
        html = "<html><body>\n"
        for line in document.splitlines():
            if line.startswith("# "):
                html += f"<h1>{line[2:]}</h1>\n"
            elif line.startswith("## "):
                html += f"<h2>{line[3:]}</h2>\n"
            elif line.startswith("- "):
                html += f"<li>{line[2:]}</li>\n"
            elif line.strip():
                html += f"<p>{line}</p>\n"
        html += "</body></html>"
        return html
    # plain
    import re

    return re.sub(r"[#*`_]", "", document)


async def run_document_generation(request: dict) -> dict:
    """Ejecuta el pipeline de generación de documentos."""
    print(f"\n[{request['id']}] {request['title']}")
    print(f"  Audiencia: {request['audience']} | Formato: {request['format']}")
    print(f"  Objetivo : {request['target_length']}")

    if not DOC_GEN_AVAILABLE:
        print("  [Modo demo — subgraph simulado]")

        # Nodo 1: planner
        print("\n  ── Nodo 1: planner ──")
        plan = simulate_planner(request)
        print(f"    Secciones planificadas: {len(plan['sections'])}")
        print(f"    ~{plan['estimated_words_per_section']} palabras/sección")
        print(f"    Tono: {plan['tone']}")

        # Nodo 2: researcher
        print("\n  ── Nodo 2: researcher ──")
        research = simulate_researcher(request, plan)
        print(f"    Conceptos clave encontrados: {len(research.get('key_concepts', []))}")
        print(f"    Ejemplos recopilados        : {len(research.get('examples', []))}")
        if research.get("references"):
            print(f"    Fuentes referenciadas       : {research['references']}")

        # Nodo 3: writer
        print("\n  ── Nodo 3: writer ──")
        draft = simulate_writer(request, plan, research)
        word_count = len(draft.split())
        print(f"    Borrador generado: {word_count} palabras, {len(draft)} chars")
        print(f"    Secciones H2     : {draft.count(chr(10) + '## ')}")

        # Nodo 4: editor
        print("\n  ── Nodo 4: editor ──")
        final_doc, edits = simulate_editor(draft, request)
        if edits:
            print(f"    Revisiones aplicadas ({len(edits)}):")
            for edit in edits:
                print(f"      • {edit}")
        else:
            print("    ✓ Sin revisiones necesarias")
        print(f"    Palabras finales: {len(final_doc.split())}")

        # Nodo 5: formatter
        print("\n  ── Nodo 5: formatter ──")
        formatted_doc = simulate_formatter(final_doc, request["format"])
        print(f"    Formato aplicado  : {request['format']}")
        print(f"    Documento final   : {len(formatted_doc)} chars")

        # Preview del documento
        lines = formatted_doc.splitlines()
        preview_lines = [l for l in lines[:12] if l.strip()][:6]
        print("\n  Preview (primeras líneas):")
        for line in preview_lines:
            print(f"    {line[:75]}")

        return {
            "id": request["id"],
            "title": request["title"],
            "word_count": len(final_doc.split()),
            "sections": plan["sections"],
            "edits_applied": len(edits),
            "format": request["format"],
            "document": formatted_doc,
        }

    # Modo real con subgraph LangGraph
    from langchain_core.messages import HumanMessage

    from prismal.agents.state import initial_state

    await register_document_generation(format=request["format"])
    subgraph = build_document_generation_subgraph(format=request["format"])

    state = initial_state()
    state["messages"] = [
        HumanMessage(
            content=(
                f"Genera un documento técnico sobre: {request['topic']}\n"
                f"Título: {request['title']}\n"
                f"Audiencia: {request['audience']}\n"
                f"Secciones: {', '.join(request['sections'])}\n"
                f"Longitud objetivo: {request['target_length']}\n"
                f"Contexto: {request['context']}"
            )
        )
    ]
    state["metadata"] = {
        "document_generation": {
            "title": request["title"],
            "topic": request["topic"],
            "audience": request["audience"],
            "sections": request["sections"],
        }
    }

    config = {"configurable": {"thread_id": f"docgen_{request['id']}_001"}}
    final_state = await subgraph.graph.ainvoke(state, config=config)

    messages = final_state.get("messages", [])
    doc_meta = final_state.get("metadata", {}).get("document_generation", {})
    return {
        "id": request["id"],
        "title": request["title"],
        "document": str(messages[-1].content) if messages else "",
        "word_count": doc_meta.get("word_count", 0),
    }


async def main() -> None:
    print("=" * 70)
    print("  Document Generation Subgraph — Dataset: Wikipedia Technical Docs")
    print("=" * 70)

    print("\n[Arquitectura del subgraph Document Generation]")
    pipeline = [
        ("planner   ", "Define secciones, audiencia, longitud, tono"),
        ("researcher", "Recopila información: LLM knowledge + RAG (opcional)"),
        ("writer    ", "Redacta borrador completo según el plan"),
        ("editor    ", "Revisa coherencia, claridad, completitud técnica"),
        ("formatter ", "Aplica formato final: markdown / plain / html"),
    ]
    for node, desc in pipeline:
        print(f"  {node}: {desc}")
    print()
    print("  Cada nodo lee/escribe metadata['document_generation']")
    print("  El formatter también añade AIMessage con el documento final")

    print(f"\n[Generando {len(DOCUMENT_REQUESTS)} documentos técnicos]")
    results = []
    for request in DOCUMENT_REQUESTS:
        result = await run_document_generation(request)
        results.append(result)
        print("─" * 70)

    # ── Estadísticas ──────────────────────────────────────────────────────────
    print("\n[Resumen de documentos generados]")
    print(f"  {'ID':<10} {'Título':<35} {'Palabras':>8} {'Edits':>6} {'Formato'}")
    print("  " + "─" * 68)
    for r in results:
        print(
            f"  {r['id']:<10} {r['title'][:34]:<35} {r['word_count']:>8} "
            f"{r['edits_applied']:>6} {r['format']}"
        )

    total_words = sum(r["word_count"] for r in results)
    print(f"\n  Total de palabras generadas: {total_words:,}")
    print(f"  Promedio por documento     : {total_words // len(results):,}")

    # ── Formatos disponibles ──────────────────────────────────────────────────
    print("\n[Formatos de salida disponibles]")
    formats_info = [
        ("markdown", "Texto con sintaxis Markdown — ideal para wikis, GitHub, Confluence"),
        ("plain", "Texto plano sin markup — útil para email, TTS, legacy systems"),
        ("html", "HTML renderizable — para portales web o emails enriquecidos"),
    ]
    for fmt, desc in formats_info:
        print(f"  {fmt:10s}: {desc}")

    print("\n[Casos de uso del Document Generation Subgraph]")
    use_cases = [
        "Documentación técnica automática de APIs (desde OpenAPI spec)",
        "Generación de reportes periódicos (weekly/monthly summaries)",
        "Redacción de RFCs y ADRs (Architecture Decision Records)",
        "Creación de manuales de usuario desde feature specs",
        "Generación de content marketing (blogs técnicos)",
        "Documentación de código (README, CONTRIBUTING, CHANGELOG)",
    ]
    for uc in use_cases:
        print(f"  ✓ {uc}")

    print("\n[Integración con RAG]")
    print("  subgraph = build_document_generation_subgraph(")
    print("      rag_engine=ChromaVectorStore(collection_name='company_docs'),")
    print("      format='markdown',")
    print("  )")
    print("  # El researcher usará RAG para fundamentar el contenido")
    print("  # en documentos internos de la empresa")


if __name__ == "__main__":
    asyncio.run(main())

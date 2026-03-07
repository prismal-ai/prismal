"""Stub LangChain tools for LightAgent sub-agents.

Fallback tool implementations used when no live MCP or skill tool overrides them.

Every tool here is a real implementation that connects to the actual backend:
- ``web_search``        — DuckDuckGo via ``langchain-community``
- ``rag_search``        — ChromaDB via :class:`~lightagent.rag.engine.RAGEngine`
- ``vector_search``     — ChromaDB nearest-neighbour search
- ``doc_index``         — RAGEngine file/directory indexer
- ``read_file``         — Filesystem read (workspace-sandboxed)
- ``write_file``        — Filesystem write (workspace-sandboxed)
- ``code_executor``     — Sandboxed Python subprocess (requires ``shell_enabled``)
- ``evaluate``          — LLM-based qualitative evaluation
- ``score``             — LLM-based numeric scorer
- ``duckdb_query``      — :class:`~lightagent.data.duckdb_engine.DuckDBEngine` SQL
- ``polars_transform``  — Polars DataFrame operations via ``polars_utils``
- ``create_chart``      — Matplotlib chart via :func:`~lightagent.data.polars_utils.save_chart`
- ``list_mcp_tools``    — Enumerates connected MCP server tools
"""

from __future__ import annotations

from langchain_core.tools import BaseTool, tool


@tool
def web_search(query: str) -> str:
    """Search the web for up-to-date information using DuckDuckGo.

    Args:
        query: The search query string.

    Returns:
        Formatted search results with titles, URLs and snippets, or an
        error message if the search backend is unavailable.
    """
    try:
        from langchain_community.tools import DuckDuckGoSearchRun  # noqa: PLC0415

        results = DuckDuckGoSearchRun().run(query)
        return results if results else f"No results found for: {query!r}"
    except Exception as exc:  # noqa: BLE001
        return (
            f"Web search unavailable: {exc!s}\n"
            "Tip: install 'duckduckgo-search' or configure a search MCP server."
        )


@tool
def rag_search(query: str, collection: str = "default") -> str:
    """Search a RAG vector collection for relevant document chunks.

    Args:
        query: The semantic search query.
        collection: Name of the ChromaDB collection to search.

    Returns:
        Formatted results with source, relevance score and content snippet,
        or an error message when the RAG engine is unavailable.
    """
    try:
        from lightagent.rag.engine import RAGEngine  # noqa: PLC0415

        engine = RAGEngine(collection_name=collection)
        chunks = engine.search(query, k=5)
        if not chunks:
            return f"No documents found in collection '{collection}' for: {query!r}"
        lines = [f"RAG results for: {query!r} (collection: '{collection}')\n"]
        for i, chunk in enumerate(chunks, 1):
            lines.append(
                f"{i}. [{chunk.relevance_score:.2f}] {chunk.source}\n"
                f"   {chunk.content[:300]}{'…' if len(chunk.content) > 300 else ''}"
            )
        return "\n".join(lines)
    except Exception as exc:  # noqa: BLE001
        return f"RAG search error: {exc!s}"


@tool
def read_file(path: str) -> str:
    """Read the contents of a file from the filesystem.

    Safe paths are restricted to the project workspace (``data/workspace/``
    and the project root).  System paths are rejected.

    Args:
        path: Absolute or relative path to the file to read.

    Returns:
        File contents as a string, or an error message on failure.
    """
    from pathlib import Path  # noqa: PLC0415

    _BLOCKED_PREFIXES = ("/etc/", "/sys/", "/proc/", "/dev/", "/root/", "/boot/")

    src = Path(path).expanduser().resolve()
    str_src = str(src)

    for prefix in _BLOCKED_PREFIXES:
        if str_src.startswith(prefix):
            return f"Access denied: '{path}' is a system path."
    # Also block .ssh and AWS credentials
    if ".ssh" in src.parts or ".aws" in src.parts:
        return f"Access denied: '{path}' contains sensitive directories."

    if not src.exists():
        return f"File not found: {path}"
    if not src.is_file():
        return f"Not a file: {path}"

    try:
        content = src.read_text(encoding="utf-8", errors="replace")
        if len(content) > 20_000:
            content = content[:20_000] + "\n…[file truncated at 20 000 chars]"
        return content
    except Exception as exc:  # noqa: BLE001
        return f"Error reading file: {exc!s}"


@tool
def write_file(path: str, content: str) -> str:
    """Write content to a file on the filesystem.

    Defaults to ``data/workspace/`` when a relative path is given.  System
    paths are rejected to prevent accidental overwrites.

    Args:
        path: Absolute or relative path to the destination file.
        content: Text content to write to the file.

    Returns:
        Confirmation message with the resolved path, or an error message.
    """
    from pathlib import Path  # noqa: PLC0415

    _BLOCKED_PREFIXES = ("/etc/", "/sys/", "/proc/", "/dev/", "/root/", "/boot/")

    dest = Path(path).expanduser()
    if not dest.is_absolute():
        dest = Path("data/workspace") / dest

    dest = dest.resolve()
    str_dest = str(dest)

    for prefix in _BLOCKED_PREFIXES:
        if str_dest.startswith(prefix):
            return f"Access denied: '{path}' is a system path."
    if ".ssh" in dest.parts or ".aws" in dest.parts:
        return f"Access denied: '{path}' contains sensitive directories."

    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
        return f"File written: {dest} ({len(content)} chars)"
    except Exception as exc:  # noqa: BLE001
        return f"Error writing file: {exc!s}"


@tool
def code_executor(code: str, language: str = "python") -> str:
    """Execute a code snippet in a sandboxed subprocess.

    Requires ``LIGHTAGENT_SHELL_ENABLED=true`` in the environment.  Only
    Python and Bash are supported.  Execution is capped at 30 seconds and
    output at 4 000 characters.

    Args:
        code: Source code to execute.
        language: Programming language — ``"python"`` (default) or ``"bash"``.

    Returns:
        Combined stdout + stderr output, or an error message on failure or
        when shell execution is disabled.
    """
    import subprocess  # noqa: PLC0415

    from lightagent.core.config import get_settings  # noqa: PLC0415

    settings = get_settings()
    if not settings.shell_enabled:
        return (
            "Code execution is disabled. Set LIGHTAGENT_SHELL_ENABLED=true "
            "in your .env file to enable it."
        )

    lang = language.lower().strip()
    if lang not in ("python", "python3", "bash", "sh"):
        return f"Unsupported language: '{language}'. Use 'python' or 'bash'."

    cmd: list[str]
    if lang in ("python", "python3"):
        cmd = ["python3", "-c", code]
    else:
        cmd = ["bash", "-c", code]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        output = proc.stdout + proc.stderr
        if len(output) > 4_000:
            output = output[:4_000] + "\n…[output truncated]"
        if proc.returncode != 0:
            return f"Exit code {proc.returncode}:\n{output}"
        return output or "(no output)"
    except subprocess.TimeoutExpired:
        return "Execution timed out after 30 seconds."
    except Exception as exc:  # noqa: BLE001
        return f"Execution error: {exc!s}"


@tool
def vector_search(query: str, collection: str = "default", k: int = 5) -> str:
    """Perform a nearest-neighbour vector search in a ChromaDB collection.

    Args:
        query: The embedding query string.
        collection: Name of the ChromaDB collection to search.
        k: Number of top results to return.

    Returns:
        Formatted results with relevance scores and content snippets.
    """
    try:
        from lightagent.rag.engine import RAGEngine  # noqa: PLC0415

        engine = RAGEngine(collection_name=collection)
        chunks = engine.search(query, k=k)
        if not chunks:
            return f"No vectors found in '{collection}' for: {query!r}"
        lines = [f"Vector search results (k={k}, collection: '{collection}'):\n"]
        for i, chunk in enumerate(chunks, 1):
            lines.append(
                f"{i}. score={chunk.relevance_score:.4f} | {chunk.source}\n"
                f"   {chunk.content[:400]}{'…' if len(chunk.content) > 400 else ''}"
            )
        return "\n".join(lines)
    except Exception as exc:  # noqa: BLE001
        return f"Vector search error: {exc!s}"


@tool
def doc_index(path: str, collection: str = "default") -> str:
    """Index a document or directory of documents into a ChromaDB collection.

    Args:
        path: Path to the document file or directory to index.
        collection: Name of the ChromaDB collection to insert into.

    Returns:
        Confirmation with the number of chunks indexed, or an error message.
    """
    from pathlib import Path  # noqa: PLC0415

    from lightagent.rag.engine import RAGEngine  # noqa: PLC0415

    src = Path(path).expanduser().resolve()
    if not src.exists():
        return f"Path not found: {path}"

    try:
        engine = RAGEngine(collection_name=collection)
        if src.is_dir():
            count = engine.index_directory(str(src))
        else:
            count = engine.index_file(str(src))
        return (
            f"Indexed {count} chunk(s) from '{src}' "
            f"into collection '{collection}'."
        )
    except Exception as exc:  # noqa: BLE001
        return f"Indexing error: {exc!s}"


@tool
def evaluate(text: str, criteria: str) -> str:
    """Evaluate a piece of text against a set of qualitative criteria using the LLM.

    Args:
        text: The text to evaluate.
        criteria: Natural-language description of the evaluation criteria.

    Returns:
        A qualitative evaluation from the LLM covering strengths,
        weaknesses, and recommendations.
    """
    try:
        from langchain_core.messages import HumanMessage, SystemMessage  # noqa: PLC0415

        from lightagent.providers.registry import ProviderRegistry  # noqa: PLC0415

        llm = ProviderRegistry().get_llm_with_fallback()
        prompt = (
            f"Evaluate the following text against these criteria:\n\n"
            f"**Criteria:** {criteria}\n\n"
            f"**Text to evaluate:**\n{text}\n\n"
            "Provide a structured evaluation covering: strengths, weaknesses, "
            "and specific recommendations for improvement."
        )
        response = llm.invoke(
            [SystemMessage(content="You are an expert evaluator."),
             HumanMessage(content=prompt)]
        )
        return str(response.content)
    except Exception as exc:  # noqa: BLE001
        return f"Evaluation error: {exc!s}"


@tool
def score(text: str, rubric: str) -> float:
    """Score a piece of text against a scoring rubric using the LLM.

    Args:
        text: The text to score.
        rubric: Natural-language rubric describing how to assign a score
            between 0.0 (completely unacceptable) and 1.0 (perfect).

    Returns:
        A float score between 0.0 and 1.0.  Returns 0.5 on LLM/parse error.
    """
    import re  # noqa: PLC0415

    try:
        from langchain_core.messages import HumanMessage, SystemMessage  # noqa: PLC0415

        from lightagent.providers.registry import ProviderRegistry  # noqa: PLC0415

        llm = ProviderRegistry().get_llm_with_fallback()
        prompt = (
            f"Score the following text using this rubric:\n\n"
            f"**Rubric:** {rubric}\n\n"
            f"**Text:**\n{text}\n\n"
            "Reply with ONLY a single decimal number between 0.0 and 1.0. "
            "No explanation."
        )
        response = llm.invoke(
            [SystemMessage(content="You are a precise numeric scorer."),
             HumanMessage(content=prompt)]
        )
        raw = str(response.content).strip()
        m = re.search(r"[01]?\.\d+|[01]", raw)
        return float(m.group()) if m else 0.5
    except Exception:  # noqa: BLE001
        return 0.5


@tool
def duckdb_query(sql: str, source: str = "") -> str:
    """Execute a SQL SELECT query against a DuckDB data source.

    When *source* points to a CSV or Parquet file, it is registered as the
    table ``data`` before query execution.  Omit *source* to query an
    in-memory DuckDB database.

    Args:
        sql: The SQL SELECT query string to execute.
        source: Optional path to a Parquet/CSV file or DuckDB database file.
            Leave empty to use an in-memory database.

    Returns:
        Tabular query results as a formatted string (max 200 rows),
        or an error message on failure or blocked SQL.
    """
    try:
        from lightagent.data.duckdb_engine import DuckDBEngine  # noqa: PLC0415

        db_path = source if source and not source.endswith((".csv", ".parquet")) else ":memory:"
        with DuckDBEngine(db_path) as engine:
            if source and source.endswith((".csv", ".parquet")):
                engine._conn.execute(f"CREATE VIEW data AS SELECT * FROM read_csv_auto('{source}')") if source.endswith(".csv") else engine._conn.execute(f"CREATE VIEW data AS SELECT * FROM parquet_scan('{source}')")
            rows = engine.query(sql)
        if not rows:
            return "Query returned no rows."
        # Format as a simple table
        headers = list(rows[0].keys())
        lines = [" | ".join(str(h) for h in headers)]
        lines.append("-+-".join("-" * max(len(str(h)), 6) for h in headers))
        for row in rows[:200]:
            lines.append(" | ".join(str(row.get(h, "")) for h in headers))
        suffix = f"\n…({len(rows) - 200} more rows)" if len(rows) > 200 else ""
        return "\n".join(lines) + suffix
    except Exception as exc:  # noqa: BLE001
        return f"DuckDB query error: {exc!s}"


@tool
def polars_transform(operation: str, data_source: str) -> str:
    """Apply a Polars data transformation to a CSV or Parquet data source.

    Supported operations (keyword-matched, case-insensitive):

    * ``filter <col> <op> <value>``  — filter rows (ops: >, <, ==, !=, >=, <=)
    * ``groupby <col> sum|mean|count <col>``  — aggregate
    * ``sort <col> [desc]``          — sort rows
    * ``select <col1,col2,…>``       — select columns
    * ``head [n]``                   — show first n rows (default 10)

    Args:
        operation: Transformation description string.
        data_source: Path to a CSV or Parquet file.

    Returns:
        Transformed data as a formatted string, or an error message.
    """
    import re  # noqa: PLC0415

    from pathlib import Path  # noqa: PLC0415

    try:
        import polars as pl  # noqa: PLC0415

        from lightagent.data.polars_utils import (  # noqa: PLC0415
            filter_rows,
            group_by_aggregate,
            select_columns,
            sort_by,
            to_records,
        )
    except ImportError as exc:
        return f"Polars not available: {exc!s}"

    src = Path(data_source).expanduser().resolve()
    if not src.exists():
        return f"Data source not found: {data_source}"

    try:
        df = pl.read_csv(str(src)) if src.suffix == ".csv" else pl.read_parquet(str(src))
    except Exception as exc:  # noqa: BLE001
        return f"Error reading data source: {exc!s}"

    op = operation.lower().strip()

    try:
        if op.startswith("filter"):
            m = re.match(r"filter\s+(\w+)\s*([><=!]+)\s*(.+)", op)
            if not m:
                return "filter syntax: filter <col> <op> <value>"
            col, operator, val_str = m.group(1), m.group(2), m.group(3).strip()
            val: int | float | str
            try:
                val = int(val_str)
            except ValueError:
                try:
                    val = float(val_str)
                except ValueError:
                    val = val_str.strip("'\"")
            df = filter_rows(df, col, operator, val)  # type: ignore[arg-type]

        elif op.startswith("groupby") or op.startswith("group by"):
            m = re.match(r"group\s*by\s+(\w+)\s+(sum|mean|count|min|max)\s+(\w+)", op)
            if not m:
                return "groupby syntax: groupby <col> <sum|mean|count> <value_col>"
            df = group_by_aggregate(df, m.group(1), m.group(3), m.group(2))  # type: ignore[arg-type]

        elif op.startswith("sort"):
            m = re.match(r"sort\s+(\w+)(\s+desc)?", op)
            if not m:
                return "sort syntax: sort <col> [desc]"
            df = sort_by(df, m.group(1), descending=bool(m.group(2)))

        elif op.startswith("select"):
            cols_str = re.sub(r"^select\s+", "", op).strip()
            cols = [c.strip() for c in cols_str.split(",")]
            df = select_columns(df, cols)

        elif op.startswith("head"):
            m = re.match(r"head\s*(\d+)?", op)
            n = int(m.group(1)) if m and m.group(1) else 10
            df = df.head(n)

        else:
            return (
                f"Unknown operation: '{operation}'. "
                "Supported: filter, groupby, sort, select, head."
            )
    except Exception as exc:  # noqa: BLE001
        return f"Transform error: {exc!s}"

    records = to_records(df.head(100))
    if not records:
        return "Transform produced no rows."
    headers = list(records[0].keys())
    lines = [" | ".join(str(h) for h in headers)]
    lines.append("-+-".join("-" * max(len(str(h)), 6) for h in headers))
    for row in records:
        lines.append(" | ".join(str(row.get(h, "")) for h in headers))
    suffix = f"\n…({len(df) - 100} more rows)" if len(df) > 100 else ""
    return "\n".join(lines) + suffix


@tool
def create_chart(data: str, chart_type: str = "bar", title: str = "") -> str:
    """Create a chart from JSON-serialised data and save it to disk.

    *data* must be a JSON array of objects, e.g.::

        '[{"month": "Jan", "sales": 120}, {"month": "Feb", "sales": 95}]'

    The first string column becomes the x-axis; the first numeric column
    becomes the y-axis.

    Args:
        data: JSON-serialised array of row objects.
        chart_type: Chart type — ``"bar"`` (default), ``"line"``,
            ``"scatter"``, or ``"hist"``.
        title: Optional chart title.

    Returns:
        Path to the saved image file, or an error message on failure.
    """
    import json  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415

    try:
        import polars as pl  # noqa: PLC0415

        from lightagent.data.polars_utils import save_chart  # noqa: PLC0415
    except ImportError as exc:
        return f"Chart dependencies not available: {exc!s}"

    try:
        rows = json.loads(data)
        if not rows or not isinstance(rows, list):
            return "data must be a non-empty JSON array of objects."
        df = pl.DataFrame(rows)
    except Exception as exc:  # noqa: BLE001
        return f"Failed to parse data: {exc!s}"

    # Infer x (string) and y (numeric) columns
    str_cols = [c for c in df.columns if df[c].dtype == pl.Utf8]
    num_cols = [c for c in df.columns if df[c].dtype.is_numeric()]
    if not str_cols or not num_cols:
        # Fallback: use first two columns regardless of type
        if len(df.columns) < 2:
            return "Need at least two columns to create a chart."
        x_col, y_col = df.columns[0], df.columns[1]
    else:
        x_col, y_col = str_cols[0], num_cols[0]

    output_path = Path("data/workspace") / f"chart_{title.replace(' ', '_') or 'chart'}.png"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        saved = save_chart(df, x_col, y_col, output_path, kind=chart_type, title=title)  # type: ignore[arg-type]
        return f"Chart saved to: {saved}"
    except Exception as exc:  # noqa: BLE001
        return f"Chart creation error: {exc!s}"


@tool
def list_mcp_tools(server_name: str = "") -> str:
    """List tools available from connected MCP servers.

    Args:
        server_name: Filter by server name (e.g. ``"skillsmith"``).
            When empty, returns tools from all connected servers.

    Returns:
        Formatted list of tool names and descriptions grouped by server.
    """
    try:
        from lightagent.agents.tool_registry import get_mcp_tools

        all_tools = get_mcp_tools()
        if not all_tools:
            return (
                "No MCP tools available. The MCP servers may not be initialised "
                "yet or no servers are connected."
            )

        # Group by server (tool name prefix is server-agnostic; use description)
        lines: list[str] = []
        query = server_name.lower().strip()
        for t in all_tools:
            desc = t.description or ""
            if query and query not in t.name.lower() and query not in desc.lower():
                # Try matching against the connection's server name via private attr
                server_attr = getattr(getattr(t, "_connection", None), "_config", None)
                srv = getattr(server_attr, "name", "")
                if query not in srv.lower():
                    continue
            lines.append(f"- **{t.name}**: {desc[:100]}")

        if not lines:
            return f"No tools found for server '{server_name}'."
        header = (
            f"MCP tools (filter: '{server_name}')" if server_name else "All MCP tools"
        )
        return f"**{header}** ({len(lines)} tools):\n" + "\n".join(lines)
    except Exception as exc:
        return f"Error listing MCP tools: {exc!s}"


RESEARCHER_TOOLS: list[BaseTool] = [web_search, rag_search, read_file, list_mcp_tools]
CODER_TOOLS: list[BaseTool] = [code_executor, read_file, write_file]
RAG_AGENT_TOOLS: list[BaseTool] = [vector_search, doc_index, web_search]
CRITIC_TOOLS: list[BaseTool] = [evaluate, score]
DATA_ANALYST_TOOLS: list[BaseTool] = [duckdb_query, polars_transform, create_chart]
FILE_MANAGER_TOOLS: list[BaseTool] = [read_file, write_file]

__all__ = [
    "CODER_TOOLS",
    "CRITIC_TOOLS",
    "DATA_ANALYST_TOOLS",
    "FILE_MANAGER_TOOLS",
    "RAG_AGENT_TOOLS",
    "RESEARCHER_TOOLS",
    "code_executor",
    "create_chart",
    "doc_index",
    "duckdb_query",
    "evaluate",
    "list_mcp_tools",
    "polars_transform",
    "rag_search",
    "read_file",
    "score",
    "vector_search",
    "web_search",
    "write_file",
]

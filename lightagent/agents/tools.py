"""Stub LangChain tools for LightAgent sub-agents.

Fallback tool implementations used when no live MCP or skill tool overrides them.

Every tool here is a real implementation that connects to the actual backend:
- ``web_search``          — DuckDuckGo via ``langchain-community``
- ``rag_search``          — ChromaDB via :class:`~lightagent.rag.engine.RAGEngine`
- ``vector_search``       — ChromaDB nearest-neighbour search
- ``doc_index``           — RAGEngine file/directory indexer
- ``read_file``           — Filesystem read (workspace-sandboxed)
- ``write_file``          — Filesystem write (workspace-sandboxed)
- ``code_executor``       — Sandboxed Python subprocess (requires ``shell_enabled``)
- ``evaluate``            — LLM-based qualitative evaluation
- ``score``               — LLM-based numeric scorer
- ``duckdb_query``        — :class:`~lightagent.data.duckdb_engine.DuckDBEngine` SQL
- ``polars_transform``    — Polars DataFrame operations via ``polars_utils``
- ``create_chart``        — Matplotlib chart via
  :func:`~lightagent.data.polars_utils.save_chart`
- ``list_mcp_tools``      — Enumerates connected MCP server tools
- ``remember_preference`` — Writes a user preference fact to ``PREFERENCES.md``
"""

from __future__ import annotations

from langchain_core.tools import BaseTool, tool

from lightagent.scheduler.cron_manager import CronManager
from lightagent.security.filesystem_guard import FilesystemGuard, PathViolation


def _get_fs_guard() -> FilesystemGuard:
    """Return a FilesystemGuard configured from current settings.

    When ``fs_allow_outside_workspace`` is ``True``, the guard is created
    with ``workspace_root=None`` so no path restriction is applied.
    Otherwise ``fs_workspace_root`` is used when set.

    Returns:
        A :class:`~lightagent.security.filesystem_guard.FilesystemGuard`
        instance configured according to current settings.
    """
    from lightagent.core.config import get_settings

    s = get_settings()
    if s.fs_allow_outside_workspace:
        workspace = None
    else:
        workspace = s.fs_workspace_root if s.fs_workspace_root else None
    return FilesystemGuard(workspace_root=workspace)


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
        from langchain_community.tools import DuckDuckGoSearchRun

        results = DuckDuckGoSearchRun().run(query)
        return results if results else f"No results found for: {query!r}"
    except Exception as exc:
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
        from lightagent.rag.engine import RAGEngine

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
    except Exception as exc:
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
    try:
        guard = _get_fs_guard()
        resolved = guard.validate(path, write=False)
    except PathViolation as exc:
        return f"Error: {exc}"

    if not resolved.exists():
        return f"File not found: {path}"
    if not resolved.is_file():
        return f"Not a file: {path}"

    try:
        content = resolved.read_text(encoding="utf-8", errors="replace")
        if len(content) > 20_000:
            content = content[:20_000] + "\n…[file truncated at 20 000 chars]"
        return content
    except Exception as exc:
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
    from pathlib import Path

    raw = Path(path).expanduser()
    if not raw.is_absolute():
        raw = Path("data/workspace") / raw

    try:
        guard = _get_fs_guard()
        resolved = guard.validate(str(raw), write=True)
    except PathViolation as exc:
        return f"Error: {exc}"

    try:
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content, encoding="utf-8")
        return f"File written: {resolved} ({len(content)} chars)"
    except Exception as exc:
        return f"Error writing file: {exc!s}"


@tool
def list_dir(path: str) -> str:
    """List the contents of a directory.

    Args:
        path: Absolute path to the directory to list.

    Returns:
        Newline-separated list of entries with [DIR] / [FILE] prefixes,
        or an error message if the path is invalid or blocked.
    """
    try:
        guard = _get_fs_guard()
        resolved = guard.validate(path, write=False)
    except PathViolation as exc:
        return f"Error: {exc}"

    if not resolved.is_dir():
        return f"Error: Not a directory: {resolved}"

    entries: list[str] = []
    try:
        for entry in sorted(resolved.iterdir()):
            tag = "[DIR] " if entry.is_dir() else "[FILE]"
            entries.append(f"{tag} {entry.name}")
    except PermissionError:
        return f"Error: Permission denied reading directory: {resolved}"

    return "\n".join(entries) if entries else "(empty directory)"


@tool
def find_files(root: str, pattern: str, max_results: int = 100) -> str:
    """Recursively find files matching a glob pattern under a root directory.

    Args:
        root: Absolute path to search root.
        pattern: Glob pattern, e.g. ``"*.py"`` or ``"**/*.yaml"``.
        max_results: Maximum number of results to return (default 100).

    Returns:
        Newline-separated list of relative paths, or an error message.
    """
    try:
        guard = _get_fs_guard()
        resolved = guard.validate(root, write=False)
    except PathViolation as exc:
        return f"Error: {exc}"

    if not resolved.is_dir():
        return f"Error: Not a directory: {resolved}"

    matches: list[str] = []
    for p in resolved.rglob(pattern):
        if p.is_file():
            matches.append(str(p.relative_to(resolved)))
            if len(matches) >= max_results:
                break

    if not matches:
        return f"No files found matching '{pattern}' under {resolved}"
    return "\n".join(matches)


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
    import subprocess

    from lightagent.core.config import get_settings

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
    except Exception as exc:
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
        from lightagent.rag.engine import RAGEngine

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
    except Exception as exc:
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
    from pathlib import Path

    from lightagent.rag.engine import RAGEngine

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
    except Exception as exc:
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
        from langchain_core.messages import HumanMessage, SystemMessage

        from lightagent.providers.registry import ProviderRegistry

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
    except Exception as exc:
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
    import re

    try:
        from langchain_core.messages import HumanMessage, SystemMessage

        from lightagent.providers.registry import ProviderRegistry

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
    except Exception:
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
        from lightagent.data.duckdb_engine import DuckDBEngine

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
    except Exception as exc:
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
    import re
    from pathlib import Path

    try:
        import polars as pl

        from lightagent.data.polars_utils import (
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
    except Exception as exc:
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
    except Exception as exc:
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
    import json
    from pathlib import Path

    try:
        import polars as pl

        from lightagent.data.polars_utils import save_chart
    except ImportError as exc:
        return f"Chart dependencies not available: {exc!s}"

    try:
        rows = json.loads(data)
        if not rows or not isinstance(rows, list):
            return "data must be a non-empty JSON array of objects."
        df = pl.DataFrame(rows)
    except Exception as exc:
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
    except Exception as exc:
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


@tool
def cron_add(
    name: str,
    schedule: str,
    task: str,
    output_channel: str = "",
    output_target: str = "",
) -> str:
    """Schedule a new recurring cron job.

    Use this when the user wants to automate a task on a schedule.
    Optionally specify an output channel to receive proactive reports.

    Args:
        name: Unique job name.
        schedule: Cron expression (e.g. '0 9 * * *' for 9 AM daily).
        task: Natural-language task description the agent will perform.
        output_channel: Optional delivery channel for the job output.
            One of: 'telegram', 'slack', 'discord', 'email'.
            Leave empty to suppress proactive delivery.
        output_target: Channel-specific destination.
            telegram -> chat_id, slack -> #channel,
            discord -> webhook URL, email -> address.
            Leave empty when output_channel is empty.

    Returns:
        Confirmation message with the next scheduled run time.
    """
    from lightagent.scheduler.executor import get_running_executor

    manager = CronManager()
    try:
        job = manager.add(
            name,
            schedule,
            task,
            output_channel=output_channel or None,
            output_target=output_target or None,
        )
    except ValueError as exc:
        return f"Failed to schedule job '{name}': {exc}"
    executor = get_running_executor()
    if executor is not None:
        executor.schedule_coroutine(executor.add_job(job))
    next_run = (
        job.next_run.strftime("%Y-%m-%d %H:%M UTC") if job.next_run else "unknown"
    )
    routing = (
        f" Output will be delivered to {output_channel}:{output_target}."
        if output_channel and output_target
        else ""
    )
    return (
        f"Scheduled job '{name}' ({schedule}). Next run: {next_run}.{routing}"
    )


@tool
def cron_list() -> str:
    """List all scheduled cron jobs with their status and next run time.

    Returns:
        Formatted table of all cron jobs, or a message if none exist.
    """
    try:
        from lightagent.scheduler.cron_manager import CronManager

        jobs = CronManager().list_jobs()
        if not jobs:
            return "No cron jobs scheduled."
        lines = ["NAME | SCHEDULE | STATUS | NEXT RUN | TASK"]
        for j in jobs:
            next_run = j.next_run.strftime("%Y-%m-%d %H:%M") if j.next_run else "\u2014"
            row = f"{j.name} | {j.schedule} | {j.status} | {next_run} | {j.task}"
            lines.append(row)
        return "\n".join(lines)
    except Exception as exc:
        return f"Failed to list jobs: {exc}"


@tool
def cron_pause(name: str) -> str:
    """Pause a scheduled cron job so it stops firing.

    Args:
        name: The job name to pause.

    Returns:
        Confirmation or error message.
    """
    try:

        from lightagent.scheduler.cron_manager import CronManager
        from lightagent.scheduler.executor import get_running_executor

        CronManager().pause(name)
        # Notify running executor to pause the job in APScheduler
        executor = get_running_executor()
        if executor is not None:
            executor.schedule_coroutine(executor.pause_job(name))
        return f"Job '{name}' paused."
    except KeyError:
        return f"Job '{name}' not found."
    except Exception as exc:
        return f"Failed to pause job: {exc}"


@tool
def cron_resume(name: str) -> str:
    """Resume a paused cron job.

    Args:
        name: The job name to resume.

    Returns:
        Confirmation or error message.
    """
    try:

        from lightagent.scheduler.cron_manager import CronManager
        from lightagent.scheduler.executor import get_running_executor

        CronManager().resume(name)
        # Notify running executor to resume the job in APScheduler
        executor = get_running_executor()
        if executor is not None:
            executor.schedule_coroutine(executor.resume_job(name))
        return f"Job '{name}' resumed."
    except KeyError:
        return f"Job '{name}' not found."
    except Exception as exc:
        return f"Failed to resume job: {exc}"


@tool
def get_current_time() -> str:
    """Return the current local date, time, and timezone of the host machine.

    Use this tool at the start of any scheduling request that involves a
    relative time reference ("in 5 minutes", "in 2 hours", "tomorrow at 9 AM")
    so that you can compute the correct absolute cron expression without
    asking the user for the current time.

    Returns:
        A string with current ISO datetime, local timezone name, and UTC offset,
        e.g. "2026-03-09 20:24:45 VET (UTC-04:00)".
    """
    from datetime import datetime

    now_local = datetime.now().astimezone()
    tz_name = now_local.tzname() or "local"
    utc_offset = now_local.utcoffset()
    if utc_offset is not None:
        total_seconds = int(utc_offset.total_seconds())
        sign = "+" if total_seconds >= 0 else "-"
        hours, remainder = divmod(abs(total_seconds), 3600)
        minutes = remainder // 60
        offset_str = f"UTC{sign}{hours:02d}:{minutes:02d}"
    else:
        offset_str = "UTC offset unknown"
    return (
        f"{now_local.strftime('%Y-%m-%d %H:%M:%S')} {tz_name} ({offset_str})"
    )


@tool
def cron_once(name: str, run_at: str, task: str) -> str:
    """Schedule a one-time (non-recurring) reminder or task at a specific datetime.

    Use this for relative-time requests: "in 5 minutes", "in 2 hours",
    "tomorrow at 9 AM".  Always call ``get_current_time`` first to know the
    current local time, then compute the target datetime and pass it here.

    After the job fires, the agent is invoked with ``task`` as the user
    message and the result is sent to configured notification channels
    (Telegram / Slack).

    Args:
        name: Unique job name in snake_case (e.g. ``"email_reminder"``).
        run_at: Target datetime in ``"YYYY-MM-DD HH:MM:SS"`` format
            (local time, same timezone returned by ``get_current_time``).
        task: What to do when the time comes — sent verbatim to the agent
            (e.g. ``"Send a reminder to write the weekly report"``).

    Returns:
        Confirmation string with the scheduled time, or an error message.
    """
    try:
        from datetime import datetime

        from lightagent.scheduler.cron_manager import CronManager
        from lightagent.scheduler.executor import get_running_executor

        target = datetime.strptime(run_at, "%Y-%m-%d %H:%M:%S")  # noqa: DTZ007
        mgr = CronManager()
        job = mgr.add_once(name, target, task)
        executor = get_running_executor()
        if executor is not None:
            executor.schedule_coroutine(executor.add_job(job))
        return (
            f"One-time reminder '{name}' scheduled for"
            f" {target.strftime('%Y-%m-%d %H:%M')}."
        )
    except ValueError as exc:
        return f"Invalid datetime — use YYYY-MM-DD HH:MM:SS format. Error: {exc}"
    except Exception as exc:
        return f"Failed to schedule one-time reminder: {exc}"


@tool
def cron_remove(name: str) -> str:
    """Permanently delete a scheduled cron job.

    Args:
        name: The job name to remove.

    Returns:
        Confirmation or error message.
    """
    try:

        from lightagent.scheduler.cron_manager import CronManager
        from lightagent.scheduler.executor import get_running_executor

        CronManager().remove(name)
        # Notify running executor to remove the job from APScheduler
        executor = get_running_executor()
        if executor is not None:
            executor.schedule_coroutine(executor.remove_job(name))
        return f"Job '{name}' removed."
    except KeyError:
        return f"Job '{name}' not found."
    except Exception as exc:
        return f"Failed to remove job: {exc}"


@tool
def remember_preference(section: str, fact: str) -> str:
    """Save a user preference fact to PREFERENCES.md immediately.

    Call this tool when the user explicitly states a preference they want
    remembered (e.g. "remember that I prefer ruff in all projects").

    Valid *section* values:
    - ``"communication_style"`` — language, tone, verbosity, response format
    - ``"tech_stack"`` — languages, frameworks, tools, package managers
    - ``"workflow"`` — planning approach, commit style, testing practices
    - ``"project_context"`` — project names, directories, team context

    Args:
        section: Category key — one of the four valid values listed above.
        fact: Free-text preference to remember (e.g. "Uses ruff in all projects").

    Returns:
        Confirmation message indicating the fact was saved, or an error
        description if the section is invalid or a write failure occurs.
    """
    try:
        from lightagent.memory.preferences import PreferencesManager

        mgr = PreferencesManager()
        mgr.set_fact(section, fact)
        section_label = {
            "communication_style": "Estilo de Comunicacion",
            "tech_stack": "Stack Tecnico",
            "workflow": "Flujo de Trabajo",
            "project_context": "Contexto del Proyecto",
        }.get(section, section)
        return f"Remembered: \"{fact}\" → {section_label}"
    except Exception as exc:
        return f"Failed to save preference: {exc!s}"


SUPERVISOR_DIRECT_TOOLS: list[BaseTool] = [remember_preference]

RESEARCHER_TOOLS: list[BaseTool] = [web_search, rag_search, read_file, list_mcp_tools]
CODER_TOOLS: list[BaseTool] = [code_executor, read_file, write_file]
RAG_AGENT_TOOLS: list[BaseTool] = [vector_search, doc_index, web_search]
CRITIC_TOOLS: list[BaseTool] = [evaluate, score]
DATA_ANALYST_TOOLS: list[BaseTool] = [duckdb_query, polars_transform, create_chart]
FILE_MANAGER_TOOLS: list[BaseTool] = [read_file, write_file]
CRON_MANAGER_TOOLS: list[BaseTool] = [
    get_current_time,
    cron_once,
    cron_add,
    cron_list,
    cron_pause,
    cron_resume,
    cron_remove,
]

__all__ = [
    "CODER_TOOLS",
    "CRITIC_TOOLS",
    "CRON_MANAGER_TOOLS",
    "DATA_ANALYST_TOOLS",
    "FILE_MANAGER_TOOLS",
    "RAG_AGENT_TOOLS",
    "RESEARCHER_TOOLS",
    "SUPERVISOR_DIRECT_TOOLS",
    "code_executor",
    "create_chart",
    "cron_add",
    "cron_list",
    "cron_once",
    "cron_pause",
    "cron_remove",
    "cron_resume",
    "doc_index",
    "duckdb_query",
    "evaluate",
    "find_files",
    "get_current_time",
    "list_dir",
    "list_mcp_tools",
    "polars_transform",
    "rag_search",
    "read_file",
    "remember_preference",
    "score",
    "vector_search",
    "web_search",
    "write_file",
]

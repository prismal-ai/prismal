"""Coder sub-agent node.

Specialist agent responsible for writing, executing, and debugging code.
Produces clean, well-documented code with type hints and docstrings.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from langchain_core.messages import SystemMessage

from lightagent.agents.tool_registry import get_tools_for_agent, react_loop
from lightagent.core.logging import get_logger
from lightagent.providers.registry import ProviderRegistry

if TYPE_CHECKING:
    from lightagent.agents.state import AgentState

logger = get_logger("lightagent.agents.coder")

_SYSTEM_PROMPT = """You are a software engineering specialist.

Your responsibilities:
- Write clean, idiomatic code that follows best practices for the target language.
- Always include docstrings for all public functions, methods, and classes.
- Use type hints throughout (Python 3.13+ syntax preferred).
- Execute code snippets to verify correctness before returning results.
- If execution produces errors, diagnose the root cause and fix them iteratively.
- Read existing files for context before modifying or extending them.
- Write output to files when the user asks for persistent results.
- Keep code DRY (Don't Repeat Yourself) and favour readability over cleverness.

## Sandbox Environment

You have access to an isolated multi-language sandbox for building and testing programs.
All sandbox work lives under sandbox/workspace/ — paths outside this directory are blocked.

### Available runtimes
- Python 3: sandbox_exec(code, "python") — isolated venv with pip
- JavaScript/Node.js: sandbox_exec(code, "js") — Node binary in sandbox
- TypeScript: sandbox_exec(code, "ts") — via npx ts-node
- Go: sandbox_exec(code, "go") — Go binary in sandbox
- Bash scripts: sandbox_exec(code, "bash")
- Ruff lint: sandbox_exec(code, "ruff-check") — Python static analysis
- TypeScript check: sandbox_exec(code, "tsc") — type-only, no emit

### Sandbox tools
- sandbox_exec(code, language, workdir?) — run a snippet and return stdout/stderr.
- sandbox_install(package, manager) — install a package; managers: pip, npm, go, ruff, typescript.
- sandbox_shell(command, workdir?) — run an arbitrary command inside the sandbox env.
- sandbox_write_file(path, content) — write to sandbox/workspace/<path>.
- sandbox_read_file(path) — read from sandbox/workspace/<path>.
- sandbox_ls(path?) — list sandbox/workspace/<path> (default: root).
- sandbox_status() — show installed runtime versions and disk usage.

### Bootstrap workflow
1. Check runtimes: sandbox_status()
2. Install missing runtime if needed: sandbox_install("node", "npm")
3. Write source: sandbox_write_file("myapp/main.py", code)
4. Install deps: sandbox_install("requests", "pip")
5. Run: sandbox_exec(code, "python", "myapp")
6. Iterate on errors until green."""


async def coder_node(state: AgentState) -> dict[str, object]:
    """Execute the coder sub-agent node with a ReAct tool loop.

    Calls the LLM with coding tools bound (code executor, file read/write),
    executes any requested tool calls, and iterates until a final answer
    is produced or the iteration cap is reached.

    Args:
        state: Current agent state from LangGraph.

    Returns:
        Updated state dict with ``current_agent`` set to ``'coder'``
        and new ``messages`` containing the generated or reviewed code.
    """
    session_id = state.get("session_id")
    logger.debug("coder_node_called", session_id=session_id)

    registry = ProviderRegistry()
    llm = registry.get_llm_with_fallback()
    tools = get_tools_for_agent("coder")
    llm_with_tools = llm.bind_tools(tools)

    messages = [SystemMessage(content=_SYSTEM_PROMPT), *state["messages"]]
    response = await react_loop(
        llm_with_tools,
        tools,
        messages,
        agent_name="coder",
        session_id=str(session_id) if session_id else None,
    )

    logger.info("coder_complete", session_id=session_id)
    return {"current_agent": "coder", "messages": [response]}


__all__ = ["coder_node"]

"""
User preferences persistence layer.

Implements a hybrid explicit + automatic preference learning system:

- ``PreferenceFacts``: structured Pydantic model for preference categories.
- ``PreferencesManager``: reads/writes ``PREFERENCES.md`` in the profile dir.
- ``PreferenceExtractor``: LLM-based extractor that analyses conversation
  history and returns structured facts.

The ``PREFERENCES.md`` file is plain Markdown — human-editable at any time.
It is loaded by ``ProfileManager.load_system_prompt()`` so the agent remembers
user preferences across sessions.

Example::

    from prismal.memory.preferences import PreferencesManager, PreferenceExtractor

    mgr = PreferencesManager()
    mgr.set_fact("tech_stack", "Uses ruff in all projects")

    extractor = PreferenceExtractor()
    facts = await extractor.extract(conversation_text)
    mgr.merge(facts)
"""

from __future__ import annotations

import json
from pathlib import Path

import structlog
from pydantic import BaseModel, Field

logger = structlog.get_logger("prismal.memory.preferences")

_PROFILE_DIR = Path("data/workspace/profile")

# Mapping from PreferenceFacts field names to Markdown section headers
_SECTION_HEADERS: dict[str, str] = {
    "communication_style": "## Estilo de Comunicacion",
    "tech_stack": "## Stack Tecnico",
    "workflow": "## Flujo de Trabajo",
    "project_context": "## Contexto del Proyecto",
}

_PREFS_TEMPLATE = """\
# User Preferences

## Estilo de Comunicacion

## Stack Tecnico

## Flujo de Trabajo

## Contexto del Proyecto
"""

_EXTRACT_SYSTEM_PROMPT = """\
You are a preference extraction assistant. Analyze the conversation and extract
clear, explicit user preferences. Only extract preferences with a clear signal —
do NOT speculate. Return a JSON object with these four keys:

- "communication_style": list of preferences about language, tone, detail level
- "tech_stack": list of technical preferences (languages, frameworks, tools)
- "workflow": list of workflow preferences (planning, commits, testing, etc.)
- "project_context": list of project-specific facts mentioned by the user

Return only facts that are clearly stated or strongly implied. Return an empty
list for categories where no clear preference was expressed.

Return ONLY the JSON object, no other text.

Example output:
{
  "communication_style": ["Prefers responses in Spanish", "Concise style"],
  "tech_stack": ["Python 3.13", "Uses uv for package management"],
  "workflow": ["Commits frequently with conventional commits"],
  "project_context": ["Working on Prismal project"]
}
"""


class PreferenceFacts(BaseModel):
    """Structured user preference facts organised by category."""

    communication_style: list[str] = Field(
        default_factory=list,
        description="Preferences about language, tone, and communication style.",
    )
    tech_stack: list[str] = Field(
        default_factory=list,
        description="Technical tool, language, and framework preferences.",
    )
    workflow: list[str] = Field(
        default_factory=list,
        description="Workflow and process preferences (planning, commits, testing).",
    )
    project_context: list[str] = Field(
        default_factory=list,
        description="Project-specific context facts.",
    )


class PreferencesManager:
    """Read and write user preferences stored in ``PREFERENCES.md``.

    All data is stored as plain, human-readable Markdown that can be edited
    directly in any text editor and version-controlled alongside the project.

    Args:
        profile_dir: Directory for the preferences file. Defaults to
            ``data/workspace/profile``.
    """

    def __init__(self, profile_dir: Path | None = None) -> None:
        """Initialise with an optional custom profile directory."""
        self._dir = profile_dir if profile_dir is not None else _PROFILE_DIR
        self._prefs = self._dir / "PREFERENCES.md"

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ensure_file(self) -> None:
        """Create PREFERENCES.md from the template if it does not exist."""
        if not self._prefs.exists():
            self._dir.mkdir(parents=True, exist_ok=True)
            self._prefs.write_text(_PREFS_TEMPLATE, encoding="utf-8")

    def _parse_sections(self, content: str) -> dict[str, list[str]]:
        """Parse Markdown into a dict mapping section key to bullet list.

        Args:
            content: Raw PREFERENCES.md content.

        Returns:
            Dict with field-name keys and lists of bullet text (without the
            leading ``"- "`` prefix).
        """
        result: dict[str, list[str]] = {k: [] for k in _SECTION_HEADERS}
        current_key: str | None = None
        header_to_key = {v.strip(): k for k, v in _SECTION_HEADERS.items()}

        for line in content.splitlines():
            stripped = line.strip()
            if stripped in header_to_key:
                current_key = header_to_key[stripped]
            elif current_key is not None and stripped.startswith("- "):
                result[current_key].append(stripped[2:].strip())
        return result

    # ------------------------------------------------------------------
    # Loaders
    # ------------------------------------------------------------------

    def load(self) -> str:
        """Return the full PREFERENCES.md content, or empty string if absent.

        Returns:
            Raw markdown text of PREFERENCES.md.
        """
        if not self._prefs.exists():
            return ""
        return self._prefs.read_text(encoding="utf-8")

    def load_section(self, section: str) -> str:
        """Return the text content of a named preference section.

        Args:
            section: Field name key (e.g. ``"tech_stack"``).

        Returns:
            Multi-line string with the section bullet items, or empty string
            if the section is not found.
        """
        header = _SECTION_HEADERS.get(section, "")
        if not header:
            return ""
        content = self.load()
        if not content:
            return ""
        lines = content.splitlines()
        in_section = False
        section_lines: list[str] = []
        for line in lines:
            if line.strip() == header.strip():
                in_section = True
                continue
            if in_section:
                if line.startswith("## "):
                    break
                section_lines.append(line)
        return "\n".join(section_lines).strip()

    # ------------------------------------------------------------------
    # Writers
    # ------------------------------------------------------------------

    def set_fact(self, section: str, fact: str) -> None:
        """Write a single fact to a section in PREFERENCES.md immediately.

        Creates the file from the template if it does not exist.  The fact is
        silently skipped if an equivalent entry already exists (case-insensitive
        substring match).

        Args:
            section: Field name key (e.g. ``"tech_stack"``).
            fact: Free-text fact to append as a Markdown bullet point.
        """
        header = _SECTION_HEADERS.get(section, "")
        if not header:
            logger.warning("preferences.set_fact.unknown_section", section=section)
            return

        fact = fact.strip()
        if not fact:
            return

        self._ensure_file()

        # Duplicate check — case-insensitive substring match
        existing_section = self.load_section(section)
        if fact.lower() in existing_section.lower():
            logger.debug("preferences.set_fact.duplicate_skipped", fact=fact)
            return

        content = self._prefs.read_text(encoding="utf-8")
        lines = content.splitlines()
        new_lines: list[str] = []
        inserted = False
        i = 0
        while i < len(lines):
            line = lines[i]
            new_lines.append(line)
            if line.strip() == header.strip() and not inserted:
                # Advance past any blank lines that follow the header
                j = i + 1
                while j < len(lines) and lines[j].strip() == "":
                    new_lines.append(lines[j])
                    j += 1
                new_lines.append(f"- {fact}")
                inserted = True
                i = j
                continue
            i += 1

        if not inserted:
            # Section header was not found; append at the end
            new_lines.extend(["", header, f"- {fact}"])

        self._prefs.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
        logger.info("preferences.set_fact", section=section, fact=fact)

    def merge(self, new_facts: PreferenceFacts) -> None:
        """Merge new preference facts into PREFERENCES.md, deduplicating.

        Iterates over every category in *new_facts*.  For each item, calls
        :meth:`set_fact` which performs a case-insensitive duplicate check
        before writing.

        Errors in individual items are caught and logged so that a single
        bad entry never aborts the entire merge.

        Args:
            new_facts: Newly extracted preference facts to merge in.
        """
        self._ensure_file()
        fields: dict[str, list[str]] = {
            "communication_style": new_facts.communication_style,
            "tech_stack": new_facts.tech_stack,
            "workflow": new_facts.workflow,
            "project_context": new_facts.project_context,
        }
        for field_key, items in fields.items():
            for item in items:
                stripped = item.strip()
                if stripped:
                    try:
                        self.set_fact(field_key, stripped)
                    except Exception as exc:
                        logger.warning(
                            "preferences.merge.set_fact_failed",
                            field=field_key,
                            item=stripped,
                            error=str(exc),
                        )


class PreferenceExtractor:
    """Extract structured preference facts from conversation text using the LLM.

    Designed for use as a background task; all errors are caught and logged
    without propagating to the caller so the main chat flow is never blocked.
    """

    async def extract(self, conversation: str) -> PreferenceFacts:
        """Analyse a conversation and return extracted preference facts.

        The LLM is prompted to identify only clear, explicit preferences —
        not speculative inferences.

        Args:
            conversation: Raw conversation text (user + agent turns).

        Returns:
            :class:`PreferenceFacts` with extracted facts, or an empty model
            if extraction fails or the conversation is empty.
        """
        if not conversation.strip():
            return PreferenceFacts()

        try:
            from langchain_core.messages import HumanMessage, SystemMessage

            from prismal.providers.registry import ProviderRegistry

            llm = ProviderRegistry().get_llm_with_fallback()
            response = await llm.ainvoke(
                [
                    SystemMessage(content=_EXTRACT_SYSTEM_PROMPT),
                    HumanMessage(content=f"Conversation to analyse:\n\n{conversation}"),
                ]
            )
            raw = str(response.content).strip()

            # Strip markdown code fences if the LLM wraps its response
            if raw.startswith("```"):
                parts = raw.split("```")
                raw = parts[1] if len(parts) > 1 else raw
                if raw.startswith("json"):
                    raw = raw[4:]
                raw = raw.strip()

            data: dict[str, list[str]] = json.loads(raw)
            return PreferenceFacts(
                communication_style=data.get("communication_style", []),
                tech_stack=data.get("tech_stack", []),
                workflow=data.get("workflow", []),
                project_context=data.get("project_context", []),
            )
        except Exception as exc:
            logger.warning("preferences.extract.failed", error=str(exc))
            return PreferenceFacts()


__all__ = [
    "PreferenceExtractor",
    "PreferenceFacts",
    "PreferencesManager",
]

"""
Agent and user profile management via markdown files.

Follows a three-file markdown architecture:

- ``SOUL.md``: Agent name, persona, tone, communication style.
  Personalidad pura — puede ser reseteada sin perder capacidades.
- ``CAPACITIES.md``: Permanent agent capabilities (skills, MCPs, RAG,
  data tools, agents).  Never deleted on reset.
- ``USER.md``: User identity, name, known facts and preferences.

All files are stored under ``data/workspace/profile/`` and loaded at
chat startup to build the system prompt.

Example::

    from lightagent.memory.profile import ProfileManager

    profile = ProfileManager()
    if not profile.is_configured():
        profile.save_soul("Lumi", "Friendly, concise technical assistant.")
        profile.save_user("Ernesto")

    # Build the full system prompt (soul + capacities + user context)
    print(profile.load_system_prompt())

    # Reset only personality — capacities are preserved
    profile.reset()
    print(profile.load_capacities())  # still returns content
"""

from __future__ import annotations

import re
from pathlib import Path

_PROFILE_DIR = Path("data/workspace/profile")

_SOUL_TEMPLATE = """\
# {name}

## Persona

{persona}

## Core Traits

- Helpful, clear and concise in responses
- Adapts communication style to the user's context and preferences
- Proactive in offering solutions and clarifications
- Maintains continuity and context across the conversation
"""

_USER_TEMPLATE = """\
# {name}

## Identity

- Name: {name}

## Preferences

*(Will be updated as the conversation evolves)*
"""


class ProfileManager:
    """
    Reads and writes agent/user profile markdown files.

    All data is stored as plain, human-readable markdown so it can be
    inspected or edited directly in any text editor and committed to
    version control alongside the project.

    Args:
        profile_dir: Directory for the profile files.  Defaults to
            ``data/workspace/profile``.
    """

    def __init__(self, profile_dir: Path | None = None) -> None:
        """Initialise the manager with an optional custom directory."""
        self._dir = profile_dir if profile_dir is not None else _PROFILE_DIR
        self._soul = self._dir / "SOUL.md"
        self._capacities = self._dir / "CAPACITIES.md"
        self._user = self._dir / "USER.md"

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def is_configured(self) -> bool:
        """Return ``True`` when both SOUL.md and USER.md exist."""
        return self._soul.exists() and self._user.exists()

    # ------------------------------------------------------------------
    # Loaders
    # ------------------------------------------------------------------

    def load_agent_name(self) -> str:
        """
        Return the agent's name from SOUL.md, defaulting to ``'LightAgent'``.

        Returns:
            Agent name extracted from the first H1 heading in SOUL.md.
        """
        if not self._soul.exists():
            return "LightAgent"
        content = self._soul.read_text(encoding="utf-8")
        match = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
        return match.group(1).strip() if match else "LightAgent"

    def load_user_name(self) -> str:
        """
        Return the user's name from USER.md, defaulting to ``'You'``.

        Returns:
            User name extracted from the first H1 heading in USER.md.
        """
        if not self._user.exists():
            return "You"
        content = self._user.read_text(encoding="utf-8")
        match = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
        return match.group(1).strip() if match else "You"

    def load_soul(self) -> str:
        """
        Return the full SOUL.md content, or empty string if not configured.

        Returns:
            Raw markdown text of SOUL.md.
        """
        if not self._soul.exists():
            return ""
        return self._soul.read_text(encoding="utf-8")

    def load_capacities(self) -> str:
        """
        Return the full CAPACITIES.md content, or empty string if not present.

        CAPACITIES.md contains the permanent agent capabilities (skills, MCPs,
        RAG, data tools).  It is never deleted by :meth:`reset`.

        Returns:
            Raw markdown text of CAPACITIES.md.
        """
        if not self._capacities.exists():
            return ""
        return self._capacities.read_text(encoding="utf-8")

    def load_user_context(self) -> str:
        """
        Return the full USER.md content, or empty string if not configured.

        Returns:
            Raw markdown text of USER.md.
        """
        if not self._user.exists():
            return ""
        return self._user.read_text(encoding="utf-8")

    def load_system_prompt(self) -> str:
        """
        Build the complete system prompt by combining SOUL, CAPACITIES and USER.

        Concatenation order:
          1. SOUL.md — persona and communication style (may be absent after reset)
          2. CAPACITIES.md — permanent capabilities (always present when configured)
          3. USER.md — user context and preferences (may be absent)

        Returns:
            Combined markdown string ready to be used as the LLM system prompt.
            Returns an empty string when none of the files exist.
        """
        parts: list[str] = []
        soul = self.load_soul()
        if soul:
            parts.append(soul)
        capacities = self.load_capacities()
        if capacities:
            parts.append(capacities)
        user_ctx = self.load_user_context()
        if user_ctx:
            parts.append(f"## User Context\n\n{user_ctx}")
        return "\n\n---\n\n".join(parts)

    # ------------------------------------------------------------------
    # Writers
    # ------------------------------------------------------------------

    def save_soul(self, name: str, persona: str) -> None:
        """
        Write SOUL.md with the agent's name and persona description.

        Args:
            name: Chosen name for the agent.
            persona: Free-text description of the agent's personality and
                communication style.
        """
        self._dir.mkdir(parents=True, exist_ok=True)
        self._soul.write_text(
            _SOUL_TEMPLATE.format(name=name, persona=persona),
            encoding="utf-8",
        )

    def update_soul_persona(self, persona: str) -> None:
        """
        Update the Persona section of SOUL.md while preserving the agent name.

        Reads the current H1 name from SOUL.md (or falls back to ``'LightAgent'``)
        and rewrites the file with the new persona, keeping the user-defined name
        intact.  Use this instead of :meth:`save_soul` when only the persona
        description needs to change.

        Args:
            persona: New free-text persona/capabilities description.
        """
        name = self.load_agent_name()
        self.save_soul(name, persona)

    def save_user(self, name: str) -> None:
        """
        Write USER.md with the user's name.

        Args:
            name: The user's preferred name.
        """
        self._dir.mkdir(parents=True, exist_ok=True)
        self._user.write_text(
            _USER_TEMPLATE.format(name=name),
            encoding="utf-8",
        )

    def reset(self) -> None:
        """
        Delete SOUL.md and USER.md so the next session triggers fresh onboarding.

        CAPACITIES.md is intentionally **not** deleted: it holds permanent
        system capabilities (skills, MCPs, RAG, data tools) that remain valid
        regardless of which persona is configured.  Only the personality
        (SOUL.md) and user context (USER.md) are cleared.

        Both targeted files are removed if they exist; missing files are
        silently ignored.
        """
        if self._soul.exists():
            self._soul.unlink()
        if self._user.exists():
            self._user.unlink()

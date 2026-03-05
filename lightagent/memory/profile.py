"""Agent and user profile management via markdown files.

Follows the OpenClaw markdown-first memory architecture:

- ``SOUL.md``: Agent name, persona, tone, communication style.
- ``USER.md``: User identity, name, known facts and preferences.

Both files are stored under ``data/workspace/profile/`` and are loaded at
chat startup so the agent can use the persona in responses and address the
user by their preferred name.

Example::

    from lightagent.memory.profile import ProfileManager

    profile = ProfileManager()
    if not profile.is_configured():
        profile.save_soul("Lumi", "Friendly, concise technical assistant.")
        profile.save_user("Ernesto")

    print(profile.load_agent_name())  # "Lumi"
    print(profile.load_user_name())  # "Ernesto"
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
    """Reads and writes agent/user profile markdown files.

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
        """Return the agent's name from SOUL.md, defaulting to ``'LightAgent'``.

        Returns:
            Agent name extracted from the first H1 heading in SOUL.md.
        """
        if not self._soul.exists():
            return "LightAgent"
        content = self._soul.read_text(encoding="utf-8")
        match = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
        return match.group(1).strip() if match else "LightAgent"

    def load_user_name(self) -> str:
        """Return the user's name from USER.md, defaulting to ``'You'``.

        Returns:
            User name extracted from the first H1 heading in USER.md.
        """
        if not self._user.exists():
            return "You"
        content = self._user.read_text(encoding="utf-8")
        match = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
        return match.group(1).strip() if match else "You"

    def load_soul(self) -> str:
        """Return the full SOUL.md content, or empty string if not configured.

        Returns:
            Raw markdown text of SOUL.md.
        """
        if not self._soul.exists():
            return ""
        return self._soul.read_text(encoding="utf-8")

    def load_user_context(self) -> str:
        """Return the full USER.md content, or empty string if not configured.

        Returns:
            Raw markdown text of USER.md.
        """
        if not self._user.exists():
            return ""
        return self._user.read_text(encoding="utf-8")

    # ------------------------------------------------------------------
    # Writers
    # ------------------------------------------------------------------

    def save_soul(self, name: str, persona: str) -> None:
        """Write SOUL.md with the agent's name and persona description.

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

    def save_user(self, name: str) -> None:
        """Write USER.md with the user's name.

        Args:
            name: The user's preferred name.
        """
        self._dir.mkdir(parents=True, exist_ok=True)
        self._user.write_text(
            _USER_TEMPLATE.format(name=name),
            encoding="utf-8",
        )

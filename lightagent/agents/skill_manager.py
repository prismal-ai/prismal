"""SkillManager agent node — manages the skills lifecycle from within a conversation.

Handles the full skill lifecycle:

- **list**          — show available, active and custom skills with their status.
- **activate**      — enable a skill that lives in ``skills/available/`` by name.
- **deactivate**    — disable an active skill by name.
- **install**       — copy a skill directory from a user-supplied filesystem path
  into ``skills/available/`` and activate it immediately.
- **install_remote** — download a skill from a GitHub repository (e.g.
  ``anthropics/skills``) via the GitHub API, wrap Claude Code YAML/MD skills as
  a ``BaseSkill`` Python class, and place it in ``skills/available/``.
- **create**        — generate a brand-new skill from a natural-language
  specification using the :mod:`~lightagent.agents.skill_creator` pipeline
  (ruff + mypy + bandit quality checks, human-review sentinel).

Example conversation::

    User: instala el skill que está en /home/ernesto/mis_skills/traductor
    Seraph> ✅ Skill `traductor` instalado desde /home/ernesto/mis_skills/traductor
            y activado correctamente.

    User: instala el skill skill-creator de anthropics
    Seraph> ✅ Skill `skill_creator` descargado desde github.com/anthropics/skills.
            Revísalo y luego dime: "activa el skill skill_creator"

    User: activa el skill web_search
    Seraph> ✅ Skill `web_search` activado correctamente.

    User: crea un skill que convierta unidades de temperatura
    Seraph> ✅ Skill generado: 'temperature_converter' …
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from langchain_core.messages import AIMessage

from lightagent.agents.skill_creator import create_skill
from lightagent.core.logging import get_logger
from lightagent.skills.manager import SkillsManager
from lightagent.skills.remote_installer import RemoteSkillInstaller

if TYPE_CHECKING:
    from lightagent.agents.state import AgentState

logger = get_logger("lightagent.agents.skill_manager")

# ---------------------------------------------------------------------------
# Regex-based intent detection (zero LLM calls — no tokens consumed)
# ---------------------------------------------------------------------------

# Patterns that indicate the user wants to list/show skills.
_RE_LIST = re.compile(
    r"\b(list|listar|show|muestra|mostrar|ver|display|skills?\s+disponibles?|"
    r"qu[eé]\s+skills?|skills?\s+tengo|tengo\s+skills?)\b",
    re.IGNORECASE,
)

# Patterns that capture a skill name after activate/enable keywords.
_RE_ACTIVATE = re.compile(
    r"\b(?:activ[ao]|activ[ao]r|enabl[eo]|habilit[ao]|habilitar|"
    r"encend[eo]r?|start)\b[^/\n]*?\b([\w_]+)\b",
    re.IGNORECASE,
)

# Patterns that capture a skill name after deactivate/disable keywords.
_RE_DEACTIVATE = re.compile(
    r"\b(?:desactiv[ao]|desactivar|disabl[eo]|deshabilit[ao]|deshabilitarl?o?|"
    r"apag[ao]r?|stop)\b[^/\n]*?\b([\w_]+)\b",
    re.IGNORECASE,
)

# Patterns that capture a filesystem path (absolute or relative).
_RE_INSTALL = re.compile(
    r"\b(?:instala[r]?|install|agrega[r]?|add|incorpora[r]?|copi[ao]r?|"
    r"a[ñn]ade[r]?)\b.{0,60}?((?:/[\w./\-]+|\.{1,2}/[\w./\-]+|~[\w./\-]*))",
    re.IGNORECASE,
)

# Patterns that indicate the user wants a new skill generated.
_RE_CREATE = re.compile(
    r"\b(?:cre[ao]|crear|genera[r]?|genera|build|construye[r]?|make|haz)\b"
    r".{0,20}?\b(?:un\s+skill|a\s+skill|skill\s+que|skill\s+para|skill\s+to)\b",
    re.IGNORECASE,
)

# Detects remote skill install intent (GitHub URL, "github" keyword, an
# owner/repo slug preceded by whitespace, or the shorthand "anthropics").
# Checked BEFORE _RE_INSTALL so "anthropics/skills" is not confused with a
# local filesystem path (which requires a leading /, ./, or ~/).
_RE_INSTALL_REMOTE = re.compile(
    r"\b(?:instala[r]?|install|agrega[r]?|add|descarga[r]?|a[ñn]ade[r]?)\b"
    r".{0,120}?"
    r"(?:https?://github|github(?:\.com)?|anthropics?"
    r"|(?<=\s)[\w][\w\-]*/[\w][\w\-]+)",
    re.IGNORECASE,
)

# Extracts the owner/repo portion from a GitHub URL.
_RE_GITHUB_URL_SLUG = re.compile(
    r"github\.com/([\w][\w\-]+)/([\w][\w\-]+)",
    re.IGNORECASE,
)

# Extracts a bare owner/repo slug (not preceded by / so not a local path).
_RE_REPO_SLUG = re.compile(
    r"(?:^|\s)([\w][\w\-]+/[\w][\w\-]+)(?=\s|$|[,;.])",
    re.IGNORECASE,
)

# Extracts a skill name from --skill flag or common natural-language patterns.
_RE_SKILL_NAME_ARG = re.compile(
    r"(?:--skill\s+|(?:el\s+|the\s+)?skill[:\s]+)([\w][\w\-_]+)",
    re.IGNORECASE,
)


def _parse_remote_install_args(user_message: str) -> tuple[str, str]:
    """Extract ``(repo_slug, skill_name)`` from a remote-install message.

    Falls back to ``"anthropics/skills"`` for the repo and ``""`` for the
    skill name when extraction is uncertain.  An empty skill name causes the
    agent to list available skills in the repo instead of installing.

    Args:
        user_message: Raw user message text.

    Returns:
        A ``(repo_slug, skill_name)`` tuple; either field may be empty.
    """
    # 1. Repo: prefer full GitHub URL, then bare owner/repo slug
    m_url = _RE_GITHUB_URL_SLUG.search(user_message)
    if m_url:
        repo = f"{m_url.group(1)}/{m_url.group(2)}"
    else:
        m_slug = _RE_REPO_SLUG.search(user_message)
        candidate = m_slug.group(1).strip() if m_slug else ""
        # Ignore internal paths like 'skills/available' or 'lightagent/skills'
        if candidate and not candidate.startswith(("skills/", "lightagent/")):
            repo = candidate
        else:
            repo = "anthropics/skills"

    # 2. Skill name: explicit --skill flag, "el skill <name>", or a
    #    hyphenated word (common pattern for skill names, e.g. "skill-creator")
    m_skill = _RE_SKILL_NAME_ARG.search(user_message)
    if m_skill:
        skill = m_skill.group(1).strip()
    else:
        m_hyphen = re.search(r"\b([\w][\w]*(?:-[\w]+)+)\b", user_message, re.IGNORECASE)
        skill = m_hyphen.group(1).strip() if m_hyphen else ""

    return repo, skill


def _detect_intent(user_message: str) -> str:
    """Classify the skill-management intent using regex patterns.

    No LLM call is made — classification is instant and consumes zero tokens.

    Priority order:
        INSTALL_REMOTE > INSTALL > ACTIVATE > DEACTIVATE > CREATE > LIST

    Args:
        user_message: Raw user input text.

    Returns:
        One of: ``"LIST"``, ``"ACTIVATE:<name>"``, ``"DEACTIVATE:<name>"``,
        ``"INSTALL:<path>"``, ``"INSTALL_REMOTE:<repo>|<skill>"``,
        ``"CREATE:<rest>"``.
    """
    # MCP guard — queries about MCP servers are NOT skill operations.
    # Return a sentinel so skill_manager_node can delegate back gracefully.
    if re.search(r"\bmcp[s]?\b", user_message, re.IGNORECASE):
        return "NOT_SKILL"

    # INSTALL_REMOTE — checked first: GitHub/slug patterns take priority over
    # local paths so "anthropics/skills" is not mistaken for a filesystem path.
    if _RE_INSTALL_REMOTE.search(user_message):
        repo, skill = _parse_remote_install_args(user_message)
        return f"INSTALL_REMOTE:{repo}|{skill}"

    # INSTALL (local path) — check next: paths are unambiguous anchors
    m = _RE_INSTALL.search(user_message)
    if m:
        return f"INSTALL:{m.group(1).strip()}"

    # ACTIVATE
    m = _RE_ACTIVATE.search(user_message)
    if m:
        return f"ACTIVATE:{m.group(1).strip()}"

    # DEACTIVATE
    m = _RE_DEACTIVATE.search(user_message)
    if m:
        return f"DEACTIVATE:{m.group(1).strip()}"

    # CREATE — extract everything after the trigger phrase
    m = _RE_CREATE.search(user_message)
    if m:
        # Return the full message as the spec; skill_creator handles it well
        return f"CREATE:{user_message.strip()}"

    # LIST — explicit keywords or fallback
    if _RE_LIST.search(user_message):
        return "LIST"

    return "LIST"


# ---------------------------------------------------------------------------
# Operation helpers
# ---------------------------------------------------------------------------


def _format_skills_list(manager: SkillsManager) -> str:
    """Render the full skill inventory as a markdown-friendly string.

    Args:
        manager: Initialised :class:`~lightagent.skills.manager.SkillsManager`.

    Returns:
        Formatted multiline string with skills grouped by status.
    """
    skills = manager.list_skills()
    if not skills:
        return "No se encontraron skills en el sistema."

    active = [s for s in skills if s.status == "active"]
    available = [s for s in skills if s.status == "available"]
    custom = [s for s in skills if s.status == "custom"]
    errors = [s for s in skills if s.status == "error"]

    lines: list[str] = ["**Skills en LightAgent:**\n"]

    if active:
        lines.append("✅ **Activos:**")
        for s in active:
            lines.append(f"  - `{s.name}` v{s.version} — {s.description}")

    if available:
        lines.append("\n📦 **Disponibles (inactivos):**")
        for s in available:
            lines.append(f"  - `{s.name}` v{s.version} — {s.description}")
        lines.append('\n  Para activar uno: dime **"activa el skill <nombre>"**')

    if custom:
        lines.append("\n🔧 **Personalizados (pendientes de revisión humana):**")
        for s in custom:
            lines.append(f"  - `{s.name}` v{s.version} — {s.description}")
        lines.append(
            "  Renombra `human_review_required.txt` → `validated_by_human.txt` "
            "para activarlos."
        )

    if errors:
        lines.append("\n❌ **Con errores de carga:**")
        for s in errors:
            lines.append(f"  - `{s.name}` — {s.error_message}")

    lines.append(f"\nTotal: {len(skills)} skill(s) descubierto(s).")
    return "\n".join(lines)


def _install_from_path(
    source_path: str,
    available_dir: Path,
) -> tuple[str, str | None]:
    """Copy a skill package from *source_path* into *available_dir*.

    Accepts three formats:

    * **Python skill** — a directory containing ``skill.py``.
    * **Markdown skill package** — a directory containing ``skill.md``
      (+ optional ``scripts/`` and ``references/``).  A ``skill.py`` wrapper
      is generated automatically via :func:`~lightagent.skills.base.generate_skill_py`.
    * **Zip archive** — a ``.zip`` file whose contents follow either of the
      above layouts.  Delegated to
      :meth:`~lightagent.skills.manager.SkillsManager.install_from_zip`.

    Args:
        source_path: Filesystem path supplied by the user (directory or .zip).
        available_dir: The ``skills/available/`` directory.

    Returns:
        ``(skill_name, None)`` on success, or ``("", error_message)`` on
        failure.
    """
    from lightagent.skills.manager import SkillsManager  # noqa: PLC0415

    src = Path(source_path).expanduser().resolve()

    # ── Zip archive ──────────────────────────────────────────────────────
    if src.suffix.lower() == ".zip":
        manager = SkillsManager()
        return manager.install_from_zip(src)

    # Tolerate pointing directly at skill.py or skill.md
    if src.is_file() and src.name in ("skill.py", "skill.md"):
        src = src.parent

    if not src.exists():
        return "", f"Ruta no encontrada: {source_path}"

    if not src.is_dir():
        return "", (
            f"La ruta debe ser un directorio con skill.py o skill.md: {source_path}"
        )

    skill_py = src / "skill.py"
    skill_md = src / "skill.md"
    if not skill_py.exists() and not skill_md.exists():
        return "", f"No se encontró skill.py ni skill.md en: {source_path}"

    skill_name = src.name
    dest = available_dir / skill_name

    if dest.exists():
        return "", (
            f"Ya existe un skill llamado '{skill_name}' en available/. "
            "Elige un nombre distinto o elimínalo primero."
        )

    try:
        shutil.copytree(str(src), str(dest))
    except Exception as exc:
        return "", f"Error al copiar el skill: {exc}"

    # ── Auto-generate skill.py for Markdown-based skill packages ─────────
    if not (dest / "skill.py").exists() and (dest / "skill.md").exists():
        try:
            from lightagent.skills.base import generate_skill_py  # noqa: PLC0415

            generate_skill_py(dest)
        except Exception as exc:  # noqa: BLE001
            shutil.rmtree(str(dest), ignore_errors=True)
            return "", f"Error generando skill.py desde skill.md: {exc}"

    return skill_name, None


# ---------------------------------------------------------------------------
# Agent node
# ---------------------------------------------------------------------------


async def skill_manager_node(state: AgentState) -> dict[str, object]:
    """Execute the skill-manager sub-agent node.

    Detects the user's intent (list / activate / deactivate / install / create)
    and performs the requested skill lifecycle operation, returning a
    human-readable result.

    Args:
        state: Current LangGraph agent state.

    Returns:
        Partial state dict with ``current_agent`` set to ``'skill_manager'``
        and a single :class:`~langchain_core.messages.AIMessage` carrying the
        operation result.
    """
    session_id: str = str(state.get("session_id", "unknown"))
    logger.debug("skill_manager_node_called", session_id=session_id)

    # Extract the last human message as the operation spec
    user_message: str = ""
    for msg in reversed(state.get("messages", [])):
        if hasattr(msg, "type") and msg.type == "human":
            user_message = str(msg.content)
            break

    if not user_message:
        return {
            "current_agent": "skill_manager",
            "messages": [
                AIMessage(content="No se recibió un mensaje para gestión de skills.")
            ],
        }

    manager = SkillsManager()
    intent = _detect_intent(user_message)
    logger.info("skill_manager_intent", intent=intent, session_id=session_id)

    result: str

    # ── NOT_SKILL (MCP query misdirected here) ────────────────────────────
    if intent == "NOT_SKILL":
        result = (
            "⚠️ Esta consulta es sobre **MCP servers**, no sobre skills.\n\n"
            "Los MCP servers se configuran en `config/mcp_servers.yaml` y se "
            "gestionan al reiniciar el agente. No los gestiono directamente — "
            "consulta al supervisor o revisa el archivo de configuración."
        )

    # ── LIST ──────────────────────────────────────────────────────────────
    elif intent == "LIST":
        result = _format_skills_list(manager)

    # ── ACTIVATE ──────────────────────────────────────────────────────────
    elif intent.startswith("ACTIVATE:"):
        skill_name = intent.split(":", 1)[1].strip()
        try:
            await manager.activate(skill_name, confirm=True)
            result = f"✅ Skill `{skill_name}` activado correctamente."
            logger.info("skill_activated_via_agent", skill=skill_name)
        except Exception as exc:
            result = (
                f"❌ No se pudo activar `{skill_name}`: {exc}\n\n"
                "Usa LIST para ver los skills disponibles."
            )

    # ── DEACTIVATE ────────────────────────────────────────────────────────
    elif intent.startswith("DEACTIVATE:"):
        skill_name = intent.split(":", 1)[1].strip()
        try:
            await manager.deactivate(skill_name)
            result = f"✅ Skill `{skill_name}` desactivado correctamente."
            logger.info("skill_deactivated_via_agent", skill=skill_name)
        except Exception as exc:
            result = f"❌ No se pudo desactivar `{skill_name}`: {exc}"

    # ── INSTALL FROM GITHUB ───────────────────────────────────────────────
    elif intent.startswith("INSTALL_REMOTE:"):
        raw = intent.split(":", 1)[1]
        parts = raw.split("|", 1)
        repo = parts[0].strip() or "anthropics/skills"
        skill_name = parts[1].strip() if len(parts) > 1 else ""

        installer = RemoteSkillInstaller(manager)

        if not skill_name:
            # No skill name given — list what's available in the repo
            try:
                available = await installer.list_available_skills(repo)
                items = "\n".join(f"  - `{s}`" for s in available)
                result = (
                    f"No especificaste qué skill instalar de `{repo}`.\n\n"
                    f"**Skills disponibles:**\n{items}\n\n"
                    f"Dime cuál quieres, por ejemplo:\n"
                    f'**"instala el skill skill-creator de {repo}"**'
                )
            except Exception as exc:
                result = (
                    f"❌ No pude listar los skills de `{repo}`: {exc}\n\n"
                    "Verifica la conexión a internet o el nombre del repositorio."
                )
        else:
            try:
                dest = await installer.install(
                    repo=repo,
                    skill_name=skill_name,
                    enable=False,
                )
                installed_name = dest.name
                result = (
                    f"✅ Skill `{installed_name}` descargado desde "
                    f"`github.com/{repo}`.\n\n"
                    f"   Instalado en: "
                    f"`lightagent/skills/available/{installed_name}/`\n\n"
                    f"⚠️  El skill requiere revisión humana antes de activarse.\n"
                    f"   Cuando lo hayas revisado, dime:\n"
                    f'   **"activa el skill {installed_name}"**'
                )
                logger.info(
                    "skill_installed_from_remote",
                    skill=installed_name,
                    repo=repo,
                    session_id=session_id,
                )
            except ValueError as exc:
                result = f"❌ {exc}"
            except Exception as exc:
                result = (
                    f"❌ No se pudo instalar `{skill_name}` desde "
                    f"`{repo}`: {exc}\n\n"
                    "Verifica la conexión a internet, el nombre del repositorio "
                    "y el nombre del skill."
                )

    # ── INSTALL FROM PATH / ZIP ───────────────────────────────────────────
    elif intent.startswith("INSTALL:"):
        source_path = intent.split(":", 1)[1].strip()
        is_zip = source_path.lower().endswith(".zip")
        skill_name, error = _install_from_path(source_path, manager._available)
        if error:
            result = f"❌ Error instalando skill desde `{source_path}`:\n{error}"
        else:
            pkg_type = "zip" if is_zip else "carpeta"
            try:
                await manager.activate(skill_name, confirm=True)
                result = (
                    f"✅ Skill `{skill_name}` instalado ({pkg_type}) y activado.\n"
                    f"   Origen:  {source_path}\n"
                    f"   Destino: lightagent/skills/available/{skill_name}/"
                )
                logger.info(
                    "skill_installed_from_path",
                    skill=skill_name,
                    source=source_path,
                )
            except Exception as exc:
                result = (
                    f"✅ Skill `{skill_name}` copiado a `available/` ({pkg_type}).\n"
                    f"   Origen: {source_path}\n\n"
                    f"⚠️  No se pudo activar automáticamente: {exc}\n"
                    f"   Revisa el skill y luego dime: "
                    f'**"activa el skill {skill_name}"**'
                )

    # ── CREATE WITH AI ────────────────────────────────────────────────────
    elif intent.startswith("CREATE:"):
        spec = intent.split(":", 1)[1].strip()
        result = await create_skill(spec)

    # ── FALLBACK ──────────────────────────────────────────────────────────
    else:
        result = _format_skills_list(manager)

    logger.info("skill_manager_complete", session_id=session_id)
    return {
        "current_agent": "skill_manager",
        "messages": [AIMessage(content=result)],
    }


__all__ = ["skill_manager_node"]

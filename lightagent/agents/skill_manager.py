"""SkillManager agent node — manages the skills lifecycle from within a conversation.

Handles the full skill lifecycle:

- **list**   — show available, active and custom skills with their status.
- **activate** — enable a skill that lives in ``skills/available/`` by name.
- **deactivate** — disable an active skill by name.
- **install** — copy a skill directory from a user-supplied filesystem path into
  ``skills/available/`` and activate it immediately.
- **create** — generate a brand-new skill from a natural-language specification
  using the :mod:`~lightagent.agents.skill_creator` pipeline (ruff + mypy +
  bandit quality checks, human-review sentinel).

Example conversation::

    User: instala el skill que está en /home/ernesto/mis_skills/traductor
    Seraph> ✅ Skill `traductor` instalado desde /home/ernesto/mis_skills/traductor
            y activado correctamente.

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


def _detect_intent(user_message: str) -> str:
    """Classify the skill-management intent using regex patterns.

    No LLM call is made — classification is instant and consumes zero tokens.

    Priority order: INSTALL > ACTIVATE > DEACTIVATE > CREATE > LIST > LIST(fallback)

    Args:
        user_message: Raw user input text.

    Returns:
        One of: ``"LIST"``, ``"ACTIVATE:<name>"``, ``"DEACTIVATE:<name>"``,
        ``"INSTALL:<path>"``, ``"CREATE:<rest>"``.
    """
    # INSTALL — check first: paths are unambiguous anchors
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
    """Copy a skill directory from *source_path* into *available_dir*.

    Validates that the source is a directory containing ``skill.py``, then
    copies the whole tree.  Does not activate — the caller is responsible.

    Args:
        source_path: Filesystem path supplied by the user.
        available_dir: The ``skills/available/`` directory.

    Returns:
        ``(skill_name, None)`` on success, or ``("", error_message)`` on
        failure.
    """
    src = Path(source_path).expanduser().resolve()

    # Tolerate the user pointing directly at skill.py
    if src.is_file() and src.name == "skill.py":
        src = src.parent

    if not src.exists():
        return "", f"Ruta no encontrada: {source_path}"

    if not src.is_dir():
        return "", (
            f"La ruta debe ser un directorio que contenga skill.py: {source_path}"
        )

    skill_py = src / "skill.py"
    if not skill_py.exists():
        return "", f"No se encontró skill.py en: {source_path}"

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

    # ── LIST ──────────────────────────────────────────────────────────────
    if intent == "LIST":
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

    # ── INSTALL FROM PATH ─────────────────────────────────────────────────
    elif intent.startswith("INSTALL:"):
        source_path = intent.split(":", 1)[1].strip()
        skill_name, error = _install_from_path(source_path, manager._available)
        if error:
            result = f"❌ Error instalando skill desde `{source_path}`:\n{error}"
        else:
            try:
                await manager.activate(skill_name, confirm=True)
                result = (
                    f"✅ Skill `{skill_name}` instalado y activado.\n"
                    f"   Origen:    {source_path}\n"
                    f"   Destino:   lightagent/skills/available/{skill_name}/"
                )
                logger.info(
                    "skill_installed_from_path",
                    skill=skill_name,
                    source=source_path,
                )
            except Exception as exc:
                result = (
                    f"✅ Skill `{skill_name}` copiado a `available/`.\n"
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

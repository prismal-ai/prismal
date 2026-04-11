"""Deterministic intent matcher for the supervisor router.

Pure-function short-circuit that recognises high-confidence cron-management
requests (English + Spanish) and returns the target agent name.  When the
matcher returns ``None`` the caller must fall through to LLM-based routing.

The module is stdlib-only (``re`` + ``unicodedata``) and side-effect-free so
the supervisor can call it on every turn without I/O cost and tests can
import it without touching settings, the filesystem, or the network.
"""

from __future__ import annotations

import re
import unicodedata

__all__ = ["match_intent"]


def _normalise(text: str) -> str:
    """Lowercase, NFKD-decompose, strip combining marks and whitespace."""
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return stripped.lower().strip()


_CRON_VERBS: str = (
    r"list|lista|listar|listame|"
    r"muestra|mostrar|mostrame|"
    r"dame|tengo|have|"
    r"pause|pausa|pausar|"
    r"resume|reanuda|reanudar|"
    r"remove|delete|borra|borrar|elimina|eliminar|"
    r"schedule|programa|programar|agenda|agendar|"
    r"set\s*up|configura|configurar|crea|crear|add|"
    r"remind|remindme|send|enviame|envia|enviar|avisa|avisame|"
    r"show|ver|que|cuales|cual|which|what"
)

_CRON_NOUNS: str = (
    r"crons?|cronjobs?|cron\s+jobs?|"
    r"scheduled\s+(?:jobs?|tasks?)|"
    r"tareas?\s+programad[oa]s?|"
    r"trabajos?\s+programad[oa]s?|"
    r"jobs?\s+programad[oa]s?|jobs?|"
    r"recordatorios?\s+(?:diarios?|semanal(?:es)?|recurrentes?)|"
    r"recurring\s+reminders?"
)

_CRON_RECURRENCE: str = (
    r"every\s+(?:day|hour|minute|week|month|morning|night|noon)|"
    r"cada\s+(?:dia|hora|minuto|semana|mes|manana|noche|mediodia)|"
    r"daily|hourly|weekly|monthly|"
    r"diariamente|"
    r"cada\s+\d+\s+(?:dias|horas|minutos|semanas|meses)|"
    r"todos\s+los\s+dias|todas\s+las\s+(?:horas|semanas)|"
    r"at\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)"
)

# Educational / explanatory questions mention cron concepts but are NOT
# management requests.  This denylist overrides any positive match below.
_CRON_EDUCATIONAL_RE: re.Pattern[str] = re.compile(
    r"\b(?:explain|explica|what\s+is|que\s+es|"
    r"how\s+do(?:es)?|como\s+funciona|"
    r"difference\s+between|diferencia\s+entre|"
    r"write\s+a\s+(?:python\s+)?script)\b",
    re.IGNORECASE,
)

# verb + noun within ~80 chars in either order.
_CRON_VERB_NOUN_RE: re.Pattern[str] = re.compile(
    rf"(?:(?:{_CRON_VERBS}).{{0,80}}?(?:{_CRON_NOUNS})"
    rf"|(?:{_CRON_NOUNS}).{{0,80}}?(?:{_CRON_VERBS}))",
    re.IGNORECASE | re.DOTALL,
)

# verb + recurrence within ~80 chars in either order.
_CRON_VERB_RECURRENCE_RE: re.Pattern[str] = re.compile(
    rf"(?:(?:{_CRON_VERBS}).{{0,80}}?(?:{_CRON_RECURRENCE})"
    rf"|(?:{_CRON_RECURRENCE}).{{0,80}}?(?:{_CRON_VERBS}))",
    re.IGNORECASE | re.DOTALL,
)


def match_intent(text: str | None) -> str | None:
    """Return a supervisor member name for high-confidence intents.

    Conservative: only fires for unambiguous management requests.
    Educational questions (``"explain how cron works"``) return ``None``
    so the supervisor falls through to LLM-based routing.

    Args:
        text: Raw user message content (the most recent ``HumanMessage``).

    Returns:
        The target agent name (currently only ``"cron_manager"``) or
        ``None`` when no high-confidence rule fires.
    """
    if text is None:
        return None
    normalised = _normalise(text)
    if not normalised:
        return None
    if _CRON_EDUCATIONAL_RE.search(normalised):
        return None
    if _CRON_VERB_NOUN_RE.search(normalised):
        return "cron_manager"
    if _CRON_VERB_RECURRENCE_RE.search(normalised):
        return "cron_manager"
    return None

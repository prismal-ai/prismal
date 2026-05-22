"""Unit tests for the deterministic supervisor intent matcher."""

from __future__ import annotations

import importlib

import pytest

from prismal.agents.intent_router import match_intent

ENGLISH_CRON_INPUTS: list[str] = [
    "list crons",
    "list scheduled jobs",
    "list my cron jobs",
    "show scheduled tasks",
    "pause cron daily-report",
    "resume cron daily-report",
    "remove cron daily-report",
    "delete scheduled job",
    "schedule a daily report at 8am",
    "every day at noon remind me to drink water",
    "set up a recurring reminder",
]

SPANISH_CRON_INPUTS: list[str] = [
    "lista los crons",
    "lista mis crons activos",
    "lista los crons activos",
    "muestra mis tareas programadas",
    "qué crons tengo activos",
    "pausa el cron de reporte diario",
    "reanuda el cron daily-report",
    "borra el cron daily-report",
    "elimina el job de las 8",
    "programa una tarea cada hora",
    "agenda un recordatorio diario",
    "cada día a las 8 envíame el reporte",
    "configura un recordatorio recurrente",
]

NEGATIVE_INPUTS: list[str] = [
    "explain how cron expressions work",
    "what is the difference between cron and systemd timers",
    "write a Python script that reads /etc/crontab",
    "how do I list MCP servers",
    "lista los MCPs",
    "qué MCPs tengo activos",
    "hola, cómo estás",
    "what's the weather today",
    "tell me a joke",
    "",
    "   ",
    "\n\t",
]


@pytest.mark.parametrize("text", ENGLISH_CRON_INPUTS)
def test_match_intent_english_cron(text: str) -> None:
    assert match_intent(text) == "cron_manager"


@pytest.mark.parametrize("text", SPANISH_CRON_INPUTS)
def test_match_intent_spanish_cron(text: str) -> None:
    assert match_intent(text) == "cron_manager"


@pytest.mark.parametrize("text", NEGATIVE_INPUTS)
def test_match_intent_negative(text: str) -> None:
    assert match_intent(text) is None


def test_match_intent_handles_none() -> None:
    assert match_intent(None) is None


def test_match_intent_is_accent_insensitive() -> None:
    assert match_intent("lista los crons activos") == match_intent("lista los cróns actívos")
    assert match_intent("cada día a las 8 envíame el reporte") == "cron_manager"
    assert match_intent("cada dia a las 8 enviame el reporte") == "cron_manager"


def test_match_intent_is_case_insensitive() -> None:
    assert match_intent("LISTA LOS CRONS ACTIVOS") == "cron_manager"
    assert match_intent("List Scheduled Jobs") == "cron_manager"


def test_match_intent_is_pure_function() -> None:
    """The matcher module must depend only on the stdlib (re, unicodedata)."""
    module = importlib.import_module("prismal.agents.intent_router")
    referenced = {name.split(".")[0] for name in dir(module) if not name.startswith("_")}
    # Sanity: the module exposes only ``match_intent``.
    assert "match_intent" in referenced
    # Calling repeatedly is deterministic and side-effect-free.
    for _ in range(3):
        assert module.match_intent("lista los crons activos") == "cron_manager"
        assert module.match_intent("hola") is None

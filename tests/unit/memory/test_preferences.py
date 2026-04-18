"""Unit tests for lightagent.memory.preferences."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from lightagent.memory.preferences import (
    _PREFS_TEMPLATE,
    PreferenceExtractor,
    PreferenceFacts,
    PreferencesManager,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _manager(tmp_path: Path) -> PreferencesManager:
    """Return a PreferencesManager pointing to a temp directory."""
    return PreferencesManager(profile_dir=tmp_path / "profile")


def _prefs_file(mgr: PreferencesManager) -> Path:
    """Return the PREFERENCES.md path for a manager."""
    return mgr._prefs


# ---------------------------------------------------------------------------
# PreferenceFacts
# ---------------------------------------------------------------------------


def test_preference_facts_defaults_are_empty_lists() -> None:
    """PreferenceFacts initialises all four fields as empty lists."""
    facts = PreferenceFacts()
    assert facts.communication_style == []
    assert facts.tech_stack == []
    assert facts.workflow == []
    assert facts.project_context == []


def test_preference_facts_accepts_values() -> None:
    """PreferenceFacts correctly stores supplied values."""
    facts = PreferenceFacts(
        communication_style=["Spanish"],
        tech_stack=["Python 3.13"],
        workflow=["Conventional commits"],
        project_context=["LightAgent"],
    )
    assert "Spanish" in facts.communication_style
    assert "Python 3.13" in facts.tech_stack
    assert "Conventional commits" in facts.workflow
    assert "LightAgent" in facts.project_context


# ---------------------------------------------------------------------------
# PreferencesManager.load / _ensure_file
# ---------------------------------------------------------------------------


def test_load_returns_empty_string_when_no_file(tmp_path: Path) -> None:
    """load() returns empty string when PREFERENCES.md does not exist."""
    mgr = _manager(tmp_path)
    assert mgr.load() == ""


def test_ensure_file_creates_from_template(tmp_path: Path) -> None:
    """_ensure_file() creates PREFERENCES.md with the four expected sections."""
    mgr = _manager(tmp_path)
    mgr._ensure_file()
    assert _prefs_file(mgr).exists()
    content = mgr.load()
    assert "## Estilo de Comunicacion" in content
    assert "## Stack Tecnico" in content
    assert "## Flujo de Trabajo" in content
    assert "## Contexto del Proyecto" in content


def test_load_returns_existing_content(tmp_path: Path) -> None:
    """load() returns the file content unchanged."""
    mgr = _manager(tmp_path)
    mgr._dir.mkdir(parents=True, exist_ok=True)
    _prefs_file(mgr).write_text("# custom content\n", encoding="utf-8")
    assert mgr.load() == "# custom content\n"


# ---------------------------------------------------------------------------
# PreferencesManager.load_section
# ---------------------------------------------------------------------------


def test_load_section_returns_empty_for_unknown_key(tmp_path: Path) -> None:
    """load_section() returns empty string for unknown section keys."""
    mgr = _manager(tmp_path)
    assert mgr.load_section("nonexistent_key") == ""


def test_load_section_returns_empty_when_no_file(tmp_path: Path) -> None:
    """load_section() returns empty string when PREFERENCES.md does not exist."""
    mgr = _manager(tmp_path)
    assert mgr.load_section("tech_stack") == ""


def test_load_section_returns_bullets(tmp_path: Path) -> None:
    """load_section() returns only the bullet items from the named section."""
    mgr = _manager(tmp_path)
    mgr._dir.mkdir(parents=True, exist_ok=True)
    _prefs_file(mgr).write_text(
        "# User Preferences\n\n"
        "## Estilo de Comunicacion\n\n"
        "## Stack Tecnico\n"
        "- Python 3.13\n"
        "- Uses uv\n\n"
        "## Flujo de Trabajo\n\n"
        "## Contexto del Proyecto\n",
        encoding="utf-8",
    )
    result = mgr.load_section("tech_stack")
    assert "Python 3.13" in result
    assert "Uses uv" in result
    # Other section content should not bleed in
    assert "Flujo de Trabajo" not in result


def test_load_section_does_not_bleed_into_next_section(tmp_path: Path) -> None:
    """load_section() stops at the next ## heading."""
    mgr = _manager(tmp_path)
    mgr._dir.mkdir(parents=True, exist_ok=True)
    _prefs_file(mgr).write_text(
        "## Stack Tecnico\n- Python\n## Flujo de Trabajo\n- daily commits\n",
        encoding="utf-8",
    )
    tech = mgr.load_section("tech_stack")
    assert "Python" in tech
    assert "daily commits" not in tech


# ---------------------------------------------------------------------------
# PreferencesManager.set_fact
# ---------------------------------------------------------------------------


def test_set_fact_creates_file_if_absent(tmp_path: Path) -> None:
    """set_fact() creates PREFERENCES.md from the template when absent."""
    mgr = _manager(tmp_path)
    assert not _prefs_file(mgr).exists()
    mgr.set_fact("tech_stack", "Python 3.13")
    assert _prefs_file(mgr).exists()


def test_set_fact_writes_bullet_to_correct_section(tmp_path: Path) -> None:
    """set_fact() writes the fact as a Markdown bullet in the right section."""
    mgr = _manager(tmp_path)
    mgr.set_fact("tech_stack", "Python 3.13")
    section_content = mgr.load_section("tech_stack")
    assert "Python 3.13" in section_content


def test_set_fact_does_not_duplicate(tmp_path: Path) -> None:
    """set_fact() silently skips a fact that is already present."""
    mgr = _manager(tmp_path)
    mgr.set_fact("tech_stack", "Python 3.13")
    mgr.set_fact("tech_stack", "Python 3.13")
    content = mgr.load()
    assert content.count("Python 3.13") == 1


def test_set_fact_duplicate_check_is_case_insensitive(tmp_path: Path) -> None:
    """set_fact() duplicate check is case-insensitive."""
    mgr = _manager(tmp_path)
    mgr.set_fact("tech_stack", "python 3.13")
    mgr.set_fact("tech_stack", "PYTHON 3.13")
    content = mgr.load()
    # Only one variant should appear
    assert content.count("python") + content.count("PYTHON") == 1


def test_set_fact_unknown_section_is_ignored(tmp_path: Path) -> None:
    """set_fact() with an unknown section key does not create a file."""
    mgr = _manager(tmp_path)
    mgr.set_fact("invalid_section", "some fact")
    assert not _prefs_file(mgr).exists()


def test_set_fact_empty_fact_is_ignored(tmp_path: Path) -> None:
    """set_fact() with an empty fact string does not modify the file."""
    mgr = _manager(tmp_path)
    mgr.set_fact("tech_stack", "   ")
    # File may not be created at all (empty fact is rejected before _ensure_file)
    if _prefs_file(mgr).exists():
        content = mgr.load()
        assert content.count("- ") == 0 or content == _PREFS_TEMPLATE


def test_set_fact_multiple_sections_independent(tmp_path: Path) -> None:
    """Facts written to different sections do not interfere."""
    mgr = _manager(tmp_path)
    mgr.set_fact("communication_style", "Spanish")
    mgr.set_fact("tech_stack", "Python 3.13")
    mgr.set_fact("workflow", "Conventional commits")
    mgr.set_fact("project_context", "LightAgent project")

    assert "Spanish" in mgr.load_section("communication_style")
    assert "Python 3.13" in mgr.load_section("tech_stack")
    assert "Conventional commits" in mgr.load_section("workflow")
    assert "LightAgent project" in mgr.load_section("project_context")


def test_set_fact_strips_surrounding_whitespace(tmp_path: Path) -> None:
    """set_fact() strips leading/trailing whitespace from the fact."""
    mgr = _manager(tmp_path)
    mgr.set_fact("tech_stack", "  Python 3.13  ")
    section = mgr.load_section("tech_stack")
    assert "Python 3.13" in section
    # Ensure no extra whitespace was written
    assert "  Python" not in section


# ---------------------------------------------------------------------------
# PreferencesManager.merge
# ---------------------------------------------------------------------------


def test_merge_adds_all_facts(tmp_path: Path) -> None:
    """merge() adds facts from all four categories to PREFERENCES.md."""
    mgr = _manager(tmp_path)
    facts = PreferenceFacts(
        communication_style=["Spanish"],
        tech_stack=["Python 3.13"],
        workflow=["Conventional commits"],
        project_context=["LightAgent"],
    )
    mgr.merge(facts)
    assert "Spanish" in mgr.load_section("communication_style")
    assert "Python 3.13" in mgr.load_section("tech_stack")
    assert "Conventional commits" in mgr.load_section("workflow")
    assert "LightAgent" in mgr.load_section("project_context")


def test_merge_deduplicates_existing_facts(tmp_path: Path) -> None:
    """merge() does not duplicate facts already in PREFERENCES.md."""
    mgr = _manager(tmp_path)
    mgr.set_fact("tech_stack", "Python 3.13")
    facts = PreferenceFacts(tech_stack=["Python 3.13", "Uses uv"])
    mgr.merge(facts)
    content = mgr.load()
    assert content.count("Python 3.13") == 1
    assert "Uses uv" in content


def test_merge_empty_facts_does_not_raise(tmp_path: Path) -> None:
    """merge() with an empty PreferenceFacts object completes without error."""
    mgr = _manager(tmp_path)
    mgr.merge(PreferenceFacts())  # should not raise


def test_merge_ignores_empty_strings(tmp_path: Path) -> None:
    """merge() skips blank strings in fact lists."""
    mgr = _manager(tmp_path)
    facts = PreferenceFacts(tech_stack=["", "  ", "Python 3.13"])
    mgr.merge(facts)
    content = mgr.load()
    assert "Python 3.13" in content
    # The blank items should produce no bullets
    assert "- \n" not in content


# ---------------------------------------------------------------------------
# PreferencesManager._parse_sections
# ---------------------------------------------------------------------------


def test_parse_sections_extracts_bullets(tmp_path: Path) -> None:
    """_parse_sections() extracts bullet lists per section."""
    mgr = _manager(tmp_path)
    content = (
        "# User Preferences\n\n"
        "## Estilo de Comunicacion\n- Spanish\n\n"
        "## Stack Tecnico\n- Python 3.13\n- uv\n\n"
        "## Flujo de Trabajo\n\n"
        "## Contexto del Proyecto\n- LightAgent\n"
    )
    result = mgr._parse_sections(content)
    assert result["communication_style"] == ["Spanish"]
    assert result["tech_stack"] == ["Python 3.13", "uv"]
    assert result["workflow"] == []
    assert result["project_context"] == ["LightAgent"]


# ---------------------------------------------------------------------------
# PreferenceExtractor
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extractor_returns_empty_for_empty_conversation() -> None:
    """PreferenceExtractor.extract() returns empty facts for blank input."""
    extractor = PreferenceExtractor()
    result = await extractor.extract("")
    assert result == PreferenceFacts()


@pytest.mark.asyncio
async def test_extractor_returns_empty_for_whitespace_only() -> None:
    """PreferenceExtractor.extract() returns empty facts for whitespace-only input."""
    extractor = PreferenceExtractor()
    result = await extractor.extract("   \n\t  ")
    assert result == PreferenceFacts()


@pytest.mark.asyncio
async def test_extractor_parses_llm_json_response() -> None:
    """PreferenceExtractor.extract() correctly parses a valid JSON response."""
    llm_response = MagicMock()
    llm_response.content = json.dumps(
        {
            "communication_style": ["Spanish"],
            "tech_stack": ["Python 3.13", "Uses uv"],
            "workflow": ["Conventional commits"],
            "project_context": ["LightAgent"],
        }
    )

    mock_llm = AsyncMock()
    mock_llm.ainvoke = AsyncMock(return_value=llm_response)

    with patch("lightagent.providers.registry.ProviderRegistry") as mock_registry_cls:
        mock_registry = MagicMock()
        mock_registry.get_llm_with_fallback.return_value = mock_llm
        mock_registry_cls.return_value = mock_registry

        extractor = PreferenceExtractor()
        result = await extractor.extract("User: I prefer Python. Agent: Got it.")

    assert "Spanish" in result.communication_style
    assert "Python 3.13" in result.tech_stack
    assert "Uses uv" in result.tech_stack
    assert "Conventional commits" in result.workflow
    assert "LightAgent" in result.project_context


@pytest.mark.asyncio
async def test_extractor_strips_markdown_fences() -> None:
    """PreferenceExtractor.extract() strips ```json code fences from LLM output."""
    raw_json = json.dumps(
        {
            "communication_style": ["English"],
            "tech_stack": [],
            "workflow": [],
            "project_context": [],
        }
    )
    llm_response = MagicMock()
    llm_response.content = f"```json\n{raw_json}\n```"

    mock_llm = AsyncMock()
    mock_llm.ainvoke = AsyncMock(return_value=llm_response)

    with patch("lightagent.providers.registry.ProviderRegistry") as mock_registry_cls:
        mock_registry = MagicMock()
        mock_registry.get_llm_with_fallback.return_value = mock_llm
        mock_registry_cls.return_value = mock_registry

        extractor = PreferenceExtractor()
        result = await extractor.extract("some conversation")

    assert "English" in result.communication_style


@pytest.mark.asyncio
async def test_extractor_returns_empty_on_llm_error() -> None:
    """PreferenceExtractor.extract() returns empty facts when the LLM fails."""
    with patch("lightagent.providers.registry.ProviderRegistry") as mock_registry_cls:
        mock_registry = MagicMock()
        mock_registry.get_llm_with_fallback.side_effect = RuntimeError("LLM unavailable")
        mock_registry_cls.return_value = mock_registry

        extractor = PreferenceExtractor()
        result = await extractor.extract("some conversation")

    assert result == PreferenceFacts()


@pytest.mark.asyncio
async def test_extractor_returns_empty_on_invalid_json() -> None:
    """PreferenceExtractor.extract() returns empty facts when JSON parse fails."""
    llm_response = MagicMock()
    llm_response.content = "not valid json at all"

    mock_llm = AsyncMock()
    mock_llm.ainvoke = AsyncMock(return_value=llm_response)

    with patch("lightagent.providers.registry.ProviderRegistry") as mock_registry_cls:
        mock_registry = MagicMock()
        mock_registry.get_llm_with_fallback.return_value = mock_llm
        mock_registry_cls.return_value = mock_registry

        extractor = PreferenceExtractor()
        result = await extractor.extract("some conversation")

    assert result == PreferenceFacts()


# ---------------------------------------------------------------------------
# Integration: set_fact → load_section round-trip
# ---------------------------------------------------------------------------


def test_roundtrip_set_fact_and_load_section(tmp_path: Path) -> None:
    """Facts written with set_fact() can be read back via load_section()."""
    mgr = _manager(tmp_path)
    facts_to_write = [
        ("communication_style", "Prefers Spanish"),
        ("tech_stack", "Python 3.13"),
        ("tech_stack", "FastAPI"),
        ("workflow", "Uses uv run"),
        ("project_context", "LightAgent"),
    ]
    for section, fact in facts_to_write:
        mgr.set_fact(section, fact)

    assert "Prefers Spanish" in mgr.load_section("communication_style")
    tech = mgr.load_section("tech_stack")
    assert "Python 3.13" in tech
    assert "FastAPI" in tech
    assert "Uses uv run" in mgr.load_section("workflow")
    assert "LightAgent" in mgr.load_section("project_context")


def test_merge_then_load_section_roundtrip(tmp_path: Path) -> None:
    """Facts written via merge() are retrievable via load_section()."""
    mgr = _manager(tmp_path)
    facts = PreferenceFacts(
        communication_style=["Concise"],
        tech_stack=["Python", "ruff"],
    )
    mgr.merge(facts)
    assert "Concise" in mgr.load_section("communication_style")
    tech = mgr.load_section("tech_stack")
    assert "Python" in tech
    assert "ruff" in tech


# ---------------------------------------------------------------------------
# Module __all__
# ---------------------------------------------------------------------------


def test_module_exports() -> None:
    """preferences module exports the expected public symbols."""
    import lightagent.memory.preferences as mod

    assert "PreferenceFacts" in mod.__all__
    assert "PreferencesManager" in mod.__all__
    assert "PreferenceExtractor" in mod.__all__

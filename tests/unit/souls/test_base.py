"""Unit tests for ``prismal.souls.base`` — SOUL.md parsing and validation.

Covers SPEC-KOK-SOUL-001 / RF-KOK-01 and the K1 "done when" criteria:
a soul loads from a ``SOUL.md`` alone; invalid/oversized souls raise
:class:`SoulValidationError`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from prismal.core.config import Settings
from prismal.core.exceptions import KokoroError, SoulValidationError
from prismal.souls.base import (
    Soul,
    SoulMetadata,
    _find_soul_md,
    _soul_md_body,
    load_soul,
    parse_soul_md,
)

if TYPE_CHECKING:
    from pathlib import Path

_SOUL_MD = """---
name: spirit
alias_jp: 魂 (tamashii)
description: The values-and-vision lens of Kokoro
role: values
temperament: principled, long-horizon, calm
values: [integrity, human-dignity, long-term-good]
version: 1.0.0
author: prismal
tags: [kokoro, soul, spirit]
---

You are **Spirit**, one of the three voices of Kokoro.
"""


def _write_soul(root: Path, soul_id: str, content: str = _SOUL_MD, name: str = "SOUL.md") -> Path:
    soul_dir = root / soul_id
    soul_dir.mkdir(parents=True, exist_ok=True)
    (soul_dir / name).write_text(content, encoding="utf-8")
    return soul_dir


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(souls_dir=str(tmp_path))


# ── _find_soul_md ─────────────────────────────────────────────────────────────


def test_find_soul_md_is_case_insensitive(tmp_path: Path) -> None:
    soul_dir = _write_soul(tmp_path, "spirit", name="Soul.MD")
    found = _find_soul_md(soul_dir)
    assert found is not None
    assert found.name == "Soul.MD"


def test_find_soul_md_missing_dir_returns_none(tmp_path: Path) -> None:
    assert _find_soul_md(tmp_path / "nope") is None


# ── parse_soul_md ─────────────────────────────────────────────────────────────


def test_parse_soul_md_returns_frontmatter(tmp_path: Path) -> None:
    soul_dir = _write_soul(tmp_path, "spirit")
    meta = parse_soul_md(soul_dir)
    assert meta["name"] == "spirit"
    assert meta["role"] == "values"
    assert meta["values"] == ["integrity", "human-dignity", "long-term-good"]


def test_parse_soul_md_empty_on_missing_file(tmp_path: Path) -> None:
    (tmp_path / "empty").mkdir()
    assert parse_soul_md(tmp_path / "empty") == {}


def test_parse_soul_md_empty_without_frontmatter(tmp_path: Path) -> None:
    soul_dir = _write_soul(tmp_path, "raw", content="no frontmatter here")
    assert parse_soul_md(soul_dir) == {}


def test_parse_soul_md_empty_on_unterminated_frontmatter(tmp_path: Path) -> None:
    soul_dir = _write_soul(tmp_path, "broken", content="---\nname: x\n(never closed)")
    assert parse_soul_md(soul_dir) == {}


def test_parse_soul_md_empty_on_invalid_yaml(tmp_path: Path) -> None:
    soul_dir = _write_soul(tmp_path, "badyaml", content="---\n: : :\n---\nbody")
    assert parse_soul_md(soul_dir) == {}


# ── _soul_md_body ─────────────────────────────────────────────────────────────


def test_soul_md_body_strips_frontmatter(tmp_path: Path) -> None:
    soul_dir = _write_soul(tmp_path, "spirit")
    body = _soul_md_body(soul_dir)
    assert body.startswith("You are **Spirit**")
    assert "name: spirit" not in body


def test_soul_md_body_empty_on_missing_file(tmp_path: Path) -> None:
    (tmp_path / "empty").mkdir()
    assert _soul_md_body(tmp_path / "empty") == ""


# ── load_soul ─────────────────────────────────────────────────────────────────


def test_load_soul_happy_path(tmp_path: Path, settings: Settings) -> None:
    soul_dir = _write_soul(tmp_path, "spirit")
    soul = load_soul(soul_dir, settings=settings)
    assert isinstance(soul, Soul)
    assert isinstance(soul.metadata, SoulMetadata)
    assert soul.metadata.name == "spirit"
    assert soul.metadata.alias_jp.startswith("魂")
    assert soul.body.startswith("You are **Spirit**")
    assert soul.source_dir == soul_dir.resolve()


def test_load_soul_missing_soul_md_raises(tmp_path: Path, settings: Settings) -> None:
    empty = tmp_path / "ghost"
    empty.mkdir()
    with pytest.raises(SoulValidationError, match="SOUL.md not found"):
        load_soul(empty, settings=settings)


def test_load_soul_missing_required_metadata_raises(tmp_path: Path, settings: Settings) -> None:
    # 'role' and 'description' are required by SoulMetadata.
    soul_dir = _write_soul(tmp_path, "incomplete", content="---\nname: incomplete\n---\nbody")
    with pytest.raises(SoulValidationError, match="invalid metadata"):
        load_soul(soul_dir, settings=settings)


def test_load_soul_oversized_body_raises(tmp_path: Path) -> None:
    settings = Settings(souls_dir=str(tmp_path), soul_max_body_chars=10)
    soul_dir = _write_soul(tmp_path, "spirit")
    with pytest.raises(SoulValidationError, match="body too large"):
        load_soul(soul_dir, settings=settings)


def test_load_soul_outside_root_raises(tmp_path: Path) -> None:
    inside_root = tmp_path / "root"
    inside_root.mkdir()
    settings = Settings(souls_dir=str(inside_root))
    outside_dir = _write_soul(tmp_path, "escapee")
    with pytest.raises(SoulValidationError, match="outside the souls root"):
        load_soul(outside_dir, settings=settings)


def test_soul_validation_error_is_kokoro_error(tmp_path: Path, settings: Settings) -> None:
    with pytest.raises(KokoroError):
        load_soul(tmp_path / "missing", settings=settings)

"""Unit tests for the zip-install path of ``prismal.skills.manager``.

Targets ``SkillsManager.install_from_zip`` and the ``_zip_detect_prefix`` /
``_zip_skill_name`` helpers, none of which were previously exercised.
"""

from __future__ import annotations

import zipfile
from typing import TYPE_CHECKING

from prismal.skills.manager import (
    SkillsManager,
    _zip_detect_prefix,
    _zip_skill_name,
)

if TYPE_CHECKING:
    from pathlib import Path

_SKILL_MD = """---
name: packaged-skill
description: A packaged skill
version: 1.2.3
author: tester
---

# Packaged skill body
"""


def _make_zip(zip_path: Path, members: dict[str, str]) -> Path:
    """Create a zip archive from a ``{member_path: text}`` mapping."""
    with zipfile.ZipFile(zip_path, "w") as zf:
        for name, text in members.items():
            zf.writestr(name, text)
    return zip_path


# ── _zip_detect_prefix ───────────────────────────────────────────────────────


def test_zip_detect_prefix_root() -> None:
    """skill.md at the archive root yields an empty prefix."""
    assert _zip_detect_prefix(["skill.md", "scripts/x.py"]) == ""


def test_zip_detect_prefix_single_dir() -> None:
    """skill.md inside one top-level dir yields that dir as prefix."""
    assert _zip_detect_prefix(["my_skill/skill.md", "my_skill/scripts/x.py"]) == "my_skill/"


def test_zip_detect_prefix_filename_case_insensitive() -> None:
    """An upper-case SKILL.md filename is still detected (lower-case dir)."""
    assert _zip_detect_prefix(["demo/SKILL.md"]) == "demo/"


def test_zip_detect_prefix_uppercase_dir_not_matched() -> None:
    """Documents a limitation: the top-dir name comparison is case-sensitive.

    ``_zip_detect_prefix`` lower-cases member names for the lookup table but
    rebuilds the probe key with the original-case directory, so an upper-case
    directory with ``SKILL.md`` is not detected. Captured here so the behaviour
    is intentional and visible rather than a silent surprise.
    """
    assert _zip_detect_prefix(["Demo/SKILL.md"]) is None


def test_zip_detect_prefix_none_when_missing() -> None:
    """No skill.md anywhere yields None."""
    assert _zip_detect_prefix(["readme.txt", "src/main.py"]) is None


# ── _zip_skill_name ──────────────────────────────────────────────────────────


def test_zip_skill_name_from_prefix(tmp_path: Path) -> None:
    """A non-empty prefix becomes a snake_case skill name."""
    assert _zip_skill_name("my-skill/", tmp_path / "ignored.zip") == "my_skill"


def test_zip_skill_name_from_filename(tmp_path: Path) -> None:
    """An empty prefix falls back to the snake_case zip stem."""
    assert _zip_skill_name("", tmp_path / "cool-tool.zip") == "cool_tool"


# ── install_from_zip ─────────────────────────────────────────────────────────


def test_install_from_zip_single_dir_layout(tmp_path: Path) -> None:
    """A well-formed single-directory zip installs and generates skill.py."""
    zip_path = _make_zip(
        tmp_path / "pkg.zip",
        {"my_demo/skill.md": _SKILL_MD, "my_demo/scripts/run.py": "print('hi')\n"},
    )
    mgr = SkillsManager(skills_root=tmp_path / "skills")

    name, err = mgr.install_from_zip(zip_path)

    assert err is None
    assert name == "my_demo"
    dest = tmp_path / "skills" / "available" / "my_demo"
    assert (dest / "skill.md").is_file()
    assert (dest / "scripts" / "run.py").is_file()
    assert (dest / "skill.py").is_file()  # auto-generated


def test_install_from_zip_root_layout_uses_filename(tmp_path: Path) -> None:
    """When skill.md sits at the root, the name derives from the filename."""
    zip_path = _make_zip(tmp_path / "rooted-skill.zip", {"skill.md": _SKILL_MD})
    mgr = SkillsManager(skills_root=tmp_path / "skills")

    name, err = mgr.install_from_zip(zip_path)

    assert err is None
    assert name == "rooted_skill"
    assert (tmp_path / "skills" / "available" / "rooted_skill" / "skill.md").is_file()


def test_install_from_zip_missing_file(tmp_path: Path) -> None:
    """A nonexistent path returns an error, not a raise."""
    mgr = SkillsManager(skills_root=tmp_path / "skills")
    name, err = mgr.install_from_zip(tmp_path / "nope.zip")
    assert name == ""
    assert err is not None


def test_install_from_zip_wrong_suffix(tmp_path: Path) -> None:
    """A non-.zip file is rejected."""
    bogus = tmp_path / "thing.tar"
    bogus.write_text("not a zip", encoding="utf-8")
    mgr = SkillsManager(skills_root=tmp_path / "skills")
    name, err = mgr.install_from_zip(bogus)
    assert name == ""
    assert err is not None and ".zip" in err


def test_install_from_zip_without_skill_md(tmp_path: Path) -> None:
    """A zip with no skill.md is rejected with an explanatory error."""
    zip_path = _make_zip(tmp_path / "nomd.zip", {"src/main.py": "x = 1\n"})
    mgr = SkillsManager(skills_root=tmp_path / "skills")
    name, err = mgr.install_from_zip(zip_path)
    assert name == ""
    assert err is not None and "skill.md" in err


def test_install_from_zip_duplicate_rejected(tmp_path: Path) -> None:
    """Installing a skill whose name already exists is rejected."""
    zip_path = _make_zip(tmp_path / "dup.zip", {"dup_skill/skill.md": _SKILL_MD})
    mgr = SkillsManager(skills_root=tmp_path / "skills")

    first_name, first_err = mgr.install_from_zip(zip_path)
    assert first_err is None and first_name == "dup_skill"

    name, err = mgr.install_from_zip(zip_path)
    assert name == ""
    assert err is not None


def test_install_from_zip_bad_archive(tmp_path: Path) -> None:
    """A corrupt .zip is reported as an invalid archive."""
    bad = tmp_path / "broken.zip"
    bad.write_bytes(b"PK\x03\x04 not really a zip")
    mgr = SkillsManager(skills_root=tmp_path / "skills")
    name, err = mgr.install_from_zip(bad)
    assert name == ""
    assert err is not None

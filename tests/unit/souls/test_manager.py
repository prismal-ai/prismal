"""Unit tests for ``prismal.souls.manager`` — tier discovery and the triad.

Covers SPEC-KOK-SOUL-002 / RF-KOK-02 / RF-KOK-03 and the K1 "done when"
criteria: ``load_triad`` returns exactly three souls or raises
:class:`KokoroConfigError`; the three default souls ship and load.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import prismal.souls as souls_pkg
from prismal.core.config import Settings
from prismal.core.exceptions import KokoroConfigError, SoulNotFoundError
from prismal.souls.manager import SoulsManager

_SOUL_TEMPLATE = """---
name: {name}
description: Test soul {name}
role: {role}
---

You are {name}.
"""


def _write_soul(tier_dir: Path, soul_id: str, role: str = "values") -> Path:
    soul_dir = tier_dir / soul_id
    soul_dir.mkdir(parents=True, exist_ok=True)
    (soul_dir / "SOUL.md").write_text(
        _SOUL_TEMPLATE.format(name=soul_id, role=role), encoding="utf-8"
    )
    return soul_dir


@pytest.fixture
def souls_root(tmp_path: Path) -> Path:
    for soul_id, role in (("spirit", "values"), ("mind", "logic"), ("heart", "empathy")):
        _write_soul(tmp_path / "available", soul_id, role)
    return tmp_path


@pytest.fixture
def manager(souls_root: Path) -> SoulsManager:
    return SoulsManager(souls_root=souls_root, settings=Settings(souls_dir=str(souls_root)))


# ── list_souls ────────────────────────────────────────────────────────────────


def test_list_souls_discovers_available(manager: SoulsManager) -> None:
    names = [m.name for m in manager.list_souls()]
    assert names == ["heart", "mind", "spirit"]


def test_list_souls_skips_invalid_souls(souls_root: Path, manager: SoulsManager) -> None:
    bad = souls_root / "available" / "broken"
    bad.mkdir()
    (bad / "SOUL.md").write_text("---\nname: broken\n---\nbody", encoding="utf-8")
    names = [m.name for m in manager.list_souls()]
    assert "broken" not in names
    assert len(names) == 3


def test_list_souls_includes_custom_tier(souls_root: Path, manager: SoulsManager) -> None:
    _write_soul(souls_root / "custom", "risk", role="risk")
    assert "risk" in [m.name for m in manager.list_souls()]


def test_active_tier_acts_as_allowlist(souls_root: Path, manager: SoulsManager) -> None:
    _write_soul(souls_root / "active", "spirit")
    names = [m.name for m in manager.list_souls()]
    assert names == ["spirit"]


# ── load ──────────────────────────────────────────────────────────────────────


def test_load_by_id(manager: SoulsManager) -> None:
    soul = manager.load("mind")
    assert soul.metadata.name == "mind"
    assert soul.metadata.role == "logic"


def test_load_unknown_id_raises(manager: SoulsManager) -> None:
    with pytest.raises(SoulNotFoundError, match="ghost"):
        manager.load("ghost")


def test_load_blocked_by_allowlist_raises(souls_root: Path, manager: SoulsManager) -> None:
    _write_soul(souls_root / "active", "spirit")
    with pytest.raises(SoulNotFoundError):
        manager.load("mind")


def test_active_soul_overrides_available(souls_root: Path, manager: SoulsManager) -> None:
    override = souls_root / "active" / "spirit"
    override.mkdir(parents=True)
    (override / "SOUL.md").write_text(
        _SOUL_TEMPLATE.format(name="spirit", role="overridden"), encoding="utf-8"
    )
    assert manager.load("spirit").metadata.role == "overridden"


# ── load_triad ────────────────────────────────────────────────────────────────


def test_load_triad_default_ids(manager: SoulsManager) -> None:
    triad = manager.load_triad()
    assert [s.metadata.name for s in triad] == ["spirit", "mind", "heart"]


def test_load_triad_explicit_ids(souls_root: Path, manager: SoulsManager) -> None:
    _write_soul(souls_root / "available", "risk", role="risk")
    triad = manager.load_triad(["risk", "mind", "heart"])
    assert [s.metadata.name for s in triad] == ["risk", "mind", "heart"]


@pytest.mark.parametrize(
    "ids",
    [
        ["spirit", "mind"],
        ["spirit", "mind", "heart", "risk"],
        ["spirit", "spirit", "mind"],
        [],
    ],
)
def test_load_triad_arity_guard(manager: SoulsManager, ids: list[str]) -> None:
    with pytest.raises(KokoroConfigError, match="exactly 3 distinct souls"):
        manager.load_triad(ids)


def test_load_triad_unknown_soul_raises(manager: SoulsManager) -> None:
    with pytest.raises(SoulNotFoundError):
        manager.load_triad(["spirit", "mind", "ghost"])


# ── packaged defaults (RF-KOK-03) ────────────────────────────────────────────


def test_packaged_default_souls_load() -> None:
    packaged_root = Path(souls_pkg.__file__).parent
    manager = SoulsManager(
        souls_root=packaged_root, settings=Settings(souls_dir=str(packaged_root))
    )
    triad = manager.load_triad(["spirit", "mind", "heart"])
    assert [s.metadata.name for s in triad] == ["spirit", "mind", "heart"]
    assert [s.metadata.role for s in triad] == ["values", "logic", "empathy"]
    aliases = [s.metadata.alias_jp for s in triad]
    assert aliases == ["魂 (tamashii)", "知 (chi)", "情 (jō)"]
    assert all(s.body.strip() for s in triad)

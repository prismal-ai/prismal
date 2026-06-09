"""Tests for the composition config loaders (Phase R — R3, SPEC-CR-005)."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from prismal.composition.config_sources import (
    DEFAULT_COLLECTION_BASE,
    apply_org_overrides,
    collection_for,
    load_mcp_config,
    resolve_skills_source,
    resolve_vector_store,
)
from prismal.core.config import Settings

pytestmark = pytest.mark.unit


class TestCollectionFor:
    def test_single_tenant_keeps_base(self) -> None:
        assert collection_for("docs", None) == "docs"

    def test_tenant_suffix(self) -> None:
        assert collection_for("docs", "acme") == "docs_acme"

    def test_applied_identically_to_rag_and_memory(self) -> None:
        # R4: same tenant -> same suffix across logical collections.
        assert collection_for("default", "acme") == "default_acme"
        assert collection_for("prismal_memory", "acme") == "prismal_memory_acme"
        # Different tenants never collide.
        assert collection_for("default", "acme") != collection_for("default", "globex")


class TestApplyOrgOverrides:
    def test_no_overrides_returns_same_object(self) -> None:
        s = Settings()
        assert apply_org_overrides(s, "acme", None) is s
        assert apply_org_overrides(s, "acme", {}) is s

    def test_overrides_applied_without_mutating_global(self) -> None:
        s = Settings(vector_store_backend="chroma")
        eff = apply_org_overrides(s, "acme", {"vector_store_backend": "lancedb"})
        assert eff.vector_store_backend == "lancedb"
        assert s.vector_store_backend == "chroma"  # global untouched
        assert eff is not s


class TestResolveVectorStore:
    def test_backend_and_collection(self) -> None:
        s = Settings(vector_store_backend="chroma")
        backend, collection = resolve_vector_store(s, "acme")
        assert backend == "chroma"
        assert collection == collection_for(DEFAULT_COLLECTION_BASE, "acme")

    def test_single_tenant(self) -> None:
        backend, collection = resolve_vector_store(Settings(), None)
        assert collection == DEFAULT_COLLECTION_BASE


class TestLoadMcpConfig:
    def test_missing_file(self, tmp_path: Path) -> None:
        cfg = load_mcp_config(tmp_path / "nope.yaml")
        assert cfg.exists is False
        assert cfg.servers == ()
        assert cfg.enabled_count == 0

    def test_parses_servers_without_connecting(self, tmp_path: Path) -> None:
        path = tmp_path / "mcp.yaml"
        path.write_text(
            textwrap.dedent(
                """
                servers:
                  - name: filesystem
                    type: stdio
                    enabled: true
                    capabilities: ["file_management", "general"]
                  - name: web_search
                    type: stdio
                    enabled: false
                    capabilities: ["research"]
                """
            ),
            encoding="utf-8",
        )
        cfg = load_mcp_config(path)
        assert cfg.exists is True
        assert len(cfg.servers) == 2
        assert cfg.enabled_count == 1
        first = cfg.servers[0]
        assert first.name == "filesystem"
        assert first.enabled is True
        assert "general" in first.capabilities

    def test_default_path_used_when_none(self) -> None:
        # Repo ships config/mcp_servers.yaml; the loader must find it.
        cfg = load_mcp_config()
        assert cfg.exists is True
        assert len(cfg.servers) > 0


class TestResolveSkillsSource:
    def test_describes_dirs_and_available(self) -> None:
        src = resolve_skills_source(Settings())
        assert src.available_dir.name == "available"
        assert src.active_dir.name == "active"
        assert src.custom_dir.name == "custom"
        # The repo ships several available skills.
        assert len(src.available_names) > 0

    def test_external_dirs_resolved(self) -> None:
        src = resolve_skills_source(Settings(external_skills_dirs=["~/somewhere"]))
        assert len(src.external_dirs) == 1
        assert src.external_dirs[0].is_absolute()

"""``ollama`` sub-tier construction (SPEC-DEC-OLL-001/010, CAS-001 — DEC2-07/09)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from prismal.core.config import Settings
from prismal.decisions.adapters import ollama as ollama_mod
from prismal.decisions.adapters.factory import build_backend, build_backends
from prismal.decisions.adapters.ollama import OllamaDecisionAdapter
from prismal.decisions.adapters.systemone_http import SystemOneHttpDecisionAdapter
from prismal.decisions.cascade import DecisionCascade

pytestmark = pytest.mark.unit


def _settings(tmp_path: Path, **kw: Any) -> Settings:
    base: dict[str, Any] = {"decision_calibration_dir": str(tmp_path)}
    base.update(kw)
    return Settings(**base)


def test_build_backends_expands_into_fast_then_accurate(tmp_path: Path) -> None:
    fast, accurate = build_backends("ollama", _settings(tmp_path))
    assert isinstance(fast, OllamaDecisionAdapter)
    assert isinstance(accurate, OllamaDecisionAdapter)
    assert (fast.name, accurate.name) == ("ollama:fast", "ollama:accurate")
    assert (fast.model_name, accurate.model_name) == ("tev1:4b", "nimble:9b-q4_K_M")
    assert fast.group is accurate.group
    assert fast.group.fast is fast
    assert fast.group.accurate is accurate


def test_only_configured_tiers(tmp_path: Path) -> None:
    (accurate,) = build_backends(
        "ollama", _settings(tmp_path, decision_ollama_models={"accurate": "nimble:9b-q4_K_M"})
    )
    assert accurate.name == "ollama:accurate"
    assert isinstance(accurate, OllamaDecisionAdapter)
    assert accurate.group.fast is None


def test_base_url_resolution(tmp_path: Path) -> None:
    (fast, _) = build_backends("ollama", _settings(tmp_path))
    assert isinstance(fast, OllamaDecisionAdapter)
    assert fast._url == "http://localhost:11434/v1/systemone"
    (fast, _) = build_backends(
        "ollama", _settings(tmp_path, ollama_base_url="http://127.0.0.1:9999/")
    )
    assert isinstance(fast, OllamaDecisionAdapter)
    assert fast._url == "http://127.0.0.1:9999/v1/systemone"
    (fast, _) = build_backends(
        "ollama",
        _settings(
            tmp_path,
            ollama_base_url="http://127.0.0.1:9999",
            decision_ollama_base_url="http://[::1]:11434",
        ),
    )
    assert isinstance(fast, OllamaDecisionAdapter)
    assert fast._url == "http://[::1]:11434/v1/systemone"


def test_settings_reach_the_adapters(tmp_path: Path) -> None:
    fast, accurate = build_backends(
        "ollama",
        _settings(
            tmp_path,
            decision_ollama_keep_alive="2h",
            decision_ollama_max_batch=3,
            decision_ollama_accurate_max_p95_ms=700,
        ),
    )
    assert isinstance(accurate, OllamaDecisionAdapter)
    assert accurate._keep_alive == "2h"
    assert accurate._max_batch == 3
    assert accurate._max_p95_ms == 700


def test_non_loopback_logs_one_warning(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[str, dict[str, Any]]] = []

    class _Log:
        def warning(self, event: str, **kw: Any) -> None:
            seen.append((event, kw))

    monkeypatch.setattr(ollama_mod, "logger", _Log())
    build_backends(
        "ollama",
        _settings(
            tmp_path,
            decision_ollama_base_url="http://10.0.0.5:11434",
            decision_ollama_allow_remote=True,
        ),
    )
    assert [e for e, _ in seen] == ["decision_ollama_remote_base_url"]
    seen.clear()
    build_backends("ollama", _settings(tmp_path))
    assert seen == []


def test_build_backends_wraps_single_backends(tmp_path: Path) -> None:
    (jev,) = build_backends("jev", _settings(tmp_path, jev_api_key="k"))
    assert isinstance(jev, SystemOneHttpDecisionAdapter)
    assert build_backends("jev", _settings(tmp_path)) == []
    assert build_backends("rule", _settings(tmp_path)) == []


def test_build_backend_still_returns_one(tmp_path: Path) -> None:
    backend = build_backend("ollama", _settings(tmp_path))
    assert backend is not None
    assert backend.name == "ollama:fast"
    jev = build_backend("jev", _settings(tmp_path, jev_api_key="k"))
    assert jev is not None
    assert jev.name == "jev"


def test_from_settings_orders_ollama_sub_tiers(tmp_path: Path) -> None:
    cascade = DecisionCascade.from_settings(
        _settings(
            tmp_path,
            decision_backend_order=["rule", "laya", "ollama", "jev", "llm"],
            jev_api_key="k",
        )
    )
    assert cascade.tier_names == (
        "rule",
        "laya",
        "ollama:fast",
        "ollama:accurate",
        "jev",
        "llm",
    )


# ── review regressions (M1, L6) ───────────────────────────────────────────────


def test_tenant_override_to_remote_url_builds_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M1: ``model_copy`` overrides skip the Settings validator; the factory refuses."""
    seen: list[str] = []

    class _Log:
        def warning(self, event: str, **kw: Any) -> None:
            seen.append(event)

    monkeypatch.setattr(ollama_mod, "logger", _Log())
    settings = _settings(tmp_path).model_copy(
        update={"decision_ollama_base_url": "http://169.254.169.254:80"}
    )
    assert build_backends("ollama", settings) == []
    assert build_backend("ollama", settings) is None
    assert seen and set(seen) == {"decision_ollama_remote_refused"}


def test_remote_global_ollama_url_fallback_builds_nothing(tmp_path: Path) -> None:
    settings = _settings(tmp_path).model_copy(
        update={"decision_ollama_base_url": None, "ollama_base_url": "http://ollama.internal:11434"}
    )
    assert build_backends("ollama", settings) == []


def test_remote_allowed_explicitly_still_builds(tmp_path: Path) -> None:
    settings = _settings(tmp_path).model_copy(
        update={
            "decision_ollama_base_url": "http://10.0.0.5:11434",
            "decision_ollama_allow_remote": True,
        }
    )
    assert len(build_backends("ollama", settings)) == 2


def test_build_backend_single_ollama_is_relinked_alone(tmp_path: Path) -> None:
    """L6: the lone ``fast`` must not defer low-frequency questions to an orphan sibling."""
    backend = build_backend("ollama", _settings(tmp_path))
    assert isinstance(backend, OllamaDecisionAdapter)
    assert backend.group.fast is backend
    assert backend.group.accurate is None


def test_sub_tiers_expose_their_own_budget() -> None:
    from prismal.core.config import Settings
    from prismal.decisions.adapters.factory import build_backends

    backends = build_backends("ollama", Settings(decision_ollama_timeout_ms=3500))
    assert [b.budget_ms for b in backends] == [3500, 3500]  # type: ignore[attr-defined]

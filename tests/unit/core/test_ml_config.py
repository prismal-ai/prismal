"""Tests for ML pipeline configuration settings."""

from prismal.core.config import Settings


def test_ml_settings_defaults() -> None:
    """ML settings have correct defaults."""
    s = Settings()
    assert s.ml_enabled is True
    assert s.ml_time_budget == 120
    assert s.ml_quality_threshold == 0.7
    assert s.ml_max_iterations == 3
    assert s.ml_workspace_root == "data/workspace/ml_models"
    assert s.ml_max_rows == 1_000_000
    assert s.ml_random_seed == 42
    assert s.ml_shap_max_samples == 1000


def test_ml_time_budget_bounds() -> None:
    """ml_time_budget must be positive."""
    import pytest

    with pytest.raises(Exception):
        Settings(ml_time_budget=0)

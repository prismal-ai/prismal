"""Tests for the token-efficiency exception hierarchy (Phase TKE — SPEC-TKE-ERR-001)."""

from __future__ import annotations

import pytest

from prismal.core.exceptions import (
    ArtifactNotFound,
    ArtifactStoreError,
    MaskedToolCallError,
    PrismalError,
    PromptCacheError,
    TokenEfficiencyConfigError,
    TokenEfficiencyError,
)


def test_token_efficiency_error_is_prismal_error() -> None:
    assert issubclass(TokenEfficiencyError, PrismalError)


@pytest.mark.parametrize(
    "exc",
    [TokenEfficiencyConfigError, PromptCacheError, ArtifactStoreError, MaskedToolCallError],
)
def test_subclasses_extend_token_efficiency_error(exc: type[TokenEfficiencyError]) -> None:
    assert issubclass(exc, TokenEfficiencyError)


def test_artifact_not_found_is_store_error() -> None:
    assert issubclass(ArtifactNotFound, ArtifactStoreError)
    with pytest.raises(PrismalError):
        raise ArtifactNotFound("missing")

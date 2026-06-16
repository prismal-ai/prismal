"""Tests for the identity exception hierarchy (Phase IDN — SPEC-IDN-ERR-001).

Every identity error is a :class:`IdentityError`, which is a :class:`PrismalError`,
so a single ``except PrismalError`` (or ``except IdentityError``) catches them all.
"""

from __future__ import annotations

import pytest

from prismal.core.exceptions import (
    CredentialResolutionError,
    DelegationError,
    DidVerificationError,
    IdentityConfigError,
    IdentityError,
    PolicyDenied,
    PrismalError,
    ScopeError,
)

_SUBCLASSES = [
    DidVerificationError,
    ScopeError,
    PolicyDenied,
    CredentialResolutionError,
    IdentityConfigError,
    DelegationError,
]


def test_identity_error_is_a_prismal_error() -> None:
    assert issubclass(IdentityError, PrismalError)


@pytest.mark.parametrize("exc", _SUBCLASSES)
def test_subclasses_descend_from_identity_error(exc: type[IdentityError]) -> None:
    assert issubclass(exc, IdentityError)


@pytest.mark.parametrize("exc", _SUBCLASSES)
def test_subclasses_caught_as_prismal_error(exc: type[IdentityError]) -> None:
    with pytest.raises(PrismalError):
        raise exc("boom")

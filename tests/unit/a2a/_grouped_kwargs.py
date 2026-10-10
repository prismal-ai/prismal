"""Test helper: fold flat OAuth / client kwargs into ``OAuthBinding`` / ``EndpointPolicy``."""

from __future__ import annotations

from typing import Any

from prismal.a2a.oauth import EndpointPolicy, OAuthBinding

_BINDING = ("vault", "org_id", "principal", "http_client", "clock", "audit")
_POLICY = ("allow_insecure_localhost", "allow_private_hosts", "allowlist", "strict")


def grouped(kw: dict[str, Any], *, binding: bool = True) -> dict[str, Any]:
    """Move policy keys into ``policy=`` and (when *binding*) runtime keys into ``binding=``."""
    out = dict(kw)
    policy = {k: out.pop(k) for k in _POLICY if k in out}
    if policy:
        out["policy"] = EndpointPolicy(**policy)
    if binding:
        bound = {k: out.pop(k) for k in _BINDING if k in out}
        if bound:
            out["binding"] = OAuthBinding(**bound)
    return out

"""Skynet swarm subgraph (Fase S — SPEC-SKY-SG-001).

``plan → dispatch (Send fan-out) → worker ⇉ reduce → evaluate → output`` with
a bounded re-plan loop.  See ``specs/skynet-swarm/`` and
:mod:`prismal.agents.skynet` for the underlying components.
"""

from __future__ import annotations

from prismal.agents.subgraphs.skynet.builder import build_skynet_subgraph, register_skynet

__all__ = ["build_skynet_subgraph", "register_skynet"]

"""Shared helpers for the skynet subgraph nodes.

All durable Skynet runtime state lives under ``state["metadata"]["skynet"]``
(RF-SKY-12) — the same namespacing pattern as Kokoro's ``metadata["kokoro"]``.
The only top-level fields the subgraph touches are ``skynet_orders`` (the
dispatcher's ``tasks_field`` — the dispatcher reads from the state root) and
``parallel_results`` (the Phase 34 ``operator.add`` channel the concurrent
workers append to, tagged with :data:`WORKER_TAG`).
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from prismal.agents.skynet.types import SwarmOrder, WorkerResult

_NAMESPACE = "skynet"

#: Tag identifying this subgraph's entries in the shared ``parallel_results``
#: channel (mirrors the dev_pipeline ``dev_unit_tester`` tag).
WORKER_TAG = "skynet_worker"


def get_skynet(state: dict[str, Any]) -> dict[str, Any]:
    """Return the ``metadata.skynet`` namespace of *state* (empty dict if absent)."""
    metadata = state.get("metadata") or {}
    skynet = metadata.get(_NAMESPACE) or {}
    return dict(skynet)


def merge_skynet(state: dict[str, Any], **updates: Any) -> dict[str, Any]:
    """Return a state update merging *updates* into ``metadata.skynet``."""
    return {
        "metadata": {
            **(state.get("metadata") or {}),
            _NAMESPACE: {**get_skynet(state), **updates},
        }
    }


def orders_to_dicts(orders: list[SwarmOrder]) -> list[dict[str, Any]]:
    """Serialize orders for the Send payload / checkpointed state."""
    return [asdict(order) for order in orders]


def dicts_to_orders(dicts: list[dict[str, Any]]) -> list[SwarmOrder]:
    """Rebuild :class:`SwarmOrder` value objects from serialized state."""
    return [SwarmOrder(**d) for d in dicts]


def dicts_to_results(dicts: list[dict[str, Any]]) -> list[WorkerResult]:
    """Rebuild :class:`WorkerResult` value objects from serialized state."""
    return [WorkerResult(**d) for d in dicts]


__all__ = [
    "WORKER_TAG",
    "dicts_to_orders",
    "dicts_to_results",
    "get_skynet",
    "merge_skynet",
    "orders_to_dicts",
]

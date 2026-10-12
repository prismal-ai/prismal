"""Artifact retention bounds (security review of the shared store).

The shared store lives for the whole process, so expired artifacts must not
accumulate and the in-memory backend must stay bounded.
"""

from __future__ import annotations

import pytest

from prismal.agents.artifacts import InMemoryArtifactStore
from prismal.core.exceptions import ArtifactNotFound


class _Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


def test_put_purges_expired_artifacts_opportunistically() -> None:
    clock = _Clock()
    store = InMemoryArtifactStore(clock=clock)
    store.put("old", run_id="r", tenant="t", kind="k", ttl_s=10)
    clock.now += 3_600  # well past both the TTL and the purge interval
    store.put("new", run_id="r", tenant="t", kind="k", ttl_s=10)
    assert len(store._records) == 1


def test_purge_runs_at_most_once_per_interval() -> None:
    clock = _Clock()
    store = InMemoryArtifactStore(clock=clock)
    store.put("a", run_id="r", tenant="t", kind="k", ttl_s=1)
    clock.now += 2  # expired, but inside the purge interval
    store.put("b", run_id="r", tenant="t", kind="k", ttl_s=1)
    assert len(store._records) == 2


def test_in_memory_store_evicts_the_oldest_beyond_its_cap() -> None:
    store = InMemoryArtifactStore(max_records=3)
    refs = [store.put(f"c{i}", run_id="r", tenant="t", kind="k") for i in range(5)]
    assert len(store._records) == 3
    with pytest.raises(ArtifactNotFound):
        store.get(refs[0].id, run_id="r", tenant="t")
    assert store.get(refs[-1].id, run_id="r", tenant="t").content == "c4"


def test_default_cap_is_bounded() -> None:
    assert 0 < InMemoryArtifactStore().max_records <= 100_000

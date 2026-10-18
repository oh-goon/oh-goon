from pathlib import Path

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.path_authority import RepositoryScopedPath
from oh_goon.integrations import fleet_content_object_lifecycle as lifecycle
from oh_goon.integrations.fleet_content_retention import FleetContentRetentionBudget
from oh_goon.integrations.fleet_content_store import FleetContentStore

OWNER = "repo-aaaaaaaaaaaaaaaaaaaa"


def _owned(path: Path) -> RepositoryScopedPath:
    return RepositoryScopedPath.create(
        owner_repository_id=OWNER,
        root=path.parent,
        path=path,
        label="retention test content",
    )


def _put_file(store: FleetContentStore, root: Path, name: str, content: str):
    source = root / name
    source.write_text(content, encoding="utf-8")
    return store.put_file(_owned(source))


def test_budget_prune_removes_oldest_idle_unreachable_until_both_budgets_hold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ticks = iter((10, 20, 30))
    monkeypatch.setattr(lifecycle.time, "time_ns", lambda: next(ticks))
    store = FleetContentStore(tmp_path / "store")
    first = _put_file(store, tmp_path, "first", "1111")
    second = _put_file(store, tmp_path, "second", "22222")
    third = _put_file(store, tmp_path, "third", "333333")
    for retained in (first, second, third):
        store.release_reference(retained.reference_id, owner_repository_id=OWNER)

    result = store.prune_to_retention_budget(
        FleetContentRetentionBudget(max_bytes=6, max_objects=1, min_idle_ns=5),
        now_ns=100,
    )

    assert result.removed == (first.object_id, second.object_id)
    assert result.before_bytes == 15
    assert result.after_bytes == 6
    assert result.before_objects == 3
    assert result.after_objects == 1
    assert result.budget_satisfied is True
    assert store._object_path(third.object_id).exists()


def test_budget_prune_never_removes_referenced_or_live_leased_objects(tmp_path: Path) -> None:
    store = FleetContentStore(tmp_path / "protected-store")
    referenced = _put_file(store, tmp_path, "referenced", "aaaa")
    leased = _put_file(store, tmp_path, "leased", "bbbb")
    store.release_reference(leased.reference_id, owner_repository_id=OWNER)

    # A live lease requires a reference to acquire; acquire first, then release the durable reference.
    lease_source = _put_file(store, tmp_path, "lease-source", "cccc")
    lease = store.acquire_lease(lease_source.reference_id, owner_repository_id=OWNER)
    store.release_reference(lease_source.reference_id, owner_repository_id=OWNER)
    try:
        result = store.prune_to_retention_budget(
            FleetContentRetentionBudget(max_bytes=0, max_objects=0, min_idle_ns=0),
            now_ns=10**30,
        )
    finally:
        lease.close()

    assert referenced.object_id in result.protected
    assert lease_source.object_id in result.protected
    assert store._object_path(referenced.object_id).exists()
    assert store._object_path(lease_source.object_id).exists()
    assert not store._object_path(leased.object_id).exists()
    assert result.budget_satisfied is False


def test_minimum_idle_age_prevents_ttl_first_eviction(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lifecycle.time, "time_ns", lambda: 100)
    store = FleetContentStore(tmp_path / "idle-store")
    retained = _put_file(store, tmp_path, "recent", "recent")
    store.release_reference(retained.reference_id, owner_repository_id=OWNER)

    result = store.prune_to_retention_budget(
        FleetContentRetentionBudget(max_bytes=0, max_objects=0, min_idle_ns=50),
        now_ns=120,
    )

    assert result.removed == ()
    assert result.idle_protected == (retained.object_id,)
    assert result.budget_satisfied is False
    assert store._object_path(retained.object_id).exists()


def test_budget_prune_refuses_object_without_lifecycle_authority(tmp_path: Path) -> None:
    store = FleetContentStore(tmp_path / "unclassified-store")
    retained = _put_file(store, tmp_path, "unclassified", "unclassified")
    store.release_reference(retained.reference_id, owner_repository_id=OWNER)
    lifecycle.lifecycle_path(store.root, retained.object_id).unlink()

    with pytest.raises(FleetPlanInvalid, match="without lifecycle authority"):
        store.prune_to_retention_budget(
            FleetContentRetentionBudget(max_bytes=0, max_objects=0, min_idle_ns=0),
            now_ns=10**30,
        )


def test_budget_prune_order_is_deterministic_when_lifecycle_times_tie(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(lifecycle.time, "time_ns", lambda: 10)
    store = FleetContentStore(tmp_path / "tie-store")
    first = _put_file(store, tmp_path, "tie-a", "alpha")
    second = _put_file(store, tmp_path, "tie-b", "beta")
    for retained in (first, second):
        store.release_reference(retained.reference_id, owner_repository_id=OWNER)

    result = store.prune_to_retention_budget(
        FleetContentRetentionBudget(max_bytes=10**9, max_objects=1, min_idle_ns=0),
        now_ns=20,
    )

    assert result.removed == (min(first.object_id, second.object_id),)

from pathlib import Path

import pytest

from oh_goon.core.path_authority import RepositoryScopedPath
from oh_goon.integrations import fleet_content_object_lifecycle as lifecycle
from oh_goon.integrations.fleet_content_maintenance import FleetContentMaintenancePolicy
from oh_goon.integrations.fleet_content_retention import (
    FleetContentRemovalLimits,
    FleetContentRetentionBudget,
)
from oh_goon.integrations.fleet_content_store import FleetContentStore

OWNER = "repo-aaaaaaaaaaaaaaaaaaaa"


def _owned(path: Path) -> RepositoryScopedPath:
    return RepositoryScopedPath.create(
        owner_repository_id=OWNER,
        root=path.parent,
        path=path,
        label="maintenance test content",
    )


def _put_file(store: FleetContentStore, root: Path, name: str, content: str):
    source = root / name
    source.write_text(content, encoding="utf-8")
    return store.put_file(_owned(source))


def _policy(*, bytes_: int, objects: int, idle: int, remove_bytes: int, remove_objects: int):
    return FleetContentMaintenancePolicy(
        retention=FleetContentRetentionBudget(
            max_bytes=bytes_, max_objects=objects, min_idle_ns=idle
        ),
        limits=FleetContentRemovalLimits(
            max_removed_bytes=remove_bytes,
            max_removed_objects=remove_objects,
        ),
    )


def test_maintenance_caps_removed_objects_and_reports_unsatisfied_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ticks = iter((10, 20, 30))
    monkeypatch.setattr(lifecycle.time, "time_ns", lambda: next(ticks))
    store = FleetContentStore(tmp_path / "store")
    retained = [_put_file(store, tmp_path, f"item-{index}", str(index) * 4) for index in range(3)]
    for item in retained:
        store.release_reference(item.reference_id, owner_repository_id=OWNER)

    receipt = store.maintain_retained_content(
        _policy(bytes_=0, objects=0, idle=0, remove_bytes=100, remove_objects=1),
        now_ns=100,
    )

    assert receipt.outcome == "MAINTAINED"
    assert receipt.removed_objects == 1
    assert receipt.removed_bytes == 4
    assert receipt.before_objects == 3
    assert receipt.after_objects == 2
    assert receipt.removal_limit_reached is True
    assert receipt.budget_satisfied is False
    assert not store._object_path(retained[0].object_id).exists()
    assert store._object_path(retained[1].object_id).exists()


def test_maintenance_byte_cap_stops_at_oldest_candidate_without_skipping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ticks = iter((10, 20))
    monkeypatch.setattr(lifecycle.time, "time_ns", lambda: next(ticks))
    store = FleetContentStore(tmp_path / "byte-cap")
    oldest = _put_file(store, tmp_path, "oldest", "123456")
    newer = _put_file(store, tmp_path, "newer", "x")
    for item in (oldest, newer):
        store.release_reference(item.reference_id, owner_repository_id=OWNER)

    receipt = store.maintain_retained_content(
        _policy(bytes_=0, objects=0, idle=0, remove_bytes=5, remove_objects=8),
        now_ns=100,
    )

    assert receipt.outcome == "NOOP"
    assert receipt.removed_objects == 0
    assert receipt.removal_limit_reached is True
    assert receipt.budget_satisfied is False
    assert store._object_path(oldest.object_id).exists()
    assert store._object_path(newer.object_id).exists()


def test_maintenance_under_budget_is_noop_and_preserves_reusable_object(tmp_path: Path) -> None:
    store = FleetContentStore(tmp_path / "under-budget")
    retained = _put_file(store, tmp_path, "reusable", "reusable")
    store.release_reference(retained.reference_id, owner_repository_id=OWNER)

    receipt = store.maintain_retained_content(
        _policy(bytes_=1000, objects=10, idle=0, remove_bytes=100, remove_objects=2),
        now_ns=10**30,
    )

    assert receipt.outcome == "NOOP"
    assert receipt.budget_satisfied is True
    assert receipt.removed_bytes == 0
    assert store._object_path(retained.object_id).exists()


def test_maintenance_refuses_object_without_current_lifecycle_authority(
    tmp_path: Path,
) -> None:
    store = FleetContentStore(tmp_path / "missing-lifecycle-store")
    retained = _put_file(store, tmp_path, "orphan-file", "orphan")
    store.release_reference(retained.reference_id, owner_repository_id=OWNER)
    lifecycle.lifecycle_path(store.root, retained.object_id).unlink()

    receipt = store.maintain_retained_content(
        _policy(bytes_=0, objects=0, idle=1, remove_bytes=100, remove_objects=2),
        now_ns=10**30,
    )

    assert receipt.outcome == "REFUSED"
    assert "without lifecycle authority" in (receipt.reason or "")
    assert receipt.removed_objects == 0
    assert store._object_path(retained.object_id).exists()

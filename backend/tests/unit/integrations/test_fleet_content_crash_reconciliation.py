from pathlib import Path

import pytest

from oh_goon.core.path_authority import RepositoryScopedPath
from oh_goon.integrations import fleet_content_object_lifecycle as lifecycle
from oh_goon.integrations.fleet_content_crash_reconciliation import (
    deletion_intent_exists,
    deletion_intent_path,
    publish_deletion_intent_locked,
)
from oh_goon.integrations.fleet_content_store import FleetContentStore
from oh_goon.integrations.fleet_content_store_lifecycle import store_lock

OWNER = "repo-aaaaaaaaaaaaaaaaaaaa"


def _owned(path: Path) -> RepositoryScopedPath:
    return RepositoryScopedPath.create(
        owner_repository_id=OWNER,
        root=path.parent,
        path=path,
        label="crash reconciliation test content",
    )


def _put_file(store: FleetContentStore, root: Path, name: str, content: str):
    source = root / name
    source.write_text(content, encoding="utf-8")
    return store.put_file(_owned(source))


def test_recovery_completes_interrupted_deletion_from_durable_intent(tmp_path: Path) -> None:
    store = FleetContentStore(tmp_path / "store")
    retained = _put_file(store, tmp_path, "payload", "stable")
    store.release_reference(retained.reference_id, owner_repository_id=OWNER)
    with store_lock(store._lock_path):
        publish_deletion_intent_locked(store.root, retained.object_id)

    result = store.reconcile_interrupted_maintenance()

    assert result.completed_deletions == (retained.object_id,)
    assert not store._object_path(retained.object_id).exists()
    assert lifecycle.read_object_lifecycle(store.root, retained.object_id) is None
    assert not deletion_intent_exists(store.root, retained.object_id)


def test_recovery_finishes_metadata_cleanup_when_object_already_absent(tmp_path: Path) -> None:
    store = FleetContentStore(tmp_path / "store-absent")
    retained = _put_file(store, tmp_path, "payload-absent", "stable")
    store.release_reference(retained.reference_id, owner_repository_id=OWNER)
    with store_lock(store._lock_path):
        publish_deletion_intent_locked(store.root, retained.object_id)
    store._object_path(retained.object_id).unlink()

    result = store.reconcile_interrupted_maintenance()

    assert result.completed_deletions == (retained.object_id,)
    assert lifecycle.read_object_lifecycle(store.root, retained.object_id) is None
    assert not deletion_intent_path(store.root, retained.object_id).exists()


def test_recovery_removes_pre_intent_orphan_lifecycle_without_inventing_content(
    tmp_path: Path,
) -> None:
    store = FleetContentStore(tmp_path / "store-orphan")
    retained = _put_file(store, tmp_path, "payload-orphan", "stable")
    store.release_reference(retained.reference_id, owner_repository_id=OWNER)
    store._object_path(retained.object_id).unlink()

    result = store.reconcile_interrupted_maintenance()

    assert result.removed_orphan_lifecycle == (retained.object_id,)
    assert lifecycle.read_object_lifecycle(store.root, retained.object_id) is None


def test_new_reference_is_refused_while_deletion_intent_exists(tmp_path: Path) -> None:
    store = FleetContentStore(tmp_path / "store-reference")
    retained = _put_file(store, tmp_path, "payload-reference", "stable")
    store.release_reference(retained.reference_id, owner_repository_id=OWNER)
    with store_lock(store._lock_path):
        publish_deletion_intent_locked(store.root, retained.object_id)

    source = tmp_path / "same-content"
    source.write_text("stable", encoding="utf-8")
    with pytest.raises(Exception, match="pending deletion"):
        store.put_file(_owned(source))


def test_abandoned_lifecycle_temp_is_removed_only_under_store_lock_recovery(tmp_path: Path) -> None:
    store = FleetContentStore(tmp_path / "store-temp")
    temp_dir = store.root / "object-lifecycle" / "file"
    temp_dir.mkdir(parents=True, exist_ok=True)
    temp = temp_dir / ".dead.json.crash.tmp"
    temp.write_text("partial", encoding="utf-8")

    result = store.reconcile_interrupted_maintenance()

    assert result.removed_temporary_records == ("object-lifecycle/file/.dead.json.crash.tmp",)
    assert not temp.exists()

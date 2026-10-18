from pathlib import Path

import pytest

from oh_goon.core.path_authority import RepositoryScopedPath
from oh_goon.integrations import fleet_content_object_lifecycle as lifecycle
from oh_goon.integrations.fleet_content_store import FleetContentStore

OWNER = "repo-aaaaaaaaaaaaaaaaaaaa"


def _owned(path: Path) -> RepositoryScopedPath:
    root = path if path.is_dir() else path.parent
    return RepositoryScopedPath.create(
        owner_repository_id=OWNER,
        root=root,
        path=path,
        label="test retained lifecycle content",
    )


def _destination(path: Path) -> RepositoryScopedPath:
    return RepositoryScopedPath.create(
        owner_repository_id=OWNER,
        root=path.parent,
        path=path,
        label="test lifecycle materialization",
    )


def test_object_lifecycle_tracks_real_use_and_gc_removes_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ticks = iter((100, 200, 300, 400))
    monkeypatch.setattr(lifecycle.time, "time_ns", lambda: next(ticks))
    source = tmp_path / "source"
    source.write_text("retained", encoding="utf-8")
    store = FleetContentStore(tmp_path / "store")

    retained = store.put_file(_owned(source))
    created = lifecycle.read_object_lifecycle(store.root, retained.object_id)
    assert created is not None
    assert created.state == lifecycle.OBJECT_READY
    assert created.byte_count == retained.identity.byte_count
    assert created.created_at_ns == 100
    assert created.last_used_at_ns == 100

    assert store.get(retained.reference_id, owner_repository_id=OWNER).identity == retained.identity
    used = lifecycle.read_object_lifecycle(store.root, retained.object_id)
    assert used is not None
    assert used.created_at_ns == 100
    assert used.last_used_at_ns == 200

    store.release_reference(retained.reference_id, owner_repository_id=OWNER)
    assert store.garbage_collect() == (retained.object_id,)
    assert lifecycle.read_object_lifecycle(store.root, retained.object_id) is None


def test_materialization_updates_last_used_after_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ticks = iter((10, 20, 30))
    monkeypatch.setattr(lifecycle.time, "time_ns", lambda: next(ticks))
    source = tmp_path / "source-tree"
    source.mkdir()
    (source / "value").write_text("stable", encoding="utf-8")
    store = FleetContentStore(tmp_path / "store-tree")
    retained = store.put_tree(_owned(source))

    store.materialize_tree(retained.reference_id, _destination(tmp_path / "materialized"))
    used = lifecycle.read_object_lifecycle(store.root, retained.object_id)
    assert used is not None
    assert used.created_at_ns == 10
    assert used.last_used_at_ns == 20


def test_lifecycle_metadata_cannot_change_content_authority(tmp_path: Path) -> None:
    source = tmp_path / "authority-source"
    source.write_text("retained", encoding="utf-8")
    store = FleetContentStore(tmp_path / "authority-store")
    retained = store.put_file(_owned(source))
    record_path = lifecycle.lifecycle_path(store.root, retained.object_id)
    payload = record_path.read_text(encoding="utf-8")
    record_path.write_text(payload.replace('"byte_count":8', '"byte_count":999'), encoding="utf-8")

    with pytest.raises(Exception, match="lifecycle metadata"):
        store.get(retained.reference_id, owner_repository_id=OWNER)

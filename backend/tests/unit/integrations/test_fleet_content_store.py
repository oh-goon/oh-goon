import errno
import os
import stat
from pathlib import Path

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.path_authority import RepositoryScopedPath
from oh_goon.core.path_layouts import ContentStorePathLayout
from oh_goon.integrations.exact_file_write import ExactPublicationDurabilityError
from oh_goon.integrations.fleet_content_store import FleetContentStore
from oh_goon.integrations.content_snapshot import snapshot_identity
from oh_goon.integrations.content_snapshot_proof import snapshot_metadata_fingerprint, strong_tree_proof

OWNER = "repo-aaaaaaaaaaaaaaaaaaaa"
OTHER = "repo-bbbbbbbbbbbbbbbbbbbb"


def _owned(path: Path, owner: str) -> RepositoryScopedPath:
    root = path if path.is_dir() else path.parent
    return RepositoryScopedPath.create(
        owner_repository_id=owner, root=root, path=path, label="test retained content"
    )


def _proof(path: Path):
    metadata = snapshot_metadata_fingerprint(path)
    return strong_tree_proof(path, snapshot_identity(path), metadata_before=metadata, metadata_after=metadata)

def _destination(path: Path, owner: str) -> RepositoryScopedPath:
    return RepositoryScopedPath.create(
        owner_repository_id=owner, root=path.parent, path=path, label="test materialization"
    )


def test_content_store_tree_is_immutable_and_materializes_by_exact_id(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "entry.txt").write_text("one", encoding="utf-8")
    store = FleetContentStore(tmp_path / "store")

    object_ = store.put_tree(_owned(source, OWNER))
    destination = tmp_path / "materialized"
    observed = store.materialize_tree(object_.reference_id, _destination(destination, OWNER))

    assert object_.object_id == f"tree-sha256-{object_.identity.sha256}"
    assert object_.reference_id.startswith("ref-")
    assert observed == object_.identity
    assert (destination / "entry.txt").read_text(encoding="utf-8") == "one"


def test_content_store_get_hashes_immutable_object_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source-get-once"
    source.mkdir()
    (source / "entry.txt").write_text("one", encoding="utf-8")
    store = FleetContentStore(tmp_path / "store-get-once")
    retained = store.put_tree(_owned(source, OWNER))

    import oh_goon.integrations.fleet_content_store as module

    real_snapshot_identity = module.snapshot_identity
    observed: list[Path] = []

    def count_identity(path: Path, **kwargs):
        observed.append(Path(path).resolve())
        return real_snapshot_identity(path, **kwargs)

    monkeypatch.setattr(module, "snapshot_identity", count_identity)
    fetched = store.get(retained.reference_id, owner_repository_id=OWNER)

    assert fetched.identity == retained.identity
    assert observed == [retained.path.resolve()]


def test_content_store_materialization_hashes_only_exact_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source-materialize-once"
    source.mkdir()
    (source / "entry.txt").write_text("one", encoding="utf-8")
    store = FleetContentStore(tmp_path / "store-materialize-once")
    retained = store.put_tree(_owned(source, OWNER))
    destination = tmp_path / "materialized-once"

    from oh_goon.integrations import content_snapshot

    real_snapshot_identity = content_snapshot.snapshot_identity
    observed: list[Path] = []

    def count_identity(path: Path, **kwargs):
        observed.append(Path(path).resolve())
        return real_snapshot_identity(path, **kwargs)

    monkeypatch.setattr(content_snapshot, "snapshot_identity", count_identity)
    materialized = store.materialize_tree(
        retained.reference_id, _destination(destination, OWNER)
    )

    assert materialized == retained.identity
    assert observed == [destination.resolve()]


def test_content_store_verified_tree_can_stage_read_only_source_exactly(tmp_path: Path) -> None:
    source = tmp_path / "verified-read-only-source"
    browsers = source / "browsers"
    nested = browsers / "chromium"
    nested.mkdir(parents=True)
    (nested / "browser").write_text("sealed-browser", encoding="utf-8")
    (source / "python-venv").mkdir()
    (source / "python-venv/python").write_text("sealed-python", encoding="utf-8")

    # Match certification supply semantics: directories and files are immutable
    # before the content-store import starts.
    for path in sorted(source.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        path.chmod(stat.S_IMODE(path.stat().st_mode) & ~0o222)
    source.chmod(stat.S_IMODE(source.stat().st_mode) & ~0o222)

    expected = snapshot_identity(source)
    store = FleetContentStore(tmp_path / "verified-read-only-store")
    retained = store.put_verified_tree(_owned(source, OWNER), _proof(source))

    assert retained.identity == expected
    assert snapshot_identity(source) == expected
    assert snapshot_identity(retained.path) == expected
    assert not (source.stat().st_mode & 0o222)


def test_content_store_verified_tree_reuses_existing_object_without_restaging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "verified-reuse-source"
    source.mkdir()
    (source / "entry.txt").write_text("verified", encoding="utf-8")
    store = FleetContentStore(tmp_path / "verified-reuse-store")
    first = store.put_verified_tree(_owned(source, OWNER), _proof(source))
    store.release_reference(first.reference_id, owner_repository_id=OWNER)

    import oh_goon.integrations.fleet_content_store as module

    monkeypatch.setattr(
        module,
        "copy_frozen_directory",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not restage")),
    )
    second = store.put_verified_tree(_owned(source, OWNER), _proof(source))

    assert second.object_id == first.object_id
    assert second.path == first.path
    assert second.reference_id != first.reference_id


def test_content_store_verified_tree_reuse_rejects_source_change_after_identity_binding(
    tmp_path: Path,
) -> None:
    source = tmp_path / "verified-reuse-mutated-source"
    source.mkdir()
    entry = source / "entry.txt"
    entry.write_text("verified", encoding="utf-8")
    store = FleetContentStore(tmp_path / "verified-reuse-mutated-store")
    proof = _proof(source)
    first = store.put_verified_tree(_owned(source, OWNER), proof)
    store.release_reference(first.reference_id, owner_repository_id=OWNER)
    entry.write_text("changed", encoding="utf-8")

    with pytest.raises(FleetPlanInvalid, match="strong proof"):
        store.put_verified_tree(_owned(source, OWNER), proof)


def test_content_store_verified_tree_reuse_rejects_tampered_retained_object(
    tmp_path: Path,
) -> None:
    source = tmp_path / "verified-reuse-object-source"
    source.mkdir()
    (source / "entry.txt").write_text("verified", encoding="utf-8")
    store = FleetContentStore(tmp_path / "verified-reuse-object-store")
    first = store.put_verified_tree(_owned(source, OWNER), _proof(source))
    store.release_reference(first.reference_id, owner_repository_id=OWNER)
    (first.path / "entry.txt").chmod(0o600)
    (first.path / "entry.txt").write_text("tampered", encoding="utf-8")

    with pytest.raises(FleetPlanInvalid, match="changed after Fleet bound its execution identity"):
        store.put_verified_tree(_owned(source, OWNER), _proof(source))


def test_content_store_verified_tree_reuses_source_owner_identity(tmp_path: Path) -> None:
    source = tmp_path / "verified-source"
    source.mkdir()
    (source / "entry.txt").write_text("verified", encoding="utf-8")
    expected = snapshot_identity(source)
    store = FleetContentStore(tmp_path / "verified-store")
    retained = store.put_verified_tree(_owned(source, OWNER), _proof(source))
    assert retained.identity == expected

    other = tmp_path / "verified-other-source"
    other.mkdir()
    (other / "entry.txt").write_text("verified", encoding="utf-8")
    with pytest.raises(FleetPlanInvalid, match="belongs to another source"):
        store.put_verified_tree(_owned(source, OWNER), _proof(other))


def test_content_store_rejects_tampered_object(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "entry.txt").write_text("one", encoding="utf-8")
    store = FleetContentStore(tmp_path / "store")
    object_ = store.put_tree(_owned(source, OWNER))
    (object_.path / "entry.txt").write_text("tampered", encoding="utf-8")

    with pytest.raises(FleetPlanInvalid, match="identity mismatch"):
        store.get(object_.reference_id, owner_repository_id=OWNER)


def test_content_store_gc_requires_reference_release(tmp_path: Path) -> None:
    store = FleetContentStore(tmp_path / "store")
    source = tmp_path / "artifact"
    source.write_text("retained", encoding="utf-8")
    retained = store.put_file(_owned(source, OWNER))

    assert store.garbage_collect() == ()
    store.release_reference(retained.reference_id, owner_repository_id=OWNER)
    assert store.garbage_collect() == (retained.object_id,)
    with pytest.raises(FleetPlanInvalid, match="unavailable"):
        store.get(retained.reference_id, owner_repository_id=OWNER)


def test_content_store_live_read_lease_blocks_gc_after_reference_release(tmp_path: Path) -> None:
    store = FleetContentStore(tmp_path / "store")
    source = tmp_path / "cache"
    source.mkdir()
    (source / "value").write_text("stable", encoding="utf-8")
    retained = store.put_tree(_owned(source, OWNER))

    lease = store.acquire_lease(
        retained.reference_id, owner_repository_id=OWNER, expected_kind="tree"
    )
    store.release_reference(retained.reference_id, owner_repository_id=OWNER)
    assert store.garbage_collect() == ()
    assert retained.path.exists()

    lease.close()
    assert store.garbage_collect() == (retained.object_id,)
    assert not retained.path.exists()


def test_content_store_gc_fails_closed_on_corrupt_reference_metadata(tmp_path: Path) -> None:
    store = FleetContentStore(tmp_path / "store")
    source = tmp_path / "artifact-corrupt"
    source.write_text("retained", encoding="utf-8")
    retained = store.put_file(_owned(source, OWNER))
    reference = ContentStorePathLayout.from_root(store.root).reference(OWNER, retained.reference_id)
    reference.write_text("{broken", encoding="utf-8")

    with pytest.raises(FleetPlanInvalid, match="GC refused"):
        store.garbage_collect()
    assert retained.path.exists()


def test_content_store_failed_reference_publication_cleans_prepared_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = FleetContentStore(tmp_path / "store")
    source = tmp_path / "artifact-reference-failure"
    source.write_text("retained", encoding="utf-8")

    def fail_replace(_source: Path, _target: Path) -> None:
        raise OSError("injected reference publication failure")

    with monkeypatch.context() as patch:
        patch.setattr(
            "oh_goon.integrations.fleet_content_store_lifecycle.replace_prepared_file", fail_replace
        )
        with pytest.raises(OSError, match="injected reference publication failure"):
            store.put_file(_owned(source, OWNER))

    layout = ContentStorePathLayout.from_root(store.root)
    assert tuple(layout.references.rglob("*.tmp")) == ()
    assert tuple(layout.references.rglob("ref-*.json")) == ()
    removed = store.garbage_collect()
    assert len(removed) == 1
    assert not any(layout.objects("file").iterdir())


def test_content_store_gc_does_not_report_failed_tree_deletion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = FleetContentStore(tmp_path / "store-gc-delete-failure")
    source = tmp_path / "gc-delete-source"
    source.mkdir()
    (source / "value").write_text("retained", encoding="utf-8")
    retained = store.put_tree(_owned(source, OWNER))
    store.release_reference(retained.reference_id, owner_repository_id=OWNER)

    def fail_rmtree(path: Path, *args: object, **kwargs: object) -> None:
        if Path(path) == retained.path:
            raise OSError("injected immutable-object deletion failure")
        import shutil

        shutil.rmtree(path, *args, **kwargs)

    monkeypatch.setattr(
        "oh_goon.integrations.fleet_content_store_lifecycle.shutil.rmtree",
        fail_rmtree,
    )
    with pytest.raises(FleetPlanInvalid, match="GC failed to remove immutable object"):
        store.garbage_collect()
    assert retained.path.exists()


def test_content_store_tree_syncs_stage_before_durable_object_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = FleetContentStore(tmp_path / "store-tree-publication")
    source = tmp_path / "tree-publication-source"
    source.mkdir()
    (source / "value").write_text("retained", encoding="utf-8")
    events: list[str] = []

    import oh_goon.integrations.fleet_content_store as module

    real_publish = module.publish_prepared_directory

    monkeypatch.setattr(module, "sync_prepared_path", lambda _stage: events.append("sync"))

    def publish(stage: Path, target: Path) -> None:
        events.append("publish")
        real_publish(stage, target)

    monkeypatch.setattr(module, "publish_prepared_directory", publish)
    retained = store.put_tree(_owned(source, OWNER))

    assert events == ["sync", "publish"]
    assert retained.path.is_dir()


def test_content_store_file_syncs_stage_before_durable_object_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = FleetContentStore(tmp_path / "store-file-publication")
    source = tmp_path / "file-publication-source"
    source.write_text("retained", encoding="utf-8")
    events: list[str] = []

    import oh_goon.integrations.fleet_content_store as module

    real_publish = module.replace_prepared_file

    monkeypatch.setattr(module, "sync_prepared_path", lambda _stage: events.append("sync"))

    def publish(stage: Path, target: Path) -> None:
        events.append("publish")
        real_publish(stage, target)

    monkeypatch.setattr(module, "replace_prepared_file", publish)
    retained = store.put_file(_owned(source, OWNER))

    assert events == ["sync", "publish"]
    assert retained.path.is_file()


def test_content_store_tree_does_not_borrow_post_publish_sync_failure_as_concurrent_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = FleetContentStore(tmp_path / "store-tree-sync-failure")
    source = tmp_path / "tree-sync-failure-source"
    source.mkdir()
    (source / "value").write_text("retained", encoding="utf-8")

    from oh_goon.integrations import exact_file_write

    real_sync = exact_file_write._fsync_directory

    def fail_object_parent(directory: Path) -> None:
        if directory == store.root / "objects" / "tree":
            raise OSError(errno.EIO, "object parent sync failed")
        real_sync(directory)

    monkeypatch.setattr(exact_file_write, "_fsync_directory", fail_object_parent)
    with pytest.raises(ExactPublicationDurabilityError, match="object parent sync failed"):
        store.put_tree(_owned(source, OWNER))

    assert tuple((store.root / "objects" / "tree").iterdir())
    assert not tuple((store.root / "references").rglob("*.json"))


def test_content_store_file_does_not_borrow_post_publish_sync_failure_as_concurrent_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = FleetContentStore(tmp_path / "store-file-sync-failure")
    source = tmp_path / "file-sync-failure-source"
    source.write_text("retained", encoding="utf-8")

    from oh_goon.integrations import exact_file_write

    real_sync = exact_file_write._fsync_directory

    def fail_object_parent(directory: Path) -> None:
        if directory == store.root / "objects" / "file":
            raise OSError(errno.EIO, "object parent sync failed")
        real_sync(directory)

    monkeypatch.setattr(exact_file_write, "_fsync_directory", fail_object_parent)
    with pytest.raises(ExactPublicationDurabilityError, match="object parent sync failed"):
        store.put_file(_owned(source, OWNER))

    assert tuple((store.root / "objects" / "file").iterdir())
    assert not tuple((store.root / "references").rglob("*.json"))


def test_content_store_reference_is_repository_scoped_even_when_bytes_deduplicate(
    tmp_path: Path,
) -> None:
    store = FleetContentStore(tmp_path / "store")
    source = tmp_path / "cache"
    source.mkdir()
    (source / "entry").write_text("same bytes", encoding="utf-8")

    owned = store.put_tree(_owned(source, OWNER))
    with pytest.raises(FleetPlanInvalid, match="content reference is unavailable to repository"):
        store.get(owned.reference_id, owner_repository_id=OTHER)
    with pytest.raises(FleetPlanInvalid, match="content reference is unavailable to repository"):
        store.acquire_lease(owned.reference_id, owner_repository_id=OTHER)
    with pytest.raises(FleetPlanInvalid, match="unavailable to repository"):
        store.release_reference(owned.reference_id, owner_repository_id=OTHER)

    second = store.put_tree(_owned(source, OTHER))
    assert second.object_id == owned.object_id
    assert second.reference_id != owned.reference_id
    assert store.get(second.reference_id, owner_repository_id=OTHER).owner_repository_id == OTHER


def test_content_store_managed_directories_are_owner_only(tmp_path: Path) -> None:
    if os.name == "nt":
        pytest.skip("POSIX permission bits are not authoritative on Windows")
    store = FleetContentStore(tmp_path / "content-store")
    source = tmp_path / "source-private"
    source.mkdir()
    (source / "value").write_text("private", encoding="utf-8")
    retained = store.put_tree(_owned(source, OWNER))
    reference = ContentStorePathLayout.from_root(store.root).reference(OWNER, retained.reference_id)

    for path in (store.root, reference.parent):
        assert stat.S_IMODE(path.stat().st_mode) == 0o700


def test_content_store_rejects_raw_host_paths_at_cross_run_boundary(tmp_path: Path) -> None:
    store = FleetContentStore(tmp_path / "store")
    source = tmp_path / "source"
    source.mkdir()
    (source / "value").write_text("owned", encoding="utf-8")

    with pytest.raises(FleetPlanInvalid, match="RepositoryScopedPath authority"):
        store.put_tree(source)  # type: ignore[arg-type]


def test_retained_tree_cannot_materialize_into_another_repository_scope(
    tmp_path: Path,
) -> None:
    store = FleetContentStore(tmp_path / "store")
    source = tmp_path / "source"
    source.mkdir()
    (source / "secret").write_text("repo-a", encoding="utf-8")
    retained = store.put_tree(_owned(source, OWNER))
    destination = tmp_path / "repo-b-run" / "cache"
    destination.parent.mkdir()

    with pytest.raises(FleetPlanInvalid, match="unavailable to repository"):
        store.materialize_tree(
            retained.reference_id,
            RepositoryScopedPath.create(
                owner_repository_id=OTHER,
                root=destination.parent,
                path=destination,
                label="repo-b cache",
            ),
        )
    assert not destination.exists()


def test_content_store_rejects_multiply_linked_publication_tree(tmp_path: Path) -> None:
    store = FleetContentStore(tmp_path / "store")
    source = tmp_path / "repo-a-cache"
    source.mkdir()
    first = source / "payload.bin"
    first.write_bytes(b"private")
    try:
        (source / "alias.bin").hardlink_to(first)
    except OSError:
        pytest.skip("hard links are unavailable on this filesystem")

    with pytest.raises(FleetPlanInvalid, match="hard-link ownership"):
        store.put_tree(_owned(source, OWNER))


def test_publish_prepared_read_only_directory_temporarily_owns_root_write_permission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from oh_goon.integrations.exact_file_write import publish_prepared_directory

    staging_parent = tmp_path / "staging"
    target_parent = tmp_path / "objects"
    staging = staging_parent / "tree"
    target = target_parent / "tree"
    (staging / "browsers").mkdir(parents=True)
    (staging / "browsers/browser").write_text("sealed", encoding="utf-8")
    target_parent.mkdir()
    staging.chmod(0o555)
    (staging / "browsers").chmod(0o555)
    (staging / "browsers/browser").chmod(0o444)

    real_rename = Path.rename
    observed_modes: list[int] = []

    def guarded_rename(self: Path, destination: Path):
        if self == staging:
            observed_modes.append(stat.S_IMODE(self.stat().st_mode))
            assert observed_modes[-1] & stat.S_IWUSR
        return real_rename(self, destination)

    monkeypatch.setattr(Path, "rename", guarded_rename)
    publish_prepared_directory(staging, target)

    assert observed_modes
    assert stat.S_IMODE(target.stat().st_mode) == 0o555
    assert stat.S_IMODE((target / "browsers").stat().st_mode) == 0o555
    assert stat.S_IMODE((target / "browsers/browser").stat().st_mode) == 0o444


def test_content_store_does_not_reuse_matching_bytes_with_wrong_root_mode(tmp_path: Path) -> None:
    source = tmp_path / "source-root-mode"
    source.mkdir()
    (source / "payload").write_text("same-bytes", encoding="utf-8")
    source.chmod(0o555)
    store = FleetContentStore(tmp_path / "root-mode-store")
    retained = store.put_verified_tree(_owned(source, OWNER), _proof(source))
    retained.path.chmod(0o755)

    with pytest.raises(FleetPlanInvalid, match="root mode differs"):
        store.put_verified_tree(_owned(source, OWNER), _proof(source))


def test_content_store_metadata_preflight_is_cheap_and_owner_kind_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "metadata-source"
    source.mkdir()
    (source / "entry.txt").write_text("one", encoding="utf-8")
    store = FleetContentStore(tmp_path / "metadata-store")
    retained = store.put_tree(_owned(source, OWNER))

    import oh_goon.integrations.fleet_content_store as module

    monkeypatch.setattr(
        module,
        "snapshot_identity",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("metadata preflight must not hash retained object bytes")
        ),
    )
    store.preflight_reference_metadata(
        retained.reference_id,
        owner_repository_id=OWNER,
        expected_kind="tree",
    )

    with pytest.raises(FleetPlanInvalid, match="unavailable"):
        store.preflight_reference_metadata(
            retained.reference_id,
            owner_repository_id=OTHER,
            expected_kind="tree",
        )

    with pytest.raises(FleetPlanInvalid, match="kind tree, expected file"):
        store.preflight_reference_metadata(
            retained.reference_id,
            owner_repository_id=OWNER,
            expected_kind="file",
        )

    for child in retained.path.iterdir():
        child.unlink()
    retained.path.rmdir()
    with pytest.raises(FleetPlanInvalid, match="object for reference is unavailable"):
        store.preflight_reference_metadata(
            retained.reference_id,
            owner_repository_id=OWNER,
            expected_kind="tree",
        )


def test_content_stage_cleanup_protects_live_creator(tmp_path: Path) -> None:
    store = FleetContentStore(tmp_path / "stage-live-store")
    stage = store._stage_path("tree")
    stage.mkdir()
    (stage / "partial").write_text("copying", encoding="utf-8")

    result = store.prune_orphan_staging()

    assert result.removed == ()
    assert stage.name in result.live
    assert stage.exists()
    from oh_goon.integrations.fleet_content_store_lifecycle import remove_owned_stage

    remove_owned_stage(stage)


def test_content_stage_cleanup_removes_only_proven_dead_creator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = FleetContentStore(tmp_path / "stage-dead-store")
    stage = store._stage_path("tree")
    stage.mkdir()
    (stage / "partial").write_text("copying", encoding="utf-8")
    monkeypatch.setattr(
        "oh_goon.integrations.fleet_content_store_lifecycle.process_identity",
        lambda _pid: None,
    )

    result = store.prune_orphan_staging()

    assert stage.name in result.removed
    assert not stage.exists()


def test_content_stage_cleanup_refuses_unowned_stage(tmp_path: Path) -> None:
    store = FleetContentStore(tmp_path / "stage-unowned-store")
    legacy = ContentStorePathLayout.from_root(store.root).staging / "tree-unowned"
    legacy.mkdir()

    result = store.prune_orphan_staging()

    assert result.removed == ()
    assert result.refused == ("tree-unowned",)
    assert legacy.exists()



import json
import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from oh_goon.core.path_authority import RepositoryScopedPath
from oh_goon.integrations import fleet_content_object_lifecycle as lifecycle
from oh_goon.integrations.fleet_content_crash_reconciliation import (
    deletion_intent_exists,
    deletion_intent_path,
    publish_deletion_intent_locked,
)
from oh_goon.integrations.fleet_content_store import FleetContentObject, FleetContentStore
from oh_goon.integrations.fleet_content_store_lifecycle import store_lock

OWNER = "repo-aaaaaaaaaaaaaaaaaaaa"


def _owned(path: Path) -> RepositoryScopedPath:
    return RepositoryScopedPath.create(
        owner_repository_id=OWNER,
        root=path.parent,
        path=path,
        label="crash reconciliation test content",
    )


def _put_file(store: FleetContentStore, root: Path, name: str, content: str) -> FleetContentObject:
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


@pytest.mark.process
def test_native_crash_after_durable_intent_reopens_and_reconciles_idempotently(
    tmp_path: Path,
) -> None:
    store = FleetContentStore(tmp_path / "native-crash-store")
    retained = _put_file(store, tmp_path, "crash-payload", "original retained bytes")
    protected = _put_file(store, tmp_path, "protected-payload", "keep referenced bytes")
    assert store.garbage_collect() == ()
    store.release_reference(retained.reference_id, owner_repository_id=OWNER)
    crash = """
import os
import signal
import sys
from pathlib import Path
from oh_goon.integrations import fleet_content_crash_reconciliation as recovery
from oh_goon.integrations.fleet_content_store import FleetContentStore

publish = recovery.publish_deletion_intent_locked
def crash_after_durable_intent(root, object_id):
    publish(root, object_id)
    os.kill(os.getpid(), signal.SIGKILL)
recovery.publish_deletion_intent_locked = crash_after_durable_intent
FleetContentStore(Path(sys.argv[1])).garbage_collect()
raise SystemExit("the selected crash cut was not reached")
"""
    crashed = subprocess.run(
        [sys.executable, "-I", "-c", crash, str(store.root)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert crashed.returncode == -signal.SIGKILL, crashed.stderr
    assert deletion_intent_exists(store.root, retained.object_id)
    assert retained.path.read_text(encoding="utf-8") == "original retained bytes"
    assert lifecycle.read_object_lifecycle(store.root, retained.object_id) is not None
    reopen = """
import json
import sys
from dataclasses import asdict
from pathlib import Path
from oh_goon.integrations.fleet_content_store import FleetContentStore
print(json.dumps(asdict(FleetContentStore(Path(sys.argv[1])).reconcile_interrupted_maintenance())))
"""
    recovered = subprocess.run(
        [sys.executable, "-I", "-c", reopen, str(store.root)],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert json.loads(recovered.stdout) == {
        "completed_deletions": [retained.object_id],
        "removed_orphan_lifecycle": [],
        "removed_temporary_records": [],
    }
    assert not os.path.lexists(retained.path)
    assert lifecycle.read_object_lifecycle(store.root, retained.object_id) is None
    assert not deletion_intent_exists(store.root, retained.object_id)
    again = FleetContentStore(store.root).reconcile_interrupted_maintenance()
    assert again.completed_deletions == again.removed_orphan_lifecycle == ()
    assert again.removed_temporary_records == ()
    assert store.get(protected.reference_id, owner_repository_id=OWNER) == protected
    assert protected.path.read_text(encoding="utf-8") == "keep referenced bytes"


def _stable_entry_metadata(path: Path) -> tuple[int, ...]:
    observed = path.stat()
    return (
        observed.st_dev,
        observed.st_ino,
        observed.st_mode,
        observed.st_size,
        observed.st_mtime_ns,
    )


def test_gc_unlinks_foreign_directory_symlink_without_entering_its_target(tmp_path: Path) -> None:
    store = FleetContentStore(tmp_path / "symlink-gc-store")
    source = tmp_path / "owned-source"
    source.mkdir()
    (source / "owned-model").write_bytes(b"owned content")
    retained = store.put_tree(_owned(source))
    assert store.garbage_collect() == ()
    store.release_reference(retained.reference_id, owner_repository_id=OWNER)
    foreign = tmp_path / "foreign-directory"
    foreign.mkdir(mode=0o700)
    sentinel = foreign / "model.bin"
    sentinel.write_bytes(b"foreign retained bytes")
    sentinel.chmod(0o640)
    before = (_stable_entry_metadata(foreign), _stable_entry_metadata(sentinel))
    shutil.rmtree(retained.path)
    retained.path.symlink_to(foreign, target_is_directory=True)

    assert store.garbage_collect() == (retained.object_id,)

    assert not os.path.lexists(retained.path)
    assert (_stable_entry_metadata(foreign), _stable_entry_metadata(sentinel)) == before
    assert sentinel.read_bytes() == b"foreign retained bytes"
    assert tuple(foreign.iterdir()) == (sentinel,)


def test_gc_removes_only_its_link_when_foreign_hardlink_retains_the_bytes(tmp_path: Path) -> None:
    store = FleetContentStore(tmp_path / "hardlink-gc-store")
    retained = _put_file(store, tmp_path, "owned-model", "retained model bytes")
    assert store.garbage_collect() == ()
    foreign = tmp_path / "foreign-model-link"
    foreign.hardlink_to(retained.path)
    before = _stable_entry_metadata(foreign)
    assert foreign.stat().st_nlink == 2
    store.release_reference(retained.reference_id, owner_repository_id=OWNER)

    assert store.garbage_collect() == (retained.object_id,)

    assert not os.path.lexists(retained.path)
    assert _stable_entry_metadata(foreign) == before
    assert foreign.stat().st_nlink == 1
    assert foreign.read_text(encoding="utf-8") == "retained model bytes"

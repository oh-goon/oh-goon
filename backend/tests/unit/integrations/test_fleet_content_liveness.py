from __future__ import annotations

import errno
import json
import os
import select
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.path_authority import RepositoryScopedPath
from oh_goon.core.process_birth import observe_process_birth
from oh_goon.integrations.fleet_content_retention import FleetContentRetentionBudget
from oh_goon.integrations.fleet_content_store import FleetContentObject, FleetContentStore
from oh_goon.integrations.fleet_content_store_lifecycle import stage_owner_path

OWNER = "repo-aaaaaaaaaaaaaaaaaaaa"
pytestmark = [
    pytest.mark.process,
    pytest.mark.skipif(sys.platform != "linux", reason="native Linux process identity required"),
]

_CHILD = """
import json
import os
import sys
from pathlib import Path
from oh_goon.integrations.fleet_content_store import FleetContentStore
from oh_goon.integrations.fleet_content_store_lifecycle import remove_owned_stage

store = FleetContentStore(Path(sys.argv[1]))
lease = store.acquire_lease(sys.argv[2], owner_repository_id=sys.argv[3])
stage = store._stage_path("tree")
stage.mkdir()
(stage / "partial").write_text("copy in progress", encoding="utf-8")
print(json.dumps({"pid": os.getpid(), "lease": str(lease.path), "stage": str(stage)}), flush=True)
try:
    sys.stdin.read(1)
finally:
    lease.close()
    remove_owned_stage(stage)
"""


@dataclass(frozen=True)
class _LiveContent:
    process: subprocess.Popen[str]
    store: FleetContentStore
    retained: FleetContentObject
    lease: Path
    stage: Path


@pytest.fixture
def live_content(tmp_path: Path) -> Iterator[_LiveContent]:
    source = tmp_path / "model.bin"
    source.write_bytes(b"retained model")
    store = FleetContentStore(tmp_path / "store")
    retained = store.put_file(
        RepositoryScopedPath.create(
            owner_repository_id=OWNER,
            root=tmp_path,
            path=source,
            label="live reader model",
        )
    )
    process = subprocess.Popen(
        [sys.executable, "-I", "-c", _CHILD, str(store.root), retained.reference_id, OWNER],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout is not None
        ready, _, _ = select.select([process.stdout], [], [], 10)
        assert ready, "native content owner did not become ready"
        payload = json.loads(process.stdout.readline(4096))
        assert payload["pid"] == process.pid
        store.release_reference(retained.reference_id, owner_repository_id=OWNER)
        live = _LiveContent(
            process, store, retained, Path(payload["lease"]), Path(payload["stage"])
        )
        # Establish a functioning live-owner baseline before injecting uncertainty.
        assert store.garbage_collect() == ()
        stages = store.prune_orphan_staging()
        assert live.stage.name in stages.live and not stages.removed
        assert live.lease.is_file() and retained.path.is_file()
        yield live
    finally:
        if process.poll() is None:
            try:
                process.communicate(input="x", timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate(timeout=10)
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                stream.close()


@pytest.mark.parametrize(
    "fault",
    ["permission", "malformed", "missing-view", "missing-view-denied", "missing-view-interrupted"],
)
@pytest.mark.parametrize(
    "operation", ["staging", "orphan-owner", "garbage-collect", "retention-budget"]
)
def test_unobservable_live_creator_never_authorizes_content_cleanup(
    live_content: _LiveContent,
    monkeypatch: pytest.MonkeyPatch,
    fault: str,
    operation: str,
) -> None:
    native_read = Path.read_text
    native_kill = os.kill
    child_stat = Path(f"/proc/{live_content.process.pid}/stat")
    published = live_content.stage.parent.parent / "published-fixture"
    if operation == "orphan-owner":
        live_content.stage.rename(published)

    def uncertain_stat(path: Path, *args: Any, **kwargs: Any) -> str:
        if path == child_stat:
            if fault == "permission":
                raise PermissionError("native child identity is unavailable")
            if fault.startswith("missing-view"):
                raise FileNotFoundError("live child is absent from this proc view")
            return "unparseable process observation\n"
        return native_read(path, *args, **kwargs)

    def uncertain_kernel_probe(pid: int, signal: int) -> None:
        if pid == live_content.process.pid and signal == 0:
            if fault == "missing-view-denied":
                raise PermissionError("native process presence is unavailable")
            if fault == "missing-view-interrupted":
                raise OSError(errno.EINTR, "native process probe interrupted")
        native_kill(pid, signal)

    with monkeypatch.context() as mutation:
        mutation.setattr(Path, "read_text", uncertain_stat)
        mutation.setattr(os, "kill", uncertain_kernel_probe)
        if operation in {"staging", "orphan-owner"}:
            result = live_content.store.prune_orphan_staging()
            assert result.removed == ()
            expected = (
                stage_owner_path(live_content.stage).name
                if operation == "orphan-owner"
                else live_content.stage.name
            )
            assert expected in result.refused
        else:
            with pytest.raises(FleetPlanInvalid):
                if operation == "garbage-collect":
                    live_content.store.garbage_collect()
                else:
                    live_content.store.prune_to_retention_budget(
                        FleetContentRetentionBudget(max_bytes=0, max_objects=0, min_idle_ns=0),
                        now_ns=10**30,
                    )
        assert live_content.process.poll() is None
        assert live_content.lease.is_file()
        assert live_content.retained.path.is_file()
        assert stage_owner_path(live_content.stage).is_file()
        assert (published if operation == "orphan-owner" else live_content.stage).is_dir()


def test_dead_native_creator_releases_lease_and_staging_for_cleanup(
    live_content: _LiveContent,
) -> None:
    live_content.process.kill()
    live_content.process.communicate(timeout=10)

    stages = live_content.store.prune_orphan_staging()
    assert live_content.stage.name in stages.removed
    assert not live_content.stage.exists()
    assert live_content.store.garbage_collect() == (live_content.retained.object_id,)
    assert not live_content.lease.exists()
    assert not live_content.retained.path.exists()


@pytest.mark.parametrize(
    "defect", ["weak-pid", "foreign-weak-pid", "invalid-pid", "empty-identity"]
)
def test_incomplete_owner_metadata_cannot_authorize_cleanup_of_live_content(
    live_content: _LiveContent, defect: str
) -> None:
    for path in (live_content.lease, stage_owner_path(live_content.stage)):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if defect == "invalid-pid":
            payload["pid"] = 0
        elif defect == "empty-identity":
            payload["process_identity"] = ""
        else:
            pid = live_content.process.pid + (1 if defect == "foreign-weak-pid" else 0)
            payload["process_identity"] = f"pid:{pid}"
        path.write_text(json.dumps(payload), encoding="utf-8")

    stages = live_content.store.prune_orphan_staging()
    assert stages.removed == () and live_content.stage.name in stages.refused
    with pytest.raises(FleetPlanInvalid):
        live_content.store.garbage_collect()
    assert live_content.process.poll() is None
    assert live_content.lease.is_file() and live_content.retained.path.is_file()


def test_known_birth_mismatch_keeps_old_owner_collectible(live_content: _LiveContent) -> None:
    birth = observe_process_birth(live_content.process.pid)
    assert birth is not None and birth.start_ticks > 0
    # Model stale metadata against a genuinely observed occupant without claiming
    # that the kernel physically reused this PID during the test.
    old_identity = f"{birth.boot_id}:{birth.start_ticks - 1}"
    for path in (live_content.lease, stage_owner_path(live_content.stage)):
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["process_identity"] = old_identity
        path.write_text(json.dumps(payload), encoding="utf-8")

    stages = live_content.store.prune_orphan_staging()
    assert live_content.stage.name in stages.removed
    assert live_content.store.garbage_collect() == (live_content.retained.object_id,)
    assert not live_content.lease.exists()
    assert not live_content.retained.path.exists()
    assert live_content.process.poll() is None

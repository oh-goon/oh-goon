"""CO-R3M original-owner mkdir/pre-publication crash quarantine.

Real host filesystem/process exits, not cgroup-v2 or independent LACE proof.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.fleet_plan_isolation import CicdResourcePolicy
from oh_goon.integrations import _fleet_plan_cgroup_resources as cg
from tests.factories.fleet_plan_executor import simulated_cgroup_custody  # noqa: F401

pytestmark = pytest.mark.usefixtures("simulated_cgroup_custody")


def _lease(root: Path) -> cg.CgroupV2AttemptLease:
    group = root / "cgroup"
    group.mkdir(exist_ok=True)
    return cg.CgroupV2AttemptLease(
        root=group,
        cgroup=group / ("attempt-" + "a" * 16),
        state_file=root / "state" / "lease.json",
        lock_path=root / "locks" / "owner.lock",
        policy=CicdResourcePolicy(),
        attempt_root=root / "attempt",
    )


def _pending(lease: cg.CgroupV2AttemptLease) -> dict[str, object]:
    return json.loads(lease.state_file.read_text(encoding="utf-8"))


def test_pending_identity_durably_published_before_mkdir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lease = _lease(tmp_path)
    mkdir = Path.mkdir
    seen: list[str] = []

    def interrupt(path: Path, *args: object, **kwargs: object) -> None:
        if path == lease.cgroup:
            pending = _pending(lease)
            assert pending["phase"] == "allocation_pending"
            assert pending["cgroup"] == lease.cgroup.name
            assert "cgroup_dev" not in pending and "cgroup_ino" not in pending
            seen.append("pending-first")
        mkdir(path, *args, **kwargs)
        if path == lease.cgroup:
            raise OSError("injected lost mkdir acknowledgement")

    monkeypatch.setattr(Path, "mkdir", interrupt)
    with pytest.raises(FleetPlanInvalid, match="retaining pending custody"):
        lease.open()
    assert seen == ["pending-first"]
    assert lease.cgroup.is_dir() and _pending(lease)["phase"] == "allocation_pending"


def test_pending_publication_failure_prevents_allocation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lease = _lease(tmp_path)
    monkeypatch.setattr(
        cg,
        "_write_pending_resource_lease",
        lambda _lease: (_ for _ in ()).throw(FleetPlanInvalid("durability unavailable")),
    )
    with pytest.raises(FleetPlanInvalid, match="durability unavailable"):
        lease.open()
    assert not lease.cgroup.exists()


def test_inode_probe_failure_preserves_pending_and_never_kills(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lease = _lease(tmp_path)
    monkeypatch.setattr(
        cg,
        "_exact_cgroup_identity",
        lambda _: (_ for _ in ()).throw(FleetPlanInvalid("identity unavailable")),
    )
    calls: list[str] = []
    monkeypatch.setattr(cg, "_kill_cgroup", lambda *args, **kwargs: calls.append("kill"))
    monkeypatch.setattr(cg, "_remove_cgroup", lambda *args, **kwargs: calls.append("remove"))
    with pytest.raises(FleetPlanInvalid, match="refusing unbound rollback"):
        lease.open()
    assert calls == []
    assert _pending(lease)["phase"] == "allocation_pending"
    assert lease.cgroup.is_dir()


@pytest.mark.parametrize("exists", [True, False])
def test_stale_pending_quarantines_without_guessing_native_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    exists: bool,
) -> None:
    lease = _lease(tmp_path)
    cg._write_pending_resource_lease(lease)
    if exists:
        lease.cgroup.mkdir()
        (lease.cgroup / "foreign").write_text("unchanged", encoding="ascii")
    monkeypatch.setattr(cg, "_process_identity", lambda pid: None)
    calls: list[str] = []
    monkeypatch.setattr(cg, "_kill_cgroup", lambda *a, **kw: calls.append("kill"))
    monkeypatch.setattr(cg, "_remove_cgroup", lambda *a, **kw: calls.append("remove"))
    with pytest.raises(FleetPlanInvalid, match="pending without durable kernel identity"):
        cg._reconcile_stale_cgroups(lease.root, lease.state_file.parent)
    assert calls == [] and lease.state_file.exists()
    if exists:
        assert (lease.cgroup / "foreign").read_text(encoding="ascii") == "unchanged"


def test_unknown_recovery_phase_does_not_enter_native_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lease = _lease(tmp_path)
    cg._write_pending_resource_lease(lease)
    body = _pending(lease)
    body["phase"] = "untrusted"
    lease.state_file.write_text(json.dumps(body), encoding="utf-8")
    monkeypatch.setattr(cg, "_process_identity", lambda pid: None)
    with pytest.raises(FleetPlanInvalid, match="unknown recovery phase"):
        cg._reconcile_stale_cgroups(lease.root, lease.state_file.parent)


def test_live_existing_record_is_never_overwritten_before_mkdir(tmp_path: Path) -> None:
    lease = _lease(tmp_path)
    cg._write_pending_resource_lease(lease)
    original = lease.state_file.read_bytes()
    with pytest.raises(FleetPlanInvalid, match="already exists"):
        lease.open()
    assert not lease.cgroup.exists()
    assert lease.state_file.read_bytes() == original


def test_real_child_exit_between_mkdir_and_identity_publication(tmp_path: Path) -> None:
    lease = _lease(tmp_path)
    # An actual child exits at the precise allocation cut. The production
    # CgroupV2AttemptLease.open path is used on temporary directories after the
    # same simulated custody boundary as the parent, with Path.mkdir fault-hooked.
    program = """
import os
from pathlib import Path
from oh_goon.integrations import _fleet_plan_cgroup_resources as cg
from oh_goon.core.models.fleet_plan_isolation import CicdResourcePolicy
cg.require_independent_native_parent_custody=lambda **_kwargs: None
root=Path(os.environ['TEST_ROOT'])
lease=cg.CgroupV2AttemptLease(root=root/'cgroup',cgroup=root/'cgroup'/('attempt-'+'a'*16),state_file=root/'state'/'lease.json',lock_path=root/'locks'/'owner.lock',policy=CicdResourcePolicy(),attempt_root=root/'attempt')
original=Path.mkdir
def die(path,*args,**kwargs):
    original(path,*args,**kwargs)
    if path==lease.cgroup:
        os._exit(77)
Path.mkdir=die
lease.open()
"""
    env = dict(os.environ, TEST_ROOT=str(tmp_path))
    result = subprocess.run([sys.executable, "-c", program], env=env, check=False, timeout=15)
    assert result.returncode == 77
    assert lease.cgroup.is_dir()
    body = _pending(lease)
    assert body["phase"] == "allocation_pending"
    assert "cgroup_ino" not in body
    with pytest.raises(FleetPlanInvalid, match="pending without durable kernel identity"):
        cg._reconcile_stale_cgroups(lease.root, lease.state_file.parent)
    assert lease.cgroup.is_dir()

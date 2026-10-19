"""CO-R3E: original legacy orphan recovery cannot act on name/PID alone.

Only the pre-existing non-LACE cgroup owner is exercised here. The authenticated
journal-owned LACE route and independent writer-custody gates stay blocked.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.integrations import _fleet_plan_cgroup_resources as cg


def _stale(tmp_path: Path, *, bound: bool = True) -> tuple[Path, Path, Path, Path]:
    root = tmp_path / "root"
    root.mkdir()
    group = root / ("attempt-" + "a" * 16)
    group.mkdir()
    (group / "cgroup.events").write_text("populated 0\n", encoding="ascii")
    (group / "cgroup.procs").write_text("", encoding="ascii")
    state = tmp_path / "state"
    state.mkdir()
    lease = state / "orphan.json"
    payload = {"pid": 999999, "process_identity": "previous-boot:123", "cgroup": group.name}
    if bound:
        dev, ino = cg._exact_cgroup_identity(group)
        payload.update(cgroup_dev=dev, cgroup_ino=ino)
    lease.write_text(json.dumps(payload), encoding="utf-8")
    return root, group, state, lease


def test_original_creator_persists_device_inode_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, group, _state, lease = _stale(tmp_path)
    owner = cg.CgroupV2AttemptLease(
        root=root,
        cgroup=group,
        state_file=lease,
        lock_path=tmp_path / "lock",
        policy=object(),
        attempt_root=tmp_path / "attempt",
        cgroup_identity=cg._exact_cgroup_identity(group),
    )
    monkeypatch.setattr(cg, "_process_identity", lambda pid: "current-boot:1")
    cg._write_resource_lease(owner)
    payload = json.loads(lease.read_text(encoding="utf-8"))
    assert (payload["cgroup_dev"], payload["cgroup_ino"]) == cg._exact_cgroup_identity(group)
    owner.cgroup_identity = None
    before = lease.read_bytes()
    with pytest.raises(FleetPlanInvalid, match="unpinned"):
        cg._write_resource_lease(owner)
    assert lease.read_bytes() == before


def test_old_or_unpinned_orphan_fails_before_native_effect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, group, state, lease = _stale(tmp_path, bound=False)
    kill = Mock()
    remove = Mock()
    monkeypatch.setattr(cg, "_process_identity", lambda pid: None)
    monkeypatch.setattr(cg, "_kill_cgroup", kill)
    monkeypatch.setattr(cg, "_remove_cgroup", remove)
    with pytest.raises(FleetPlanInvalid, match="lacks durable original kernel identity"):
        cg._reconcile_stale_cgroups(root, state)
    kill.assert_not_called()
    remove.assert_not_called()
    assert group.is_dir() and lease.is_file()


@pytest.mark.parametrize(
    "attack",
    [
        {"cgroup_dev": False},
        {"cgroup_ino": "3"},
        {"cgroup_dev": -1},
        {"cgroup_ino": None},
        {"cgroup_dev": 0},
    ],
)
def test_invalid_kernel_identity_must_not_mutate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    attack: dict[str, object],
) -> None:
    root, group, state, lease = _stale(tmp_path)
    payload = json.loads(lease.read_text(encoding="utf-8")) | attack
    lease.write_text(json.dumps(payload), encoding="utf-8")
    kill = Mock()
    monkeypatch.setattr(cg, "_process_identity", lambda pid: None)
    monkeypatch.setattr(cg, "_kill_cgroup", kill)
    with pytest.raises(FleetPlanInvalid, match="lacks durable"):
        cg._reconcile_stale_cgroups(root, state)
    kill.assert_not_called()
    assert group.is_dir() and lease.is_file()


def test_foreign_replacement_cannot_be_killed_or_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, group, state, lease = _stale(tmp_path)
    former = tmp_path / "former"
    group.rename(former)
    group.mkdir()
    (group / "cgroup.events").write_text("populated 0\n", encoding="ascii")
    (group / "cgroup.procs").write_text("", encoding="ascii")
    kill = Mock()
    remove = Mock()
    monkeypatch.setattr(cg, "_process_identity", lambda pid: None)
    monkeypatch.setattr(cg, "_kill_cgroup", kill)
    monkeypatch.setattr(cg, "_remove_cgroup", remove)
    with pytest.raises(FleetPlanInvalid, match="replaced"):
        cg._reconcile_stale_cgroups(root, state)
    kill.assert_not_called()
    remove.assert_not_called()
    assert group.is_dir() and former.is_dir() and lease.is_file()


def test_incomplete_original_kernel_observation_retains_orphan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, group, state, lease = _stale(tmp_path)
    (group / "cgroup.events").unlink()
    monkeypatch.setattr(cg, "_process_identity", lambda pid: None)
    monkeypatch.setattr(cg, "_kill_cgroup", lambda _, **_kw: None)
    remove = Mock()
    monkeypatch.setattr(cg, "_remove_cgroup", remove)
    with pytest.raises(FleetPlanInvalid, match="physical custody"):
        cg._reconcile_stale_cgroups(root, state)
    remove.assert_not_called()
    assert lease.is_file()


def test_original_identity_may_retire_only_bookkeeping_after_lost_ack(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, group, state, lease = _stale(tmp_path)
    group.joinpath("cgroup.events").unlink()
    group.joinpath("cgroup.procs").unlink()
    group.rmdir()
    monkeypatch.setattr(cg, "_process_identity", lambda pid: None)
    kill = Mock()
    monkeypatch.setattr(cg, "_kill_cgroup", kill)
    with pytest.raises(FleetPlanInvalid, match="physical custody"):
        cg._reconcile_stale_cgroups(root, state)
    kill.assert_not_called()
    assert lease.exists()  # pathname absence does not prove former inode retirement


def test_missing_original_with_foreign_symlink_is_not_acknowledged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, group, state, lease = _stale(tmp_path)
    former = tmp_path / "former"
    group.rename(former)
    group.symlink_to(former, target_is_directory=True)
    monkeypatch.setattr(cg, "_process_identity", lambda pid: None)
    with pytest.raises(FleetPlanInvalid, match="symlink"):
        cg._reconcile_stale_cgroups(root, state)
    assert lease.exists() and former.is_dir()


def test_original_lace_journal_ownership_still_refuses_pid_scavenging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, group, state, lease = _stale(tmp_path)
    payload = json.loads(lease.read_text(encoding="utf-8"))
    payload["lace_intent"] = {"original": True}
    lease.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(cg, "_process_identity", lambda pid: None)
    kill = Mock()
    monkeypatch.setattr(cg, "_kill_cgroup", kill)
    with pytest.raises(FleetPlanInvalid, match="original journal recovery"):
        cg._reconcile_stale_cgroups(root, state)
    kill.assert_not_called()
    assert group.is_dir() and lease.is_file()

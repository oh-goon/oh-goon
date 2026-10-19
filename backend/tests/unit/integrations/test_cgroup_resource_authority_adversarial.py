from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.fleet_plan_isolation import CicdResourcePolicy
from oh_goon.integrations import _fleet_plan_cgroup_resources as cg


def _root(
    tmp_path: Path, *, available: str = "cpu memory pids", enabled: str = "cpu memory pids"
) -> Path:
    root = tmp_path / "cg"
    root.mkdir()
    for name in ("cgroup.procs", "cpu.max", "memory.max", "pids.max"):
        (root / name).write_text("", encoding="ascii")
    (root / "cgroup.controllers").write_text(available, encoding="ascii")
    (root / "cgroup.subtree_control").write_text(enabled, encoding="ascii")
    return root


def _patch_physical(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cg, "_is_cgroup2_path", lambda path: True)
    monkeypatch.setattr(cg.os, "access", lambda path, mode: True)


def test_delegated_root_validation_fails_closed_for_platform_files_controllers_and_write(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_physical(monkeypatch)
    root = _root(tmp_path)
    assert (
        cg.require_delegated_cgroup_v2_root(root, required_controllers={"cpu", "memory"})
        == root.resolve()
    )
    monkeypatch.setattr(cg, "_is_cgroup2_path", lambda path: False)
    with pytest.raises(FleetPlanInvalid, match="not backed"):
        cg.require_delegated_cgroup_v2_root(root, required_controllers={"cpu"})
    monkeypatch.setattr(cg, "_is_cgroup2_path", lambda path: True)
    missing = tmp_path / "missing"
    with pytest.raises(FleetPlanInvalid, match="does not exist"):
        cg.require_delegated_cgroup_v2_root(missing, required_controllers={"cpu"})
    (root / "cpu.max").unlink()
    with pytest.raises(FleetPlanInvalid, match="lacks required"):
        cg.require_delegated_cgroup_v2_root(root, required_controllers={"cpu"})
    (root / "cpu.max").write_text("", encoding="ascii")
    (root / "cgroup.controllers").write_text("memory", encoding="ascii")
    with pytest.raises(FleetPlanInvalid, match="unavailable"):
        cg.require_delegated_cgroup_v2_root(root, required_controllers={"cpu"})
    (root / "cgroup.controllers").write_text("cpu memory", encoding="ascii")
    (root / "cgroup.subtree_control").write_text("memory", encoding="ascii")
    with pytest.raises(FleetPlanInvalid, match="not-enabled"):
        cg.require_delegated_cgroup_v2_root(root, required_controllers={"cpu"})
    monkeypatch.setattr(cg.os, "access", lambda path, mode: False)
    with pytest.raises(FleetPlanInvalid, match="not writable"):
        cg._inspect_delegated_cgroup_v2_root(root, required_controllers={"cpu"})


def test_controller_enable_and_process_subgroup_validate_names_and_cleanup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_physical(monkeypatch)
    root = _root(tmp_path, enabled="memory")
    monkeypatch.setattr(
        cg,
        "require_delegated_cgroup_v2_root",
        lambda path, required_controllers: Path(path).resolve(),
    )
    with pytest.raises(FleetPlanInvalid, match="native delegated control write"):
        cg.enable_delegated_cgroup_v2_controllers(root, required_controllers={"cpu", "memory"})
    assert "+cpu" not in (root / "cgroup.subtree_control").read_text()
    for name in ("", ".", "..", "a/b", "cgroup.bad", "a\x00b"):
        with pytest.raises(FleetPlanInvalid, match="Invalid delegated"):
            cg.place_current_process_in_delegated_cgroup_v2_subgroup(
                root, subgroup=name, required_controllers=set()
            )

    def fail_place(cgroup: Path, pid: int, *, expected_identity: tuple[int, int]) -> None:
        raise FleetPlanInvalid("Fleet cannot place process in delegated subgroup")

    monkeypatch.setattr(cg, "_write_process_to_cgroup", fail_place)
    with pytest.raises(FleetPlanInvalid, match="native delegated child creation"):
        cg.place_current_process_in_delegated_cgroup_v2_subgroup(
            root, subgroup="runner", required_controllers=set()
        )
    assert not (root / "runner").exists()


def test_resource_evidence_parsers_limits_identity_and_configuration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Historical post-custody fixture simulation; NOT a native authority grant.
    monkeypatch.setattr(cg, "require_independent_native_parent_custody", lambda **_kw: None)
    root = tmp_path / "cg"
    root.mkdir()
    (root / "cpu.stat").write_text("usage_usec 123\nother 1\n", encoding="ascii")
    (root / "memory.peak").write_text("456\n", encoding="ascii")
    (root / "pids.peak").write_text("bad\n", encoding="ascii")
    (root / "memory.events").write_text("oom_kill 2\n", encoding="ascii")
    policy = CicdResourcePolicy(cpu_millis=1000, memory_mib=64, max_processes=16)
    ev = cg._read_resource_evidence(root, policy)
    assert (
        ev.cpu_usage_usec == 123
        and ev.memory_peak_bytes == 456
        and ev.pids_peak is None
        and ev.oom_kill_count == 2
    )
    assert cg._plain_int(root / "missing") is None
    assert cg._keyed_int(root / "missing", "x") is None
    assert cg._keyed_int(root / "cpu.stat", "missing") is None
    cg._write_limit(root / "limit", "9")
    assert (root / "limit").read_text() == "9"
    backend = SimpleNamespace(cgroup_root=None)
    assert cg.cgroup_configuration_identity(backend)["cgroup_root"] is None
    backend.cgroup_root = root
    (root / "cgroup.controllers").write_text("cpu memory", encoding="utf-8")
    (root / "cgroup.subtree_control").write_text("cpu", encoding="utf-8")
    config = cg.cgroup_configuration_identity(backend)
    assert config["controllers"] == ["cpu", "memory"]


def test_mountinfo_decoder_and_cgroup_mount_selection(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    assert cg._decode_mountinfo_path(r"/a\040b\011c\012d\134e") == "/a b\tc\nd\\e"
    path = tmp_path / "child"
    path.mkdir()
    mountinfo = (
        f"1 2 0:1 / {tmp_path} rw - cgroup2 cgroup rw\n3 2 0:2 / {path} rw - tmpfs tmpfs rw\n"
    )
    monkeypatch.setattr(
        Path,
        "read_text",
        lambda self, **kwargs: mountinfo
        if str(self) == "/proc/self/mountinfo"
        else Path.__dict__["read_text"](self, **kwargs),
    )
    assert cg._is_cgroup2_path(tmp_path)
    assert not cg._is_cgroup2_path(path)


def test_stale_reconciliation_refuses_corrupt_metadata_and_reaps_dead_owner(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "root"
    state = tmp_path / "state"
    root.mkdir()
    state.mkdir()
    bad = state / "bad.json"
    bad.write_text("not-json", encoding="utf-8")
    with pytest.raises(FleetPlanInvalid, match="unreadable"):
        cg._reconcile_stale_cgroups(root, state)
    bad.write_text(
        json.dumps({"pid": 1, "process_identity": "x", "cgroup": "wrong"}), encoding="utf-8"
    )
    with pytest.raises(FleetPlanInvalid, match="invalid cgroup name"):
        cg._reconcile_stale_cgroups(root, state)
    bad.unlink()
    name = "attempt-" + "a" * 16
    cgroup = root / name
    cgroup.mkdir()
    (cgroup / "cgroup.events").write_text("populated 0\n", encoding="ascii")
    (cgroup / "cgroup.procs").write_text("", encoding="ascii")
    dev, ino = cg._exact_cgroup_identity(cgroup)
    row = state / "a.json"
    row.write_text(
        json.dumps(
            {
                "pid": 123,
                "process_identity": "old",
                "cgroup": name,
                "cgroup_dev": dev,
                "cgroup_ino": ino,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(cg, "_process_identity", lambda pid: "new")
    monkeypatch.setattr(cg, "_kill_cgroup", lambda path, **_kw: None)

    def retire_fake_cgroup(path: Path) -> None:
        (path / "cgroup.events").unlink()
        (path / "cgroup.procs").unlink()
        path.rmdir()

    monkeypatch.setattr(cg, "_remove_cgroup", lambda path, **_kw: retire_fake_cgroup(path))
    monkeypatch.setattr(cg, "remove_file_durably", lambda path: Path(path).unlink(missing_ok=True))
    with pytest.raises(FleetPlanInvalid, match="physical custody"):
        cg._reconcile_stale_cgroups(root, state)
    assert cgroup.exists() and row.exists()


def test_kill_wait_remove_and_process_identity_edge_cases(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Historical post-custody fixture simulation; NOT a native authority grant.
    monkeypatch.setattr(cg, "require_independent_native_parent_custody", lambda **_kw: None)
    cgroup = tmp_path / "cg"
    cgroup.mkdir()
    cg._kill_cgroup(tmp_path / "missing")
    (cgroup / "cgroup.kill").write_text("", encoding="ascii")
    cg._kill_cgroup(cgroup)
    assert (cgroup / "cgroup.kill").read_text() == "1"
    (cgroup / "cgroup.events").write_text("populated 0\n", encoding="ascii")
    (cgroup / "cgroup.procs").write_text("", encoding="ascii")
    cg._wait_for_cgroup_empty(cgroup, timeout_seconds=0.01)
    (cgroup / "cgroup.events").unlink()
    with pytest.raises(FleetPlanInvalid, match="observations unavailable"):
        cg._wait_for_cgroup_empty(cgroup, timeout_seconds=0.01)
    (cgroup / "cgroup.kill").unlink()
    (cgroup / "cgroup.procs").unlink()
    cg._remove_cgroup(cgroup)
    assert not cgroup.exists()
    assert cg._process_identity(999999999) is None
    missing = tmp_path / "none"
    cg._remove_cgroup(missing)


def test_require_host_and_attempt_lease_command_prefix(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    policy = CicdResourcePolicy(cpu_millis=1000, memory_mib=64, max_processes=None)
    backend = SimpleNamespace(
        cgroup_root=None, cgroup_lock=tmp_path / "lock", cgroup_state_dir=tmp_path / "state"
    )
    with pytest.raises(FleetPlanInvalid, match="cgroup_root"):
        cg.require_cgroup_v2_host(backend, policy)
    root = _root(tmp_path)
    backend.cgroup_root = root
    monkeypatch.setattr(cg, "require_cgroup_v2_host", lambda backend, policy: None)
    with pytest.raises(FleetPlanInvalid, match="native cgroup admission"):
        cg.CgroupV2AttemptLease.create(backend, tmp_path / "attempt", policy)
    # Admission always refuses; the legacy prefix-construction assertions below
    # model an already provisioned host without granting production authority.
    monkeypatch.setattr(cg, "require_independent_native_parent_custody", lambda **_kw: None)
    # Pure unit test of command argument construction, NOT an admitted lease.
    lease = cg.CgroupV2AttemptLease(
        root=root,
        cgroup=root / "attempt-fixture",
        state_file=tmp_path / "lease.json",
        lock_path=tmp_path / "lock",
        policy=policy,
        attempt_root=tmp_path / "attempt",
    )
    with pytest.raises(FleetPlanInvalid, match="lacks original cgroup identity"):
        lease.command_prefix()
    lease.cgroup.mkdir()
    lease.cgroup_identity = cg._exact_cgroup_identity(lease.cgroup)
    prefix = lease.command_prefix()
    assert prefix[0] and prefix[-3:] == (
        str(lease.cgroup),
        str(lease.cgroup_identity[0]),
        str(lease.cgroup_identity[1]),
    )

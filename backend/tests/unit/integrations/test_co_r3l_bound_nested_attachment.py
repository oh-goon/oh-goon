"""CO-R3L native-fd tests; no claim of physical LACE or exclusive cgroup parent custody."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.fleet_plan_isolation import CicdResourcePolicy
from oh_goon.integrations import _fleet_plan_cgroup_resources as cg
from oh_goon.integrations import _fleet_plan_nested_cgroup_launcher as launcher


def _control(directory: Path, name: str) -> Path:
    directory.mkdir(exist_ok=True)
    path = directory / name
    path.write_text("", encoding="ascii")
    return path


def test_subtree_write_is_bound_to_creation_identity_and_rejects_substitution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Historical post-custody syscall simulation; NOT a native LACE permit.
    monkeypatch.setattr(cg, "require_independent_native_parent_custody", lambda **_kw: None)
    root = tmp_path / "original"
    control = _control(root, "cgroup.subtree_control")
    identity = cg._exact_cgroup_identity(root)
    moved = tmp_path / "moved"
    native_open = os.open

    def substitute(name, flags, *args, **kwargs):
        if name == "cgroup.subtree_control" and not moved.exists():
            root.rename(moved)
            _control(root, "cgroup.subtree_control").write_text("foreign", encoding="ascii")
        return native_open(name, flags, *args, **kwargs)

    monkeypatch.setattr(cg.os, "open", substitute)
    with pytest.raises(FleetPlanInvalid, match="replaced"):
        cg._write_subtree_controllers(root, {"cpu"}, expected_identity=identity)
    assert (moved / control.name).read_text() == ""
    assert (root / control.name).read_text() == "foreign"


def test_control_writes_refuse_symlink_and_short_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Historical post-custody syscall simulation; NOT a native LACE permit.
    monkeypatch.setattr(cg, "require_independent_native_parent_custody", lambda **_kw: None)
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.write_text("foreign", encoding="ascii")
    (root / "cgroup.procs").symlink_to(outside)
    identity = cg._exact_cgroup_identity(root)
    with pytest.raises(FleetPlanInvalid, match="unavailable"):
        cg._write_process_to_cgroup(root, 200, expected_identity=identity)
    assert outside.read_text() == "foreign"
    (root / "cgroup.procs").unlink()
    _control(root, "cgroup.procs")
    monkeypatch.setattr(cg.os, "write", lambda fd, data: 0)
    with pytest.raises(FleetPlanInvalid, match="incomplete"):
        cg._write_process_to_cgroup(root, 200, expected_identity=identity)
    assert (root / "cgroup.procs").read_text() == ""


def test_child_creation_rejects_parent_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Historical post-custody syscall simulation; NOT a native LACE permit.
    monkeypatch.setattr(cg, "require_independent_native_parent_custody", lambda **_kw: None)
    parent = tmp_path / "parent"
    parent.mkdir()
    identity = cg._exact_cgroup_identity(parent)
    renamed = tmp_path / "renamed"
    native_mkdir = os.mkdir

    def substitute(name, *args, **kwargs):
        if name == "nested-execution" and not renamed.exists():
            parent.rename(renamed)
            parent.mkdir()
        return native_mkdir(name, *args, **kwargs)

    monkeypatch.setattr(cg.os, "mkdir", substitute)
    with pytest.raises(FleetPlanInvalid, match="replaced"):
        cg._mkdir_bound_cgroup_child(parent, identity, "nested-execution")
    assert (renamed / "nested-execution").is_dir()
    assert not (parent / "nested-execution").exists()


def test_nested_launcher_cli_rejects_unbound_or_invalid_identity() -> None:
    for args in (
        ["a", "b", "--", "bwrap"],
        ["a", "b", "0", "1", "2", "3", "--", "bwrap"],
        ["a", "b", "foo", "1", "2", "3", "--", "bwrap"],
    ):
        with pytest.raises(FleetPlanInvalid, match="authority|unproven|invalid"):
            launcher.main(args)


def test_direct_process_bootstrap_refuses_replaced_cgroup_and_does_not_exec(
    tmp_path: Path,
) -> None:
    group = tmp_path / "group"
    _control(group, "cgroup.procs")
    dev, ino = cg._exact_cgroup_identity(group)
    replaced = tmp_path / "replaced"
    group.rename(replaced)
    group.mkdir()
    marker = tmp_path / "executed"
    proc = subprocess.run(
        [
            sys.executable,
            "-S",
            "-c",
            cg._CGROUP_EXEC,
            str(group),
            str(dev),
            str(ino),
            sys.executable,
            "-S",
            "-c",
            f'open({str(marker)!r},"w").write("unsafe")',
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode != 0
    assert not marker.exists()
    assert (replaced / "cgroup.procs").read_text() == ""


def test_direct_process_bootstrap_rejects_symlink_control_and_does_not_exec(
    tmp_path: Path,
) -> None:
    group = tmp_path / "group"
    group.mkdir()
    marker = tmp_path / "executed"
    foreign = tmp_path / "foreign"
    foreign.write_text("foreign", encoding="ascii")
    (group / "cgroup.procs").symlink_to(foreign)
    dev, ino = cg._exact_cgroup_identity(group)
    proc = subprocess.run(
        [
            sys.executable,
            "-S",
            "-c",
            cg._CGROUP_EXEC,
            str(group),
            str(dev),
            str(ino),
            sys.executable,
            "-S",
            "-c",
            f'open({str(marker)!r},"w").write("unsafe")',
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode != 0
    assert not marker.exists() and foreign.read_text() == "foreign"


def test_normal_direct_launcher_cannot_be_constructed_before_allocation(tmp_path: Path) -> None:
    lease = cg.CgroupV2AttemptLease(
        root=tmp_path,
        cgroup=tmp_path / "group",
        state_file=tmp_path / "lease.json",
        lock_path=tmp_path / "lock",
        policy=CicdResourcePolicy(),
        attempt_root=tmp_path,
    )
    with pytest.raises(FleetPlanInvalid, match="native cgroup process launch"):
        lease.command_prefix()


def test_nested_launcher_cannot_be_constructed_without_both_child_identities(
    tmp_path: Path,
) -> None:
    lease = cg.CgroupV2AttemptLease(
        root=tmp_path,
        cgroup=tmp_path / "group",
        state_file=tmp_path / "lease.json",
        lock_path=tmp_path / "lock",
        policy=CicdResourcePolicy(),
        attempt_root=tmp_path,
        nested_delegation=tmp_path / "group/nested-execution",
        nested_monitor=tmp_path / "group/nested-monitor",
    )
    with pytest.raises(FleetPlanInvalid, match="native cgroup process launch"):
        lease.command_prefix()


def test_subgroup_attachment_replacement_keeps_foreign_and_refuses_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "delegated"
    root.mkdir()
    for name in ("cgroup.procs", "cgroup.controllers", "cgroup.subtree_control"):
        _control(root, name)
    # HISTORICAL POST-CUSTODY SIMULATION ONLY. The malicious inode replacement
    # is exercised behind a fake provisioned host, not native authorization.
    monkeypatch.setattr(cg, "require_independent_native_parent_custody", lambda **_kwargs: None)
    monkeypatch.setattr(cg, "_is_cgroup2_path", lambda _: True)
    monkeypatch.setattr(cg.os, "access", lambda *_args: True)
    original = tmp_path / "original"

    def substitute(child: Path, pid: int, *, expected_identity: tuple[int, int]) -> None:
        assert expected_identity == cg._exact_cgroup_identity(child)
        child.rename(original)
        child.mkdir()
        (child / "foreign").write_text("preserved", encoding="ascii")
        raise FleetPlanInvalid("injected interrupted membership effect")

    monkeypatch.setattr(cg, "_write_process_to_cgroup", substitute)
    with pytest.raises(FleetPlanInvalid, match="rollback unproven"):
        cg.place_current_process_in_delegated_cgroup_v2_subgroup(
            root,
            subgroup="runner",
            required_controllers=set(),
        )
    assert original.is_dir()
    assert (root / "runner" / "foreign").read_text() == "preserved"

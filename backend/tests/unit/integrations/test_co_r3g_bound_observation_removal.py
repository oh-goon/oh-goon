"""CO-R3G: original-owner native cgroup observation and removal fences.

A local cgroup empty snapshot never proves LACE-wide writer absence. Linux
rmdir(name,dir_fd) is not an atomic identity-conditional deletion API.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.integrations import _fleet_plan_cgroup_resources as cg
from tests.factories.fleet_plan_executor import simulated_cgroup_custody  # noqa: F401

pytestmark = pytest.mark.usefixtures("simulated_cgroup_custody")


def _group(tmp_path: Path) -> Path:
    group = tmp_path / ("attempt-" + "a" * 16)
    group.mkdir()
    (group / "cgroup.events").write_text("populated 0\n", encoding="ascii")
    (group / "cgroup.procs").write_text("", encoding="ascii")
    return group


def test_bound_observation_reports_quiescence_for_original_kernel_controls(tmp_path: Path) -> None:
    group = _group(tmp_path)
    cg._wait_for_cgroup_empty(group, expected_identity=cg._exact_cgroup_identity(group))


def test_symlink_or_missing_observation_control_refuses_quiescence(tmp_path: Path) -> None:
    group = _group(tmp_path)
    identity = cg._exact_cgroup_identity(group)
    target = tmp_path / "external"
    target.write_text("populated 0\n", encoding="ascii")
    (group / "cgroup.events").unlink()
    (group / "cgroup.events").symlink_to(target)
    with pytest.raises(FleetPlanInvalid, match="observations unavailable"):
        cg._wait_for_cgroup_empty(group, expected_identity=identity)
    (group / "cgroup.events").unlink()
    with pytest.raises(FleetPlanInvalid, match="observations unavailable"):
        cg._wait_for_cgroup_empty(group, expected_identity=identity)


def test_replacement_during_observation_cannot_yield_false_absence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    group = _group(tmp_path)
    identity = cg._exact_cgroup_identity(group)
    moved = tmp_path / "original"
    original_open = os.open
    fired = False

    def redirected(path: str | os.PathLike[str], flags: int, *args: object, **kw: object) -> int:
        nonlocal fired
        if path == "cgroup.events" and "dir_fd" in kw and not fired:
            fired = True
            group.rename(moved)
            group.mkdir()
            (group / "cgroup.events").write_text("populated 0\n")
            (group / "cgroup.procs").write_text("")
        return original_open(path, flags, *args, **kw)

    monkeypatch.setattr(cg.os, "open", redirected)
    with pytest.raises(FleetPlanInvalid, match="replaced"):
        cg._wait_for_cgroup_empty(group, expected_identity=identity)
    assert fired
    assert (moved / "cgroup.events").read_text() == "populated 0\n"
    assert (group / "cgroup.events").read_text() == "populated 0\n"


def test_bounded_control_and_malformed_observation_fail_closed(tmp_path: Path) -> None:
    group = _group(tmp_path)
    identity = cg._exact_cgroup_identity(group)
    (group / "cgroup.events").write_bytes(b"a" * 4097)
    with pytest.raises(FleetPlanInvalid, match="exceeds bound"):
        cg._wait_for_cgroup_empty(group, expected_identity=identity)
    (group / "cgroup.events").write_bytes(b"populated 0\xff\n")
    with pytest.raises(FleetPlanInvalid, match="observations unavailable"):
        cg._wait_for_cgroup_empty(group, expected_identity=identity)


def test_removal_uses_original_parent_handle_and_bound_identity(tmp_path: Path) -> None:
    group = tmp_path / "attempt-aaaaaaaaaaaaaaaa"
    group.mkdir()
    child = group / "nested"
    child.mkdir()
    cg._remove_cgroup(group, expected_identity=cg._exact_cgroup_identity(group))
    assert not group.exists()


def test_replacement_before_remove_refuses_native_effect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    group = tmp_path / "attempt-aaaaaaaaaaaaaaaa"
    group.mkdir()
    identity = cg._exact_cgroup_identity(group)
    moved = tmp_path / "former"
    original_stat = os.stat
    fired = False

    def substitute(path: str | os.PathLike[str], *args: object, **kw: object) -> os.stat_result:
        nonlocal fired
        if path == group.name and "dir_fd" in kw and not fired:
            fired = True
            group.rename(moved)
            group.mkdir()
        return original_stat(path, *args, **kw)

    monkeypatch.setattr(cg.os, "stat", substitute)
    with pytest.raises(FleetPlanInvalid, match="removal target replaced"):
        cg._remove_cgroup(group, expected_identity=identity)
    assert fired
    assert moved.is_dir() and group.is_dir()


def test_symlink_child_refuses_recursive_deletion(tmp_path: Path) -> None:
    group = tmp_path / "attempt-aaaaaaaaaaaaaaaa"
    group.mkdir()
    external = tmp_path / "external"
    external.mkdir()
    (group / "foreign-child").symlink_to(external, target_is_directory=True)
    with pytest.raises(FleetPlanInvalid, match="child is a symlink"):
        cg._remove_cgroup(group, expected_identity=cg._exact_cgroup_identity(group))
    assert external.is_dir() and group.is_dir()


def test_missing_and_foreign_identity_must_not_be_treated_as_completed_removal(
    tmp_path: Path,
) -> None:
    group = tmp_path / "attempt-aaaaaaaaaaaaaaaa"
    group.mkdir()
    original = cg._exact_cgroup_identity(group)
    group.rename(tmp_path / "old")
    group.mkdir()
    with pytest.raises(FleetPlanInvalid, match="replaced"):
        cg._remove_cgroup(group, expected_identity=original)
    assert group.exists()
    group.rmdir()
    with pytest.raises(FleetPlanInvalid, match="identity unavailable"):
        cg._remove_cgroup(group, expected_identity=original)


def test_child_replaced_during_directory_inventory_must_not_be_retired(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    group = tmp_path / "attempt-aaaaaaaaaaaaaaaa"
    group.mkdir()
    child = group / "original-child"
    child.mkdir()
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    original_stat = os.stat
    fired = False

    def substitute(path: str | os.PathLike[str], *args: object, **kw: object) -> os.stat_result:
        nonlocal fired
        if path == child.name and "dir_fd" in kw and not fired:
            fired = True
            child.rename(tmp_path / "moved")
            foreign.rename(child)
        return original_stat(path, *args, **kw)

    monkeypatch.setattr(cg.os, "stat", substitute)
    with pytest.raises(FleetPlanInvalid, match="child inventory was replaced"):
        cg._remove_cgroup(group, expected_identity=cg._exact_cgroup_identity(group))
    assert fired and child.exists() and (tmp_path / "moved").exists()


def test_post_remove_foreign_reappearance_is_not_acknowledged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    group = tmp_path / "attempt-aaaaaaaaaaaaaaaa"
    group.mkdir()
    identity = cg._exact_cgroup_identity(group)
    original_rmdir = os.rmdir

    def after_remove(path: str | os.PathLike[str], *args: object, **kw: object) -> None:
        original_rmdir(path, *args, **kw)
        if path == group.name and "dir_fd" in kw:
            group.mkdir()

    monkeypatch.setattr(cg.os, "rmdir", after_remove)
    with pytest.raises(FleetPlanInvalid, match="reappeared after removal"):
        cg._remove_cgroup(group, expected_identity=identity)
    assert group.exists()

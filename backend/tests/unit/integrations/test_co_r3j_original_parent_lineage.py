"""CO-R3J parent lineage negative controls on real local directories.

These tests do NOT establish exclusive cgroup parent mutation custody and do
NOT make rmdir identity-conditional. Such native privileges must be qualified
independently by the original Enforcer/LACE owner.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.integrations import _fleet_plan_cgroup_resources as cg


def test_unchanged_parent_can_retire_original_inode(tmp_path: Path) -> None:
    parent = tmp_path / "original-parent"
    parent.mkdir()
    target = parent / "attempt"
    target.mkdir()
    cg._remove_cgroup(target, expected_identity=cg._exact_cgroup_identity(target))
    assert parent.is_dir() and not target.exists()


def test_displaced_parent_is_rejected_before_destructive_cut(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "parent"
    parent.mkdir()
    target = parent / "attempt"
    target.mkdir()
    expected = cg._exact_cgroup_identity(target)
    displaced = tmp_path / "displaced-parent"
    original_check = cg._require_original_cgroup
    checks = 0

    def displace_after_original_check(path: Path, identity: tuple[int, int]) -> None:
        nonlocal checks
        original_check(path, identity)
        checks += 1
        if checks == 3:
            parent.rename(displaced)
            parent.mkdir()
            (parent / "attempt").mkdir()

    monkeypatch.setattr(cg, "_require_original_cgroup", displace_after_original_check)
    with pytest.raises(FleetPlanInvalid, match="parent lineage replaced"):
        cg._remove_cgroup(target, expected_identity=expected)
    assert (displaced / "attempt").is_dir()
    assert (parent / "attempt").is_dir()


def test_parent_replaced_at_native_rmdir_cannot_ack_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "parent"
    parent.mkdir()
    target = parent / "attempt"
    target.mkdir()
    expected = cg._exact_cgroup_identity(target)
    displaced = tmp_path / "displaced-parent"
    native_rmdir = os.rmdir
    raced = False

    def displace_parent_at_cut(
        name: str | os.PathLike[str], *args: object, **kwargs: object
    ) -> None:
        nonlocal raced
        if name == target.name and "dir_fd" in kwargs and not raced:
            raced = True
            parent.rename(displaced)
            parent.mkdir()
            (parent / "attempt").mkdir()
        native_rmdir(name, *args, **kwargs)

    monkeypatch.setattr(cg.os, "rmdir", displace_parent_at_cut)
    with pytest.raises(FleetPlanInvalid, match="parent lineage replaced"):
        cg._remove_cgroup(target, expected_identity=expected)
    assert raced
    assert not (displaced / "attempt").exists()  # original effect occurred, cannot undo it
    assert (parent / "attempt").is_dir()  # foreign path was not deleted via pinned fd


def test_parent_postcut_observation_failure_refuses_ack(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "parent"
    parent.mkdir()
    target = parent / "attempt"
    target.mkdir()
    expected = cg._exact_cgroup_identity(target)
    native_stat = os.stat

    def unavailable(path: object, *args: object, **kwargs: object) -> os.stat_result:
        if path == parent and not target.exists():
            raise OSError("parent changed or became unobservable")
        return native_stat(path, *args, **kwargs)

    monkeypatch.setattr(cg.os, "stat", unavailable)
    with pytest.raises(FleetPlanInvalid, match="parent lineage unobservable"):
        cg._remove_cgroup(target, expected_identity=expected)
    assert not target.exists()


def test_local_parent_stability_cannot_claim_independent_custody(
    tmp_path: Path,
) -> None:
    parent = tmp_path / "parent"
    parent.mkdir()
    target = parent / "attempt"
    target.mkdir()
    expected = cg._exact_cgroup_identity(target)
    cg._remove_cgroup(target, expected_identity=expected)
    # A successful local removal must not fabricate a signed LACE fact or
    # independently authorized workspace-writer absence.
    assert not target.exists()

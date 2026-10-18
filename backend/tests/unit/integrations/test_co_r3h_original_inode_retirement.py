"""CO-R3H: native removal ACK must describe the opened original inode.

These are adversarial local filesystem negative controls, NOT physical LACE
writer-absence evidence or a proof that Linux rmdir is identity-conditional.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.integrations import _fleet_plan_cgroup_resources as cg


def test_original_directory_retirement_has_zero_links(tmp_path: Path) -> None:
    original = tmp_path / 'attempt-original'
    original.mkdir()
    identity = cg._exact_cgroup_identity(original)
    cg._remove_cgroup(original, expected_identity=identity)
    assert not original.exists()


def test_foreign_deleted_at_destructive_cut_must_not_ack_original_retirement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = tmp_path / 'attempt-original'
    original.mkdir()
    identity = cg._exact_cgroup_identity(original)
    moved = tmp_path / 'original-still-linked'
    native_rmdir = os.rmdir
    diverted = False

    def substitute_before_kernel_remove(
        name: str | os.PathLike[str], *args: object, **kwargs: object,
    ) -> None:
        nonlocal diverted
        if name == original.name and 'dir_fd' in kwargs and not diverted:
            diverted = True
            original.rename(moved)
            original.mkdir()  # wrong inode at original name, empty and removable
        native_rmdir(name, *args, **kwargs)

    monkeypatch.setattr(cg.os, 'rmdir', substitute_before_kernel_remove)
    with pytest.raises(FleetPlanInvalid, match='original cgroup inode is still linked'):
        cg._remove_cgroup(original, expected_identity=identity)
    assert diverted
    assert moved.is_dir()
    assert cg._exact_cgroup_identity(moved) == identity
    assert not original.exists()  # prior pathname-only check would falsely pass


def test_original_inode_postcut_observation_failure_is_not_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = tmp_path / 'attempt-original'
    original.mkdir()
    identity = cg._exact_cgroup_identity(original)
    native_fstat = os.fstat
    observed = False

    def cannot_recheck(fd: int) -> os.stat_result:
        nonlocal observed
        if not original.exists():
            observed = True
            raise OSError('kernel inode observation unavailable')
        return native_fstat(fd)

    monkeypatch.setattr(cg.os, 'fstat', cannot_recheck)
    with pytest.raises(FleetPlanInvalid, match='cannot establish original cgroup inode retirement'):
        cg._remove_cgroup(original, expected_identity=identity)
    assert observed
    assert not original.exists()


def test_nested_substitution_blocks_parent_retirement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = tmp_path / 'attempt-original'
    original.mkdir()
    child = original / 'nested'
    child.mkdir()
    root_identity = cg._exact_cgroup_identity(original)
    child_identity = cg._exact_cgroup_identity(child)
    moved = tmp_path / 'original-nested'
    native_rmdir = os.rmdir

    def substitute_nested(name: str | os.PathLike[str], *args: object, **kwargs: object) -> None:
        if name == 'nested' and 'dir_fd' in kwargs:
            child.rename(moved)
            child.mkdir()
        native_rmdir(name, *args, **kwargs)

    monkeypatch.setattr(cg.os, 'rmdir', substitute_nested)
    with pytest.raises(FleetPlanInvalid, match='original cgroup inode is still linked'):
        cg._remove_cgroup(original, expected_identity=root_identity)
    assert cg._exact_cgroup_identity(moved) == child_identity
    assert original.is_dir()

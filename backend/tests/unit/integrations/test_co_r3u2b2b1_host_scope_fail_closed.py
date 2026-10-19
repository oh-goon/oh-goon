"""CO-R3U2B2B1: necessary host custody prerequisites, never kernel permission."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.original_native_custody import OriginalNativeCustodyRequirement
from oh_goon.integrations import _fleet_plan_cgroup_resources as cg


def _delegated_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "delegated"
    root.mkdir()
    for name in ("cgroup.procs", "cgroup.controllers", "cgroup.subtree_control"):
        (root / name).write_text("cpu memory pids", encoding="ascii")
    monkeypatch.setattr(cg, "_is_cgroup2_path", lambda _p: True)
    monkeypatch.setattr(cg.os, "access", lambda _p, _mode: True)
    return root


@pytest.mark.parametrize("mount_error", ["readonly", "unavailable"])
def test_delegated_root_denies_missing_mount_write_authority_before_native_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mount_error: str,
) -> None:
    root = _delegated_fixture(tmp_path, monkeypatch)
    effects = (Mock(), Mock(), Mock())
    monkeypatch.setattr(cg.os, "write", effects[0])
    monkeypatch.setattr(cg.os, "rmdir", effects[1])
    monkeypatch.setattr(cg.os, "mkdir", effects[2])

    def statvfs(_p: Path) -> SimpleNamespace:
        if mount_error == "unavailable":
            raise OSError("mount status withheld")
        return SimpleNamespace(f_flag=getattr(os, "ST_RDONLY", 1))

    monkeypatch.setattr(cg.os, "statvfs", statvfs)
    with pytest.raises(FleetPlanInvalid, match="read-only|mount status unavailable"):
        cg.require_delegated_cgroup_v2_root(root, required_controllers=set())
    for effect in effects:
        effect.assert_not_called()
    assert sorted(p.name for p in root.iterdir()) == [
        "cgroup.controllers",
        "cgroup.procs",
        "cgroup.subtree_control",
    ]


def test_writable_mount_is_only_a_prerequisite_not_custody(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _delegated_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(cg.os, "statvfs", lambda _p: SimpleNamespace(f_flag=0))
    assert cg.require_delegated_cgroup_v2_root(root, required_controllers=set()) == root
    with pytest.raises(FleetPlanInvalid, match="NOT PROVEN"):
        cg.require_independent_native_parent_custody(operation="fake ready delegated mount")


def _requirement(**changes: object) -> OriginalNativeCustodyRequirement:
    values: dict[str, object] = dict(
        run_id="run-1",
        step_id="step-1",
        control_epoch=42,
        executor_generation="executor-generation-77",
        attempt_identity="attempt-1",
        parent_device=5,
        parent_inode=6,
        resource_device=5,
        resource_inode=7,
    )
    values.update(changes)
    return OriginalNativeCustodyRequirement(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "missing",
    [
        {"executor_generation": None},
        {"parent_device": None},
        {"parent_inode": None},
        {"resource_device": None},
        {"resource_inode": None},
    ],
)
def test_missing_generation_or_kernel_identity_rejected_before_native_effect(
    missing: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    effect = Mock()
    monkeypatch.setattr(cg.os, "write", effect)
    with pytest.raises(FleetPlanInvalid, match="scope incomplete"):
        cg.require_independent_native_parent_custody(
            operation="hostile effect",
            requirement=_requirement(**missing),
        )
    effect.assert_not_called()


def test_forged_complete_scope_does_not_issue_physical_permission() -> None:
    with pytest.raises(FleetPlanInvalid, match="no independently provisioned kernel parent owner"):
        cg.require_independent_native_parent_custody(
            operation="forged complete scope",
            requirement=_requirement(),
        )
    with pytest.raises(FleetPlanInvalid, match="type invalid"):
        cg.require_independent_native_parent_custody(
            operation="untyped claim", requirement=object()
        )

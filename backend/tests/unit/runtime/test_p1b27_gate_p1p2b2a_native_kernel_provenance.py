"""P1/P2B2A: real-host mount provenance uses one original pinned FD roster."""

from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.integrations.fleet_plan_bind_source_custody import OriginalBindSourceLease
from oh_goon.repository_tooling.architecture.repository_layout import ROOT
from oh_goon.repository_tooling.architecture.x0_single_execution_contract import (
    _check_p1p2b2a_effectful_source_provenance,
)
from tests.native.test_p1b27b2b3b2b3a_effectful_mount import _pinned_kernel_mount_identity
from tests.unit.runtime.test_p1b27b2b2a_pinned_bind_source import _prepared


def test_expected_kernel_identity_is_read_from_original_pinned_fd(tmp_path: Path):
    request = _prepared(tmp_path)
    target = request.attempt.paths.home
    with OriginalBindSourceLease.pin_prepared(request) as lease:
        expected = _pinned_kernel_mount_identity(lease, "/home/fleet")
        assert expected == f"{os.stat(target).st_dev}:{os.stat(target).st_ino}"
        target.rename(target.with_name("home-renamed"))
        target.mkdir()
        # The new pathname resolves to a different inode; the original pinned
        # descriptor still identifies the object that Bubblewrap must consume.
        assert expected == _pinned_kernel_mount_identity(lease, "/home/fleet")
        assert expected != f"{os.stat(target).st_dev}:{os.stat(target).st_ino}"
    with pytest.raises(FleetPlanInvalid, match="closed"):
        _pinned_kernel_mount_identity(lease, "/home/fleet")


def test_unlisted_mount_target_cannot_supply_kernel_witness(tmp_path: Path):
    request = _prepared(tmp_path)
    with OriginalBindSourceLease.pin_prepared(request) as lease:
        with pytest.raises(AssertionError, match="exactly one"):
            _pinned_kernel_mount_identity(lease, "/foreign")


def test_effectful_native_witness_fails_closed_without_kernel_correspondence():
    witness = (ROOT / "backend/tests/native/test_p1b27b2b3b2b3a_effectful_mount.py").read_text()
    tree = ast.parse(witness)
    for name in (
        "test_original_fd_bind_argv_is_consumed_by_real_bubblewrap",
        "test_original_effectful_fd_mount_lifecycle_is_supervised",
    ):
        native = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
        shell = [
            n.value
            for n in ast.walk(native)
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and "stat -Lc" in n.value
        ]
        assert len(shell) == 1
        assert shell[0].count("stat -Lc '%d:%i'") == 2
    for required in (
        "/home/fleet",
        "/workspace",
        "/tmp/oh-goon-fd-proof",
        "sealed-FD-data-v1",
        "lease.verify_kernel_custody()",
    ):
        assert required in witness
    assert _check_p1p2b2a_effectful_source_provenance(ROOT) == []


@pytest.mark.parametrize(
    "old,new",
    (
        ("stat -Lc '%d:%i' /home/fleet", 'echo "$4"'),
        ("stat -Lc '%d:%i' /workspace", 'echo "$5"'),
        ('stat -Lc \'%d:%i\' /home/fleet)" = "$4"', 'stat -Lc \'%d:%i\' /home/fleet)" = "$5"'),
        ("lease.verify_kernel_custody()", "pass"),
    ),
)
def test_x0_refuses_native_witness_bypass(tmp_path: Path, old: str, new: str):
    source = ROOT / "backend/tests/native/test_p1b27b2b3b2b3a_effectful_mount.py"
    output = tmp_path / "backend/tests/native/test_p1b27b2b3b2b3a_effectful_mount.py"
    output.parent.mkdir(parents=True)
    text = source.read_text()
    assert old in text
    output.write_text(text.replace(old, new, 1))
    assert _check_p1p2b2a_effectful_source_provenance(tmp_path)

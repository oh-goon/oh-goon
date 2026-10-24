"""P1/P2B2A: real-host mount provenance uses one original pinned FD roster."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.integrations.fleet_plan_bind_source_custody import OriginalBindSourceLease
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

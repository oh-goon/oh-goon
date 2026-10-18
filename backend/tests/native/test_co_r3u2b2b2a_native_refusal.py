"""CO-R3U2B2B2A: actual Linux host, native LACE *refusal* witness.

No native effects are attempted. A green run proves that this particular
source refuses unprovisioned effects under this host's observed prerequisites;
it NEVER proves custody, exclusive writers, teardown, or release authority.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.fleet_plan_isolation import CicdResourcePolicy
from oh_goon.core.models.original_native_custody import OriginalNativeCustodyRequirement
from oh_goon.integrations import _fleet_plan_cgroup_resources as cg


def _kernel_root() -> Path:
    if sys.platform != 'linux':
        pytest.fail('real Linux kernel required; never skip an enrolled native witness')
    root = Path('/sys/fs/cgroup')
    if not root.is_dir() or not cg._is_cgroup2_path(root):
        pytest.fail('real mounted cgroup-v2 required; fake or missing host is not PASS')
    return root


def test_live_kernel_mount_prerequisite_never_grants_effect_permission() -> None:
    root = _kernel_root()
    flags = os.statvfs(root).f_flag  # Real host, read-only inspection only.
    assert type(flags) is int
    # A writable mount is a prerequisite, not a security grant. A read-only
    # mount must be reported as BLOCKED without trying to probe by writing.
    if flags & getattr(os, 'ST_RDONLY', 1):
        with pytest.raises(FleetPlanInvalid, match='read-only'):
            cg.require_delegated_cgroup_v2_root(root, required_controllers=set())
    with pytest.raises(FleetPlanInvalid, match='NOT PROVEN'):
        cg.require_independent_native_parent_custody(operation='real host no-effect witness')
    print('CO_R3U2B2B2A_NATIVE_REFUSAL=PASS kernel=cgroup2 effect_permission=NONE')


def test_live_kernel_identity_scope_cannot_self_issue_custody() -> None:
    root = _kernel_root()
    original = root.stat()
    requirement = OriginalNativeCustodyRequirement(
        run_id='untrusted-test-run', step_id='untrusted-step', control_epoch=1,
        executor_generation='untrusted-generation', attempt_identity='untrusted-attempt',
        parent_device=original.st_dev, parent_inode=original.st_ino,
        resource_device=original.st_dev, resource_inode=original.st_ino,
    )
    assert requirement.complete_scope
    with pytest.raises(FleetPlanInvalid, match='no independently provisioned kernel parent owner'):
        cg.require_independent_native_parent_custody(
            operation='complete-looking untrusted host scope', requirement=requirement,
        )
    # A scoped inode is merely a sampled identity, not an exclusive syscall grant.
    assert (root.stat().st_dev, root.stat().st_ino) == (original.st_dev, original.st_ino)


def test_live_kernel_original_lease_refuses_before_lock_and_syscall(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _kernel_root()
    lease = cg.CgroupV2AttemptLease(
        root=root, cgroup=root / 'oh-goon-native-refusal-not-created',
        state_file=tmp_path / 'must-not-create.json',
        lock_path=tmp_path / 'must-not-lock',
        policy=CicdResourcePolicy(), attempt_root=tmp_path,
        cgroup_identity=(root.stat().st_dev, root.stat().st_ino),
    )
    physical_lock = Mock(side_effect=AssertionError('native lock/effect reached'))
    monkeypatch.setattr(cg, '_resource_lock', physical_lock)
    for operation in (lease.open, lease.close, lease.command_prefix):
        with pytest.raises(FleetPlanInvalid, match='NOT PROVEN'):
            operation()
    physical_lock.assert_not_called()
    assert not lease.cgroup.exists()
    assert not lease.state_file.exists()
    assert not lease.lock_path.exists()
    print('CO_R3U2B2B2A_EFFECT_CUTS=REFUSED allocation=0 teardown=0 launch=0')

"""P1B27B2B3B2B2: one original sealed/native FD prelaunch handoff.

Actual O_PATH and FD inheritance semantics are exercised on Linux; no synthetic
result is independent LACE custody, an effectful Bubblewrap test, or Gate B.
"""
from __future__ import annotations

import fcntl
import os
from pathlib import Path

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.integrations.fleet_plan_bind_source_custody import OriginalBindSourceLease
from oh_goon.integrations.fleet_plan_bubblewrap_isolation import _require_original_native_fd_handoff
from tests.unit.runtime.test_p1b27b2b2a_pinned_bind_source import _argv, _prepared


class AtomicStub:
    """Identity-approved runtime lease placeholder; never signs/adopts authority."""

    def __init__(self, fds: tuple[int, ...]):
        self.pass_fds = fds
        self.checked = 0

    def verify_kernel_custody(self) -> None:
        self.checked += 1


def _mounted(request):
    lease = OriginalBindSourceLease.pin_prepared(request)
    argv = _argv(lease)
    lease.consume_validated_argv(argv)
    return lease, tuple(argv)


def test_native_handoff_preserves_exact_disjoint_sealed_and_pinned_fds(tmp_path):
    request = _prepared(tmp_path)
    with OriginalBindSourceLease.pin_prepared(request) as lease:
        argv = _argv(lease)
        lease.consume_validated_argv(argv)
        runtime_fd = os.memfd_create('test-handoff', os.MFD_CLOEXEC)
        try:
            runtime = AtomicStub((runtime_fd,))
            inherited = _require_original_native_fd_handoff(tuple(argv), runtime, lease)
            assert inherited == (runtime_fd, *lease.pass_fds)
            assert runtime.checked == 1
            assert len(set(inherited)) == len(inherited)
            for pin in lease.pins:
                assert fcntl.fcntl(pin.fd, fcntl.F_GETFL) & os.O_PATH
                assert fcntl.fcntl(pin.fd, fcntl.F_GETFD) & fcntl.FD_CLOEXEC
        finally:
            os.close(runtime_fd)


def test_cross_lease_fd_number_alias_rejected_before_launch(tmp_path):
    request = _prepared(tmp_path)
    with OriginalBindSourceLease.pin_prepared(request) as lease:
        argv = _argv(lease)
        lease.consume_validated_argv(argv)
        runtime = AtomicStub((lease.pass_fds[0],))
        with pytest.raises(FleetPlanInvalid, match='overlaps'):
            _require_original_native_fd_handoff(tuple(argv), runtime, lease)
        assert runtime.checked == 1


@pytest.mark.parametrize('runtime_fds', [(), (0,), (1,), (-1,), ('4',)])
def test_missing_standard_or_untyped_runtime_fd_rejected(tmp_path, runtime_fds):
    request = _prepared(tmp_path)
    with OriginalBindSourceLease.pin_prepared(request) as lease:
        argv = _argv(lease)
        lease.consume_validated_argv(argv)
        with pytest.raises(FleetPlanInvalid, match='invalid'):
            _require_original_native_fd_handoff(tuple(argv), AtomicStub(runtime_fds), lease)


def test_pin_same_kernel_inode_as_readable_fd_is_not_a_native_source_pin(tmp_path):
    request = _prepared(tmp_path)
    with OriginalBindSourceLease.pin_prepared(request) as lease:
        original = lease.pins[0]
        fd = os.open(original.source, os.O_RDONLY | os.O_CLOEXEC)
        try:
            assert os.fstat(fd).st_ino == original.inode
            os.dup2(fd, original.fd)
            with pytest.raises(FleetPlanInvalid, match='cloexec O_PATH'):
                lease.verify_kernel_custody()
        finally:
            os.close(fd)


def test_non_cloexec_native_bind_source_pin_rejected(tmp_path):
    request = _prepared(tmp_path)
    with OriginalBindSourceLease.pin_prepared(request) as lease:
        fd = lease.pass_fds[0]
        original_flags = fcntl.fcntl(fd, fcntl.F_GETFD)
        try:
            fcntl.fcntl(fd, fcntl.F_SETFD, original_flags & ~fcntl.FD_CLOEXEC)
            with pytest.raises(FleetPlanInvalid, match='cloexec O_PATH'):
                lease.verify_kernel_custody()
        finally:
            fcntl.fcntl(fd, fcntl.F_SETFD, original_flags)


def test_handoff_rejects_changed_fd_mount_target(tmp_path):
    request = _prepared(tmp_path)
    with OriginalBindSourceLease.pin_prepared(request) as lease:
        argv = _argv(lease)
        lease.consume_validated_argv(argv)
        argv[argv.index('--bind-fd') + 2] = '/substituted'
        runtime_fd = os.memfd_create('test-handoff', os.MFD_CLOEXEC)
        try:
            with pytest.raises(FleetPlanInvalid, match='argv differs'):
                _require_original_native_fd_handoff(tuple(argv), AtomicStub((runtime_fd,)), lease)
        finally:
            os.close(runtime_fd)


def test_closed_bind_pin_rejected_by_unified_handoff(tmp_path):
    request = _prepared(tmp_path)
    lease, argv = _mounted(request)
    lease.close()
    runtime_fd = os.memfd_create('test-handoff', os.MFD_CLOEXEC)
    try:
        with pytest.raises(FleetPlanInvalid, match='closed'):
            _require_original_native_fd_handoff(argv, AtomicStub((runtime_fd,)), lease)
    finally:
        os.close(runtime_fd)


def test_x0_detects_bypassed_or_duplicated_fd_handoff(tmp_path):
    import shutil

    from oh_goon.repository_tooling.architecture.x0_single_execution_contract import (
        _check_p1b27b2b3b2b2_fd_handoff,
    )
    from oh_goon.repository_tooling.architecture.repository_layout import ROOT

    original = ROOT / 'backend/src/oh_goon'
    assert _check_p1b27b2b3b2b2_fd_handoff(original) == []
    for filename in ('fleet_plan_bind_source_custody.py', 'fleet_plan_bubblewrap_isolation.py'):
        dst = tmp_path / 'integrations' / filename
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(original / 'integrations' / filename, dst)
    owner = tmp_path / 'integrations/fleet_plan_bubblewrap_isolation.py'
    old = owner.read_text()
    assert 'pass_fds=inherited_fds' in old
    owner.write_text(old.replace('pass_fds=inherited_fds',
                                 'pass_fds=(*atomic_authority.pass_fds, *mount_source_lease.pass_fds)', 1))
    assert _check_p1b27b2b3b2b2_fd_handoff(tmp_path)
    owner.write_text(old)
    pin = tmp_path / 'integrations/fleet_plan_bind_source_custody.py'
    old = pin.read_text()
    pin.write_text(old.replace('flags & os.O_PATH', 'flags & os.O_RDONLY', 1))
    assert _check_p1b27b2b3b2b2_fd_handoff(tmp_path)


def test_original_run_command_inherits_exact_live_linux_fd_objects(tmp_path):
    """Real subprocess handoff; NOT Bubblewrap or physical absence proof."""
    from oh_goon.engine.command import run_command

    request = _prepared(tmp_path)
    with OriginalBindSourceLease.pin_prepared(request) as lease:
        argv = _argv(lease)
        lease.consume_validated_argv(argv)
        runtime_fd = os.memfd_create('test-handoff', os.MFD_CLOEXEC)
        try:
            inherited = _require_original_native_fd_handoff(
                tuple(argv), AtomicStub((runtime_fd,)), lease,
            )
            # Native shell only checks actual inherited Linux descriptor objects.
            # It cannot confer authority or replace the original Bubblewrap path.
            result = run_command(
                '/bin/sh', '-c',
                'for fd do test -e "/proc/self/fd/$fd" || exit 77; done',
                'native-fd-check', *(str(fd) for fd in inherited),
                timeout=10, cwd=None, env={'PATH': '/bin:/usr/bin'},
                pass_fds=inherited,
            )
            assert result.returncode == 0, result.stderr
        finally:
            os.close(runtime_fd)

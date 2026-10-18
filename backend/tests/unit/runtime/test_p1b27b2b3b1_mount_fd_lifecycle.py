"""P1B27B2B3B1: original LACE visible mount/FD lifecycle is fail-closed.

These are synthetic /proc fixtures, not physical absence or production Gate B.
"""
from __future__ import annotations

from pathlib import Path
import shutil

import pytest

from oh_goon.integrations import linux_physical_observer as linux
from oh_goon.core.ports.physical_observation import PhysicalObservationScope
from oh_goon.repository_tooling.architecture.x0_single_execution_contract import (
    _check_p1b27b2b3b1_mount_fd_lifecycle,
)
from oh_goon.repository_tooling.architecture.repository_layout import ROOT

PACKAGE = ROOT / 'backend/src/oh_goon'


def _fixture(tmp_path: Path) -> tuple[Path, PhysicalObservationScope]:
    proc = tmp_path / 'proc'
    worker = proc / '812'
    (worker / 'ns').mkdir(parents=True)
    (worker / 'fd').mkdir()
    (worker / 'fdinfo').mkdir()
    (proc / 'sys/kernel/random').mkdir(parents=True)
    (proc / 'sys/kernel/random/boot_id').write_text('bootB27\n')
    (worker / 'stat').write_text('812 (mount worker) ' + ' '.join(['S'] + ['0'] * 18 + ['944']))
    (worker / 'ns/mnt').symlink_to('mnt:[4026538123]')
    (worker / 'ns/net').symlink_to('net:[4026538124]')
    (worker / 'mountinfo').write_text(
        '43 2 8:1 / /workspace rw - ext4 /dev/sda1 rw\n'
        '44 2 8:1 /home /home/fleet rw - ext4 /dev/sda1 rw\n'
    )
    scope = PhysicalObservationScope('run', 'sha256:' + 'a' * 64,
        str(tmp_path / 'cg'), 1, 2, 812, 'bootB27:944',
        ('/workspace', '/home/fleet'), None, None, None)
    return proc, scope


def _fd(proc: Path, mount_id: str = '43') -> None:
    (proc / '812/fd/9').symlink_to('socket:[123]')
    (proc / '812/fdinfo/9').write_text(f'mnt_id:\t{mount_id}\n')


def test_complete_mount_roster_binds_same_kernel_digest_to_visible_fd(tmp_path):
    proc, scope = _fixture(tmp_path)
    _fd(proc)
    mount = linux._tracked_namespace_inventory(scope, proc)['tracked_mount_namespace']
    evidence = linux._visible_mount_fd_holders(scope, proc)
    assert mount['state'] == evidence['state'] == 'PRESENT'
    expected = mount['detail'].split('target_kernel_sha256=', 1)[1].split(';', 1)[0]
    assert f'target_kernel_sha256={expected}' in evidence['detail']
    assert 'matching_mount_fds=1' in evidence['detail']
    assert evidence == linux._visible_mount_fd_holders(scope, proc)


def test_no_visible_fd_is_scoped_nondetection_only(tmp_path):
    proc, scope = _fixture(tmp_path)
    evidence = linux._visible_mount_fd_holders(scope, proc)
    assert evidence['state'] == 'NOT_OBSERVED'
    assert 'hidden tasks, detached mounts, and kernel-held references unverified' in evidence['detail']


@pytest.mark.parametrize('mutation', ['partial', 'stacked', 'invalid-device', 'bad-root-escape'])
def test_fd_probe_refuses_incomplete_or_ambiguous_kernel_mount_target(tmp_path, mutation):
    proc, scope = _fixture(tmp_path)
    _fd(proc)
    mountinfo = proc / '812/mountinfo'
    original = mountinfo.read_text()
    if mutation == 'partial':
        mountinfo.write_text(original.splitlines(keepends=True)[0])
    elif mutation == 'stacked':
        mountinfo.write_text(original + '45 2 8:1 /evil /workspace rw - ext4 /dev/sda1 rw\n')
    elif mutation == 'invalid-device':
        mountinfo.write_text(original.replace('8:1', 'bad', 1))
    else:
        mountinfo.write_text(original.replace('/home /home/fleet', r'/bad\noops /home/fleet'))
    assert linux._visible_mount_fd_holders(scope, proc)['state'] == 'UNKNOWN'


def test_fd_probe_rejects_mount_target_identity_change_during_one_scan(tmp_path, monkeypatch):
    proc, scope = _fixture(tmp_path)
    _fd(proc)
    original_read = linux._read_regular
    mountinfo = proc / '812/mountinfo'
    n = 0

    def racing_read(path, limit):
        nonlocal n
        if path == mountinfo:
            n += 1
            if n == 2:
                mountinfo.write_text(mountinfo.read_text().replace('43 2 8:1 /', '51 2 8:1 /'))
        return original_read(path, limit)

    monkeypatch.setattr(linux, '_read_regular', racing_read)
    assert linux._visible_mount_fd_holders(scope, proc)['state'] == 'UNKNOWN'
    assert n == 2


def test_fd_probe_rejects_fd_list_change_during_one_scan(tmp_path, monkeypatch):
    proc, scope = _fixture(tmp_path)
    _fd(proc)
    original_read = linux._read_regular
    fdinfo = proc / '812/fdinfo/9'

    def racing_read(path, limit):
        if path == fdinfo:
            (proc / '812/fd/10').symlink_to('socket:[456]')
            (proc / '812/fdinfo/10').write_text('mnt_id:\t44\n')
        return original_read(path, limit)

    monkeypatch.setattr(linux, '_read_regular', racing_read)
    assert linux._visible_mount_fd_holders(scope, proc)['state'] == 'UNKNOWN'


def test_original_lace_authority_and_x0_guards_remain_closed():
    assert _check_p1b27b2b3b1_mount_fd_lifecycle(PACKAGE) == []
    port = (PACKAGE / 'core/ports/physical_observation.py').read_text()
    assert 'physical_absence_proven: bool = False' in port
    assert 'teardown_proven: bool = False' in port
    assert 'effect_permission: str = "NONE"' in port


@pytest.mark.parametrize('mutation', ['omit-coverage', 'accept-stacked', 'omit-mount-race', 'omit-fd-race', 'erase-digest'])
def test_x0_rejects_observer_lifecycle_guard_drift(tmp_path, mutation):
    owner = tmp_path / 'integrations/linux_physical_observer.py'
    owner.parent.mkdir(parents=True)
    shutil.copy2(PACKAGE / 'integrations/linux_physical_observer.py', owner)
    source = owner.read_text()
    old, new = {
        'omit-coverage': ('set(matched_targets) != targets', 'False'),
        'accept-stacked': ('len(entries) != 1', 'len(entries) < 0'),
        'omit-mount-race': ('_read_regular(original / "mountinfo", 1_048_576).splitlines() != raw', 'False'),
        'omit-fd-race': ('after_names != names', 'False'),
        'erase-digest': ('f"target_kernel_sha256={target_fingerprint}; inspected_fds={inspected_fds}; "',
                         'f"inspected_fds={inspected_fds}; "'),
    }[mutation]
    prefix, owner_source = source.split('def _visible_mount_fd_holders(', 1)
    assert old in owner_source
    owner.write_text(prefix + 'def _visible_mount_fd_holders(' + owner_source.replace(old, new, 1))
    assert _check_p1b27b2b3b1_mount_fd_lifecycle(tmp_path)

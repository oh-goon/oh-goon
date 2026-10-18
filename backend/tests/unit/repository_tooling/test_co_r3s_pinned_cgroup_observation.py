"""CO-R3S: original physical observer must never report a replaced cgroup as stable.

These facts are diagnostic only; they cannot authorize destruction or Gate B.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from oh_goon.integrations import linux_physical_observer as observer
from oh_goon.repository_tooling.local_lace_physical_preflight import parse_scope


def _setup(tmp_path: Path):
    group = tmp_path / 'original'; group.mkdir()
    (group / 'cgroup.events').write_text('populated 0\n', encoding='ascii')
    (group / 'cgroup.procs').write_text('', encoding='ascii')
    scope = parse_scope({
        'run_id': 'original-r3s', 'subject_identity': 'sha256:' + 'a' * 64,
        'cgroup_path': str(group), 'cgroup_device': group.stat().st_dev,
        'cgroup_inode': group.stat().st_ino, 'tracked_pid': 10001,
        'tracked_birth': 'boot:123', 'mount_targets': ['/target'],
        'network_namespace': 'net', 'host_veth': 'veth', 'nft_table': 'nft',
    })
    return scope, group


def test_stable_original_cgroup_is_a_scoped_present_fact(tmp_path):
    scope, group = _setup(tmp_path)
    fact = observer._cgroup(scope)
    assert fact['state'] == 'PRESENT'
    assert 'reported empty' in fact['detail']
    (group / 'cgroup.events').write_text('populated 1\n', encoding='ascii')
    assert observer._cgroup(scope)['state'] == 'PRESENT'


@pytest.mark.parametrize('replace', ['foreign_directory', 'removed_directory'])
def test_substitution_mid_observation_refuses_false_stability(tmp_path, monkeypatch, replace):
    scope, group = _setup(tmp_path)
    former = tmp_path / 'former'
    opened = observer.os.open
    fired = False

    def substitute(path, flags, *args, **kwargs):
        nonlocal fired
        if path == 'cgroup.procs' and 'dir_fd' in kwargs and not fired:
            fired = True
            group.rename(former)
            if replace == 'foreign_directory':
                group.mkdir()
                (group / 'cgroup.events').write_text('populated 0\n', encoding='ascii')
                (group / 'cgroup.procs').write_text('', encoding='ascii')
        return opened(path, flags, *args, **kwargs)

    monkeypatch.setattr(observer.os, 'open', substitute)
    fact = observer._cgroup(scope)
    assert fired and fact['state'] == 'UNKNOWN'
    assert (former / 'cgroup.events').is_file()
    if replace == 'foreign_directory':
        assert group.is_dir()


def test_symlink_control_and_overbudget_read_fail_closed(tmp_path):
    scope, group = _setup(tmp_path)
    control = group / 'cgroup.procs'
    control.unlink()
    foreign = tmp_path / 'foreign'; foreign.write_text('')
    control.symlink_to(foreign)
    assert observer._cgroup(scope)['state'] == 'UNKNOWN'
    assert foreign.read_text() == ''
    control.unlink()
    control.write_text('5' * 65_537)
    assert observer._cgroup(scope)['state'] == 'UNKNOWN'
    control.write_text('')
    (group / 'cgroup.events').write_bytes(b'populated 0\xff')
    assert observer._cgroup(scope)['state'] == 'UNKNOWN'


def test_original_identity_lost_before_fd_open_is_unknown(tmp_path, monkeypatch):
    scope, group = _setup(tmp_path)
    former = tmp_path / 'former'; opened = observer.os.open; fired = False

    def replace_just_before_directory_open(path, flags, *args, **kwargs):
        nonlocal fired
        if Path(path) == group and not fired:
            fired = True
            group.rename(former)
            group.mkdir()
        return opened(path, flags, *args, **kwargs)

    monkeypatch.setattr(observer.os, 'open', replace_just_before_directory_open)
    assert observer._cgroup(scope)['state'] == 'UNKNOWN'
    assert fired and former.is_dir() and group.is_dir()

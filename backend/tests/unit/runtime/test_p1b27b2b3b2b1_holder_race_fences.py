"""B3B2B1: source-bound LACE holder censuses refuse intra-scan PID/FD reuse.

Synthetic proc tests, never effectful Bubblewrap or physical absence evidence.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from oh_goon.integrations import linux_physical_observer as linux
from oh_goon.repository_tooling.architecture.repository_layout import ROOT
from oh_goon.repository_tooling.architecture.x0_single_execution_contract import (
    _check_p1b27b2b3b2b1_holder_race_fences,
)
from tests.unit.runtime.test_p1b27b2b3b1_mount_fd_lifecycle import _fixture, _fd

PACKAGE = ROOT / 'backend/src/oh_goon'


def test_namespace_fd_holder_is_still_visible_with_stable_roster(tmp_path):
    proc, scope = _fixture(tmp_path)
    (proc / '812/fd/9').symlink_to('mnt:[4026538123]')
    result = linux._namespace_fd_holders(scope, proc)
    assert result['state'] == 'PRESENT'
    assert 'matching_namespace_fds=1' in result['detail']


def test_namespace_fd_holder_refuses_reused_fd_number(tmp_path, monkeypatch):
    proc, scope = _fixture(tmp_path)
    fd = proc / '812/fd/9'
    fd.symlink_to('mnt:[4026538123]')
    original = os.readlink
    moved = False

    def replace_link(path, *args, **kwargs):
        nonlocal moved
        value = original(path, *args, **kwargs)
        if str(path) == str(fd) and not moved:
            moved = True
            fd.unlink()
            fd.symlink_to('net:[4026538999]')
        return value

    monkeypatch.setattr(linux.os, 'readlink', replace_link)
    assert linux._namespace_fd_holders(scope, proc)['state'] == 'UNKNOWN'
    assert moved


def test_namespace_fd_holder_refuses_fd_roster_growth(tmp_path, monkeypatch):
    proc, scope = _fixture(tmp_path)
    fd = proc / '812/fd/9'
    fd.symlink_to('mnt:[4026538123]')
    original = os.readlink
    added = False

    def add_fd(path, *args, **kwargs):
        nonlocal added
        value = original(path, *args, **kwargs)
        if str(path) == str(fd) and not added:
            added = True
            (proc / '812/fd/10').symlink_to('mnt:[4026538123]')
        return value

    monkeypatch.setattr(linux.os, 'readlink', add_fd)
    assert linux._namespace_fd_holders(scope, proc)['state'] == 'UNKNOWN'
    assert added


@pytest.mark.parametrize('probe', ['namespace', 'mount'])
def test_original_holder_probes_reject_new_visible_process_during_census(tmp_path, monkeypatch, probe):
    proc, scope = _fixture(tmp_path)
    _fd(proc)
    original = linux._read_regular
    calls = 0

    def introduce_process(path, limit):
        nonlocal calls
        value = original(path, limit)
        if (path == proc / '812/stat' and probe == 'namespace') or (
            path == proc / '812/fdinfo/9' and probe == 'mount'
        ):
            calls += 1
            if (probe == 'namespace' and calls == 2) or (probe == 'mount' and calls == 1):
                newcomer = proc / '913'
                (newcomer / 'ns').mkdir(parents=True)
                (newcomer / 'fd').mkdir()
                (newcomer / 'fdinfo').mkdir()
                (newcomer / 'stat').write_text('913 (newcomer) ' + ' '.join(['S'] + ['0'] * 18 + ['946']))
                (newcomer / 'ns/mnt').symlink_to('mnt:[4026538123]')
                (newcomer / 'ns/net').symlink_to('net:[4026538124]')
        return value

    monkeypatch.setattr(linux, '_read_regular', introduce_process)
    fn = linux._namespace_fd_holders if probe == 'namespace' else linux._visible_mount_fd_holders
    assert fn(scope, proc)['state'] == 'UNKNOWN'
    assert calls >= (2 if probe == 'namespace' else 1)


def test_mount_fd_probe_refuses_reused_fd_same_number_new_mount_id(tmp_path, monkeypatch):
    proc, scope = _fixture(tmp_path)
    _fd(proc)
    original = linux._read_regular
    fdinfo = proc / '812/fdinfo/9'
    changed = False

    def reuse_fd(path, limit):
        nonlocal changed
        value = original(path, limit)
        if path == fdinfo and not changed:
            changed = True
            fdinfo.write_text('mnt_id:\t44\n')
        return value

    monkeypatch.setattr(linux, '_read_regular', reuse_fd)
    assert linux._visible_mount_fd_holders(scope, proc)['state'] == 'UNKNOWN'
    assert changed


def test_mount_fd_probe_rejects_intra_scan_fdinfo_disappearance(tmp_path, monkeypatch):
    proc, scope = _fixture(tmp_path)
    _fd(proc)
    original = linux._read_regular
    fdinfo = proc / '812/fdinfo/9'
    removed = False

    def close_fd(path, limit):
        nonlocal removed
        value = original(path, limit)
        if path == fdinfo and not removed:
            removed = True
            fdinfo.unlink()
        return value

    monkeypatch.setattr(linux, '_read_regular', close_fd)
    assert linux._visible_mount_fd_holders(scope, proc)['state'] == 'UNKNOWN'
    assert removed


def test_original_guard_remains_enabled():
    assert _check_p1b27b2b3b2b1_holder_race_fences(PACKAGE) == []


@pytest.mark.parametrize('attack', [
    'omit_namespace_fd_roster', 'omit_namespace_fd_target', 'omit_namespace_pid_roster',
    'omit_mount_fd_id_recheck', 'omit_mount_pid_roster',
])
def test_x0_rejects_holder_race_fence_bypass(tmp_path, attack):
    src = PACKAGE / 'integrations/linux_physical_observer.py'
    out = tmp_path / 'integrations/linux_physical_observer.py'
    out.parent.mkdir(parents=True)
    shutil.copy2(src, out)
    source = out.read_text()
    old, replacement = {
        'omit_namespace_fd_roster': ('final_fds != fds', 'False'),
        'omit_namespace_fd_target': ('os.readlink(fd_dir / fd) != fd_links[fd]', 'False'),
        'omit_namespace_pid_roster': ('final_candidates != candidates', 'False'),
        'omit_mount_fd_id_recheck': ('read_mount_id(fd) != mount_ids[fd]', 'False'),
        'omit_mount_pid_roster': ('final_pids != pids', 'False'),
    }[attack]
    assert old in source
    if attack == 'omit_mount_pid_roster':
        # CO-R3R also checks the foreign-mount PID roster. Mutate the
        # original mount-FD owner, not the newly earlier foreign probe.
        prefix, suffix = source.split('def _visible_mount_fd_holders(', 1)
        assert old in suffix
        source = prefix + 'def _visible_mount_fd_holders(' + suffix.replace(old, replacement, 1)
    else:
        source = source.replace(old, replacement, 1)
    out.write_text(source)
    assert _check_p1b27b2b3b2b1_holder_race_fences(tmp_path)

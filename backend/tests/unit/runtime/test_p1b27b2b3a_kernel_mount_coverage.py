"""P1B27B2B3A: original Linux LACE target coverage must not be partial."""
from __future__ import annotations

from pathlib import Path
import shutil

import pytest

from oh_goon.integrations import linux_physical_observer as linux
from oh_goon.core.ports.physical_observation import PhysicalObservationScope
from oh_goon.repository_tooling.architecture.x0_single_execution_contract import (
    _check_p1b27b2b3a_mount_coverage,
)
from oh_goon.repository_tooling.architecture.repository_layout import ROOT

PACKAGE = ROOT / 'backend/src/oh_goon'


def _fixture(tmp_path: Path):
    proc = tmp_path / 'proc'
    pid = proc / '812'
    (pid / 'ns').mkdir(parents=True)
    (proc / 'sys/kernel/random').mkdir(parents=True)
    (proc / 'sys/kernel/random/boot_id').write_text('bootB27\n')
    (pid / 'stat').write_text('812 (mount worker) ' + ' '.join(['S'] + ['0'] * 18 + ['944']))
    (pid / 'ns/mnt').symlink_to('mnt:[4026538123]')
    (pid / 'ns/net').symlink_to('net:[4026538124]')
    scope = PhysicalObservationScope('run', 'sha256:' + 'a' * 64,
        str(tmp_path / 'cg'), 1, 2, 812, 'bootB27:944',
        ('/workspace', '/home/fleet'), None, None, None)
    return proc, pid / 'mountinfo', scope


def _line(mount_id: int, target: str, root: str = '/') -> str:
    return f'{mount_id} 2 8:1 {root} {target} rw - ext4 /dev/sda1 rw\n'


def test_complete_visible_kernel_mount_roster_has_stable_identity(tmp_path):
    proc, mountinfo, scope = _fixture(tmp_path)
    mountinfo.write_text(_line(43, '/workspace') + _line(44, '/home/fleet', '/home'))
    a = linux._tracked_namespace_inventory(scope, proc)['tracked_mount_namespace']
    b = linux._tracked_namespace_inventory(scope, proc)['tracked_mount_namespace']
    assert a == b and a['state'] == 'PRESENT'
    assert 'visible_targets=2/2' in a['detail']
    assert 'target_mount_ids=[43, 44]' in a['detail']
    assert 'target_kernel_sha256=' in a['detail']
    assert 'foreign/detached mounts unverified' in a['detail']


def test_partial_original_mount_roster_is_unknown_not_present(tmp_path):
    proc, mountinfo, scope = _fixture(tmp_path)
    mountinfo.write_text(_line(43, '/workspace'))
    result = linux._tracked_namespace_inventory(scope, proc)['tracked_mount_namespace']
    assert result['state'] == 'UNKNOWN'
    assert 'visible_targets=1/2' in result['detail']
    assert 'target_mount_ids=[43]' in result['detail']


def test_missing_both_targets_is_only_scoped_non_detection(tmp_path):
    proc, mountinfo, scope = _fixture(tmp_path)
    mountinfo.write_text(_line(88, '/other'))
    result = linux._tracked_namespace_inventory(scope, proc)['tracked_mount_namespace']
    assert result['state'] == 'NOT_OBSERVED'
    assert 'visible_targets=0/2' in result['detail']


@pytest.mark.parametrize('mutation', ['stacked', 'invalid-device', 'duplicate-id', 'bad-escape'])
def test_mount_identity_ambiguity_fails_closed(tmp_path, mutation):
    proc, mountinfo, scope = _fixture(tmp_path)
    source = _line(43, '/workspace') + _line(44, '/home/fleet')
    if mutation == 'stacked':
        source += _line(45, '/workspace', '/other')
    elif mutation == 'invalid-device':
        source = source.replace('8:1', 'bad', 1)
    elif mutation == 'duplicate-id':
        source = source.replace('44 2', '43 2')
    else:
        source = source.replace('/workspace', r'/work\noops')
    mountinfo.write_text(source)
    result = linux._tracked_namespace_inventory(scope, proc)['tracked_mount_namespace']
    assert result['state'] == 'UNKNOWN', mutation


def test_pinned_mount_identity_change_across_scans_is_unknown(tmp_path, monkeypatch):
    proc, mountinfo, scope = _fixture(tmp_path)
    mountinfo.write_text(_line(43, '/workspace') + _line(44, '/home/fleet'))
    real = linux._tracked_namespace_inventory
    count = 0

    def raced(*args):
        nonlocal count
        count += 1
        if count == 2:
            mountinfo.write_text(_line(55, '/workspace') + _line(44, '/home/fleet'))
        return real(*args)

    monkeypatch.setattr(linux, '_tracked_namespace_inventory', raced)
    methods = {n: getattr(linux, n) for n in linux.PROBES}
    methods['_tracked_namespace_inventory'] = raced
    # Isolate other kernel probes: the same shared observer remains the only scanner.
    for key in methods:
        if key != '_tracked_namespace_inventory':
            methods[key] = lambda *_args: {'state': 'UNKNOWN', 'detail': 'fixture'}
    # tracked namespace yields TWO fact components; no second read path.
    facts = linux.observe_kernel(scope, proc_root=proc, diagnostic_probes=methods)
    assert facts.to_mapping()['tracked_mount_namespace']['state'] == 'UNKNOWN'
    assert facts.to_mapping()['tracked_mount_namespace']['detail'] == 'observation changed during read'


def test_gate_and_original_execution_authority_unmodified():
    assert _check_p1b27b2b3a_mount_coverage(PACKAGE) == []
    port = (PACKAGE / 'core/ports/physical_observation.py').read_text()
    assert 'physical_absence_proven: bool = False' in port
    assert 'teardown_proven: bool = False' in port
    assert 'effect_permission: str = "NONE"' in port


@pytest.mark.parametrize('mutation', ['missing-coverage', 'remove-stacked', 'remove-digest', 'dead-target-match'])
def test_x0_rejects_drift_in_original_shared_linux_probe(tmp_path, mutation):
    owner = tmp_path / 'integrations/linux_physical_observer.py'
    owner.parent.mkdir(parents=True)
    shutil.copy2(PACKAGE / 'integrations/linux_physical_observer.py', owner)
    text = owner.read_text()
    before, after = {
        'missing-coverage': ('ambiguous or (matched and missing)', 'ambiguous'),
        'remove-stacked': ('len(entries) != 1', 'len(entries) < 0'),
        'remove-digest': ('target_kernel_sha256=', 'target_hash='),
        'dead-target-match': ('if target in targets:', 'if False:'),
    }[mutation]
    assert before in text
    owner.write_text(text.replace(before, after, 1))
    assert _check_p1b27b2b3a_mount_coverage(tmp_path)

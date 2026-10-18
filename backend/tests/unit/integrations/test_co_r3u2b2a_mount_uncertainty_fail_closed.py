"""CO-R3U2B2A: mountinfo failure/false negative must not open original LACE effect path.

Hosted checks only, never proof of independently enforced kernel parent custody.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.integrations import _fleet_plan_cgroup_resources as cg
from oh_goon.integrations import _fleet_plan_nested_cgroup_launcher as nested


@pytest.mark.parametrize('classification', [False, None, 'error'])
@pytest.mark.parametrize('operation', [
    'kill', 'remove', 'limit', 'child', 'control', 'open', 'close', 'prefix', 'nested',
])
def test_no_original_effect_or_record_when_mount_classification_unreliable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    classification: bool | None | str, operation: str,
) -> None:
    root = tmp_path / 'root'
    root.mkdir()
    target = root / 'attempt'
    target.mkdir()
    (target / 'cpu.max').write_text('original', encoding='ascii')
    identity = cg._exact_cgroup_identity(target)
    state = tmp_path / 'lease.json'
    calls = {'classify': 0}

    def classify(_path: Path) -> bool:
        calls['classify'] += 1
        if classification == 'error':
            raise OSError('unreadable /proc/self/mountinfo')
        if classification is None:
            raise FileNotFoundError('/proc/self/mountinfo')
        return classification

    monkeypatch.setattr(cg, '_is_cgroup2_path', classify)
    write, rmdir, mkdir, pipe, popen, open_fd = (Mock() for _ in range(6))
    monkeypatch.setattr(cg.os, 'write', write)
    monkeypatch.setattr(cg.os, 'rmdir', rmdir)
    monkeypatch.setattr(cg.os, 'mkdir', mkdir)
    monkeypatch.setattr(nested.os, 'pipe', pipe)
    lease = cg.CgroupV2AttemptLease(
        root=root, cgroup=target, state_file=state,
        lock_path=tmp_path / 'lock', policy=SimpleNamespace(
            cpu_millis=1000, memory_bytes=None, max_processes=None,
        ), attempt_root=tmp_path, cgroup_identity=identity,
    )
    with pytest.raises(FleetPlanInvalid, match='NOT PROVEN'):
        if operation == 'kill':
            cg._kill_cgroup(target, expected_identity=identity)
        elif operation == 'remove':
            cg._remove_cgroup(target, expected_identity=identity)
        elif operation == 'limit':
            cg._apply_resource_limits(target, lease.policy, expected_identity=identity)
        elif operation == 'child':
            cg._mkdir_bound_cgroup_child(root, cg._exact_cgroup_identity(root), 'new-child')
        elif operation == 'control':
            cg._write_bound_cgroup_control(target, identity, 'cgroup.procs', b'1\n')
        elif operation == 'open':
            lease.open()
        elif operation == 'close':
            lease.close()
        elif operation == 'prefix':
            lease.command_prefix()
        elif operation == 'nested':
            nested.launch_nested_bubblewrap(
                target, root, ('bwrap',), nested_identity=identity,
                monitor_identity=cg._exact_cgroup_identity(root),
                move_process=Mock(), popen=popen,
            )
    assert calls['classify'] == 0  # no probing; classification is NOT permission
    for syscall in (write, rmdir, mkdir, pipe, popen, open_fd):
        syscall.assert_not_called()
    assert (target / 'cpu.max').read_text(encoding='ascii') == 'original'
    assert not state.exists()
    assert not (tmp_path / 'lock').exists()
    assert not (root / 'new-child').exists()

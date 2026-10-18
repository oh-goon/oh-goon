"""CO-R3U2B1: original LACE launch/effect cuts reject unprovisioned kernels.

The tmpfs controls here are hostile zero-effect regressions, NOT independent
Linux custody qualification. No locally monkeypatched permit is relied upon.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.integrations import _fleet_plan_cgroup_resources as cg
from oh_goon.integrations import _fleet_plan_nested_cgroup_launcher as nested
from oh_goon.integrations import _fleet_plan_restricted_egress as net
from tests.unit.integrations.test_co_r3u2a_original_network_native_custody import _lease


def test_s_launcher_cannot_attach_even_with_correct_original_dev_ino(tmp_path: Path) -> None:
    group = tmp_path / 'group'
    group.mkdir()
    procs = group / 'cgroup.procs'
    procs.write_text('', encoding='ascii')
    marker = tmp_path / 'ran'
    device, inode = cg._exact_cgroup_identity(group)
    proc = subprocess.run(
        (sys.executable, '-S', '-c', cg._CGROUP_EXEC, str(group), str(device), str(inode),
         sys.executable, '-S', '-c', f'open({str(marker)!r}, "w").write("ran")'),
        capture_output=True, text=True, check=False,
    )
    assert proc.returncode != 0
    assert 'independent kernel custody NOT PROVEN' in proc.stderr
    assert procs.read_text(encoding='ascii') == ''
    assert not marker.exists()


@pytest.mark.parametrize('nested_mode', [False, True])
def test_constructed_lease_cannot_release_native_launch_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, nested_mode: bool,
) -> None:
    root = tmp_path / 'root'; root.mkdir()
    attempt = root / 'attempt'; attempt.mkdir()
    identity = cg._exact_cgroup_identity(attempt)
    lease = cg.CgroupV2AttemptLease(
        root=root, cgroup=attempt, state_file=tmp_path/'record.json',
        lock_path=tmp_path/'lock', policy=SimpleNamespace(),
        attempt_root=tmp_path, cgroup_identity=identity,
        nested_delegation=(attempt/'nested-execution') if nested_mode else None,
        nested_monitor=(attempt/'nested-monitor') if nested_mode else None,
        nested_identity=identity if nested_mode else None,
        monitor_identity=identity if nested_mode else None,
    )
    monkeypatch.setattr(cg, '_is_cgroup2_path', lambda _: True)
    with pytest.raises(FleetPlanInvalid, match='native cgroup process launch'):
        lease.command_prefix()
    assert not (tmp_path/'lock').exists()
    assert not (tmp_path/'record.json').exists()


def test_nested_launcher_refuses_before_move_pipe_or_popen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempt = tmp_path/'attempt'; attempt.mkdir()
    monitor = tmp_path/'monitor'; monitor.mkdir()
    monkeypatch.setattr(cg, '_is_cgroup2_path', lambda _: True)
    move, popen, pipe = Mock(), Mock(), Mock()
    monkeypatch.setattr(nested.os, 'pipe', pipe)
    with pytest.raises(FleetPlanInvalid, match='native nested cgroup process launch'):
        nested.launch_nested_bubblewrap(
            attempt, monitor, ('bwrap', '--'),
            nested_identity=cg._exact_cgroup_identity(attempt),
            monitor_identity=cg._exact_cgroup_identity(monitor),
            move_process=move, popen=popen,
        )
    move.assert_not_called()
    popen.assert_not_called()
    pipe.assert_not_called()


def test_nested_monitor_is_independently_checked_before_any_process_move(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempt = tmp_path/'attempt'; attempt.mkdir()
    monitor = tmp_path/'monitor'; monitor.mkdir()
    # Even a false-negative mount classification cannot skip the first gate.
    monkeypatch.setattr(cg, '_is_cgroup2_path', lambda p: Path(p) == monitor)
    move, popen = Mock(), Mock()
    with pytest.raises(FleetPlanInvalid, match='native nested cgroup process launch'):
        nested.launch_nested_bubblewrap(
            attempt, monitor, ('bwrap', '--'),
            nested_identity=cg._exact_cgroup_identity(attempt),
            monitor_identity=cg._exact_cgroup_identity(monitor),
            move_process=move, popen=popen,
        )
    move.assert_not_called()
    popen.assert_not_called()


def test_direct_resource_limit_helper_refuses_before_kernel_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    group = tmp_path/'attempt'; group.mkdir()
    control = group/'cpu.max'; control.write_text('original', encoding='ascii')
    monkeypatch.setattr(cg, '_is_cgroup2_path', lambda _: True)
    write = Mock()
    monkeypatch.setattr(cg.os, 'write', write)
    policy = SimpleNamespace(cpu_millis=1000, memory_bytes=None, max_processes=None)
    with pytest.raises(FleetPlanInvalid, match='native cgroup limit write'):
        cg._apply_resource_limits(group, policy, expected_identity=cg._exact_cgroup_identity(group))
    write.assert_not_called()
    assert control.read_text(encoding='ascii') == 'original'
    with pytest.raises(FleetPlanInvalid, match='native cgroup direct limit write'):
        cg._write_limit(control, '200')
    assert control.read_text(encoding='ascii') == 'original'


def test_network_prefix_is_not_a_direct_namespace_execution_bypass(tmp_path: Path) -> None:
    lease = _lease(tmp_path)
    with pytest.raises(FleetPlanInvalid, match='native restricted-egress process launch'):
        lease.command_prefix()
    assert not lease.state_file.exists()
    assert not lease.rules_file.exists()
    assert not lease.lock_path.exists()

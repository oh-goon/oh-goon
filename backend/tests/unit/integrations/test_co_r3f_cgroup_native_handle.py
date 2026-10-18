"""CO-R3F: physical cgroup kill remains pinned to the ORIGINAL opened object.

Local cgroup operations only; no LACE writer-absence or release authority.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.integrations import _fleet_plan_cgroup_resources as cg


def _cgroup(tmp_path: Path) -> Path:
    group = tmp_path / ('attempt-' + 'a' * 16)
    group.mkdir()
    (group / 'cgroup.kill').write_text('', encoding='ascii')
    return group


def test_bound_kill_targets_exact_opened_kernel_directory(tmp_path: Path) -> None:
    group = _cgroup(tmp_path)
    cg._kill_cgroup(group, expected_identity=cg._exact_cgroup_identity(group))
    assert (group / 'cgroup.kill').read_text(encoding='ascii') == '1'


def test_stale_identity_rejected_before_native_effect(tmp_path: Path) -> None:
    group = _cgroup(tmp_path)
    original = cg._exact_cgroup_identity(group)
    group.rename(tmp_path / 'old')
    replacement = _cgroup(tmp_path)
    with pytest.raises(FleetPlanInvalid, match='identity replaced'):
        cg._kill_cgroup(group, expected_identity=original)
    assert (replacement / 'cgroup.kill').read_text() == ''


def test_missing_or_symlink_control_refuses_pid_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    group = _cgroup(tmp_path)
    identity = cg._exact_cgroup_identity(group)
    (group / 'cgroup.procs').write_text('123\n', encoding='ascii')
    (group / 'cgroup.kill').unlink()
    kill_calls: list[tuple[int, int]] = []
    monkeypatch.setattr(cg.os, 'kill', lambda pid, signo: kill_calls.append((pid, signo)))
    with pytest.raises(FleetPlanInvalid, match='refusing PID fallback'):
        cg._kill_cgroup(group, expected_identity=identity)
    external = tmp_path / 'external-kill'
    external.write_text('', encoding='ascii')
    (group / 'cgroup.kill').symlink_to(external)
    with pytest.raises(FleetPlanInvalid, match='refusing PID fallback'):
        cg._kill_cgroup(group, expected_identity=identity)
    assert external.read_text() == ''
    assert kill_calls == []


def test_replacement_between_directory_pin_and_control_write_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    group = _cgroup(tmp_path)
    identity = cg._exact_cgroup_identity(group)
    orig_open = os.open
    moved = tmp_path / 'moved'
    triggered = False

    def race_open(path: str | os.PathLike[str], flags: int, *args: object, **kwargs: object) -> int:
        nonlocal triggered
        if path == 'cgroup.kill' and 'dir_fd' in kwargs and not triggered:
            triggered = True
            group.rename(moved)
            _cgroup(tmp_path)  # foreign object at same pathname
        return orig_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(cg.os, 'open', race_open)
    with pytest.raises(FleetPlanInvalid, match='replaced'):
        cg._kill_cgroup(group, expected_identity=identity)
    assert triggered
    assert (group / 'cgroup.kill').read_text() == ''
    assert (moved / 'cgroup.kill').read_text() == ''


def test_replacement_at_actual_native_write_never_targets_foreign_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    group = _cgroup(tmp_path)
    identity = cg._exact_cgroup_identity(group)
    moved = tmp_path / 'moved'
    original_write = os.write
    triggered = False

    def race_write(fd: int, payload: bytes) -> int:
        nonlocal triggered
        if payload == b'1' and not triggered:
            triggered = True
            group.rename(moved)
            _cgroup(tmp_path)
        return original_write(fd, payload)

    monkeypatch.setattr(cg.os, 'write', race_write)
    cg._kill_cgroup(group, expected_identity=identity)
    assert triggered
    assert (moved / 'cgroup.kill').read_text() == '1'
    assert (group / 'cgroup.kill').read_text() == ''


def test_partial_native_write_refuses_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    group = _cgroup(tmp_path)
    monkeypatch.setattr(cg.os, 'write', lambda fd, payload: 0)
    with pytest.raises(FleetPlanInvalid, match='write incomplete'):
        cg._kill_cgroup(group, expected_identity=cg._exact_cgroup_identity(group))

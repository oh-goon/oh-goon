"""CO-R3K: fail-before-effects cgroup creation and bound rollback.

Real temporary files exercise native descriptor semantics, NOT cgroup-v2,
independent LACE writer absence, or authenticated parent mutation custody.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.fleet_plan_isolation import CicdResourcePolicy
from oh_goon.integrations import _fleet_plan_cgroup_resources as cg


def _lease(tmp_path: Path, policy: CicdResourcePolicy | None = None) -> cg.CgroupV2AttemptLease:
    root = tmp_path / 'delegated'; root.mkdir()
    state = tmp_path / 'state' / 'lease.json'
    return cg.CgroupV2AttemptLease(
        root=root, cgroup=root / ('attempt-' + 'f' * 16),
        state_file=state, lock_path=tmp_path / 'locks' / 'cgroup.lock',
        policy=policy or CicdResourcePolicy(), attempt_root=tmp_path / 'attempt',
    )


def _controls(group: Path) -> None:
    (group / 'cgroup.kill').write_text('', encoding='ascii')
    (group / 'cgroup.events').write_text('populated 0\n', encoding='ascii')
    (group / 'cgroup.procs').write_text('', encoding='ascii')
    for control in ('cpu.max', 'memory.max', 'pids.max'):
        (group / control).write_text('', encoding='ascii')


def test_original_lease_is_persisted_before_first_controller_effect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    lease = _lease(tmp_path, CicdResourcePolicy(cpu_millis=1000, memory_mib=64, max_processes=16))
    native_mkdir = Path.mkdir
    def setup(group: Path, *args: object, **kwargs: object) -> None:
        native_mkdir(group, *args, **kwargs)
        if group == lease.cgroup:
            _controls(group)
    monkeypatch.setattr(Path, 'mkdir', setup)
    original_apply = cg._apply_resource_limits
    saw = False
    def record(group: Path, policy: CicdResourcePolicy, *, expected_identity: tuple[int, int]) -> None:
        nonlocal saw
        body = json.loads(lease.state_file.read_text(encoding='utf-8'))
        assert (body['cgroup_dev'], body['cgroup_ino']) == expected_identity
        assert expected_identity == cg._exact_cgroup_identity(group)
        saw = True
        original_apply(group, policy, expected_identity=expected_identity)
    monkeypatch.setattr(cg, '_apply_resource_limits', record)
    lease.open()
    assert saw
    assert (lease.cgroup / 'cpu.max').read_text() == '100000 100000'
    assert (lease.cgroup / 'memory.max').read_text() == str(64 * 1024 * 1024)
    assert (lease.cgroup / 'pids.max').read_text() == '16'
    assert lease.state_file.exists()


def test_creation_identity_failure_never_attempts_unbound_kill_or_remove(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    lease = _lease(tmp_path)
    monkeypatch.setattr(cg, '_exact_cgroup_identity', lambda _: (_ for _ in ()).throw(
        FleetPlanInvalid('identity probe unavailable')))
    calls: list[str] = []
    monkeypatch.setattr(cg, '_kill_cgroup', lambda *a, **kw: calls.append('kill'))
    monkeypatch.setattr(cg, '_remove_cgroup', lambda *a, **kw: calls.append('remove'))
    with pytest.raises(FleetPlanInvalid, match='refusing unbound rollback'):
        lease.open()
    assert calls == []
    assert lease.cgroup.is_dir()
    pending = json.loads(lease.state_file.read_text())
    assert pending['phase'] == 'allocation_pending'
    assert 'cgroup_ino' not in pending


def test_failed_controller_effect_rolls_back_only_pinned_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    lease = _lease(tmp_path)
    native_mkdir = Path.mkdir
    def setup(group: Path, *args: object, **kwargs: object) -> None:
        native_mkdir(group, *args, **kwargs)
        if group == lease.cgroup:
            _controls(group)
    monkeypatch.setattr(Path, 'mkdir', setup)
    def fail(group: Path, policy: CicdResourcePolicy, *, expected_identity: tuple[int, int]) -> None:
        assert lease.state_file.exists()
        raise FleetPlanInvalid('injected controller effect failure')
    monkeypatch.setattr(cg, '_apply_resource_limits', fail)
    native_kill = cg._kill_cgroup
    native_remove = cg._remove_cgroup
    calls: list[str] = []
    def kill(group: Path, *, expected_identity: tuple[int, int]) -> None:
        assert expected_identity == lease.cgroup_identity
        calls.append('kill')
        native_kill(group, expected_identity=expected_identity)
    def remove(group: Path, *, expected_identity: tuple[int, int]) -> None:
        assert expected_identity == lease.cgroup_identity
        calls.append('remove')
        for child in group.iterdir():
            child.unlink()  # temp FS control fixture only; cgroupfs uses kernel files
        native_remove(group, expected_identity=expected_identity)
    monkeypatch.setattr(cg, '_kill_cgroup', kill)
    monkeypatch.setattr(cg, '_remove_cgroup', remove)
    with pytest.raises(FleetPlanInvalid, match='injected controller effect failure'):
        lease.open()
    assert calls == ['kill', 'remove']
    assert not lease.cgroup.exists()
    assert not lease.state_file.exists()


def test_replaced_path_during_initialization_retains_record_and_foreign_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    lease = _lease(tmp_path)
    original = tmp_path / 'original'
    foreign = 'unmodified'
    def substitute(group: Path, policy: CicdResourcePolicy, *, expected_identity: tuple[int, int]) -> None:
        group.rename(original)
        group.mkdir()
        (group / 'foreign').write_text(foreign, encoding='ascii')
        raise FleetPlanInvalid('simulated interrupted allocation')
    monkeypatch.setattr(cg, '_apply_resource_limits', substitute)
    kill: list[str] = []
    remove: list[str] = []
    monkeypatch.setattr(cg, '_kill_cgroup', lambda *a, **kw: kill.append('kill'))
    monkeypatch.setattr(cg, '_remove_cgroup', lambda *a, **kw: remove.append('remove'))
    with pytest.raises(FleetPlanInvalid, match='rollback unproven'):
        lease.open()
    assert kill == [] and remove == []
    assert (lease.cgroup / 'foreign').read_text() == foreign
    assert original.is_dir()
    record = json.loads(lease.state_file.read_text(encoding='utf-8'))
    assert (record['cgroup_dev'], record['cgroup_ino']) == lease.cgroup_identity
    assert cg._exact_cgroup_identity(lease.cgroup) != lease.cgroup_identity


def test_failed_native_rollback_retains_original_recovery_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    lease = _lease(tmp_path)
    def fail(group: Path, policy: CicdResourcePolicy, *, expected_identity: tuple[int, int]) -> None:
        raise FleetPlanInvalid('effect failed')
    monkeypatch.setattr(cg, '_apply_resource_limits', fail)
    monkeypatch.setattr(cg, '_kill_cgroup', lambda *args, **kwargs: (_ for _ in ()).throw(
        FleetPlanInvalid('native kill uncertain')))
    with pytest.raises(FleetPlanInvalid, match='rollback unproven'):
        lease.open()
    assert lease.cgroup.is_dir()
    assert lease.state_file.exists()
    assert json.loads(lease.state_file.read_text())['cgroup_ino'] == lease.cgroup_identity[1]


def test_bound_limit_rejects_foreign_path_after_directory_pin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    group = tmp_path / 'attempt'; group.mkdir()
    (group / 'cpu.max').write_text('old', encoding='ascii')
    identity = cg._exact_cgroup_identity(group)
    old = tmp_path / 'old'
    original_open = os.open
    def substitute(name: str | os.PathLike[str], flags: int, *args: object, **kwargs: object) -> int:
        if name == 'cpu.max' and 'dir_fd' in kwargs and not old.exists():
            group.rename(old)
            group.mkdir()
            (group / 'cpu.max').write_text('foreign', encoding='ascii')
        return original_open(name, flags, *args, **kwargs)
    monkeypatch.setattr(cg.os, 'open', substitute)
    with pytest.raises(FleetPlanInvalid, match='replaced'):
        cg._apply_resource_limits(
            group, CicdResourcePolicy(cpu_millis=500), expected_identity=identity,
        )
    assert (old / 'cpu.max').read_text() == 'old'
    assert (group / 'cpu.max').read_text() == 'foreign'


def test_bound_limit_refuses_symlink_and_short_kernel_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    group = tmp_path / 'attempt'; group.mkdir()
    outside = tmp_path / 'outside'; outside.write_text('safe', encoding='ascii')
    (group / 'cpu.max').symlink_to(outside)
    identity = cg._exact_cgroup_identity(group)
    with pytest.raises(FleetPlanInvalid, match='limit cpu.max unavailable'):
        cg._apply_resource_limits(
            group, CicdResourcePolicy(cpu_millis=500), expected_identity=identity,
        )
    assert outside.read_text() == 'safe'
    (group / 'cpu.max').unlink()
    (group / 'cpu.max').write_text('', encoding='ascii')
    monkeypatch.setattr(cg.os, 'write', lambda fd, payload: 0)
    with pytest.raises(FleetPlanInvalid, match='write incomplete'):
        cg._apply_resource_limits(
            group, CicdResourcePolicy(cpu_millis=500), expected_identity=identity,
        )

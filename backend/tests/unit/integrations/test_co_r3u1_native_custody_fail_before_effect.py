"""CO-R3U1 native custody is not an in-process permission or hosted fake PASS."""
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.original_native_custody import OriginalNativeCustodyRequirement
from oh_goon.integrations import _fleet_plan_cgroup_resources as cgroup


def scope(**changes: object) -> OriginalNativeCustodyRequirement:
    fields = dict(run_id='run-a', step_id='step-a', control_epoch=14,
                  executor_generation='executor-g4', attempt_identity='sha256:attempt',
                  parent_device=6, parent_inode=7, resource_device=6, resource_inode=8)
    fields.update(changes)
    return OriginalNativeCustodyRequirement(**fields)


def test_immutable_complete_claim_never_confers_kernel_parent_custody() -> None:
    claim = scope()
    assert claim.complete_scope
    with pytest.raises(FrozenInstanceError):
        claim.control_epoch = 20  # type: ignore[misc]
    with pytest.raises(FleetPlanInvalid, match='NOT PROVEN'):
        cgroup.require_independent_native_parent_custody(
            operation='original journal orphan teardown', requirement=claim,
        )


@pytest.mark.parametrize('change', [
    {'control_epoch': True}, {'control_epoch': -1}, {'executor_generation': ''},
    {'run_id': ''}, {'step_id': ''}, {'parent_inode': 0},
    {'resource_device': False},
])
def test_invalid_immutable_scope_cannot_be_transported(change: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        scope(**change)


def test_spoofed_dict_or_local_receipt_is_not_kernel_capability() -> None:
    with pytest.raises(FleetPlanInvalid, match='type invalid'):
        cgroup.require_independent_native_parent_custody(
            operation='native cgroup removal', requirement={'trusted': True},
        )
    assert not scope(executor_generation=None, parent_inode=None).complete_scope
    with pytest.raises(FleetPlanInvalid, match='NOT PROVEN'):
        cgroup.require_independent_native_parent_custody(
            operation='native cgroup removal', requirement=scope(executor_generation=None),
        )


def test_native_admission_rejects_before_allocation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / 'native-root'
    root.mkdir()
    state = tmp_path / 'records'; state.mkdir()
    host = SimpleNamespace(cgroup_root=root, cgroup_state_dir=state,
                           cgroup_lock=tmp_path / 'lock')
    monkeypatch.setattr(cgroup, 'require_cgroup_v2_host', lambda *_args, **_kwargs: None)
    policy = SimpleNamespace(cpu_millis=None, memory_mib=None, max_processes=None)
    with pytest.raises(FleetPlanInvalid, match='native cgroup admission'):
        cgroup.CgroupV2AttemptLease.create(host, tmp_path / 'attempt', policy)
    assert not list(root.iterdir()) and not list(state.iterdir())
    assert not host.cgroup_lock.exists()


@pytest.mark.parametrize('operation', ['native cgroup kill', 'native cgroup removal'])
def test_real_kernel_cgroup_effect_fails_before_syscall(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str,
) -> None:
    target = tmp_path / 'attempt-a'; target.mkdir()
    identity = cgroup._exact_cgroup_identity(target)
    monkeypatch.setattr(cgroup, '_is_cgroup2_path', lambda _path: True)
    native_write, native_rmdir = Mock(), Mock()
    monkeypatch.setattr(cgroup.os, 'write', native_write)
    monkeypatch.setattr(cgroup.os, 'rmdir', native_rmdir)
    with pytest.raises(FleetPlanInvalid, match='NOT PROVEN'):
        if operation.endswith('kill'):
            cgroup._kill_cgroup(target, expected_identity=identity)
        else:
            cgroup._remove_cgroup(target, expected_identity=identity)
    native_write.assert_not_called()
    native_rmdir.assert_not_called()
    assert target.is_dir()


def test_existing_lease_native_close_fails_before_kill_or_record_unlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / 'attempt-b'; target.mkdir()
    state = tmp_path / 'record.json'; state.write_text('durable')
    lease = cgroup.CgroupV2AttemptLease(
        root=tmp_path, cgroup=target, state_file=state, lock_path=tmp_path / 'lock',
        policy=SimpleNamespace(), attempt_root=tmp_path,
        cgroup_identity=cgroup._exact_cgroup_identity(target),
    )
    monkeypatch.setattr(cgroup, '_is_cgroup2_path', lambda _path: True)
    kill, remove = Mock(), Mock()
    monkeypatch.setattr(cgroup, '_kill_cgroup', kill)
    monkeypatch.setattr(cgroup, '_remove_cgroup', remove)
    with pytest.raises(FleetPlanInvalid, match='native cgroup teardown'):
        lease.close()
    kill.assert_not_called()
    remove.assert_not_called()
    assert state.read_text() == 'durable'


def test_constructed_lease_cannot_bypass_native_admission_with_direct_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / 'root'; root.mkdir()
    target = root / 'attempt-c'
    state = tmp_path / 'record.json'
    lease = cgroup.CgroupV2AttemptLease(
        root=root, cgroup=target, state_file=state, lock_path=tmp_path / 'lock',
        policy=SimpleNamespace(), attempt_root=tmp_path,
    )
    monkeypatch.setattr(cgroup, '_is_cgroup2_path', lambda _path: True)
    with pytest.raises(FleetPlanInvalid, match='native cgroup allocation'):
        lease.open()
    assert not target.exists() and not state.exists()
    assert not (tmp_path / 'lock').exists()


@pytest.mark.parametrize('kind', ['delegated child', 'delegated control'])
def test_direct_delegation_helper_cannot_write_native_kernel_before_custody(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str,
) -> None:
    root = tmp_path / 'root'; root.mkdir()
    identity = cgroup._exact_cgroup_identity(root)
    monkeypatch.setattr(cgroup, '_is_cgroup2_path', lambda _path: True)
    mkdir, write = Mock(), Mock()
    monkeypatch.setattr(cgroup.os, 'mkdir', mkdir)
    monkeypatch.setattr(cgroup.os, 'write', write)
    with pytest.raises(FleetPlanInvalid, match='NOT PROVEN'):
        if kind == 'delegated child':
            cgroup._mkdir_bound_cgroup_child(root, identity, 'nested')
        else:
            cgroup._write_bound_cgroup_control(root, identity, 'cgroup.procs', b'1\n')
    mkdir.assert_not_called()
    write.assert_not_called()

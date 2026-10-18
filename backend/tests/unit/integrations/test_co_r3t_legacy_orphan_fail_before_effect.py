"""CO-R3T: dead PID, resource name and local inode are NOT physical custody."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.integrations import _fleet_plan_cgroup_resources as cgroup
from oh_goon.integrations import _fleet_plan_restricted_egress as network
from tests.unit.integrations.test_b12_egress_teardown_truth import _lease as network_lease
from tests.unit.integrations.test_co_r3e_durable_cgroup_recovery import _stale as cgroup_lease


@pytest.mark.parametrize('intruder', ['matching', 'absent', 'swapped', 'caller-claimed-proof'])
def test_legacy_cgroup_pid_cannot_authorize_destructive_cleanup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, intruder: str) -> None:
    root, target, state, recovery = cgroup_lease(tmp_path)
    original = target.stat()
    if intruder == 'absent':
        (target / 'cgroup.events').unlink()
        (target / 'cgroup.procs').unlink()
        target.rmdir()
    elif intruder == 'swapped':
        target.rename(tmp_path / 'original')
        target.mkdir()
    elif intruder == 'caller-claimed-proof':
        row = json.loads(recovery.read_text())
        row['physical_custody'] = {'claimed': True, 'epoch': 9223372036854775807}
        row['cgroup_dev'], row['cgroup_ino'] = original.st_dev, original.st_ino
        recovery.write_text(json.dumps(row), encoding='utf-8')
    monkeypatch.setattr(cgroup, '_process_identity', lambda _pid: None)
    kill, remove, unlink = Mock(), Mock(), Mock()
    monkeypatch.setattr(cgroup, '_kill_cgroup', kill)
    monkeypatch.setattr(cgroup, '_remove_cgroup', remove)
    monkeypatch.setattr(cgroup, 'remove_file_durably', unlink)
    with pytest.raises(FleetPlanInvalid, match='(physical custody|replaced)'):
        cgroup._reconcile_stale_cgroups(root, state)
    kill.assert_not_called()
    remove.assert_not_called()
    unlink.assert_not_called()
    assert recovery.exists()
    if intruder == 'swapped':
        assert (tmp_path / 'original').exists() and target.exists()


@pytest.mark.parametrize('claim', [None, {'epoch': 1}, {'kernel_attested': True, 'run_id': 'foreign'}])
def test_legacy_network_pid_cannot_delete_foreign_resources(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, claim: dict | None) -> None:
    lease = network_lease(tmp_path)
    if claim is not None:
        row = json.loads(lease.state_file.read_text())
        row['physical_custody'] = claim
        lease.state_file.write_text(json.dumps(row), encoding='utf-8')
    monkeypatch.setattr(network, '_process_identity', lambda _pid: None)
    cleanup, unlink, native = Mock(), Mock(), Mock()
    monkeypatch.setattr(network, '_cleanup_egress_resources', cleanup)
    monkeypatch.setattr(network, 'remove_file_durably', unlink)
    monkeypatch.setattr(network, 'run_command', native)
    with pytest.raises(FleetPlanInvalid, match='physical custody'):
        network._reconcile_stale_egress_leases(lease)
    cleanup.assert_not_called()
    unlink.assert_not_called()
    native.assert_not_called()
    assert lease.state_file.exists()


def test_original_live_attempt_and_explicit_active_native_teardown_remain_distinct(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lease = network_lease(tmp_path)
    monkeypatch.setattr(network, '_process_identity', lambda _pid: 'old-boot:123')
    native = Mock()
    monkeypatch.setattr(network, '_cleanup_egress_resources', native)
    network._reconcile_stale_egress_leases(lease)
    native.assert_not_called()
    assert lease.state_file.exists()

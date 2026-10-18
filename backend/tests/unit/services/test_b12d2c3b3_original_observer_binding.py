"""B12D2C3B3: a recovery hook cannot substitute native executable or resource paths."""
from __future__ import annotations

from dataclasses import replace

import pytest

from oh_goon.runtime.enforcer_lace_journal import CausalJournalError
from oh_goon.integrations.fleet_plan_bubblewrap_isolation import BubblewrapProcessIsolation
from oh_goon.services.fleet_local_lace_takeover import OriginalLocalLaceTakeoverCoordinator
from tests.unit.runtime.test_b12d2c3b_takeover_recovery import setup_takeover
from tests.unit.services.test_b12d2c3b2_production_recovery_entrypoint import (
    coordinator, production_session,
)


def test_exact_backend_native_paths_allow_original_read_only_recovery(monkeypatch, tmp_path):
    conn, journal, owner = setup_takeover(tmp_path)
    execution, _session, _leases, native, ticket, *_ = production_session(
        monkeypatch, journal, owner, tmp_path
    )
    claim, receipt = execution.resume_local_lace_takeover(
        ticket, coordinator(tmp_path), intent_run_seq=2
    )
    assert receipt == claim + 1
    assert len(native) == 1
    assert journal.verify('run-one', auth=owner.authenticator)[-1]['payload']['cleanup_permitted'] is False
    conn.close()


@pytest.mark.parametrize('path', [
    'cgroup_root', 'cgroup_state_dir', 'cgroup_lock',
    'network_state_dir', 'network_lock', 'ip', 'nft',
])
def test_modified_native_observer_path_rejects_before_recovery_claim(monkeypatch, tmp_path, path):
    conn, journal, owner = setup_takeover(tmp_path)
    execution, _session, _leases, native, ticket, *_ = production_session(
        monkeypatch, journal, owner, tmp_path
    )
    before = journal.verify('run-one', auth=owner.authenticator)
    candidate = coordinator(tmp_path)
    new_value = '/usr/bin/false' if path in {'ip', 'nft'} else tmp_path / 'foreign'
    candidate = replace(candidate, **{path: new_value})
    with pytest.raises(CausalJournalError, match='differs from admitted executor inventory'):
        execution.resume_local_lace_takeover(ticket, candidate, intent_run_seq=2)
    assert native == []
    assert journal.verify('run-one', auth=owner.authenticator) == before
    conn.close()


def test_only_original_configured_backend_can_supply_native_recovery_paths(tmp_path):
    candidate = coordinator(tmp_path)
    with pytest.raises(CausalJournalError, match='original mandatory-intent'):
        candidate.require_original_backend(object())
    backend = BubblewrapProcessIsolation(
        executable='/usr/bin/true', ip_executable='/usr/bin/true', nft_executable='/usr/bin/true',
        cgroup_root=tmp_path / 'cg', cgroup_state_dir=tmp_path / 'cg-state',
        cgroup_lock=tmp_path / 'cg.lock', egress_state_dir=tmp_path / 'net-state',
        egress_lock=tmp_path / 'net.lock', require_local_resource_intent=False,
    )
    with pytest.raises(CausalJournalError, match='original mandatory-intent'):
        candidate.require_original_backend(backend)
    backend.require_local_resource_intent = True
    backend.ip_executable = 'ip'  # PATH lookup is not acceptable deployment custody
    with pytest.raises(CausalJournalError, match='differs from admitted'):
        candidate.require_original_backend(backend)

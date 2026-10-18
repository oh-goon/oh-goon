"""Original native path is invoked only after authenticated renewed claim."""
from __future__ import annotations

from pathlib import Path
from dataclasses import replace

import pytest

from oh_goon.services.fleet_local_lace_takeover import OriginalLocalLaceTakeoverCoordinator
from oh_goon.core.ports.local_lace_crash_reconciliation import LocalLaceCrashObservation
from oh_goon.runtime.enforcer_lace_journal import CausalJournalError
from tests.unit.runtime.test_b12d2c3b_takeover_recovery import setup_takeover
from tests.unit.core.test_enforcer_lace_trust import authenticator


def coordinator(tmp_path):
    return OriginalLocalLaceTakeoverCoordinator(
        tmp_path / "cgroup", tmp_path / "cgroup-state", tmp_path / "cgroup.lock",
        tmp_path / "network-state", tmp_path / "network.lock", "/usr/bin/true", "/usr/bin/true")


def trusted_backend(tmp_path):
    from oh_goon.integrations.fleet_plan_bubblewrap_isolation import BubblewrapProcessIsolation
    return BubblewrapProcessIsolation(
        executable="/usr/bin/true", ip_executable="/usr/bin/true", nft_executable="/usr/bin/true",
        cgroup_root=tmp_path / "cgroup", cgroup_state_dir=tmp_path / "cgroup-state",
        cgroup_lock=tmp_path / "cgroup.lock", egress_state_dir=tmp_path / "network-state",
        egress_lock=tmp_path / "network.lock", require_local_resource_intent=True,
    )


def test_original_native_composition_passes_renewed_subject_only(monkeypatch, tmp_path):
    conn, journal, owner = setup_takeover(tmp_path)
    calls = []
    def native(subject, **kwargs):
        events = journal.verify("run-one", auth=authenticator())
        assert events[-1]["kind"] == "RESOURCE_RECOVERY_CLAIMED"
        assert subject.control_epoch == 2 and subject.claim_run_seq == events[-1]["run_seq"]
        assert kwargs["cgroup_root"] == tmp_path / "cgroup"
        calls.append("scan")
        return LocalLaceCrashObservation(subject.subject_identity, "UNRESOLVED")
    monkeypatch.setattr("oh_goon.services.fleet_local_lace_takeover.observe_native_crash", native)
    receipts = coordinator(tmp_path).resume(owner, intent_run_seq=2, original_backend=trusted_backend(tmp_path))
    assert len(calls) == 1
    assert receipts[1] == journal.verify("run-one", auth=authenticator())[-1]["run_seq"]
    assert journal.verify("run-one", auth=authenticator())[-1]["payload"]["teardown_proven"] is False
    conn.close()


@pytest.mark.parametrize("attack", ["no-owner", "relative-root", "bad-binary", "cancelled"])
def test_invalid_composition_rejects_before_native_scan(monkeypatch, tmp_path, attack):
    conn, journal, owner = setup_takeover(tmp_path)
    if attack == "no-owner": owner = object()
    if attack == "relative-root": configuration = replace(coordinator(tmp_path), cgroup_root=Path("relative"))
    elif attack == "bad-binary": configuration = replace(coordinator(tmp_path), ip="/tmp/foreign")
    else: configuration = coordinator(tmp_path)
    if attack == "cancelled": owner.session.cancelled = True
    calls = []
    monkeypatch.setattr("oh_goon.services.fleet_local_lace_takeover.observe_native_crash",
                        lambda *a, **k: calls.append(1))
    before = journal.verify("run-one", auth=authenticator())
    with pytest.raises(CausalJournalError):
        configuration.resume(owner, intent_run_seq=2, original_backend=trusted_backend(tmp_path))
    assert not calls and before == journal.verify("run-one", auth=authenticator())
    conn.close()

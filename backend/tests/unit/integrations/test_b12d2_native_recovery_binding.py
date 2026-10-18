"""B12D2A: native orphan ownership is tagged before physical effects.

Never authorize deletion from a process liveness probe for journal-owned leases.
"""

from __future__ import annotations

import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.ports.local_lace_intent import LocalLaceIntentProof
from oh_goon.integrations import _fleet_plan_cgroup_resources as cg
from oh_goon.integrations import _fleet_plan_restricted_egress as net
from oh_goon.integrations import fleet_plan_local_lace_allocation as collector
from oh_goon.integrations.fleet_plan_local_lace_recovery import lace_resource_binding
from tests.unit.integrations.test_b12_egress_teardown_truth import _lease as egress_lease
from tests.unit.integrations.test_b12d1_native_allocation_observer import _cgroup, _observed_request


def _proof():
    return LocalLaceIntentProof("run-one", "step-1", 1, 2, "sha256:" + "d" * 64)


def test_lace_recovery_native_record_survives_crash_and_refuses_unfenced_delete(
    tmp_path, monkeypatch
):
    root = tmp_path / "root"
    root.mkdir()
    state_dir = tmp_path / "cgstate"
    state_dir.mkdir()
    name = "attempt-" + "a" * 16
    resource = root / name
    resource.mkdir()
    record = state_dir / "a.json"
    record.write_text(
        json.dumps(
            {
                "pid": 99999,
                "process_identity": "dead-birth",
                "cgroup": name,
                "lace_intent": lace_resource_binding(_proof()),
            }
        )
    )
    resource_kill = Mock()
    monkeypatch.setattr(cg, "_process_identity", lambda _pid: None)
    monkeypatch.setattr(cg, "_kill_cgroup", resource_kill)
    with pytest.raises(FleetPlanInvalid, match="original journal recovery"):
        cg._reconcile_stale_cgroups(root, state_dir)
    resource_kill.assert_not_called()
    assert record.exists() and resource.exists()

    lease = egress_lease(tmp_path)
    native_cleanup = Mock()
    raw = json.loads(lease.state_file.read_text())
    raw["lace_intent"] = lace_resource_binding(_proof())
    lease.state_file.write_text(json.dumps(raw))
    monkeypatch.setattr(net, "_process_identity", lambda _pid: None)
    monkeypatch.setattr(net, "_cleanup_egress_resources", native_cleanup)
    with pytest.raises(FleetPlanInvalid, match="original journal recovery"):
        net._reconcile_stale_egress_leases(lease)
    native_cleanup.assert_not_called()
    assert lease.state_file.exists()


def test_live_lace_lease_is_not_scavenged_by_parallel_resource_creator(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    state_dir = tmp_path / "cgstate"
    state_dir.mkdir()
    name = "attempt-" + "a" * 16
    (root / name).mkdir()
    (state_dir / "a.json").write_text(
        json.dumps(
            {
                "pid": 22,
                "process_identity": "active",
                "cgroup": name,
                "lace_intent": lace_resource_binding(_proof()),
            }
        )
    )
    monkeypatch.setattr(cg, "_process_identity", lambda _pid: "active")
    cg._reconcile_stale_cgroups(root, state_dir)


def test_original_cgroup_and_network_records_write_same_journal_identity(tmp_path, monkeypatch):
    # Existing transport test simulates post-provisioning; not a native custody grant.
    monkeypatch.setattr(net, "require_independent_native_parent_custody", lambda **_kwargs: None)
    binding = lace_resource_binding(_proof())
    original = tmp_path / "cg"
    original.mkdir()
    state = tmp_path / "cgstate" / "c.json"
    state.parent.mkdir()
    policy = SimpleNamespace()
    lease = cg.CgroupV2AttemptLease(
        root=original,
        cgroup=original,
        state_file=state,
        lock_path=tmp_path / "cg.lock",
        policy=policy,
        attempt_root=tmp_path / "attempt",
        lace_intent=binding,
        cgroup_identity=cg._exact_cgroup_identity(original),
    )
    monkeypatch.setattr(cg, "_process_identity", lambda pid: "boot:1")
    cg._write_resource_lease(lease)
    recorded = json.loads(state.read_text())
    assert recorded["lace_intent"] == binding
    network = egress_lease(tmp_path)
    network.lace_intent = binding
    monkeypatch.setattr(net, "_process_identity", lambda pid: "boot:1")
    net._write_egress_lease_record(network)
    assert json.loads(network.state_file.read_text())["lace_intent"] == binding


def test_native_observer_rejects_changed_recovery_identity(tmp_path, monkeypatch):
    cgroup = _cgroup(tmp_path, monkeypatch)
    original = json.loads(cgroup.state_file.read_text())
    original["lace_intent"] = lace_resource_binding(_proof())
    cgroup.state_file.write_text(json.dumps(original))
    cgroup.state_file.chmod(0o600)
    result = collector.observe_local_allocation(
        cgroup, None, _observed_request(tmp_path), proof=_proof()
    )
    assert result.cgroup_identity.startswith("sha256:")
    original["lace_intent"]["intent_run_seq"] = 88
    cgroup.state_file.write_text(json.dumps(original))
    cgroup.state_file.chmod(0o600)
    with pytest.raises(FleetPlanInvalid, match="does not bind original LACE journal"):
        collector.observe_local_allocation(
            cgroup, None, _observed_request(tmp_path), proof=_proof()
        )
    cgroup.state_file.write_text(
        json.dumps({k: v for k, v in original.items() if k != "lace_intent"})
    )
    with pytest.raises(FleetPlanInvalid, match="differs from live executor"):
        collector.observe_local_allocation(
            cgroup, None, _observed_request(tmp_path), proof=_proof()
        )


@pytest.mark.parametrize("attack", ["bad-type", "bad-digest", "no-intent-sequence"])
def test_bad_transport_identity_does_not_authorize_any_native_record(attack):
    proof = _proof()
    if attack == "bad-type":
        proof = object()
    if attack == "bad-digest":
        proof = replace(proof, attempt_identity="foreign")
    if attack == "no-intent-sequence":
        proof = replace(proof, run_seq=1)
    with pytest.raises(FleetPlanInvalid, match="original committed LACE identity"):
        lace_resource_binding(proof)


def test_production_bubblewrap_tags_both_native_leases_before_open(tmp_path, monkeypatch):
    """No physical owner may be created without the same original journal identity."""
    from oh_goon.core.errors import FleetPlanInvalid
    from oh_goon.integrations import fleet_plan_bubblewrap_isolation as owner
    from tests.unit.core.test_enforcer_lace_trust import authenticator
    from tests.unit.integrations.test_b12c_live_prework_resource_bridge import (
        _backend,
        _permit,
        _request,
    )

    backend, _unused = _backend(monkeypatch)
    request = _request(tmp_path)
    conn, journal, permit = _permit(tmp_path, request, backend)
    request.local_resource_intent = permit
    order = []
    cgroup = SimpleNamespace(lace_intent=None, close=Mock())
    network = SimpleNamespace(lace_intent=None, close=Mock())
    expected = {
        "run_id": "run-one",
        "step_id": "step-1",
        "intent_run_seq": 2,
        "attempt_identity": None,
    }

    def cgroup_open():
        order.append("cgroup")
        assert cgroup.lace_intent is not None
        expected["attempt_identity"] = cgroup.lace_intent["attempt_identity"]
        assert cgroup.lace_intent == expected
        raise FleetPlanInvalid("physical creation cancelled")

    cgroup.open = cgroup_open
    network.open = Mock()
    monkeypatch.setattr(owner.CgroupV2AttemptLease, "create", lambda *a, **k: cgroup)
    monkeypatch.setattr(owner.RestrictedEgressLease, "create", lambda *a, **k: network)
    with pytest.raises(FleetPlanInvalid, match="physical creation cancelled"):
        backend.execute(request, timeout=1, cancel_requested=None)
    assert order == ["cgroup"]
    assert network.lace_intent == expected
    network.open.assert_not_called()
    assert [e["kind"] for e in journal.verify("run-one", auth=authenticator())] == [
        "RUN_ADMITTED",
        "RESOURCE_INTENT",
    ]
    conn.close()


def test_egress_precreate_recovery_rejection_never_tears_down_foreign_lease(tmp_path, monkeypatch):
    from tests.unit.integrations.test_b12_egress_teardown_truth import _lease

    lease = _lease(tmp_path)
    before = lease.state_file.read_bytes()
    cleanup = Mock()
    monkeypatch.setattr(net.RestrictedEgressLease, "close", lambda _self: cleanup())
    # Test only the already-custody-approved recovery branch with a simulated
    # future host. Unqualified native refusal is separately regression tested.
    monkeypatch.setattr(net, "require_independent_native_parent_custody", lambda **_kwargs: None)

    def deny_before_creation():
        raise FleetPlanInvalid("orphaned LACE lease must be reconciled by journal")

    monkeypatch.setattr(
        net, "_reconcile_stale_egress_leases", lambda _lease: deny_before_creation()
    )
    with pytest.raises(FleetPlanInvalid, match="orphaned LACE"):
        lease.open(cancel_requested=None)
    cleanup.assert_not_called()
    assert lease.state_file.read_bytes() == before
    assert lease._owned_record_written is False

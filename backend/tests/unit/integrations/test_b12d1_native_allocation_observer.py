"""B12D1: recoverable physical owners must be native-observed, never asserted."""
from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.integrations import fleet_plan_local_lace_allocation as collector
from oh_goon.integrations import fleet_plan_bubblewrap_isolation as owner
from tests.unit.integrations.test_b12c_live_prework_resource_bridge import _backend, _permit, _request
from tests.unit.runtime.test_b12d1_native_allocation_journal import observation
from tests.unit.core.test_enforcer_lace_trust import authenticator


def _observed_request(tmp_path):
    request = _request(tmp_path)
    request.attempt.egress.mode = "none"
    return request



def _cgroup(tmp_path: Path, monkeypatch):
    path = tmp_path / "cgroup" / "attempt-0123456789abcdef"
    path.mkdir(parents=True)
    (path / "cgroup.procs").write_text("")
    (path / "cgroup.events").write_text("populated 0\n")
    state = tmp_path / "state" / "cgroup.json"
    state.parent.mkdir()
    monkeypatch.setattr(collector, "_is_cgroup2_path", lambda _: True)
    monkeypatch.setattr(collector, "_process_identity", lambda _pid: "boot:42")
    state.write_text(json.dumps({"pid": os.getpid(), "process_identity": "boot:42", "cgroup": path.name}))
    state.chmod(0o600)
    return SimpleNamespace(cgroup=path, state_file=state)


def test_original_cgroup_record_and_native_cgroup_are_both_required(tmp_path, monkeypatch):
    lease = _cgroup(tmp_path, monkeypatch)
    proof = collector.observe_local_allocation(lease, None, _observed_request(tmp_path))
    assert proof.cgroup_path == str(lease.cgroup)
    assert proof.network_kind == "not-allocated"
    lease.state_file.write_text("{}")
    with pytest.raises(FleetPlanInvalid, match="differs from live executor"):
        collector.observe_local_allocation(lease, None, _observed_request(tmp_path))
    lease.state_file.unlink()
    with pytest.raises(FleetPlanInvalid, match="unavailable"):
        collector.observe_local_allocation(lease, None, _observed_request(tmp_path))
    (lease.cgroup / "cgroup.procs").unlink()
    with pytest.raises(FleetPlanInvalid, match="actual cgroup"):
        collector.observe_local_allocation(lease, None, _observed_request(tmp_path))


@pytest.mark.parametrize("kind", ["netns", "link", "nft", "malformed", "nonzero", "foreign-record"])
def test_native_network_observation_fails_closed(tmp_path, monkeypatch, kind):
    lease = _cgroup(tmp_path, monkeypatch)
    state = tmp_path / "state" / "network.json"
    expected = {"pid": os.getpid(), "process_identity": "boot:42",
                "namespace": "uvf0123456789", "host_veth": "ufh0123456789", "table": "uvf_0123456789"}
    if kind == "foreign-record":
        expected["namespace"] = "foreign"
    state.write_text(json.dumps(expected)); state.chmod(0o600)
    network = SimpleNamespace(state_file=state, ip="/sbin/ip", nft="/sbin/nft",
                              namespace="uvf0123456789", host_veth="ufh0123456789", table="uvf_0123456789")
    def native(binary, *args, **kwargs):
        if kind == "nonzero":
            return SimpleNamespace(returncode=1, stdout="")
        if kind == "malformed":
            return SimpleNamespace(returncode=0, stdout="not-json")
        if "netns" in args:
            data = [{"name": "foreign" if kind == "netns" else network.namespace}]
        elif "link" in args:
            data = [{"ifname": "foreign" if kind == "link" else network.host_veth}]
        else:
            data = {"nftables": [{"table": {"family": "inet", "name": "foreign" if kind == "nft" else network.table}}]}
        return SimpleNamespace(returncode=0, stdout=json.dumps(data))
    monkeypatch.setattr(collector, "run_command", native)
    with pytest.raises(FleetPlanInvalid, match="observation|unproven|owner record"):
        collector.observe_local_allocation(lease, network, _observed_request(tmp_path))


def test_native_network_requires_real_present_objects(tmp_path, monkeypatch):
    lease = _cgroup(tmp_path, monkeypatch)
    state = tmp_path / "state" / "network.json"
    record = {"pid": os.getpid(), "process_identity": "boot:42",
              "namespace": "uvf0123456789", "host_veth": "ufh0123456789", "table": "uvf_0123456789"}
    state.write_text(json.dumps(record)); state.chmod(0o600)
    network = SimpleNamespace(state_file=state, ip="ip", nft="nft", namespace=record["namespace"],
                              host_veth=record["host_veth"], table=record["table"])
    def native(_binary, *args, **_kw):
        if "netns" in args:
            value = [{"name": network.namespace}]
        elif "link" in args:
            value = [{"ifname": network.host_veth}]
        else:
            value = {"nftables": [{"table": {"family": "inet", "name": network.table}}]}
        return SimpleNamespace(returncode=0, stdout=json.dumps(value))
    monkeypatch.setattr(collector, "run_command", native)
    result = collector.observe_local_allocation(lease, network, _observed_request(tmp_path))
    assert result.network_kind == "host-netns"
    assert result.network_namespace == network.namespace
    assert result.network_identity.startswith("sha256:")


def test_failed_native_observation_stops_before_sandbox_launch_and_attempts_cleanup(tmp_path, monkeypatch):
    backend, touched = _backend(monkeypatch)
    request = _observed_request(tmp_path)
    conn, journal, permit = _permit(tmp_path, request, backend)
    request.local_resource_intent = permit
    network_cleanup = Mock()
    cgroup_cleanup = Mock()
    monkeypatch.setattr(owner.CgroupV2AttemptLease, "create", lambda *a, **k:
                        SimpleNamespace(open=Mock(), close=cgroup_cleanup))
    monkeypatch.setattr(owner.RestrictedEgressLease, "create", lambda *a, **k:
                        SimpleNamespace(open=Mock(), close=network_cleanup))
    launch = Mock(side_effect=AssertionError("sandbox must not launch"))
    monkeypatch.setattr(owner, "sandbox_process_launch", launch)
    monkeypatch.setattr(owner, "observe_local_allocation", Mock(side_effect=FleetPlanInvalid("native observation unavailable")))
    with pytest.raises(FleetPlanInvalid, match="native observation unavailable"):
        backend.execute(request, timeout=1, cancel_requested=None)
    assert [event["kind"] for event in journal.verify("run-one", auth=authenticator())] == [
        "RUN_ADMITTED", "RESOURCE_INTENT",
    ]
    launch.assert_not_called()
    cgroup_cleanup.assert_called_once()
    network_cleanup.assert_called_once()
    conn.close()


def test_missing_recorder_prevents_any_native_creation(tmp_path, monkeypatch):
    backend, touched = _backend(monkeypatch)
    request = _observed_request(tmp_path)
    conn, journal, permit = _permit(tmp_path, request, backend)
    class ReserveOnly:
        reserve = permit.reserve
    request.local_resource_intent = ReserveOnly()
    with pytest.raises(FleetPlanInvalid, match="allocation recorder"):
        backend.execute(request, timeout=1, cancel_requested=None)
    touched.assert_not_called()
    conn.close()


def test_original_backend_observation_commits_before_sandbox_launch(tmp_path, monkeypatch):
    from dataclasses import replace
    from oh_goon.core.enforcer_lace_trust import canonical_bytes, digest
    from oh_goon.integrations.fleet_plan_local_lace_intent import local_resource_identities
    from tests.unit.runtime.test_b12d1_native_allocation_journal import observation

    backend, _touched = _backend(monkeypatch)
    request = _observed_request(tmp_path)
    conn, journal, permit = _permit(tmp_path, request, backend)
    request.local_resource_intent = permit
    net_close, cg_close = Mock(), Mock()
    monkeypatch.setattr(owner.CgroupV2AttemptLease, "create", lambda *a, **k:
                        SimpleNamespace(open=Mock(), close=cg_close))
    monkeypatch.setattr(owner.RestrictedEgressLease, "create", lambda *a, **k:
                        SimpleNamespace(open=Mock(), close=net_close))
    receipt = replace(observation(), resource_intent_identity=digest(
        "oh-goon/b12d/resource-binding/v1", canonical_bytes(local_resource_identities(request)),
    ))
    monkeypatch.setattr(owner, "observe_local_allocation", Mock(return_value=receipt))

    def launch(*_args):
        events = journal.verify("run-one", auth=authenticator())
        assert [event["kind"] for event in events] == [
            "RUN_ADMITTED", "RESOURCE_INTENT", "RESOURCE_ALLOCATION_OBSERVED",
        ]
        raise RuntimeError("sandbox launch reached only after observed allocation")

    monkeypatch.setattr(owner, "sandbox_process_launch", launch)
    # This test reaches the post-allocation launch boundary: its inherited
    # prepared-source fake must support the original FD-custody verifier.
    from contextlib import nullcontext
    monkeypatch.setattr(owner.OriginalBindSourceLease, "pin_prepared",
                        lambda *_: nullcontext(SimpleNamespace(verify_kernel_custody=Mock())))
    with pytest.raises(RuntimeError, match="only after observed allocation"):
        backend.execute(request, timeout=1, cancel_requested=None)
    net_close.assert_called_once()
    cg_close.assert_called_once()
    conn.close()


def test_untrusted_owner_state_file_is_rejected(tmp_path, monkeypatch):
    lease = _cgroup(tmp_path, monkeypatch)
    lease.state_file.chmod(0o644)
    with pytest.raises(FleetPlanInvalid, match="unsafe permissions"):
        collector.observe_local_allocation(lease, None, _observed_request(tmp_path))
    lease.state_file.chmod(0o600)
    substitute = lease.state_file.with_suffix(".other")
    lease.state_file.rename(substitute)
    lease.state_file.symlink_to(substitute)
    with pytest.raises(FleetPlanInvalid, match="unavailable"):
        collector.observe_local_allocation(lease, None, _observed_request(tmp_path))


def test_restricted_egress_never_silently_downgrades_to_no_lease(tmp_path, monkeypatch):
    lease = _cgroup(tmp_path, monkeypatch)
    request = _observed_request(tmp_path)
    request.attempt.egress = SimpleNamespace(
        mode="cidr_allowlist", model_dump=lambda **_: {"mode": "cidr_allowlist"},
    )
    with pytest.raises(FleetPlanInvalid, match="requires native network lease"):
        collector.observe_local_allocation(lease, None, request)

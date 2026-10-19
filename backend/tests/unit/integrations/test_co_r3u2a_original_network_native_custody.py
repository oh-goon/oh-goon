"""CO-R3U2A: independent network custody remains unavailable, including direct paths."""

from __future__ import annotations

from ipaddress import IPv4Network
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.fleet_plan_step_contract import PlanStepCapabilities
from oh_goon.integrations import _fleet_plan_cgroup_resources as cg
from oh_goon.integrations import _fleet_plan_restricted_egress as net


def _lease(tmp_path: Path) -> net.RestrictedEgressLease:
    record = tmp_path / "state" / "aaaaaaaaaa.json"
    return net.RestrictedEgressLease(
        ip="/usr/bin/ip",
        nft="/usr/bin/nft",
        namespace="uvfaaaaaaaaaa",
        host_veth="ufhaaaaaaaaaa",
        peer_veth="ufpaaaaaaaaaa",
        table="uvf_aaaaaaaaaa",
        pool=IPv4Network("10.253.0.0/29"),
        lock_path=tmp_path / "network.lock",
        rules_file=tmp_path / "rules.nft",
        egress=PlanStepCapabilities(
            egress={
                "mode": "cidr_allowlist",
                "rules": [{"cidr": "203.0.113.0/24", "protocol": "tcp", "ports": [443]}],
            }
        ).egress,
        state_file=record,
    )


@pytest.mark.parametrize(
    "method",
    ["create", "open", "_open_link", "_run_setup", "close", "_write_egress_lease_record", "remove"],
)
def test_original_network_effect_entrypoints_refuse_without_kernel_custody(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
) -> None:
    lease = _lease(tmp_path)
    record = lease.state_file
    record.parent.mkdir()
    record.write_text("durable crash recovery", encoding="utf-8")
    lease._owned_record_written = True
    lease.rules_file.write_text("durable rules", encoding="utf-8")
    run = Mock()
    lock = Mock(side_effect=AssertionError("must not acquire allocator lock"))
    unlink = Mock(side_effect=AssertionError("must not retire record"))
    monkeypatch.setattr(net, "run_command", run)
    monkeypatch.setattr(net, "_egress_allocation_lock", lock)
    monkeypatch.setattr(net, "remove_file_durably", unlink)
    monkeypatch.setattr(net, "require_restricted_egress_host", lambda _host: None)
    if method == "create":
        backend = SimpleNamespace(
            ip_executable=lease.ip,
            nft_executable=lease.nft,
            egress_pool=str(lease.pool),
            egress_lock=lease.lock_path,
            egress_state_dir=record.parent,
        )

        def operation():
            return net.RestrictedEgressLease.create(backend, tmp_path, lease.egress)
    elif method == "open":

        def operation():
            return lease.open(cancel_requested=None)
    elif method == "_open_link":

        def operation():
            return lease._open_link(None)
    elif method == "_run_setup":

        def operation():
            return lease._run_setup((lease.ip, "netns", "add", lease.namespace), None)
    elif method == "close":
        operation = lease.close
    elif method == "_write_egress_lease_record":

        def operation():
            return net._write_egress_lease_record(lease)
    else:

        def operation():
            return net._cleanup_egress_resources(
                lease.ip,
                lease.nft,
                lease.namespace,
                lease.host_veth,
                lease.table,
            )

    with pytest.raises(FleetPlanInvalid, match="physical custody NOT PROVEN"):
        operation()
    assert record.read_text(encoding="utf-8") == "durable crash recovery"
    assert lease.rules_file.read_text(encoding="utf-8") == "durable rules"
    assert not lease.lock_path.exists()
    run.assert_not_called()
    lock.assert_not_called()
    unlink.assert_not_called()


def test_native_network_rejects_local_claim_env_and_fake_host(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OH_GOON_NESTED_CGROUP_DELEGATED", "1")
    monkeypatch.setenv("OH_GOON_NATIVE_CUSTODY_PROVEN", "1")
    lease = _lease(tmp_path)
    for operation in ("native restricted-egress allocation", "native restricted-egress removal"):
        with pytest.raises(FleetPlanInvalid, match="NOT PROVEN"):
            cg.require_independent_native_parent_custody(operation=operation)
    assert not lease.state_file.exists()
    with pytest.raises(FleetPlanInvalid, match="NOT PROVEN"):
        lease.open(cancel_requested=None)
    assert not lease.state_file.exists()


def test_rollback_with_written_pending_record_keeps_record_on_denied_custody(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lease = _lease(tmp_path)
    lease.state_file.parent.mkdir()
    lease.state_file.write_text("pending")
    lease._owned_record_written = True
    attempted = Mock()
    monkeypatch.setattr(net, "_cleanup_egress_resources", attempted)
    with pytest.raises(FleetPlanInvalid, match="NOT PROVEN"):
        lease.open(cancel_requested=None)
    attempted.assert_not_called()
    assert lease.state_file.read_text() == "pending"


def test_direct_remove_still_refuses_when_all_linux_commands_are_stubbed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lease = _lease(tmp_path)
    native = Mock()
    monkeypatch.setattr(net, "run_command", native)
    with pytest.raises(FleetPlanInvalid, match="native restricted-egress removal"):
        net._cleanup_egress_resources(
            lease.ip,
            lease.nft,
            lease.namespace,
            lease.host_veth,
            lease.table,
        )
    native.assert_not_called()

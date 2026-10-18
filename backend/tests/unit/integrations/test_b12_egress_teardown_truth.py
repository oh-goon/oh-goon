"""B12 network-resource cleanup cannot retire recovery authority on failure."""

from __future__ import annotations

import json
from ipaddress import IPv4Network
from pathlib import Path

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.fleet_plan_step_contract import PlanStepCapabilities
from oh_goon.engine.command import CommandResult
from oh_goon.integrations import _fleet_plan_restricted_egress as egress


@pytest.fixture(autouse=True)
def _simulate_future_provisioned_host_for_legacy_native_observation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # These legacy tests cover failure behavior *after* custody is already
    # enforced. The mocked allow is NOT runtime authorization or Gate B proof.
    # CO-R3U2A's separate hostile tests assert the real gate always denies.
    monkeypatch.setattr(egress, "require_independent_native_parent_custody", lambda **_kwargs: None)


def _lease(tmp_path: Path) -> egress.RestrictedEgressLease:
    state = tmp_path / "egress" / "aaaaaaaaaa.json"
    state.parent.mkdir()
    state.write_text(
        json.dumps(
            {
                "pid": 4242,
                "process_identity": "old-boot:123",
                "namespace": "uvfaaaaaaaaaa",
                "host_veth": "ufhaaaaaaaaaa",
                "table": "uvf_aaaaaaaaaa",
            }
        )
    )
    rules = tmp_path / "rules.nft"
    rules.write_text("")
    return egress.RestrictedEgressLease(
        ip="/usr/bin/ip",
        nft="/usr/bin/nft",
        namespace="uvfaaaaaaaaaa",
        host_veth="ufhaaaaaaaaaa",
        peer_veth="ufpaaaaaaaaaa",
        table="uvf_aaaaaaaaaa",
        pool=IPv4Network("10.253.0.0/29"),
        lock_path=tmp_path / "egress.lock",
        rules_file=rules,
        egress=PlanStepCapabilities(
            egress={
                "mode": "cidr_allowlist",
                "rules": [
                    {"cidr": "203.0.113.0/24", "protocol": "tcp", "ports": [443]},
                ],
            }
        ).egress,
        state_file=state,
    )


@pytest.mark.parametrize("failure", ["nft", "netns", "link", "exception"])
def test_failed_cleanup_retains_recovery_and_checks_all_commands(
    tmp_path: Path, monkeypatch, failure: str
) -> None:
    lease = _lease(tmp_path)
    commands: list[tuple[str, ...]] = []

    def fake_run(*argv, **kwargs):
        del kwargs
        commands.append(argv)
        if failure == "exception" and len(commands) % 3 == 2:
            raise OSError("ip unavailable")
        bad = {"nft": 1, "netns": 2, "link": 3}.get(failure)
        return CommandResult(
            argv=argv,
            returncode=1 if len(commands) % 3 == (bad % 3) else 0,
            stdout="",
            stderr="error",
        )

    monkeypatch.setattr(egress, "run_command", fake_run)
    monkeypatch.setattr(egress, "_process_identity", lambda pid: None)
    with pytest.raises(FleetPlanInvalid, match="physical custody"):
        egress._reconcile_stale_egress_leases(lease)
    assert lease.state_file.exists() and commands == []
    with pytest.raises(FleetPlanInvalid, match="cleanup UNPROVEN"):
        egress._cleanup_egress_resources(
            lease.ip, lease.nft, lease.namespace, lease.host_veth, lease.table
        )
    assert len(commands) == 3
    with pytest.raises(FleetPlanInvalid, match="cleanup UNPROVEN"):
        lease.close()
    assert lease.state_file.exists()
    assert lease.rules_file.exists()


def test_all_successful_cleanup_retires_lease(tmp_path: Path, monkeypatch) -> None:
    lease = _lease(tmp_path)
    commands = []

    def fake_run(*argv, **kwargs):
        del kwargs
        commands.append(argv)
        observations = {
            ("-j", "netns", "list"): "[]",
            ("-j", "link", "show"): "[]",
            ("-j", "list", "tables"): '{"nftables":[{"metainfo":{"version":"1"}}]}',
        }
        return CommandResult(
            argv=argv, returncode=0, stdout=observations.get(argv[1:], ""), stderr=""
        )

    monkeypatch.setattr(egress, "run_command", fake_run)
    monkeypatch.setattr(egress, "_process_identity", lambda pid: None)
    with pytest.raises(FleetPlanInvalid, match="physical custody"):
        egress._reconcile_stale_egress_leases(lease)
    assert commands == [] and lease.state_file.exists()
    egress._cleanup_egress_resources(
        lease.ip, lease.nft, lease.namespace, lease.host_veth, lease.table
    )
    assert len(commands) == 6
    assert lease.state_file.exists()  # only original owner may retire it

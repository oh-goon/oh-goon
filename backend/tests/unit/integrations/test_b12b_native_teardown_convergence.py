"""B12B: native post-delete absence and independent local teardown owners."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.engine.command import CommandResult
from oh_goon.integrations import _fleet_plan_restricted_egress as egress
from oh_goon.integrations.fleet_plan_bubblewrap_isolation import _close_local_resource_leases
from tests.unit.integrations.test_b12_egress_teardown_truth import _lease




@pytest.fixture(autouse=True)
def _simulate_future_provisioned_host_for_legacy_native_observation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # These legacy tests cover failure behavior *after* custody is already
    # enforced. The mocked allow is NOT runtime authorization or Gate B proof.
    # CO-R3U2A's separate hostile tests assert the real gate always denies.
    monkeypatch.setattr(egress, "require_independent_native_parent_custody",
                        lambda **_kwargs: None)

@pytest.mark.parametrize("wrong", [
    "netns-remains", "link-remains", "link-peer-suffix", "nft-remains", "malformed-netns",
    "malformed-link", "malformed-nft", "missing-key", "probe-command-failed",
    "probe-command-error", "unknown-nft-record",
])
def test_native_absence_must_be_fresh_and_unambiguous(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, wrong: str,
) -> None:
    lease = _lease(tmp_path)
    executed: list[tuple[str, ...]] = []
    outputs = {
        ("-j", "netns", "list"): "[]",
        ("-j", "link", "show"): "[]",
        ("-j", "list", "tables"): '{"nftables":[]}',
    }
    drift = {
        "netns-remains": (("-j", "netns", "list"), '[{"name":"uvfaaaaaaaaaa"}]'),
        "link-remains": (("-j", "link", "show"), '[{"ifname":"ufhaaaaaaaaaa"}]'),
        "link-peer-suffix": (("-j", "link", "show"), '[{"ifname":"ufhaaaaaaaaaa@if9"}]'),
        "nft-remains": (("-j", "list", "tables"), '{"nftables":[{"table":{"family":"inet","name":"uvf_aaaaaaaaaa"}}]}'),
        "malformed-netns": (("-j", "netns", "list"), "not-json"),
        "malformed-link": (("-j", "link", "show"), "{}"),
        "malformed-nft": (("-j", "list", "tables"), "[]"),
        "missing-key": (("-j", "netns", "list"), '[{}]'),
        "unknown-nft-record": (("-j", "list", "tables"), '{"nftables":[{"strange":{}}]}'),
    }
    if wrong in drift:
        key, value = drift[wrong]
        outputs[key] = value

    def fake_run(*argv, **_kwargs):
        executed.append(argv)
        command = argv[1:]
        if wrong == "probe-command-error" and command == ("-j", "netns", "list"):
            raise OSError("failed to read netns")
        code = int(wrong == "probe-command-failed" and command == ("-j", "netns", "list"))
        return CommandResult(argv=argv, returncode=code, stdout=outputs.get(command, ""), stderr="error")

    monkeypatch.setattr(egress, "run_command", fake_run)
    monkeypatch.setattr(egress, "_process_identity", lambda _pid: None)
    with pytest.raises(FleetPlanInvalid, match="physical custody"):
        egress._reconcile_stale_egress_leases(lease)
    assert lease.state_file.is_file() and executed == []
    with pytest.raises(FleetPlanInvalid, match="cleanup UNPROVEN"):
        egress._cleanup_egress_resources(lease.ip, lease.nft, lease.namespace, lease.host_veth, lease.table)
    assert len(executed) >= 4  # direct original native owner exercises effect/absence


def test_native_absence_checks_all_three_exact_original_resources(tmp_path: Path, monkeypatch) -> None:
    lease = _lease(tmp_path)
    commands = []
    outputs = {
        ("-j", "netns", "list"): '[{"name":"a-different-namespace"}]',
        ("-j", "link", "show"): '[{"ifname":"another-link"}]',
        ("-j", "list", "tables"): '{"nftables":[{"table":{"family":"ip","name":"uvf_aaaaaaaaaa"}}]}',
    }
    def native(*argv, **_kwargs):
        commands.append(argv)
        return CommandResult(argv=argv, returncode=0, stdout=outputs.get(argv[1:], ""), stderr="")
    monkeypatch.setattr(egress, "run_command", native)
    monkeypatch.setattr(egress, "_process_identity", lambda _pid: None)
    with pytest.raises(FleetPlanInvalid, match="physical custody"):
        egress._reconcile_stale_egress_leases(lease)
    assert commands == [] and lease.state_file.exists()
    egress._cleanup_egress_resources(lease.ip, lease.nft, lease.namespace, lease.host_veth, lease.table)
    assert len(commands) == 6 and lease.state_file.exists()


@pytest.mark.parametrize("fail", ["network", "cgroup", "both", "neither"])
def test_cleanup_attempts_both_leases_and_retains_each_failure(fail: str) -> None:
    visited = []
    def close(name: str):
        visited.append(name)
        if fail in (name, "both"):
            raise FleetPlanInvalid(name + " still exists")
    network = SimpleNamespace(close=lambda: close("network"))
    cgroup = SimpleNamespace(close=lambda: close("cgroup"))
    if fail == "both":
        with pytest.raises(ExceptionGroup) as exc:
            _close_local_resource_leases(network, cgroup)
        assert len(exc.value.exceptions) == 2
    elif fail == "neither":
        _close_local_resource_leases(network, cgroup)
    else:
        with pytest.raises(FleetPlanInvalid, match=fail):
            _close_local_resource_leases(network, cgroup)
    assert visited == ["network", "cgroup"]


def test_cgroup_cleanup_still_attempted_with_no_network() -> None:
    visited = []
    _close_local_resource_leases(None, SimpleNamespace(close=lambda: visited.append("cgroup")))
    assert visited == ["cgroup"]


def test_network_interrupt_still_attempts_cgroup_teardown() -> None:
    visited = []
    def interrupted():
        visited.append("network")
        raise KeyboardInterrupt("interrupted cleanup")
    with pytest.raises(KeyboardInterrupt):
        _close_local_resource_leases(
            SimpleNamespace(close=interrupted),
            SimpleNamespace(close=lambda: visited.append("cgroup")),
        )
    assert visited == ["network", "cgroup"]

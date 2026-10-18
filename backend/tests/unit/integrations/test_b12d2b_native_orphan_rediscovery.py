"""B12D2B read-only native correlation, not teardown or native absence."""
from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.ports.local_lace_orphan_inventory import LocalLaceOrphanSubject
from oh_goon.core.ports.local_lace_intent import LocalLaceIntentProof
from oh_goon.integrations import fleet_plan_local_lace_allocation as live
from oh_goon.integrations import fleet_plan_local_lace_orphan_inventory as orphan
from oh_goon.integrations.fleet_plan_local_lace_recovery import lace_resource_binding
from tests.unit.integrations.test_b12d1_native_allocation_observer import _cgroup, _observed_request


def _fixture(tmp_path: Path, monkeypatch):
    cgroup = _cgroup(tmp_path, monkeypatch)
    proof = LocalLaceIntentProof("run-one", "step-1", 1, 2, "sha256:" + "d" * 64)
    native_record = json.loads(cgroup.state_file.read_text())
    native_record["lace_intent"] = lace_resource_binding(proof)
    cgroup.state_file.write_text(json.dumps(native_record))
    cgroup.state_file.chmod(0o600)
    observed = live.observe_local_allocation(cgroup, None, _observed_request(tmp_path), proof=proof)
    subject = LocalLaceOrphanSubject("run-one", 2, 4, 1, "step-1", proof.attempt_identity,
                                    {}, observed.__dict__ if hasattr(observed, "__dict__") else
                                    {name: getattr(observed, name) for name in observed.__dataclass_fields__},
                                    "sha256:" + "f" * 64)
    monkeypatch.setattr(orphan, "_is_cgroup2_path", lambda _: True)
    monkeypatch.setattr(orphan, "_process_identity", lambda pid: "boot:42")
    state_network = tmp_path / "net-state"; state_network.mkdir()
    options = dict(cgroup_root=cgroup.cgroup.parent, cgroup_state_dir=cgroup.state_file.parent,
                   network_state_dir=state_network, ip="ip", nft="nft")
    return cgroup, subject, options


def test_native_cgroup_inode_and_original_label_survive_reader_restart(tmp_path, monkeypatch):
    cgroup, subject, options = _fixture(tmp_path, monkeypatch)
    result = orphan.rediscover_local_orphan(subject, **options)
    assert result.state == "OBSERVED_PRESENT"
    assert result.cgroup_identity == subject.observation["cgroup_identity"]
    assert result.cgroup_inode == cgroup.cgroup.stat().st_ino
    assert result.cgroup_processes == ()
    assert not any((result.native_absence_proven, result.mounts_proven, result.process_tree_proven))
    assert orphan.rediscover_local_orphan(subject, **options) == result


@pytest.mark.parametrize("attack", ["missing", "symlink", "foreign-intent", "missing-cgroup",
                                     "changed-inode", "bad-permissions", "wrong-events", "bad-procs"])
def test_native_orphan_substitutions_deny_inventory_and_preserve_resources(tmp_path, monkeypatch, attack):
    cgroup, subject, options = _fixture(tmp_path, monkeypatch)
    if attack == "missing":
        cgroup.state_file.unlink()
    elif attack == "symlink":
        source = cgroup.state_file.with_suffix(".original")
        cgroup.state_file.rename(source)
        cgroup.state_file.symlink_to(source)
    elif attack == "foreign-intent":
        record = json.loads(cgroup.state_file.read_text())
        record["lace_intent"]["run_id"] = "foreign"
        cgroup.state_file.write_text(json.dumps(record))
    elif attack == "missing-cgroup":
        for child in cgroup.cgroup.iterdir(): child.unlink()
        cgroup.cgroup.rmdir()
    elif attack == "changed-inode":
        new = cgroup.cgroup.with_name("new")
        cgroup.cgroup.rename(new)
        cgroup.cgroup.mkdir()
        (cgroup.cgroup / "cgroup.procs").write_text("")
        (cgroup.cgroup / "cgroup.events").write_text("populated 0\n")
    elif attack == "bad-permissions":
        cgroup.state_file.chmod(0o644)
    elif attack == "wrong-events":
        (cgroup.cgroup / "cgroup.events").write_text("populated 2\n")
    elif attack == "bad-procs":
        (cgroup.cgroup / "cgroup.procs").write_text("garbage\n")
    with pytest.raises(FleetPlanInvalid):
        orphan.rediscover_local_orphan(subject, **options)
    assert cgroup.state_file.exists() if attack != "missing" else not cgroup.state_file.exists()


def test_recovered_process_birth_and_mount_namespace_are_observed_but_not_quiescent(tmp_path, monkeypatch):
    cgroup, subject, options = _fixture(tmp_path, monkeypatch)
    (cgroup.cgroup / "cgroup.procs").write_text("4567\n")
    (cgroup.cgroup / "cgroup.events").write_text("populated 1\n")
    proc = tmp_path / "proc" / "4567"
    (proc / "ns").mkdir(parents=True)
    (proc / "ns" / "mnt").symlink_to("mnt:[601]")
    (proc / "mountinfo").write_text("one mount observation\n")
    options["proc_root"] = tmp_path / "proc"
    result = orphan.rediscover_local_orphan(subject, **options)
    assert result.cgroup_processes[0][:3] == (4567, "boot:42", "mnt:[601]")
    assert result.cgroup_processes[0][3].startswith("sha256:")
    assert result.process_tree_proven is False
    assert result.mounts_proven is False
    (proc / "ns" / "mnt").unlink()
    with pytest.raises(FleetPlanInvalid, match="mount namespace"):
        orphan.rediscover_local_orphan(subject, **options)


def test_intent_only_crash_never_converts_missing_resources_to_absence(tmp_path):
    subject = LocalLaceOrphanSubject("run-one", 2, 3, 1, "step-1", "sha256:" + "d" * 64,
                                    {}, None, "sha256:" + "f" * 64)
    inventory = orphan.rediscover_local_orphan(subject, cgroup_root=tmp_path / "nonexistent",
        cgroup_state_dir=tmp_path / "none", network_state_dir=tmp_path / "none", ip="ip", nft="nft")
    assert inventory.state == "INTENT_ONLY_UNKNOWN"
    assert inventory.native_absence_proven is False
    assert inventory.cgroup_identity is None


def test_native_owner_pid_reuse_does_not_authorize_orphan_recovery(tmp_path, monkeypatch):
    cgroup, subject, options = _fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(orphan, "_process_identity", lambda _: "new-boot:999")
    with pytest.raises(FleetPlanInvalid, match="PID birth changed"):
        orphan.rediscover_local_orphan(subject, **options)
    assert cgroup.state_file.exists() and cgroup.cgroup.exists()


def test_orphan_netns_veth_nft_presence_fenced_to_original_native_record(tmp_path, monkeypatch):
    cgroup, subject, options = _fixture(tmp_path, monkeypatch)
    record = {"pid": os.getpid(), "process_identity": "boot:42",
              "namespace": "uvf0123456789", "host_veth": "ufh0123456789", "table": "uvf_0123456789",
              "lace_intent": lace_resource_binding(LocalLaceIntentProof(
                  "run-one", "step-1", 1, 2, "sha256:" + "d" * 64))}
    net_file = options["network_state_dir"] / "net.json"
    net_file.write_text(json.dumps(record)); net_file.chmod(0o600)
    from oh_goon.integrations.fleet_plan_local_lace_intent import _identity
    net_identity = _identity("observed-host-network", {"owner_record": record})
    subject = replace(subject, observation={**subject.observation,
        "network_kind": "host-netns", "network_namespace": record["namespace"],
        "host_veth": record["host_veth"], "nft_table": record["table"],
        "network_identity": net_identity})
    def native(_tool, args, *, kind):
        if kind == "netns": return [{"name": record["namespace"]}]
        if kind == "link": return [{"ifname": record["host_veth"]}]
        return [{"table": {"family": "inet", "name": record["table"]}}]
    monkeypatch.setattr(orphan, "_native_list", native)
    result = orphan.rediscover_local_orphan(subject, **options)
    assert result.network_identity == net_identity
    for kind in ("netns", "link", "nft"):
        def drift(_tool, args, *, kind=kind):
            if kind == "netns": return [{"name": "foreign"}]
            if kind == "link": return [{"ifname": "foreign"}]
            return [{"table": {"family": "inet", "name": "foreign"}}]
        monkeypatch.setattr(orphan, "_native_list", drift)
        with pytest.raises(FleetPlanInvalid, match="unresolved"):
            orphan.rediscover_local_orphan(subject, **options)
    record["process_identity"] = "other:22"
    net_file.write_text(json.dumps(record))
    with pytest.raises(FleetPlanInvalid, match="cgroup/network owner"):
        orphan.rediscover_local_orphan(subject, **options)


def test_native_mount_namespace_pid_birth_race_is_not_accepted(tmp_path, monkeypatch):
    cgroup, subject, options = _fixture(tmp_path, monkeypatch)
    (cgroup.cgroup / "cgroup.procs").write_text("1234\n")
    proc = tmp_path / "proc" / "1234"
    (proc / "ns").mkdir(parents=True)
    (proc / "ns" / "mnt").symlink_to("mnt:[333]")
    (proc / "mountinfo").write_text("sample\n")
    options["proc_root"] = tmp_path / "proc"
    count = [0]
    def pid_birth(pid):
        if pid == os.getpid(): return "boot:42"
        count[0] += 1
        return "boot:42" if count[0] == 1 else "boot:43"
    monkeypatch.setattr(orphan, "_process_identity", pid_birth)
    with pytest.raises(FleetPlanInvalid, match="changed during observation"):
        orphan.rediscover_local_orphan(subject, **options)

"""Native reaper never treats an inventory, ticket or PID death as deletion permission."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.integrations import fleet_plan_local_lace_native_reaper as reaper
from oh_goon.integrations import _fleet_plan_cgroup_resources as cg
from oh_goon.integrations import fleet_plan_local_lace_orphan_inventory as observer
from oh_goon.runtime.enforcer_lace_native_cleanup import NativeCleanupResult, NativeCleanupFailure
from tests.unit.integrations.test_b12d2b_native_orphan_rediscovery import _fixture
from tests.unit.runtime.test_b12d2c1_cleanup_preparation import seeded


def setup(tmp_path, monkeypatch):
    cgroup, subject, opts = _fixture(tmp_path, monkeypatch)
    snapshot = observer.rediscover_local_orphan(subject, **opts)
    (tmp_path / "db").mkdir()
    conn, journal, owner, item = seeded(tmp_path / "db")
    ticket = owner.prepare_cleanup(item)
    native = Mock()
    native.execute_native_cleanup.side_effect = lambda ticket, action: action()
    # Ordinary temp directories have regular fixture files. Real cgroupfs
    # removes kernel control files automatically; emulate that only after the
    # original owner's identity-bound preflight, then use its real rmdir path.
    native_remove = cg._remove_cgroup
    def cgroupfs_remove(path, *, expected_identity):
        for name in ("cgroup.events", "cgroup.procs"):
            (path / name).unlink()
        native_remove(path, expected_identity=expected_identity)
    monkeypatch.setattr(reaper, "_remove_cgroup", cgroupfs_remove)
    kwargs = dict(cgroup_root=opts["cgroup_root"], cgroup_state_dir=opts["cgroup_state_dir"],
                  network_state_dir=opts["network_state_dir"],
                  cgroup_lock=tmp_path / "cgroup.lock", network_lock=tmp_path / "network.lock",
                  ip="ip", nft="nft")
    # Journal-native identity correspondence is supplied by actual Fleet in production.
    ticket = __import__("dataclasses").replace(ticket, run_id=subject.run_id, subject_identity=subject.subject_identity,
                                                  control_epoch=subject.control_epoch)
    return cgroup, subject, snapshot, native, ticket, kwargs, conn


def test_empty_native_cgroup_attempt_retains_original_recovery_record(tmp_path, monkeypatch):
    cgroup, subject, snapshot, native, ticket, kwargs, conn = setup(tmp_path, monkeypatch)
    monkeypatch.setattr(reaper, "_process_identity", lambda _: None)
    # CO-R3U1: absence of independent kernel parent custody blocks before any
    # deletion; the original persisted record remains for qualified recovery.
    with pytest.raises(NativeCleanupFailure) as failure:
        reaper.cleanup_local_orphan(native, ticket, subject, snapshot, **kwargs)
    assert failure.value.attempted == ()
    assert cgroup.cgroup.exists()
    assert cgroup.state_file.is_file()
    conn.close()


@pytest.mark.parametrize("attack", ["alive-owner", "changed-resource", "process-present", "foreign-inventory"])
def test_native_reaper_rejects_unsafe_state_without_delete(tmp_path, monkeypatch, attack):
    cgroup, subject, snapshot, native, ticket, kwargs, conn = setup(tmp_path, monkeypatch)
    monkeypatch.setattr(reaper, "_process_identity", lambda _: "boot:42" if attack == "alive-owner" else None)
    if attack == "changed-resource":
        (cgroup.cgroup / "cgroup.events").write_text("populated 1\n")
    elif attack == "process-present":
        (cgroup.cgroup / "cgroup.procs").write_text("333\n")
    elif attack == "foreign-inventory":
        snapshot = __import__("dataclasses").replace(snapshot, cgroup_inode=99999)
    deletion = Mock()
    monkeypatch.setattr(reaper, "_remove_cgroup", deletion)
    with pytest.raises(NativeCleanupFailure):
        reaper.cleanup_local_orphan(native, ticket, subject, snapshot, **kwargs)
    deletion.assert_not_called()
    assert cgroup.state_file.is_file()
    conn.close()


def test_network_cleanup_uses_native_owner_and_retains_records(tmp_path, monkeypatch):
    from dataclasses import replace
    from types import SimpleNamespace
    cgroup, subject, snapshot, native, ticket, kwargs, conn = setup(tmp_path, monkeypatch)
    monkeypatch.setattr(reaper, "_process_identity", lambda _: None)
    old_observation = dict(subject.observation)
    old_observation.update(network_kind="host-netns", network_namespace="uvf1234567890",
                           host_veth="ufh1234567890", nft_table="uvf_1234567890")
    subject = replace(subject, observation=old_observation)
    snapshot = replace(snapshot, network_identity="sha256:" + "c" * 64)
    monkeypatch.setattr(reaper, "rediscover_local_orphan", lambda *_args, **_kwargs: snapshot)
    calls = []
    def fake_run(*args, **kwargs):
        calls.append(("pids", args))
        return SimpleNamespace(returncode=0, stdout="")
    def fake_cleanup(*args):
        calls.append(("delete", args))
    monkeypatch.setattr("oh_goon.integrations._fleet_plan_restricted_egress.run_command", fake_run)
    monkeypatch.setattr(reaper, "_cleanup_egress_resources", fake_cleanup)
    real_remove = reaper._remove_cgroup
    def track_remove(path, *, expected_identity):
        calls.append(("rmdir", path))
        real_remove(path, expected_identity=expected_identity)
    monkeypatch.setattr(reaper, "_remove_cgroup", track_remove)
    with pytest.raises(NativeCleanupFailure) as failure:
        reaper.cleanup_local_orphan(native, ticket, subject, snapshot, **kwargs)
    assert failure.value.attempted == ()
    assert calls == []  # no network probe, deletion or cgroup rmdir
    assert cgroup.state_file.exists()
    conn.close()


@pytest.mark.parametrize("native_pids", ["1\n", "unknown\n"])
def test_unknown_namespace_processes_prevent_any_deletion(tmp_path, monkeypatch, native_pids):
    from dataclasses import replace
    from types import SimpleNamespace
    cgroup, subject, snapshot, native, ticket, kwargs, conn = setup(tmp_path, monkeypatch)
    monkeypatch.setattr(reaper, "_process_identity", lambda _: None)
    current = dict(subject.observation)
    current.update(network_kind="host-netns", network_namespace="uvf1234567890",
                   host_veth="ufh1234567890", nft_table="uvf_1234567890")
    subject = replace(subject, observation=current)
    snapshot = replace(snapshot, network_identity="sha256:" + "c" * 64)
    monkeypatch.setattr(reaper, "rediscover_local_orphan", lambda *_args, **_kwargs: snapshot)
    monkeypatch.setattr("oh_goon.integrations._fleet_plan_restricted_egress.run_command",
                        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout=native_pids))
    deletion = Mock()
    monkeypatch.setattr(reaper, "_cleanup_egress_resources", deletion)
    with pytest.raises(NativeCleanupFailure):
        reaper.cleanup_local_orphan(native, ticket, subject, snapshot, **kwargs)
    deletion.assert_not_called()
    conn.close()


def test_partial_network_effect_failure_reports_attempted_owner(tmp_path, monkeypatch):
    from dataclasses import replace
    from types import SimpleNamespace
    cgroup, subject, snapshot, native, ticket, kwargs, conn = setup(tmp_path, monkeypatch)
    monkeypatch.setattr(reaper, "_process_identity", lambda _: None)
    current = dict(subject.observation)
    current.update(network_kind="host-netns", network_namespace="uvf1234567890",
                   host_veth="ufh1234567890", nft_table="uvf_1234567890")
    subject = replace(subject, observation=current)
    snapshot = replace(snapshot, network_identity="sha256:" + "c" * 64)
    monkeypatch.setattr(reaper, "rediscover_local_orphan", lambda *_args, **_kwargs: snapshot)
    monkeypatch.setattr("oh_goon.integrations._fleet_plan_restricted_egress.run_command",
                        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout=""))
    monkeypatch.setattr(reaper, "_cleanup_egress_resources",
                        lambda *_args: (_ for _ in ()).throw(FleetPlanInvalid("native netns failed")))
    with pytest.raises(NativeCleanupFailure) as failure:
        reaper.cleanup_local_orphan(native, ticket, subject, snapshot, **kwargs)
    assert failure.value.attempted == ()
    assert cgroup.state_file.is_file()
    conn.close()

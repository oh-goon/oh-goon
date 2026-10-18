"""Native partial network and intent-only crash classification is read-only."""

from __future__ import annotations

from dataclasses import replace

from oh_goon.integrations import fleet_plan_local_lace_crash_reconciliation as crash
from tests.unit.integrations.test_b12d2b_native_orphan_rediscovery import _fixture


def test_intent_only_never_looks_at_host(tmp_path, monkeypatch):
    _, subject, settings = _fixture(tmp_path, monkeypatch)
    subject = replace(subject, observation=None)
    monkeypatch.setattr(
        crash,
        "rediscover_local_orphan",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("host accessed")),
    )
    settings.update(cgroup_lock=tmp_path / "cg.lock", network_lock=tmp_path / "net.lock")
    result = crash.observe_native_crash(subject, **settings)
    assert result.state == "INTENT_ONLY_UNKNOWN" and not result.native_absence_proven


def test_original_cgroup_still_present_is_not_absence(tmp_path, monkeypatch):
    cgroup, subject, settings = _fixture(tmp_path, monkeypatch)
    settings.update(cgroup_lock=tmp_path / "cg.lock", network_lock=tmp_path / "net.lock")
    result = crash.observe_native_crash(subject, **settings)
    assert result.state == "PRESENT_MATCHING"
    assert result.teardown_proven is False


def test_all_native_network_objects_missing_is_only_partial(tmp_path, monkeypatch):
    cgroup, subject, settings = _fixture(tmp_path, monkeypatch)
    subject = replace(
        subject,
        observation={
            **subject.observation,
            "network_kind": "host-netns",
            "network_namespace": "uvf0123456789",
            "host_veth": "ufh0123456789",
            "nft_table": "uvf_0123456789",
        },
    )
    original_record = crash._matching_record
    real_owner = original_record(
        settings["cgroup_state_dir"],
        kind="cgroup",
        subject=subject,
        resource_name=cgroup.cgroup.name,
    )
    monkeypatch.setattr(
        crash,
        "_matching_record",
        lambda directory, *, kind, subject, resource_name: {
            "pid": real_owner["pid"],
            "process_identity": real_owner["process_identity"],
            "host_veth": "ufh0123456789",
            "table": "uvf_0123456789",
        }
        if kind == "network"
        else original_record(directory, kind=kind, subject=subject, resource_name=resource_name),
    )
    monkeypatch.setattr(crash, "_process_identity", lambda *_args: None)
    monkeypatch.setattr(crash, "_is_cgroup2_path", lambda *_args: True)
    monkeypatch.setattr(
        crash,
        "rediscover_local_orphan",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            __import__("oh_goon.core.errors", fromlist=["FleetPlanInvalid"]).FleetPlanInvalid(
                "network missing"
            )
        ),
    )
    monkeypatch.setattr(crash, "_native_list", lambda *_args, **_kwargs: [])
    settings.update(cgroup_lock=tmp_path / "cg.lock", network_lock=tmp_path / "net.lock")
    result = crash.observe_native_crash(subject, **settings)
    assert result.state == "PARTIAL_NETWORK" and not result.native_absence_proven
    # A remaining native object is NOT a partially completed cleanup.
    monkeypatch.setattr(
        crash,
        "_native_list",
        lambda _prog, _args, **kw: [{"name": "uvf0123456789"}] if kw["kind"] == "netns" else [],
    )
    assert crash.observe_native_crash(subject, **settings).state == "UNRESOLVED"


def test_native_owner_substitution_stays_unresolved(tmp_path, monkeypatch):
    cgroup, subject, settings = _fixture(tmp_path, monkeypatch)
    settings.update(cgroup_lock=tmp_path / "cg.lock", network_lock=tmp_path / "net.lock")
    import json

    record = json.loads(cgroup.state_file.read_text())
    record["lace_intent"]["run_id"] = "foreign"
    cgroup.state_file.write_text(json.dumps(record))
    # A forged / replacement native resource can never qualify as partial cleanup.
    result = crash.observe_native_crash(subject, **settings)
    assert result.state == "UNRESOLVED"
    assert result.native_absence_proven is False


def test_missing_original_cgroup_record_remains_unresolved(tmp_path, monkeypatch):
    cgroup, subject, settings = _fixture(tmp_path, monkeypatch)
    settings.update(cgroup_lock=tmp_path / "cg.lock", network_lock=tmp_path / "net.lock")
    cgroup.state_file.unlink()
    result = crash.observe_native_crash(subject, **settings)
    assert result.state == "UNRESOLVED" and not result.native_absence_proven

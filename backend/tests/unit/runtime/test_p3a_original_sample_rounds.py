"""P3A: original-owner observation windows and native cleanup ancestry.

Repeated kernel snapshots are new ORIGINAL reads, not independent custody or
physical absence. Journal-round forgery must fail even with a repaired hash chain.
"""

from __future__ import annotations

import json

import pytest

from oh_goon.core.enforcer_lace_trust import canonical_bytes
from oh_goon.core.ports.local_lace_cleanup import NativeCleanupResult
from oh_goon.core.ports.physical_observation import PhysicalKernelFact, PhysicalKernelSnapshot
from oh_goon.integrations import linux_physical_observer
from oh_goon.runtime.enforcer_lace_journal import CausalJournalError, _event_identity
from tests.unit.core.test_enforcer_lace_trust import authenticator
from tests.unit.runtime.test_b22_original_physical_sampling import mount_descriptor, seeded


def _original_sample(owner, scope, tmp_path):
    return owner.sample_recovery_physical_unqualified(
        intent_run_seq=2,
        scope=scope,
        mount_intent=mount_descriptor(),
        proc_root=tmp_path,
    )


def _fixed_read(monkeypatch):
    reads = []
    snapshot = PhysicalKernelSnapshot(
        (("cgroup", PhysicalKernelFact("NOT_OBSERVED", "scoped only")),)
    )

    def observe(scope, *, proc_root):
        reads.append(scope)
        return snapshot

    monkeypatch.setattr(linux_physical_observer, "observe_kernel", observe)
    return reads


def test_multiple_identical_kernel_scans_remain_distinct_non_authorizing_rounds(
    tmp_path, monkeypatch
):
    conn, journal, session, owner, scope = seeded(tmp_path)
    reads = _fixed_read(monkeypatch)
    try:
        for ordinal in range(1, 4):
            sample = _original_sample(owner, scope, tmp_path)
            assert sample.classification == "ORIGINAL_OWNER_READ_ONLY_UNQUALIFIED"
            assert not sample.teardown_proven and not sample.physical_absence_proven
            samples = [
                e
                for e in journal.verify("run-one", auth=authenticator())
                if e["kind"] == "RESOURCE_PHYSICAL_SAMPLE_RECORDED"
            ]
            assert len(samples) == ordinal
            payload = samples[-1]["payload"]
            assert payload["sample_round"] == ordinal
            assert payload["previous_sample_run_seq"] == (
                samples[-2]["run_seq"] if ordinal > 1 else None
            )
            assert payload["cleanup_started_run_seq"] is None
            assert payload["cleanup_recorded_run_seq"] is None
            assert payload["observer_custody"] == "UNVERIFIED"
            assert not payload["teardown_proven"]
        assert len(reads) == 3 and session.commit_count == 6
    finally:
        conn.close()


def test_post_cleanup_round_uses_original_start_and_record_events(tmp_path, monkeypatch):
    conn, journal, _, owner, scope = seeded(tmp_path)
    _fixed_read(monkeypatch)
    try:
        _original_sample(owner, scope, tmp_path)
        subject = owner.rediscovery_subject(intent_run_seq=2)
        ticket = owner.prepare_cleanup(subject)
        result = owner.execute_native_cleanup(
            ticket,
            lambda: NativeCleanupResult(("CGROUP",), "CLEANUP_ACTIONS_ATTEMPTED"),
        )
        assert result.result == "CLEANUP_ACTIONS_ATTEMPTED"
        events = journal.verify("run-one", auth=authenticator())
        start = [e for e in events if e["kind"] == "RESOURCE_CLEANUP_NATIVE_STARTED"][-1]
        done = [e for e in events if e["kind"] == "RESOURCE_CLEANUP_NATIVE_RECORDED"][-1]
        _original_sample(owner, scope, tmp_path)
        recorded = journal.verify("run-one", auth=authenticator())[-1]["payload"]
        assert recorded["cleanup_started_run_seq"] == start["run_seq"]
        assert recorded["cleanup_recorded_run_seq"] == done["run_seq"]
        assert recorded["sample_round"] == 2
        assert not recorded["teardown_proven"]
    finally:
        conn.close()


def test_interrupted_native_cleanup_remains_visible_but_unproven(tmp_path, monkeypatch):
    conn, journal, _, owner, scope = seeded(tmp_path)
    _fixed_read(monkeypatch)
    try:
        subject = owner.rediscovery_subject(intent_run_seq=2)
        ticket = owner.prepare_cleanup(subject)
        with pytest.raises(KeyboardInterrupt):
            owner.execute_native_cleanup(ticket, lambda: (_ for _ in ()).throw(KeyboardInterrupt()))
        start = journal.verify("run-one", auth=authenticator())[-1]["run_seq"]
        assert (
            journal.verify("run-one", auth=authenticator())[-1]["kind"]
            == "RESOURCE_CLEANUP_NATIVE_STARTED"
        )
        _original_sample(owner, scope, tmp_path)
        payload = journal.verify("run-one", auth=authenticator())[-1]["payload"]
        assert payload["cleanup_started_run_seq"] == start
        assert payload["cleanup_recorded_run_seq"] is None
        assert payload["effect_permission"] == "NONE"
    finally:
        conn.close()


@pytest.mark.parametrize(
    "key,value",
    (
        ("sample_round", 1),
        ("previous_sample_run_seq", None),
        ("cleanup_started_run_seq", 17),
        ("cleanup_recorded_run_seq", 19),
        ("observer_custody", "INDEPENDENT"),
        ("teardown_proven", True),
    ),
)
def test_rehashed_sample_round_and_custody_forgeries_are_rejected(
    tmp_path, monkeypatch, key, value
):
    conn, journal, _, owner, scope = seeded(tmp_path)
    _fixed_read(monkeypatch)
    try:
        _original_sample(owner, scope, tmp_path)
        _original_sample(owner, scope, tmp_path)
        row = conn.execute(
            "SELECT run_seq,event_kind,event_payload,previous_identity "
            "FROM lace_run_events WHERE run_id=? ORDER BY run_seq DESC LIMIT 1",
            ("run-one",),
        ).fetchone()
        number, kind, encoded, prev = row
        payload = json.loads(encoded)
        payload[key] = value
        updated = canonical_bytes(payload)
        identity = _event_identity("run-one", number, kind, updated, prev)
        conn.execute(
            "UPDATE lace_run_events SET event_payload=?, event_identity=? "
            "WHERE run_id=? AND run_seq=?",
            (updated, identity, "run-one", number),
        )
        conn.execute(
            "UPDATE lace_run_authority SET head_identity=? WHERE run_id=?", (identity, "run-one")
        )
        with pytest.raises(CausalJournalError):
            journal.verify("run-one", auth=authenticator())
    finally:
        conn.close()


def test_x0_rejects_removed_original_sample_round_or_journal_guard(tmp_path):
    import shutil

    from oh_goon.repository_tooling.architecture.repository_layout import ROOT
    from oh_goon.repository_tooling.architecture.x0_single_execution_contract import (
        _check_p3a_original_sample_rounds,
    )

    originals = ROOT / "backend/src/oh_goon"
    names = (
        "runtime/enforcer_lace_original_physical_sampling.py",
        "runtime/enforcer_lace_fleet_resource_owner.py",
        "runtime/enforcer_lace_journal.py",
    )
    for name in names:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(originals / name, target)
    assert _check_p3a_original_sample_rounds(tmp_path) == []
    for name, removed in (
        (names[0], "len(samples) >= 256"),
        (names[1], "after = validate_cut(connection)"),
        (names[2], "supplied_position != expected_position"),
    ):
        path = tmp_path / name
        original = path.read_text()
        assert removed in original
        path.write_text(
            original.replace(removed, "False" if "!=" in removed or ">=" in removed else "pass", 1)
        )
        assert _check_p3a_original_sample_rounds(tmp_path)
        path.write_text(original)

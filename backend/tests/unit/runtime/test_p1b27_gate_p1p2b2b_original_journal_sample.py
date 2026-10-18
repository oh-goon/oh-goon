"""P1/P2B2B: one original Fleet journal records ONLY unqualified kernel samples.

These tests do not prove effectful mount custody, teardown or Gate B.
"""
from __future__ import annotations

from pathlib import Path
import json
from dataclasses import replace
import shutil

import pytest

from oh_goon.core.enforcer_lace_trust import canonical_bytes
from oh_goon.core.ports.physical_observation import PhysicalKernelFact, PhysicalKernelSnapshot
from oh_goon.integrations import linux_physical_observer
from oh_goon.runtime.enforcer_lace_journal import CausalJournalError, _event_identity
from oh_goon.repository_tooling.architecture.x0_single_execution_contract import _check_p1p2b1_causal_kernel_ledger
from tests.unit.core.test_enforcer_lace_trust import authenticator
from tests.unit.runtime.test_b22_original_physical_sampling import mount_descriptor, seeded


def _record(tmp_path: Path, monkeypatch, *, unknown: bool = False):
    conn, journal, session, owner, scope = seeded(tmp_path)
    facts = PhysicalKernelSnapshot((
        ("visible_mount_fd_holders", PhysicalKernelFact("UNKNOWN" if unknown else "NOT_OBSERVED", "scoped only")),
        ("cgroup", PhysicalKernelFact("UNKNOWN", "cannot establish absence")),
    ))
    observed = []
    def observe(s, *, proc_root):
        observed.append((s, proc_root))
        return facts
    monkeypatch.setattr(linux_physical_observer, "observe_kernel", observe)
    baseline = journal.verify("run-one", auth=authenticator())
    sample = owner.sample_recovery_physical_unqualified(
        intent_run_seq=2, scope=scope, mount_intent=mount_descriptor(), proc_root=tmp_path)
    return conn, journal, session, owner, scope, facts, sample, baseline, observed


def test_original_single_reader_one_journal_append_and_ordered_kernel_transcript(tmp_path, monkeypatch):
    conn, journal, session, _, scope, facts, sample, before, observed = _record(tmp_path, monkeypatch)
    events = journal.verify("run-one", auth=authenticator())
    assert events[:-1] == before and len(events) == len(before) + 1
    record = events[-1]
    assert record["kind"] == "RESOURCE_PHYSICAL_SAMPLE_RECORDED"
    payload = record["payload"]
    assert (payload["run_id"], payload["claim_run_seq"], payload["control_epoch"]) == ("run-one", 4, 1)
    assert payload["sample_identity"] == sample.causal_observation_identity
    assert payload["snapshot_identity"] == facts.identity
    assert [entry["component"] for entry in payload["observed"]] == [name for name, _ in facts.components]
    assert payload["scope"]["mount_targets"] == list(scope.mount_targets)
    assert payload["observer_custody"] == "UNVERIFIED"
    assert payload["coverage"] == "INCOMPLETE_NATIVE_RESOURCE_SCOPE"
    assert payload["effect_permission"] == "NONE"
    assert payload["physical_absence_proven"] is False
    assert payload["teardown_proven"] is False
    assert observed == [(scope, tmp_path)] and session.commit_count == 2
    conn.close()


def test_unknown_is_recorded_without_absence_or_extra_kernel_scan(tmp_path, monkeypatch):
    conn, journal, _, _, _, _, sample, _, observed = _record(tmp_path, monkeypatch, unknown=True)
    assert sample.facts.to_mapping()["visible_mount_fd_holders"]["state"] == "UNKNOWN"
    assert len(observed) == 1
    payload = journal.verify("run-one", auth=authenticator())[-1]["payload"]
    assert payload["observed"][0]["state"] == "UNKNOWN"
    assert not any(payload[key] for key in (
        "physical_absence_proven", "quiescence_proven", "teardown_proven"))
    conn.close()


def test_same_claim_same_snapshot_is_a_distinct_unqualified_round(tmp_path, monkeypatch):
    conn, journal, session, owner, scope, facts, *_ = _record(tmp_path, monkeypatch)
    first = journal.verify("run-one", auth=authenticator())[-1]
    sample = owner.sample_recovery_physical_unqualified(
        intent_run_seq=2, scope=scope, mount_intent=mount_descriptor(), proc_root=tmp_path)
    last = journal.verify("run-one", auth=authenticator())[-1]
    assert first["payload"]["sample_round"] == 1
    assert first["payload"]["previous_sample_run_seq"] is None
    assert last["payload"]["sample_round"] == 2
    assert last["payload"]["previous_sample_run_seq"] == first["run_seq"]
    assert last["payload"]["sample_identity"] == first["payload"]["sample_identity"]
    assert last["payload"]["snapshot_identity"] == facts.identity
    assert sample.effect_permission == "NONE" and not sample.teardown_proven
    assert last["payload"]["observer_custody"] == "UNVERIFIED"
    assert session.commit_count == 4  # every round requires BOTH original lease cuts
    conn.close()


@pytest.mark.parametrize("forgery", ("teardown", "custody", "kernel", "scope", "identity"))
def test_even_rehashed_journal_cannot_promote_or_misbind_unqualified_sample(tmp_path, monkeypatch, forgery):
    conn, journal, _, _, _, _, _, _, _ = _record(tmp_path, monkeypatch)
    row = conn.execute("SELECT run_seq, event_kind, event_payload, previous_identity "
                       "FROM lace_run_events WHERE run_id=? ORDER BY run_seq DESC LIMIT 1", ("run-one",)).fetchone()
    sequence, kind, encoded, previous = row
    payload = json.loads(encoded)
    if forgery == "teardown":
        payload["teardown_proven"] = True
    elif forgery == "custody":
        payload["observer_custody"] = "INDEPENDENT"
    elif forgery == "kernel":
        payload["observed"][0]["detail"] = "tampered observation"
    elif forgery == "scope":
        payload["scope"]["tracked_pid"] = 999
    else:
        payload["sample_identity"] = "sha256:" + "f" * 64
    updated = canonical_bytes(payload)
    new_identity = _event_identity("run-one", sequence, kind, updated, previous)
    conn.execute("UPDATE lace_run_events SET event_payload=?,event_identity=? WHERE run_id=? AND run_seq=?",
                 (updated, new_identity, "run-one", sequence))
    conn.execute("UPDATE lace_run_authority SET head_identity=? WHERE run_id=?", (new_identity, "run-one"))
    conn.commit()
    with pytest.raises(CausalJournalError, match="invalid|transcript"):
        journal.verify("run-one", auth=authenticator())
    conn.close()


def test_x0_rejects_removed_canonical_journal_write(tmp_path):
    from oh_goon.repository_tooling.architecture.repository_layout import ROOT
    root = ROOT / "backend/src/oh_goon"
    for name in ("runtime/enforcer_lace_original_physical_sampling.py",
                 "runtime/enforcer_lace_fleet_resource_owner.py",
                 "core/ports/physical_observation.py"):
        destination = tmp_path / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / name, destination)
    owner = tmp_path / "runtime/enforcer_lace_fleet_resource_owner.py"
    source = owner.read_text()
    assert "journal._append_locked(self.run_id, \"RESOURCE_PHYSICAL_SAMPLE_RECORDED\", payload)" in source
    owner.write_text(source.replace("journal._append_locked(self.run_id, \"RESOURCE_PHYSICAL_SAMPLE_RECORDED\", payload)", "pass", 1))
    assert _check_p1p2b1_causal_kernel_ledger(tmp_path)

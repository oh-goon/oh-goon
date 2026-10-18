"""Original-session takeover requires a predecessor crash receipt; no replay."""

from __future__ import annotations

import sqlite3
from dataclasses import replace

import pytest

from oh_goon.core.ports.local_lace_crash_reconciliation import LocalLaceCrashObservation
from oh_goon.runtime.enforcer_lace_takeover_recovery import pending_takeover_in
from tests.fixtures.db1b2_legacy_sqlite import CausalJournalError, CausalRunJournal
from tests.unit.core.test_enforcer_lace_trust import authenticator
from tests.unit.runtime.test_b12d2c1_cleanup_preparation import seeded


def setup_takeover(tmp_path, *, reconciled=True):
    conn, journal, owner, subject = seeded(tmp_path)
    if reconciled:
        owner.reconcile_crash(
            intent_run_seq=2,
            native_observer=lambda s: LocalLaceCrashObservation(
                s.subject_identity, "PRESENT_MATCHING"
            ),
        )
    journal.transfer_control(
        "run-one", epoch=1, lease_identity="lease-one", next_epoch=2, next_lease_identity="new"
    )
    owner.session.token = "new"
    return conn, journal, owner


def test_takeover_renews_fresh_claim_and_reconciles_without_native_effect(tmp_path):
    conn, journal, owner = setup_takeover(tmp_path)
    obligations = owner.pending_takeover_recovery()
    assert len(obligations) == 1
    assert (
        obligations[0].intent_run_seq,
        obligations[0].predecessor_epoch,
        obligations[0].next_epoch,
        obligations[0].effect_permission,
    ) == (2, 1, 2, "NONE")
    probes = []

    def observe(subject):
        probes.append((subject.control_epoch, subject.claim_run_seq))
        return LocalLaceCrashObservation(subject.subject_identity, "UNRESOLVED")

    claim, reconciliation = owner.resume_reconciled_takeover(
        intent_run_seq=2, native_observer=observe
    )
    events = journal.verify("run-one", auth=authenticator())
    assert events[-2]["run_seq"] == claim and events[-2]["kind"] == "RESOURCE_RECOVERY_CLAIMED"
    assert (
        events[-1]["run_seq"] == reconciliation
        and events[-1]["kind"] == "RESOURCE_CRASH_RECONCILED"
    )
    assert probes == [(2, claim)]
    assert events[-1]["payload"]["cleanup_permitted"] is False
    assert events[-1]["payload"]["replay_permitted"] is False
    with pytest.raises(CausalJournalError):
        owner.resume_reconciled_takeover(intent_run_seq=2, native_observer=observe)
    assert len(probes) == 1
    conn.close()


def test_requires_predecessor_reconciliation_before_claim(tmp_path):
    conn, journal, owner = setup_takeover(tmp_path, reconciled=False)
    before = journal.verify("run-one", auth=authenticator())
    called = []
    with pytest.raises(CausalJournalError, match="reconciliation"):
        owner.resume_reconciled_takeover(
            intent_run_seq=2, native_observer=lambda s: called.append(s)
        )
    assert not called and before == journal.verify("run-one", auth=authenticator())
    conn.close()


@pytest.mark.parametrize(
    "attack",
    ["wrong-lease", "cancelled", "final-validator", "wrong-intent", "not-callable", "same-epoch"],
)
def test_takeover_attacks_reject_before_new_claim_or_native_scan(tmp_path, attack):
    if attack == "same-epoch":
        conn, journal, owner, _ = seeded(tmp_path)
        owner.reconcile_crash(
            intent_run_seq=2,
            native_observer=lambda s: LocalLaceCrashObservation(
                s.subject_identity, "PRESENT_MATCHING"
            ),
        )
    else:
        conn, journal, owner = setup_takeover(tmp_path)
    if attack == "wrong-lease":
        owner.session.token = "foreign"
    if attack == "cancelled":
        owner.session.cancelled = True
    if attack == "final-validator":
        owner = replace(
            owner,
            validate_authority_in=lambda _: (_ for _ in ()).throw(CausalJournalError("revoked")),
        )
    intent = 3 if attack == "wrong-intent" else 2
    native = (
        None
        if attack == "not-callable"
        else lambda s: (_ for _ in ()).throw(AssertionError("native called"))
    )
    before = journal.verify("run-one", auth=authenticator())
    with pytest.raises(CausalJournalError):
        owner.resume_reconciled_takeover(intent_run_seq=intent, native_observer=native)
    assert before == journal.verify("run-one", auth=authenticator())
    conn.close()


def test_native_scan_failure_keeps_new_claim_as_unresolved_durable_obligation(tmp_path):
    conn, journal, owner = setup_takeover(tmp_path)

    def fail_native(_subject):
        raise RuntimeError("read-only host unavailable")

    with pytest.raises(RuntimeError, match="unavailable"):
        owner.resume_reconciled_takeover(intent_run_seq=2, native_observer=fail_native)
    events = journal.verify("run-one", auth=authenticator())
    assert events[-1]["kind"] == "RESOURCE_RECOVERY_CLAIMED"
    assert events[-1]["payload"]["control_epoch"] == 2
    with pytest.raises(CausalJournalError):
        owner.resume_reconciled_takeover(intent_run_seq=2, native_observer=fail_native)
    conn.close()
    restarted = sqlite3.connect(tmp_path / "original.sqlite", isolation_level=None)
    assert (
        CausalRunJournal(restarted).verify("run-one", auth=authenticator())[-1]["kind"]
        == "RESOURCE_RECOVERY_CLAIMED"
    )
    restarted.close()


def test_read_worklist_requires_original_write_transaction(tmp_path):
    conn, journal, owner = setup_takeover(tmp_path)
    with pytest.raises(CausalJournalError, match="transaction"):
        pending_takeover_in(
            journal, run_id="run-one", epoch=2, lease_identity="new", auth=authenticator()
        )
    conn.close()


def test_second_takeover_uses_new_reconciled_epoch_only(tmp_path):
    conn, journal, owner = setup_takeover(tmp_path)
    owner.resume_reconciled_takeover(
        intent_run_seq=2,
        native_observer=lambda s: LocalLaceCrashObservation(s.subject_identity, "UNRESOLVED"),
    )
    journal.transfer_control(
        "run-one", epoch=2, lease_identity="new", next_epoch=3, next_lease_identity="next"
    )
    owner.session.token = "next"
    pending = owner.pending_takeover_recovery()
    assert len(pending) == 1 and pending[0].predecessor_epoch == 2
    owner.resume_reconciled_takeover(
        intent_run_seq=2,
        native_observer=lambda s: LocalLaceCrashObservation(s.subject_identity, "UNRESOLVED"),
    )
    assert [
        e["payload"]["control_epoch"]
        for e in journal.verify("run-one", auth=authenticator())
        if e["kind"] == "RESOURCE_RECOVERY_CLAIMED"
    ] == [1, 2, 3]
    conn.close()


@pytest.mark.parametrize("race", ["lease-takeover", "forged-native", "native-interrupted"])
def test_claim_persists_but_no_reconciliation_on_race_after_native_scan(tmp_path, race):
    conn, journal, owner = setup_takeover(tmp_path)

    def native(subject):
        if race == "lease-takeover":
            owner.session.token = "stale-owner"
        if race == "native-interrupted":
            raise KeyboardInterrupt()
        return LocalLaceCrashObservation(
            "sha256:" + "1" * 64 if race == "forged-native" else subject.subject_identity,
            "UNRESOLVED",
        )

    with pytest.raises(KeyboardInterrupt if race == "native-interrupted" else CausalJournalError):
        owner.resume_reconciled_takeover(intent_run_seq=2, native_observer=native)
    events = journal.verify("run-one", auth=authenticator())
    assert events[-1]["kind"] == "RESOURCE_RECOVERY_CLAIMED"
    assert events[-1]["payload"]["control_epoch"] == 2
    assert len([e for e in events if e["kind"] == "RESOURCE_CRASH_RECONCILED"]) == 1
    conn.close()

"""B12D2C3: crash reconciliation does not grant native effects or replay."""

from __future__ import annotations

import sqlite3
from dataclasses import replace

import pytest

from oh_goon.core.ports.local_lace_cleanup import NativeCleanupFailure, NativeCleanupResult
from oh_goon.core.ports.local_lace_crash_reconciliation import LocalLaceCrashObservation
from oh_goon.runtime.enforcer_lace_journal import CausalJournalError
from tests.fixtures.db1b2_legacy_sqlite import CausalRunJournal
from tests.unit.core.test_enforcer_lace_trust import authenticator
from tests.unit.runtime.test_b12d2c1_cleanup_preparation import seeded


def _events(journal):
    return journal.verify("run-one", auth=authenticator())


def test_started_without_record_is_not_replayed_even_after_takeover(tmp_path):
    connection, journal, old, subject = seeded(tmp_path)
    ticket = old.prepare_cleanup(subject)
    with pytest.raises(KeyboardInterrupt):
        old.execute_native_cleanup(ticket, lambda: (_ for _ in ()).throw(KeyboardInterrupt()))
    journal.transfer_control(
        "run-one", epoch=1, lease_identity="lease-one", next_epoch=2, next_lease_identity="new"
    )
    old.session.token = "new"
    new = old
    new.claim(intent_run_seq=2)
    current = new.rediscovery_subject(intent_run_seq=2)
    calls = []
    seq = new.reconcile_crash(
        intent_run_seq=2,
        native_observer=lambda s: (
            calls.append("observe") or LocalLaceCrashObservation(s.subject_identity, "UNRESOLVED")
        ),
    )
    assert calls == ["observe"]
    event = _events(journal)[-1]
    assert seq == event["run_seq"]
    assert event["kind"] == "RESOURCE_CRASH_RECONCILED"
    assert event["payload"]["window"] == "STARTED_UNRECORDED"
    assert event["payload"]["attempted_labels"] == []
    assert event["payload"]["cleanup_permitted"] is False
    assert event["payload"]["replay_permitted"] is False
    assert current.claim_run_seq == event["payload"]["claim_run_seq"]
    with pytest.raises(CausalJournalError):
        new.reconcile_crash(
            intent_run_seq=2,
            native_observer=lambda s: LocalLaceCrashObservation(s.subject_identity, "UNRESOLVED"),
        )
    with pytest.raises(CausalJournalError):
        new.execute_native_cleanup(
            ticket, lambda: NativeCleanupResult(("CGROUP",), "CLEANUP_ACTIONS_ATTEMPTED")
        )
    connection.close()
    restarted = sqlite3.connect(tmp_path / "original.sqlite", isolation_level=None)
    assert (
        CausalRunJournal(restarted).verify("run-one", auth=authenticator())[-1]["kind"]
        == "RESOURCE_CRASH_RECONCILED"
    )
    restarted.close()


@pytest.mark.parametrize(
    "failure", ["wrong-lease", "cancelled", "forged", "claim-drift", "duplicate", "final-validator"]
)
def test_attacks_cannot_commit_or_authorize_native_cleanup(tmp_path, failure):
    connection, journal, owner, subject = seeded(tmp_path)
    if failure == "wrong-lease":
        owner.session.token = "different"
    if failure == "cancelled":
        owner.session.cancelled = True
    if failure == "claim-drift":
        journal.transfer_control(
            "run-one", epoch=1, lease_identity="lease-one", next_epoch=2, next_lease_identity="new"
        )
    if failure == "duplicate":
        owner.reconcile_crash(
            intent_run_seq=2,
            native_observer=lambda s: LocalLaceCrashObservation(
                s.subject_identity, "PRESENT_MATCHING"
            ),
        )
    if failure == "final-validator":
        owner = replace(
            owner,
            validate_authority_in=lambda _: (_ for _ in ()).throw(CausalJournalError("validator")),
        )
    before = len(_events(journal))
    calls = []

    def probe(s):
        calls.append("observed")
        return LocalLaceCrashObservation(
            "sha256:" + "0" * 64 if failure == "forged" else s.subject_identity, "PRESENT_MATCHING"
        )

    with pytest.raises(CausalJournalError):
        owner.reconcile_crash(intent_run_seq=2, native_observer=probe)
    assert len(_events(journal)) == before
    connection.close()


def test_native_partial_failure_labels_are_bounded_and_not_success(tmp_path):
    connection, journal, owner, subject = seeded(tmp_path)
    ticket = owner.prepare_cleanup(subject)
    result = owner.execute_native_cleanup(
        ticket, lambda: (_ for _ in ()).throw(NativeCleanupFailure(("NETWORK",)))
    )
    assert result.result == "NATIVE_FAILED"
    event_seq = owner.reconcile_crash(
        intent_run_seq=2,
        native_observer=lambda s: LocalLaceCrashObservation(s.subject_identity, "PARTIAL_NETWORK"),
    )
    event = _events(journal)[-1]
    assert event_seq == event["run_seq"]
    assert event["payload"]["window"] == "RECORDED_UNQUALIFIED"
    assert event["payload"]["attempted_labels"] == ["NETWORK"]
    assert event["payload"]["native_state"] == "PARTIAL_NETWORK"
    assert event["payload"]["teardown_proven"] is False
    connection.close()


def test_intent_only_crash_cannot_become_native_absence(tmp_path):
    connection, journal, owner, _ = seeded(tmp_path, allocated=False)
    owner.reconcile_crash(
        intent_run_seq=2,
        native_observer=lambda s: LocalLaceCrashObservation(
            s.subject_identity, "INTENT_ONLY_UNKNOWN"
        ),
    )
    event = _events(journal)[-1]["payload"]
    assert event["window"] == "INTENT_ONLY_UNKNOWN"
    assert not event["cleanup_permitted"] and not event["native_absence_proven"]
    connection.close()


@pytest.mark.parametrize(
    "attack", ["false-absence", "intent-only-present", "present-as-intent-only", "wrong-type"]
)
def test_crash_observation_cannot_invent_physical_proof(tmp_path, attack):
    connection, journal, owner, item = seeded(tmp_path, allocated=attack != "intent-only-present")
    before = len(_events(journal))

    def observe(s):
        base = LocalLaceCrashObservation(s.subject_identity, "PRESENT_MATCHING")
        if attack == "false-absence":
            return replace(base, native_absence_proven=True)
        if attack == "intent-only-present":
            return base
        if attack == "present-as-intent-only":
            return replace(base, state="INTENT_ONLY_UNKNOWN")
        return {"subject_identity": s.subject_identity, "state": "PRESENT_MATCHING"}

    with pytest.raises(CausalJournalError):
        owner.reconcile_crash(intent_run_seq=2, native_observer=observe)
    assert len(_events(journal)) == before
    connection.close()


def test_lease_changed_during_native_scan_rejects_original_journal_cut(tmp_path):
    connection, journal, owner, item = seeded(tmp_path)
    before = len(_events(journal))

    def racing_observer(s):
        owner.session.token = "takeover-lease"
        return LocalLaceCrashObservation(s.subject_identity, "PRESENT_MATCHING")

    with pytest.raises(CausalJournalError):
        owner.reconcile_crash(intent_run_seq=2, native_observer=racing_observer)
    assert len(_events(journal)) == before
    connection.close()

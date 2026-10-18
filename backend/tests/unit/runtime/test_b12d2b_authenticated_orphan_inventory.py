"""B12D2B negative proof: native inventory is never cleanup permission."""

from __future__ import annotations

import sqlite3
from dataclasses import replace

import pytest

from oh_goon.core.ports.local_lace_orphan_inventory import LocalLaceOrphanInventory
from oh_goon.runtime.enforcer_lace_fleet_resource_owner import OriginalFleetLocalLaceRecoveryOwner
from oh_goon.runtime.enforcer_lace_journal import CausalJournalError
from oh_goon.runtime.enforcer_lace_orphan_subject import inventory_in, subject_in
from tests.fixtures.db1b2_legacy_sqlite import CausalRunJournal
from tests.unit.core.test_enforcer_lace_trust import authenticator
from tests.unit.runtime.test_b12_local_resource_intent import _reserve, _store
from tests.unit.runtime.test_b12c2_original_fleet_local_lace import _OriginalSession
from tests.unit.runtime.test_b12d1_native_allocation_journal import observation, publish
from tests.unit.runtime.test_b12d2_recovery_claims import _claim


def subject(journal, *, epoch=1, lease="lease-one"):
    return journal._transaction(
        lambda: subject_in(
            journal,
            "run-one",
            epoch=epoch,
            lease_identity=lease,
            auth=authenticator(),
            intent_run_seq=2,
        )
    )


def record(journal, item, inventory, *, epoch=1, lease="lease-one"):
    return journal._transaction(
        lambda: inventory_in(
            journal, item, inventory, epoch=epoch, lease_identity=lease, auth=authenticator()
        )
    )


def unknown(item):
    return LocalLaceOrphanInventory(
        item.subject_identity, "INTENT_ONLY_UNKNOWN", None, None, None, (), None, None
    )


def observed(item):
    return LocalLaceOrphanInventory(
        item.subject_identity,
        "OBSERVED_PRESENT",
        observation().cgroup_identity,
        123,
        456,
        (),
        observation().network_identity,
        "sha256:" + "c" * 64,
    )


@pytest.mark.parametrize("has_observation", [False, True])
def test_orphan_subject_is_exact_authenticated_claim_after_restart(tmp_path, has_observation):
    path = tmp_path / "journal.db"
    conn, journal = _store(path)
    _reserve(journal)
    if has_observation:
        publish(journal)
    _claim(journal)
    first = subject(journal)
    assert first.observation is not None if has_observation else first.observation is None
    assert first.intent_run_seq == 2
    assert first.control_epoch == 1
    conn.close()
    reopened = sqlite3.connect(path, isolation_level=None)
    journal = CausalRunJournal(reopened)
    assert subject(journal) == first
    inv = observed(first) if has_observation else unknown(first)
    assert record(journal, first, inv) == (5 if has_observation else 4)
    receipt = journal.verify("run-one", auth=authenticator())[-1]["payload"]
    assert receipt["stage"] == "READ_ONLY_UNQUALIFIED"
    assert receipt["cleanup_permitted"] is False
    assert receipt["teardown_proven"] is False
    assert not receipt["inventory"]["native_absence_proven"]
    with pytest.raises(CausalJournalError, match="already committed"):
        record(journal, first, inv)
    reopened.close()


def test_claim_required_and_takeover_invalidates_earlier_inventory(tmp_path):
    conn, journal = _store(tmp_path / "journal.db")
    _reserve(journal)
    with pytest.raises(CausalJournalError, match="claim"):
        subject(journal)
    _claim(journal)
    original = subject(journal)
    journal.transfer_control(
        "run-one",
        epoch=1,
        lease_identity="lease-one",
        next_epoch=2,
        next_lease_identity="new-owner",
    )
    with pytest.raises(CausalJournalError, match="stale"):
        record(journal, original, unknown(original))
    _claim(journal, epoch=2, lease="new-owner")
    next_subject = subject(journal, epoch=2, lease="new-owner")
    assert next_subject.subject_identity != original.subject_identity
    with pytest.raises(CausalJournalError, match="stale"):
        record(journal, original, unknown(original), epoch=2, lease="new-owner")
    assert record(journal, next_subject, unknown(next_subject), epoch=2, lease="new-owner") == 5
    conn.close()


@pytest.mark.parametrize(
    "attack",
    [
        "foreign-subject",
        "forged-absence",
        "forged-present",
        "invented-resource",
        "bad-mount-claim",
        "uncommitted",
    ],
)
def test_inventory_attacks_fail_without_new_journal_effects(tmp_path, attack):
    conn, journal = _store(tmp_path / "journal.db")
    _reserve(journal)
    _claim(journal)
    item = subject(journal)
    native = unknown(item)
    if attack == "foreign-subject":
        item = replace(item, step_id="different")
    elif attack == "forged-absence":
        native = replace(native, native_absence_proven=True)
    elif attack == "forged-present":
        native = replace(native, state="OBSERVED_PRESENT")
    elif attack == "invented-resource":
        native = replace(native, cgroup_inode=5)
    elif attack == "bad-mount-claim":
        native = replace(native, mounts_proven=True)
    before = len(journal.verify("run-one", auth=authenticator()))
    if attack == "uncommitted":
        with pytest.raises(CausalJournalError, match="transaction"):
            inventory_in(
                journal, item, native, epoch=1, lease_identity="lease-one", auth=authenticator()
            )
    else:
        with pytest.raises(CausalJournalError):
            record(journal, item, native)
    assert len(journal.verify("run-one", auth=authenticator())) == before
    conn.close()


def test_original_session_checks_authority_before_and_after_native_receipt(tmp_path):
    conn, journal = _store(tmp_path / "journal.db")
    _reserve(journal)
    _claim(journal)
    session = _OriginalSession(conn)
    original = OriginalFleetLocalLaceRecoveryOwner(
        session=session,
        validate_authority_in=lambda _: None,
        authenticator=authenticator(),
        run_id="run-one",
    )
    item = original.rediscovery_subject(intent_run_seq=2)
    assert item == subject(journal)
    session.token = "wrong-owner"
    with pytest.raises(CausalJournalError, match="original Fleet owner"):
        original.record_rediscovery(item, unknown(item))
    session.token = "lease-one"
    assert original.record_rediscovery(item, unknown(item)) == 4
    conn.close()

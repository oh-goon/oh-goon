"""B12D2C1: cleanup preparation carries no physical effect or teardown authority."""
from __future__ import annotations

import sqlite3
from dataclasses import replace

import pytest

from oh_goon.runtime.enforcer_lace_journal import CausalJournalError
from tests.fixtures.db1b2_legacy_sqlite import CausalRunJournal
from oh_goon.runtime.enforcer_lace_cleanup_attempt import prepare_cleanup_in, abort_cleanup_in
from oh_goon.runtime.enforcer_lace_fleet_resource_owner import OriginalFleetLocalLaceRecoveryOwner
from tests.unit.core.test_enforcer_lace_trust import authenticator
from tests.unit.runtime.test_b12_local_resource_intent import _store, _reserve
from tests.unit.runtime.test_b12d1_native_allocation_journal import publish
from tests.unit.runtime.test_b12d2_recovery_claims import _claim
from tests.unit.runtime.test_b12d2b_authenticated_orphan_inventory import subject, record, observed, unknown
from tests.unit.runtime.test_b12c2_original_fleet_local_lace import _OriginalSession


def seeded(tmp_path, *, allocated=True, inventory=True):
    conn, journal = _store(tmp_path / "original.sqlite")
    _reserve(journal)
    if allocated:
        publish(journal)
    _claim(journal)
    item = subject(journal)
    if inventory:
        record(journal, item, observed(item) if allocated else unknown(item))
    owner = OriginalFleetLocalLaceRecoveryOwner(_OriginalSession(conn), lambda _: None,
                                                authenticator(), "run-one")
    return conn, journal, owner, item


def test_ticket_is_durable_non_executable_and_only_failure_can_be_reported(tmp_path):
    conn, journal, owner, item = seeded(tmp_path)
    ticket = owner.prepare_cleanup(item)
    assert ticket.intent_run_seq == 2 and ticket.claim_run_seq == item.claim_run_seq
    assert ticket.ticket_identity.startswith("sha256:")
    event = journal.verify("run-one", auth=authenticator())[-1]
    assert event["kind"] == "RESOURCE_CLEANUP_PREPARED"
    assert event["payload"]["effect_permission"] == "NONE"
    assert event["payload"]["stage"] == "NATIVE_EXECUTION_NOT_IMPLEMENTED"
    assert not any(event["payload"][x] for x in (
        "cleanup_complete", "native_absence_proven", "process_tree_proven", "mounts_proven", "teardown_proven"))
    assert owner.record_cleanup_blocker(ticket, failure_code="NATIVE_UNAVAILABLE") == ticket.prepare_run_seq + 1
    conn.close()
    restarted = sqlite3.connect(tmp_path / "original.sqlite", isolation_level=None)
    assert CausalRunJournal(restarted).verify("run-one", auth=authenticator())[-1]["kind"] == "RESOURCE_CLEANUP_BLOCKED"
    restarted.close()


@pytest.mark.parametrize("attack", ["no-inventory", "unknown", "forged-subject", "wrong-lease",
                                     "cancelled", "rollback", "duplicate", "stale-epoch"])
def test_cleanup_preparation_fails_without_new_journal_effects(tmp_path, attack):
    conn, journal, owner, item = seeded(tmp_path, allocated=attack != "unknown", inventory=attack != "no-inventory")
    if attack == "forged-subject":
        item = replace(item, step_id="foreign")
    if attack == "wrong-lease":
        owner.session.token = "foreign"
    if attack == "cancelled":
        owner.session.cancelled = True
    if attack == "stale-epoch":
        journal.transfer_control("run-one", epoch=1, lease_identity="lease-one",
                                 next_epoch=2, next_lease_identity="next")
    if attack == "duplicate":
        owner.prepare_cleanup(item)
    before = len(journal.verify("run-one", auth=authenticator()))
    if attack == "rollback":
        def reject(_):
            raise CausalJournalError("final admission revalidation failed")
        owner = replace(owner, validate_authority_in=reject)
    with pytest.raises(CausalJournalError):
        owner.prepare_cleanup(item)
    assert len(journal.verify("run-one", auth=authenticator())) == before
    conn.close()


@pytest.mark.parametrize("attack", ["forged-ticket", "bad-code", "duplicate", "takeover", "cancelled-subject", "cancelled", "uncommitted"])
def test_cleanup_failure_reporting_is_still_original_epoch_fenced(tmp_path, attack):
    conn, journal, owner, item = seeded(tmp_path)
    ticket = owner.prepare_cleanup(item)
    if attack == "forged-ticket":
        ticket = replace(ticket, inventory_identity="sha256:" + "1" * 64)
    if attack == "bad-code":
        code = "native-error--customer-secret"
    else:
        code = "NATIVE_UNAVAILABLE"
    if attack == "duplicate":
        owner.record_cleanup_blocker(ticket, failure_code=code)
    if attack == "takeover":
        journal.transfer_control("run-one", epoch=1, lease_identity="lease-one",
                                 next_epoch=2, next_lease_identity="new")
    if attack == "cancelled-subject":
        owner.session.token = "other"
    if attack == "cancelled":
        owner.session.cancelled = True
    before = len(journal.verify("run-one", auth=authenticator()))
    with pytest.raises(CausalJournalError):
        if attack == "uncommitted":
            abort_cleanup_in(journal, ticket, epoch=1, lease_identity="lease-one",
                             auth=authenticator(), failure_code=code)
        else:
            owner.record_cleanup_blocker(ticket, failure_code=code)
    assert len(journal.verify("run-one", auth=authenticator())) == before
    conn.close()


def test_preparation_only_original_session_not_direct_call(tmp_path):
    conn, journal, _, item = seeded(tmp_path)
    with pytest.raises(CausalJournalError, match="transaction"):
        prepare_cleanup_in(journal, item, epoch=1, lease_identity="lease-one", auth=authenticator())
    conn.close()


def test_failed_final_validation_rolls_back_cleanup_ticket(tmp_path):
    conn, journal, owner, item = seeded(tmp_path)
    calls = [0]
    def validate(_):
        calls[0] += 1
        if calls[0] == 2:
            raise CausalJournalError("original Fleet authority changed before commit")
    owner = replace(owner, validate_authority_in=validate)
    before = len(journal.verify("run-one", auth=authenticator()))
    with pytest.raises(CausalJournalError, match="before commit"):
        owner.prepare_cleanup(item)
    assert len(journal.verify("run-one", auth=authenticator())) == before
    # Rollback must not consume the one attempt for the original claim.
    assert replace(owner, validate_authority_in=lambda _: None).prepare_cleanup(item).prepare_run_seq == before + 1
    conn.close()

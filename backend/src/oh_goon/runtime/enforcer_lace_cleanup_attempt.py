"""B12D2C1 original Fleet authority for *non-executable* orphan cleanup tickets.

A durable ticket records a specific observed native inventory as a recovery
obligation. It is NOT a native deletion capability, a replay token or an
absence/quiescence receipt. Native operations must be separately fenced and
implemented by the next physical cleanup owner.
"""

from __future__ import annotations

from oh_goon.core.enforcer_lace_trust import ReceiptAuthenticator, canonical_bytes, digest
from oh_goon.core.ports.local_lace_cleanup import LocalLaceCleanupTicket
from oh_goon.core.ports.local_lace_orphan_inventory import LocalLaceOrphanSubject
from oh_goon.runtime.enforcer_lace_journal import CausalJournalError, CausalRunJournal
from oh_goon.runtime.enforcer_lace_orphan_subject import subject_in


def _inventory_event(
    journal: CausalRunJournal, subject: LocalLaceOrphanSubject, *, auth: ReceiptAuthenticator
) -> dict:
    matches = [
        e
        for e in journal.verify(subject.run_id, auth=auth)
        if e["kind"] == "RESOURCE_ORPHAN_INVENTORIED"
        and e["payload"].get("claim_run_seq") == subject.claim_run_seq
    ]
    if len(matches) != 1:
        raise CausalJournalError("cleanup ticket requires one original committed inventory")
    event = matches[0]
    payload = event["payload"]
    observed = payload.get("inventory")
    if (
        payload.get("subject_identity") != subject.subject_identity
        or payload.get("intent_run_seq") != subject.intent_run_seq
        or payload.get("control_epoch") != subject.control_epoch
        or payload.get("cleanup_permitted") is not False
        or payload.get("teardown_proven") is not False
        or payload.get("stage") != "READ_ONLY_UNQUALIFIED"
        or type(observed) is not dict
        or observed.get("state") != "OBSERVED_PRESENT"
        or observed.get("subject_identity") != subject.subject_identity
        or type(observed.get("native_record_identity")) is not str
        or not observed["native_record_identity"].startswith("sha256:")
        or len(observed["native_record_identity"]) != 71
        or observed.get("teardown_proven") is not False
        or observed.get("native_absence_proven") is not False
        or observed.get("mounts_proven") is not False
        or observed.get("process_tree_proven") is not False
    ):
        raise CausalJournalError(
            "cleanup ticket requires exact unqualified original native inventory"
        )
    return event


def prepare_cleanup_in(
    journal: CausalRunJournal,
    subject: LocalLaceOrphanSubject,
    *,
    epoch: int,
    lease_identity: str,
    auth: ReceiptAuthenticator,
) -> LocalLaceCleanupTicket:
    """Persist the one non-executable attempt ticket under original Fleet write lock."""
    if not journal.connection.in_transaction:
        raise CausalJournalError("original cleanup preparation transaction required")
    if type(subject) is not LocalLaceOrphanSubject:
        raise CausalJournalError("original cleanup subject required")
    current = subject_in(
        journal,
        subject.run_id,
        epoch=epoch,
        lease_identity=lease_identity,
        auth=auth,
        intent_run_seq=subject.intent_run_seq,
    )
    if subject != current:
        raise CausalJournalError("stale or forged cleanup subject")
    inventory = _inventory_event(journal, subject, auth=auth)
    events = journal.verify(subject.run_id, auth=auth)
    if any(
        e["kind"] == "RESOURCE_CLEANUP_PREPARED"
        and e["payload"].get("intent_run_seq") == subject.intent_run_seq
        and e["payload"].get("claim_run_seq") == subject.claim_run_seq
        for e in events
    ):
        raise CausalJournalError("cleanup preparation replay under same recovery claim")
    inv_identity = digest(
        "oh-goon/b12d2c1/orphan-inventory/v1", canonical_bytes(inventory["payload"]["inventory"])
    )
    fields = {
        "run_id": subject.run_id,
        "intent_run_seq": subject.intent_run_seq,
        "claim_run_seq": subject.claim_run_seq,
        "inventory_run_seq": inventory["run_seq"],
        "control_epoch": epoch,
        "subject_identity": subject.subject_identity,
        "inventory_identity": inv_identity,
    }
    ticket_identity = digest("oh-goon/b12d2c1/cleanup-ticket/v1", canonical_bytes(fields))
    seq = journal._append_locked(
        subject.run_id,
        "RESOURCE_CLEANUP_PREPARED",
        {
            **fields,
            "ticket_identity": ticket_identity,
            "effect_permission": "NONE",
            "stage": "NATIVE_EXECUTION_NOT_IMPLEMENTED",
            "cleanup_complete": False,
            "native_absence_proven": False,
            "mounts_proven": False,
            "process_tree_proven": False,
            "teardown_proven": False,
        },
    )
    return LocalLaceCleanupTicket(**fields, prepare_run_seq=seq, ticket_identity=ticket_identity)


def abort_cleanup_in(
    journal: CausalRunJournal,
    ticket: LocalLaceCleanupTicket,
    *,
    epoch: int,
    lease_identity: str,
    auth: ReceiptAuthenticator,
    failure_code: str,
) -> int:
    """Commit bounded failure evidence; never claim cleanup succeeded."""
    if not journal.connection.in_transaction:
        raise CausalJournalError("original cleanup failure transaction required")
    if type(ticket) is not LocalLaceCleanupTicket or failure_code not in {
        "OWNER_FENCED",
        "NATIVE_UNAVAILABLE",
        "INVENTORY_DRIFT",
        "PREDECESSOR_PRESENT",
        "MOUNT_UNRESOLVED",
        "RECOVERY_INTERRUPTED",
    }:
        raise CausalJournalError("untrusted cleanup ticket or failure code")
    journal._check_fence(journal._row(ticket.run_id), epoch=epoch, lease_identity=lease_identity)
    if ticket.control_epoch != epoch:
        raise CausalJournalError("cleanup ticket belongs to stale control epoch")
    events = journal.verify(ticket.run_id, auth=auth)
    event = next((e for e in events if e["run_seq"] == ticket.prepare_run_seq), None)
    if (
        event is None
        or event["kind"] != "RESOURCE_CLEANUP_PREPARED"
        or event["payload"].get("ticket_identity") != ticket.ticket_identity
        or any(
            event["payload"].get(k) != getattr(ticket, k)
            for k in (
                "intent_run_seq",
                "claim_run_seq",
                "inventory_run_seq",
                "control_epoch",
                "subject_identity",
                "inventory_identity",
            )
        )
    ):
        raise CausalJournalError("cleanup ticket not journal-authenticated")
    if any(
        e["kind"] == "RESOURCE_CLEANUP_BLOCKED"
        and e["payload"].get("prepare_run_seq") == ticket.prepare_run_seq
        for e in events
    ):
        raise CausalJournalError("cleanup failure report replay")
    # A newer takeover/claim or changed source cannot be downgraded to this ticket.
    subject = subject_in(
        journal,
        ticket.run_id,
        epoch=epoch,
        lease_identity=lease_identity,
        auth=auth,
        intent_run_seq=ticket.intent_run_seq,
    )
    if (
        subject.claim_run_seq != ticket.claim_run_seq
        or subject.subject_identity != ticket.subject_identity
    ):
        raise CausalJournalError("cleanup subject drift")
    inventory = _inventory_event(journal, subject, auth=auth)
    if (
        inventory["run_seq"] != ticket.inventory_run_seq
        or digest(
            "oh-goon/b12d2c1/orphan-inventory/v1",
            canonical_bytes(inventory["payload"]["inventory"]),
        )
        != ticket.inventory_identity
    ):
        raise CausalJournalError("cleanup inventory drift")
    return journal._append_locked(
        ticket.run_id,
        "RESOURCE_CLEANUP_BLOCKED",
        {
            "prepare_run_seq": ticket.prepare_run_seq,
            "ticket_identity": ticket.ticket_identity,
            "control_epoch": epoch,
            "failure_code": failure_code,
            "effect_permission": "NONE",
            "cleanup_complete": False,
            "native_absence_proven": False,
            "mounts_proven": False,
            "process_tree_proven": False,
            "teardown_proven": False,
        },
    )

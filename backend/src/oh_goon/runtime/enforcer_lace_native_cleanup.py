"""B12D2C2: original Fleet fenced, one-shot native cleanup *attempt*.

A C1 preparation ticket is never a native capability. A fresh journal arm and
an original-session transaction must surround the actual native effect. Even a
successful attempt leaves physical quiescence and TEARDOWN_PROVEN unestablished.
"""

from __future__ import annotations

from typing import Callable

from oh_goon.core.enforcer_lace_trust import ReceiptAuthenticator
from oh_goon.core.ports.local_lace_cleanup import NativeCleanupFailure, NativeCleanupResult
from oh_goon.runtime.enforcer_lace_cleanup_attempt import (
    LocalLaceCleanupTicket,
    _inventory_event,
)
from oh_goon.runtime.enforcer_lace_journal import CausalJournalError, CausalRunJournal
from oh_goon.runtime.enforcer_lace_orphan_subject import subject_in


def _current_ticket(
    journal: CausalRunJournal,
    ticket: LocalLaceCleanupTicket,
    *,
    epoch: int,
    lease_identity: str,
    auth: ReceiptAuthenticator,
) -> list[dict]:
    if not journal.connection.in_transaction or type(ticket) is not LocalLaceCleanupTicket:
        raise CausalJournalError("original cleanup execution transaction and exact ticket required")
    journal._check_fence(journal._row(ticket.run_id), epoch=epoch, lease_identity=lease_identity)
    if ticket.control_epoch != epoch:
        raise CausalJournalError("stale native cleanup control epoch")
    events = journal.verify(ticket.run_id, auth=auth)
    preparation = next((e for e in events if e["run_seq"] == ticket.prepare_run_seq), None)
    if (
        preparation is None
        or preparation["kind"] != "RESOURCE_CLEANUP_PREPARED"
        or preparation["payload"].get("ticket_identity") != ticket.ticket_identity
        or preparation["payload"].get("effect_permission") != "NONE"
        or any(
            preparation["payload"].get(k) != getattr(ticket, k)
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
        raise CausalJournalError("untrusted original native cleanup ticket")
    current = subject_in(
        journal,
        ticket.run_id,
        epoch=epoch,
        lease_identity=lease_identity,
        auth=auth,
        intent_run_seq=ticket.intent_run_seq,
    )
    if (
        current.claim_run_seq != ticket.claim_run_seq
        or current.subject_identity != ticket.subject_identity
        or _inventory_event(journal, current, auth=auth)["run_seq"] != ticket.inventory_run_seq
    ):
        raise CausalJournalError("native cleanup subject or inventory changed")
    if any(
        e["kind"] in {"RESOURCE_CLEANUP_NATIVE_STARTED", "RESOURCE_CLEANUP_BLOCKED"}
        and e["payload"].get("prepare_run_seq") == ticket.prepare_run_seq
        for e in events
    ):
        raise CausalJournalError("native cleanup cannot replay a started or blocked ticket")
    return events


def arm_native_cleanup_in(
    journal: CausalRunJournal,
    ticket: LocalLaceCleanupTicket,
    *,
    epoch: int,
    lease_identity: str,
    auth: ReceiptAuthenticator,
) -> int:
    _current_ticket(journal, ticket, epoch=epoch, lease_identity=lease_identity, auth=auth)
    return journal._append_locked(
        ticket.run_id,
        "RESOURCE_CLEANUP_NATIVE_STARTED",
        {
            "prepare_run_seq": ticket.prepare_run_seq,
            "ticket_identity": ticket.ticket_identity,
            "inventory_identity": ticket.inventory_identity,
            "control_epoch": epoch,
            "cleanup_only": True,
            "replay_step": False,
            "cleanup_complete": False,
            "teardown_proven": False,
        },
    )


def execute_native_cleanup_in(
    journal: CausalRunJournal,
    ticket: LocalLaceCleanupTicket,
    *,
    started_run_seq: int,
    epoch: int,
    lease_identity: str,
    auth: ReceiptAuthenticator,
    native_action: Callable[[], NativeCleanupResult],
) -> NativeCleanupResult:
    """Execute inside original BEGIN IMMEDIATE, never inside a second authority.

    An interrupted process leaves durable STARTED without completion; no automatic
    replay of that ticket is possible. All native recovery records are retained.
    """
    if not journal.connection.in_transaction:
        raise CausalJournalError("original native effect transaction required")
    journal._check_fence(journal._row(ticket.run_id), epoch=epoch, lease_identity=lease_identity)
    events = journal.verify(ticket.run_id, auth=auth)
    start = next((e for e in events if e["run_seq"] == started_run_seq), None)
    if (
        start is None
        or start["kind"] != "RESOURCE_CLEANUP_NATIVE_STARTED"
        or start["payload"].get("ticket_identity") != ticket.ticket_identity
        or start["payload"].get("prepare_run_seq") != ticket.prepare_run_seq
        or start["payload"].get("control_epoch") != epoch
        or ticket.control_epoch != epoch
        or any(
            e["kind"] == "RESOURCE_CLEANUP_NATIVE_RECORDED"
            and e["payload"].get("started_run_seq") == started_run_seq
            for e in events
        )
    ):
        raise CausalJournalError("native cleanup start is forged, replayed or stale")
    current = subject_in(
        journal,
        ticket.run_id,
        epoch=epoch,
        lease_identity=lease_identity,
        auth=auth,
        intent_run_seq=ticket.intent_run_seq,
    )
    if (
        current.claim_run_seq != ticket.claim_run_seq
        or current.subject_identity != ticket.subject_identity
        or _inventory_event(journal, current, auth=auth)["run_seq"] != ticket.inventory_run_seq
    ):
        raise CausalJournalError("native cleanup authority drift before effect")
    # Only a trusted in-process native owner is allowed to supply this callback.
    # Exceptions from actual native mutations are recorded as failure. A process
    # crash (BaseException) intentionally leaves STARTED unresolved.
    try:
        result = native_action()
        if (
            type(result) is not NativeCleanupResult
            or result.result != "CLEANUP_ACTIONS_ATTEMPTED"
            or not result.attempted
            or len(result.attempted) > 2
            or any(x not in {"NETWORK", "CGROUP"} for x in result.attempted)
            or len(set(result.attempted)) != len(result.attempted)
        ):
            raise CausalJournalError("native cleanup owner returned invalid result")
    except NativeCleanupFailure as exc:
        result = NativeCleanupResult(exc.attempted, "NATIVE_FAILED")
    except Exception:
        result = NativeCleanupResult((), "NATIVE_FAILED")
    journal._append_locked(
        ticket.run_id,
        "RESOURCE_CLEANUP_NATIVE_RECORDED",
        {
            "started_run_seq": started_run_seq,
            "ticket_identity": ticket.ticket_identity,
            "control_epoch": epoch,
            "result": result.result,
            "attempted": list(result.attempted),
            "cleanup_complete": False,
            "native_absence_proven": False,
            "mounts_proven": False,
            "process_tree_proven": False,
            "teardown_proven": False,
        },
    )
    return result

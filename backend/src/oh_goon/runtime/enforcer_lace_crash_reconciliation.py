"""B12D2C3: authenticated crash-window classification, strictly non-executable.

The current original Fleet session owns this journal transaction. Neither an
old STARTED ticket nor an apparently deleted resource can authorize a retry.
"""
from __future__ import annotations

from oh_goon.core.enforcer_lace_trust import ReceiptAuthenticator
from oh_goon.core.ports.local_lace_crash_reconciliation import LocalLaceCrashObservation
from oh_goon.runtime.enforcer_lace_journal import CausalRunJournal, CausalJournalError
from oh_goon.runtime.enforcer_lace_orphan_subject import subject_in


_ALLOWED = {"PRESENT_MATCHING", "PARTIAL_NETWORK", "INTENT_ONLY_UNKNOWN", "UNRESOLVED"}


def reconcile_crash_in(journal: CausalRunJournal, *, run_id: str, intent_run_seq: int,
                       epoch: int, lease_identity: str, auth: ReceiptAuthenticator,
                       observation: LocalLaceCrashObservation) -> int:
    """Persist one bounded original-Journal reconciliation for this recovery claim.

    All states are unresolved obligations. Any future native action requires a
    NEW claim in a strictly greater epoch and an independently fresh inventory.
    """
    if not journal.connection.in_transaction:
        raise CausalJournalError("original Fleet reconciliation transaction required")
    journal._check_fence(journal._row(run_id), epoch=epoch, lease_identity=lease_identity)
    current = subject_in(journal, run_id, epoch=epoch, lease_identity=lease_identity,
                         auth=auth, intent_run_seq=intent_run_seq)
    if (type(observation) is not LocalLaceCrashObservation
            or type(observation.subject_identity) is not str
            or observation.subject_identity != current.subject_identity
            or observation.state not in _ALLOWED
            or observation.native_absence_proven is not False
            or observation.teardown_proven is not False
            or ((current.observation is None) !=
                (observation.state == "INTENT_ONLY_UNKNOWN"))):
        raise CausalJournalError("untrusted or incongruent native crash observation")
    events = journal.verify(run_id, auth=auth)
    if any(e["kind"] == "RESOURCE_CRASH_RECONCILED" and
           e["payload"].get("claim_run_seq") == current.claim_run_seq for e in events):
        raise CausalJournalError("crash reconciliation cannot replay in same claim")
    # Trace the authentic old native attempt for this *intent*, regardless of
    # whether a different Fleet epoch now owns recovery. NEVER replay its ticket.
    prepared = {e["run_seq"]: e for e in events if
                e["kind"] == "RESOURCE_CLEANUP_PREPARED" and
                e["payload"].get("intent_run_seq") == intent_run_seq}
    started = [e for e in events if e["kind"] == "RESOURCE_CLEANUP_NATIVE_STARTED"
               and e["payload"].get("prepare_run_seq") in prepared]
    recorded = [e for e in events if e["kind"] == "RESOURCE_CLEANUP_NATIVE_RECORDED"
                and any(e["payload"].get("started_run_seq") == s["run_seq"] for s in started)]
    if len(started) > 1 or len(recorded) > 1 or (recorded and not started):
        raise CausalJournalError("ambiguous native crash history")
    if recorded:
        result = recorded[0]["payload"]
        if (result.get("result") not in {"NATIVE_FAILED", "CLEANUP_ACTIONS_ATTEMPTED"}
                or type(result.get("attempted")) is not list
                or len(result["attempted"]) > 2
                or len(set(result["attempted"])) != len(result["attempted"])
                or any(x not in {"NETWORK", "CGROUP"} for x in result["attempted"])):
            raise CausalJournalError("invalid original native cleanup attempt history")
        window = "RECORDED_UNQUALIFIED"
        attempted = result["attempted"]
    elif started:
        window = "STARTED_UNRECORDED"
        attempted = []  # UNKNOWN: process may have died after any native effect
    elif current.observation is None:
        window = "INTENT_ONLY_UNKNOWN"
        attempted = []
    else:
        window = "ALLOCATED_NO_NATIVE_START"
        attempted = []
    # Never treat deletion attempt, missing native resource, or empty cgroup
    # as teardown proof. Do not serialize native paths, stderr, or identities.
    return journal._append_locked(run_id, "RESOURCE_CRASH_RECONCILED", {
        "intent_run_seq": intent_run_seq,
        "claim_run_seq": current.claim_run_seq,
        "subject_identity": current.subject_identity,
        "control_epoch": epoch,
        "window": window,
        "native_state": observation.state,
        "attempted_labels": attempted,
        "cleanup_permitted": False,
        "replay_permitted": False,
        "cleanup_complete": False,
        "native_absence_proven": False,
        "mounts_proven": False,
        "process_tree_proven": False,
        "teardown_proven": False,
    })

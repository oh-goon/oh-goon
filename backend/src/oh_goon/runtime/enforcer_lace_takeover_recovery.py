"""B12D2C3B1: original-journal takeover worklist and renewed recovery gate.

Read and validate the predecessor's authenticated *reconciliation* before a
current Fleet owner can renew a cleanup-only claim. This module never issues
native effects, grants execution authority, or interprets missing evidence as
resource absence. It is an owner-bound coordinator seam, not a background reaper.
"""

from __future__ import annotations

from dataclasses import dataclass

from oh_goon.core.enforcer_lace_trust import ReceiptAuthenticator
from oh_goon.runtime.enforcer_lace_journal import CausalJournalError, CausalRunJournal

_ALLOWED_WINDOWS = frozenset(
    {
        "STARTED_UNRECORDED",
        "RECORDED_UNQUALIFIED",
        "INTENT_ONLY_UNKNOWN",
        "ALLOCATED_NO_NATIVE_START",
    }
)
_ALLOWED_STATES = frozenset(
    {
        "PRESENT_MATCHING",
        "PARTIAL_NETWORK",
        "INTENT_ONLY_UNKNOWN",
        "UNRESOLVED",
    }
)


@dataclass(frozen=True, slots=True)
class LocalLaceTakeoverObligation:
    run_id: str
    intent_run_seq: int
    predecessor_claim_run_seq: int
    predecessor_reconciliation_run_seq: int
    predecessor_epoch: int
    next_epoch: int
    window: str
    native_state: str
    # This is a worklist hint, NOT a native cleanup capability.
    effect_permission: str = "NONE"
    teardown_proven: bool = False


def pending_takeover_in(
    journal: CausalRunJournal,
    *,
    run_id: str,
    epoch: int,
    lease_identity: str,
    auth: ReceiptAuthenticator,
    max_intents: int = 128,
) -> tuple[LocalLaceTakeoverObligation, ...]:
    """Return bounded, original-session, journal-authenticated obligations.

    A missing/ambiguous historical fact fails closed. No mutated state, no
    privileged host access, and no native resource side effects occur here.
    """
    if not journal.connection.in_transaction:
        raise CausalJournalError("original Fleet takeover read transaction required")
    journal._check_fence(journal._row(run_id), epoch=epoch, lease_identity=lease_identity)
    events = journal.verify(run_id, auth=auth)
    intents = [e for e in events if e["kind"] == "RESOURCE_INTENT"]
    if len(intents) > max_intents:
        raise CausalJournalError("takeover LACE intent count exceeds bounded recovery scan")
    result = []
    for intent in intents:
        seq = intent["run_seq"]
        claims = [
            e
            for e in events
            if e["kind"] == "RESOURCE_RECOVERY_CLAIMED"
            and e["payload"].get("intent_run_seq") == seq
        ]
        if not claims:
            # Unclaimed intents are NOT eligible for implicit recovery admission.
            continue
        prior = claims[-1]
        previous_epoch = prior["payload"].get("control_epoch")
        if (
            type(previous_epoch) is not int
            or previous_epoch >= epoch
            or prior["payload"].get("cleanup_only") is not True
        ):
            # Current-epoch claims must not be reissued. Wrong histories fail.
            raise CausalJournalError("LACE takeover requires strictly older cleanup-only claim")
        reconcilers = [
            e
            for e in events
            if e["kind"] == "RESOURCE_CRASH_RECONCILED"
            and e["payload"].get("claim_run_seq") == prior["run_seq"]
        ]
        if len(reconcilers) != 1:
            raise CausalJournalError(
                "LACE takeover requires exactly one previous crash reconciliation"
            )
        reconciliation = reconcilers[0]
        payload = reconciliation["payload"]
        if (
            payload.get("intent_run_seq") != seq
            or payload.get("control_epoch") != previous_epoch
            or payload.get("window") not in _ALLOWED_WINDOWS
            or payload.get("native_state") not in _ALLOWED_STATES
            or payload.get("cleanup_permitted") is not False
            or payload.get("replay_permitted") is not False
            or payload.get("cleanup_complete") is not False
            or any(
                payload.get(flag) is not False
                for flag in (
                    "native_absence_proven",
                    "mounts_proven",
                    "process_tree_proven",
                    "teardown_proven",
                )
            )
        ):
            raise CausalJournalError(
                "previous crash reconciliation does not authorize renewed recovery"
            )
        result.append(
            LocalLaceTakeoverObligation(
                run_id,
                seq,
                prior["run_seq"],
                reconciliation["run_seq"],
                previous_epoch,
                epoch,
                payload["window"],
                payload["native_state"],
            )
        )
    return tuple(result)

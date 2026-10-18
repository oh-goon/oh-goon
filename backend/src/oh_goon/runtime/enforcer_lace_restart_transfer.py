"""B12D2C3B5 original-issuer signed custody transfer, strictly cleanup-only.

One original scheduler BEGIN IMMEDIATE cut transfers the old dead execution lease,
advances the existing causal epoch and durably records the issuer-signed predecessor.
This never authorizes Plan/Step replay, process launching, or native deletion.
"""
from __future__ import annotations

from oh_goon.core.enforcer_lace_trust import (
    AdmittedRunContract, PROTOCOL_VERSION, ReceiptAuthenticator, TrustProtocolError,
)
from oh_goon.runtime.enforcer_lace_journal import CausalRunJournal, CausalJournalError
from oh_goon.runtime.execution import ExecutionCoordinator, ExecutionTicket


def commit_original_restart_transfer_in(
    *, journal: CausalRunJournal, coordinator: ExecutionCoordinator, run_id: str,
    predecessor_token: str, expected_epoch: int, auth: ReceiptAuthenticator,
    admitted_generation: int, admitted_trust_epoch: int,
    admitted_issuer_key_id: str,
) -> ExecutionTicket:
    """Transfer only orphan custody with original issuer + executor binding.

    Called only by FleetRunLifecycle inside its ORIGINAL owner-validated SQLite
    transaction. The caller MUST roll back everything on failure; this function
    may not establish membership, create a new run or reselect its executor.
    """
    if (not journal.connection.in_transaction
            or type(run_id) is not str or not run_id
            or type(predecessor_token) is not str or not predecessor_token
            or type(expected_epoch) is not int or expected_epoch < 1
            or type(auth) is not ReceiptAuthenticator
            or type(admitted_issuer_key_id) is not str
            or auth.key_id != admitted_issuer_key_id
            or type(admitted_generation) is not int or admitted_generation < 1
            or type(admitted_trust_epoch) is not int or admitted_trust_epoch < 0):
        raise CausalJournalError("exact original signer/epoch/generation required")
    row = journal._row(run_id)
    journal._check_fence(row, epoch=expected_epoch, lease_identity=predecessor_token)
    events = journal.verify(run_id, auth=auth)
    if not any(event["kind"] == "RESOURCE_INTENT" for event in events):
        raise CausalJournalError("original local LACE intent required for restart custody")
    admitted = journal.connection.execute(
        """SELECT admitted_contract,admission_key_id FROM lace_run_authority
           WHERE run_id=?""", (run_id,),
    ).fetchone()
    if admitted is None or admitted[1] != admitted_issuer_key_id:
        raise CausalJournalError("original issuer signer not enrolled for this Run")
    contract = AdmittedRunContract(admitted[0], row[0])
    contract.verify()
    if (contract.fields["run_id"] != run_id
            or contract.fields["executor_generation"] != admitted_generation):
        raise CausalJournalError("restart transfer contradicts frozen executor generation")
    if any(e["kind"] == "RUN_RECOVERY_CUSTODY_TRANSFERRED"
           and e["payload"]["binding"]["predecessor_token"] == predecessor_token
           for e in events):
        raise CausalJournalError("original predecessor has already been reconciled")
    ticket, predecessor = coordinator.transfer_dead_fleet_to_recovery_in(
        journal.connection, run_id=run_id, predecessor_token=predecessor_token,
        predecessor_epoch=expected_epoch,
    )
    next_epoch = expected_epoch + 1
    binding = {
        "protocol": PROTOCOL_VERSION, "decision": "RECOVERY_CUSTODY_ONLY",
        "run_id": run_id, "contract_identity": contract.identity,
        "issuer_key_id": admitted_issuer_key_id,
        "executor_generation": admitted_generation,
        "executor_trust_epoch": admitted_trust_epoch,
        "predecessor_token": predecessor_token,
        "successor_token": ticket._lease.token,
        "predecessor_epoch": expected_epoch, "next_epoch": next_epoch,
        "predecessor_session": predecessor["predecessor_session"],
        "predecessor_pid": predecessor["predecessor_pid"],
        "predecessor_birth": predecessor["predecessor_birth"],
        "successor_session": predecessor["successor_session"],
        "successor_pid": predecessor["successor_pid"],
        "successor_birth": predecessor["successor_birth"],
        "effect_permission": "NONE", "step_replay_permitted": False,
        "native_absence_proven": False, "predecessor_reconciliation": "UNRESOLVED",
    }
    receipt = auth.issue("recovery-transfer", binding)
    if auth.verify(receipt, domain="recovery-transfer") != binding:
        raise TrustProtocolError("original transfer signature failed before commit")
    journal.connection.execute(
        """UPDATE lace_run_authority SET control_epoch=?,lease_identity=?
           WHERE run_id=? AND control_epoch=? AND lease_identity=? AND state='ACTIVE'""",
        (next_epoch, ticket._lease.token, run_id, expected_epoch, predecessor_token),
    )
    # Preserve the original predecessor reconciliation authority: a mere dead
    # PID or unclaimed intent must never become a newly authorized worklist.
    # Worklist evaluation is inside this same original SQLite writer cut; if any
    # intent lacks exactly one valid previous crash reconciliation, EVERYTHING
    # (including the execution lease CAS) rolls back.
    from oh_goon.runtime.enforcer_lace_takeover_recovery import pending_takeover_in
    obligations = pending_takeover_in(
        journal, run_id=run_id, epoch=next_epoch,
        lease_identity=ticket._lease.token, auth=auth,
    )
    if (len(obligations) != len([
            e for e in events if e["kind"] == "RESOURCE_INTENT"])
            or any(item.predecessor_epoch != expected_epoch for item in obligations)):
        raise CausalJournalError("unreconciled predecessor intent prevents durable custody transfer")
    # The lease and epoch advance are in the same transaction as the event.
    journal._append_locked(run_id, "RUN_RECOVERY_CUSTODY_TRANSFERRED", {
        "binding": binding, "signature": receipt.mac_hex,
        "signature_domain": receipt.domain, "signature_key_id": receipt.key_id,
    })
    return ticket

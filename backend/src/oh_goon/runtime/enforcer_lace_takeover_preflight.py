"""B12D2C3B4: proof-only original Fleet recovery lineage, never takeover authority.

The original Fleet write cut already proves the live lease and unchanged placement.
This check additionally rejects cross-generation/issuer drift and foreign original
RESOURCE_INTENT data before even a cleanup-only recovery claim is committed.
"""

from __future__ import annotations

from dataclasses import dataclass

from oh_goon.core.enforcer_lace_trust import (
    PROTOCOL_VERSION,
    AdmittedRunContract,
    AuthenticatedReceipt,
    ReceiptAuthenticator,
)
from oh_goon.runtime.enforcer_lace_journal import CausalJournalError, CausalRunJournal


@dataclass(frozen=True, slots=True)
class OriginalTakeoverPreflight:
    """Non-executable diagnostic identity; possession grants NO further permission."""

    run_id: str
    intent_run_seq: int
    contract_identity: str
    issuer_key_id: str
    executor_generation: int
    control_epoch: int
    effect_permission: str = "NONE"


def require_original_takeover_lineage_in(
    journal: CausalRunJournal,
    *,
    run_id: str,
    intent_run_seq: int,
    epoch: int,
    lease_identity: str,
    auth: ReceiptAuthenticator,
    admitted_executor_generation: int,
    admitted_issuer_key_id: str,
) -> OriginalTakeoverPreflight:
    """Compare original signed admission, resource intent and current session in one cut."""
    if not journal.connection.in_transaction:
        raise CausalJournalError("original Fleet takeover write transaction required")
    if (
        type(admitted_executor_generation) is not int
        or admitted_executor_generation < 1
        or type(admitted_issuer_key_id) is not str
        or not admitted_issuer_key_id
        or type(intent_run_seq) is not int
        or intent_run_seq < 2
    ):
        raise CausalJournalError("invalid original takeover authority binding")
    row = journal._row(run_id)
    journal._check_fence(row, epoch=epoch, lease_identity=lease_identity)
    admitted = journal.connection.execute(
        """SELECT admitted_contract, admission_receipt, admission_mac, admission_key_id,
                  admission_domain FROM lace_run_authority WHERE run_id=?""",
        (run_id,),
    ).fetchone()
    if (
        admitted is None
        or admitted[3] != admitted_issuer_key_id
        or auth.key_id != admitted_issuer_key_id
    ):
        raise CausalJournalError("original Fleet takeover issuer differs from admitted signer")
    contract = AdmittedRunContract(admitted[0], row[0])
    contract.verify()
    receipt = AuthenticatedReceipt(admitted[4], admitted[3], admitted[1], admitted[2])
    if auth.verify(receipt, domain="admission") != {
        "decision": "ADMITTED",
        "run_id": run_id,
        "contract_identity": contract.identity,
        "protocol": PROTOCOL_VERSION,
    }:
        raise CausalJournalError("original Fleet takeover admission receipt not bound")
    fields = contract.fields
    if fields["run_id"] != run_id or fields["executor_generation"] != admitted_executor_generation:
        raise CausalJournalError("original Fleet takeover executor generation changed")
    events = journal.verify(run_id, auth=auth)
    matches = [event for event in events if event["run_seq"] == intent_run_seq]
    if len(matches) != 1 or matches[0]["kind"] != "RESOURCE_INTENT":
        raise CausalJournalError("original Fleet takeover intent not authenticated")
    payload = matches[0]["payload"]
    step = payload.get("step_id")
    if (
        payload.get("contract_identity") != contract.identity
        or payload.get("backend") != "bubblewrap"
        or payload.get("execution_environment_identity") != fields["execution_environment_identity"]
        or payload.get("backend_capability_manifest_identity")
        != fields["backend_capability_manifest_identity"]
        or type(step) is not str
        or step not in fields["step_envelopes"]
        or step not in fields["step_teardown_envelopes"]
    ):
        raise CausalJournalError("original Fleet takeover intent differs from enrolled Run/Step")
    return OriginalTakeoverPreflight(
        run_id,
        intent_run_seq,
        contract.identity,
        admitted_issuer_key_id,
        admitted_executor_generation,
        epoch,
    )

"""B12D2C3B8: bind original signer trust generation to admitted causal Run.

Only the original strict Fleet ingress may append this authenticated event in
its existing admission transaction. No new signer, schema, or journal is made.
"""

from __future__ import annotations

from oh_goon.core.enforcer_lace_trust import (
    PROTOCOL_VERSION,
    ReceiptAuthenticator,
    TrustProtocolError,
)
from oh_goon.runtime.enforcer_lace_journal import CausalJournalError, CausalRunJournal
from oh_goon.runtime.enforcer_lace_signer_trust_epoch import SignerTrustGrant


def append_original_admitted_signer_epoch_in(
    journal: CausalRunJournal,
    *,
    run_id: str,
    auth: ReceiptAuthenticator,
    grant: SignerTrustGrant,
) -> None:
    """Commit a signed trust snapshot along with ORIGINAL RUN_ADMITTED (seq 2).

    Must happen exactly once after strict admission and before admission commit.
    The original outer SQLite writer owns all rollback on failure. The source
    is the signer grant already captured by the fixed native issuer path.
    """
    if (
        type(journal) is not CausalRunJournal
        or not journal.connection.in_transaction
        or type(run_id) is not str
        or not run_id
        or type(auth) is not ReceiptAuthenticator
        or type(grant) is not SignerTrustGrant
        or grant.epoch < 1
    ):
        raise TrustProtocolError("original strict Fleet signed signer generation required")
    row = journal._row(run_id)
    if row[3] != 1:
        raise CausalJournalError("signer epoch must immediately follow original Run admission")
    binding = {
        "protocol": PROTOCOL_VERSION,
        "run_id": run_id,
        "contract_identity": row[0],
        "issuer_key_id": auth.key_id,
        "trust_epoch": grant.epoch,
        "policy_identity": grant.policy_identity,
        "root_identity": grant.root_identity,
    }
    signed = auth.issue("admission-signer-epoch", binding)
    journal._append_locked(
        run_id,
        "RUN_SIGNER_EPOCH_BOUND",
        {
            "binding": binding,
            "signature": signed.mac_hex,
            "signature_domain": signed.domain,
            "signature_key_id": signed.key_id,
        },
    )


def require_original_admitted_signer_epoch_in(
    journal: CausalRunJournal,
    *,
    run_id: str,
    auth: ReceiptAuthenticator,
    expected: SignerTrustGrant,
) -> None:
    """Verify the authentic original admission epoch, not merely today's active row."""
    if not isinstance(journal, CausalRunJournal) or not journal.connection.in_transaction:
        raise TrustProtocolError("original causal Run write transaction required")
    verified = journal.verify(run_id, auth=auth)
    if len(verified) < 2 or verified[1]["kind"] != "RUN_SIGNER_EPOCH_BOUND":
        raise CausalJournalError("original signed Run signer epoch missing; no legacy upgrade")
    b = verified[1]["payload"]["binding"]
    if (b["trust_epoch"], b["policy_identity"], b["root_identity"]) != (
        expected.epoch,
        expected.policy_identity,
        expected.root_identity,
    ):
        raise TrustProtocolError("original Run signer epoch revoked or rotated since admission")

"""B12D2C3B8: original provisioner/signer epoch precommit restart fence.

This is a negative guard on the ORIGINAL Fleet SQLite write transaction.
It does not issue keys, enroll a Run, establish a new issuer, authorize Steps,
or attest independent OS custody. Its caller owns rollback and commit.
"""
from __future__ import annotations

import os
import sqlite3

from oh_goon.core.enforcer_lace_trust import TrustProtocolError
from oh_goon.native_issuer.enforcer_lace_signer_custody import (
    deployment_signer_policy_snapshot,
)
from oh_goon.runtime.enforcer_lace_signer_trust_epoch import (
    SignerTrustEpochAuthority, SignerTrustGrant,
)
from oh_goon.services.fleet_prework_provisioner import FleetPreworkProvisioner
from oh_goon.services.fleet_strict_policy_enrollment import StrictFleetPolicyEnrollment


def require_original_restart_issuer_in(
    connection: sqlite3.Connection,
    *,
    provisioner: FleetPreworkProvisioner,
    binding: StrictFleetPolicyEnrollment,
    run_id: str,
    grant: SignerTrustGrant,
) -> None:
    """Verify original native issuer, roots and *same-DB* revocation in one cut.

    Called once before the lease transfer and again after the signed journal
    append, immediately before the enclosing original transaction commits.
    The second cut rejects filesystem/OS custody drift; the same SQLite write
    transaction excludes an interleaved SQLite revoke/rotate writer. The
    provisioner itself performs the original six-file and native inventory
    observations; no user-supplied callback or fallback signer is accepted.
    """
    if (not isinstance(connection, sqlite3.Connection)
            or not connection.in_transaction
            or type(provisioner) is not FleetPreworkProvisioner
            or type(binding) is not StrictFleetPolicyEnrollment
            or type(run_id) is not str or not run_id or binding.run_id != run_id
            or type(grant) is not SignerTrustGrant):
        raise TrustProtocolError("original Fleet issuer transaction, enrollment and captured epoch required")
    # Exactly the original object: even an equal-but-recreated enrollment is
    # not an authority to change issuer while a recovery transfer is in flight.
    if provisioner.resolve(run_id) is not binding:
        raise TrustProtocolError("original Fleet enrollment changed before custody commit")
    # B9: post-restart custody requires OS-independent public-root ownership.
    # Legacy same-principal keyrings are still usable for historical non-recovery
    # paths, but cannot authorize a new cleanup custodian. The original
    # provisioner is the only permitted native observer, not a receipt from
    # the Run, CLI or recovery caller.
    observation = provisioner.inspect_independent_root_custody(run_id)
    observer_uid = os.geteuid()
    if (observation.get("schema") != "oh-goon.fleet-deployment-os-custody/v1"
            or observation.get("status") != "OBSERVED_NOT_ATTESTED"
            or observation.get("run_id") != run_id
            or type(observation.get("signer_process_euid")) is not int
            or observation["signer_process_euid"] != observer_uid
            or type(observation.get("independent_public_root_owner_uid")) is not int
            or observation["independent_public_root_owner_uid"] == observer_uid
            or observation["independent_public_root_owner_uid"] < 0
            or type(observation.get("issuer_roots_checked")) is not int
            or observation["issuer_roots_checked"] != 6
            or observation.get("enrollment") != "OBSERVED_NOT_ADMITTED"
            or observation.get("external_provisioning_attestation") != "NOT_ESTABLISHED"
            or provisioner.public_root_owner_uid != observation["independent_public_root_owner_uid"]):
        raise TrustProtocolError("independent original Fleet public-root OS custody unproven")
    binding.configured.require_unchanged(binding.current)
    roots = binding.trusted_public_roots
    authority = SignerTrustEpochAuthority(connection)
    authority.require_in(
        connection, grant=grant,
        policy_snapshot=deployment_signer_policy_snapshot(
            key_directory=binding.key_directory, trusted_public_roots=roots,
        ),
        roots=roots,
    )
    # An active row is NOT sufficient: the original Run may have been admitted
    # under an older, now revoked/rotated signer epoch. Verify its original
    # issuer-MAC-bound grant from the single causal Run journal before mutation.
    from oh_goon.runtime.enforcer_lace_admitted_signer_epoch import (
        require_original_admitted_signer_epoch_in,
    )
    from oh_goon.runtime.enforcer_lace_journal import CausalRunJournal
    require_original_admitted_signer_epoch_in(
        CausalRunJournal(connection), run_id=run_id,
        auth=binding.authenticator, expected=grant,
    )

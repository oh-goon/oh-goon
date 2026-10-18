"""M164 fixed non-live bindings between the six native issuer owners and custody.

    Resolve once, transport once, execute once, report once.

Only the fixed named native modules below can observe/sign their own authority.
There is intentionally no public callback, caller-supplied repository selection,
caller-selected issuer field, or generic signing API. Deployment initialization
must own journal, path authority, key directory, trusted roots and inventory.
The resulting bundle is NOT a live or atomic pre-effect admission decision.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING

from oh_goon.core.enforcer_lace_issuer_evidence import (
    AUTHORITY_FIELDS, IssuerEvidence, TrustedIssuerVerifier,
)
from oh_goon.core.enforcer_lace_trust import AdmittedRunContract, TrustProtocolError
from oh_goon.core.path_authority import StrictHostPathAuthority
from oh_goon.native_issuer import (
    enforcer_lace_backend_capability_issuer as capability_owner,
    enforcer_lace_captured_run_issuer as run_owner,
    enforcer_lace_execution_environment_issuer as environment_owner,
    enforcer_lace_native_lock_issuer as lock_owner,
    enforcer_lace_native_tool_issuer as tool_owner,
)
from oh_goon.native_issuer.enforcer_lace_signer_custody import (
    deployment_signer_policy_snapshot, issue_with_deployment_signer,
)
from oh_goon.runtime.journal import Journal
from oh_goon.native_issuer.enforcer_lace_prework_capture import PreworkRunCapture


if TYPE_CHECKING:
    from oh_goon.runtime.enforcer_lace_signer_trust_epoch import SignerTrustEpochAuthority, SignerTrustGrant


def issue_fixed_native_admission_evidence(
    *, journal: Journal, contract: AdmittedRunContract,
    path_authority: StrictHostPathAuthority, key_directory: Path,
    trusted_public_roots: Mapping[str, tuple[str, bytes]],
    control_state_root: Path | None = None,
    prework: PreworkRunCapture | None = None,
) -> tuple[IssuerEvidence, ...]:
    """Issue six fixed owner claims under one unchanged signer policy generation.

    Owner functions are selected *inside* this trusted module, never supplied by
    the Run or by callers. All six issuer modules still observe their actual
    sources twice and compare the exact contract field. Before and after the
    bundle the operational RUN_ADMITTED is read again, and the *deployment*
    signer policy is independently checked for rotation/revocation. Failure
    discards the entire bundle (no partial evidence is returned).
    """
    if (not isinstance(journal, Journal) or
            not isinstance(path_authority, StrictHostPathAuthority) or
            not isinstance(key_directory, Path) or
            (control_state_root is not None and not isinstance(control_state_root, Path))):
        raise TrustProtocolError("trusted native Journal, tool and signer owners required")
    contract.verify()
    if prework is not None and type(prework) is not PreworkRunCapture:
        raise TrustProtocolError("trusted native pre-work capture required")
    extra = {} if prework is None else {"prework": prework}
    # Validate the exact six public trust roots independently of signer custody.
    roots = dict(TrustedIssuerVerifier(trusted_public_roots)._roots)
    start, revision = run_owner._read_original_admission(journal, contract.fields["run_id"], prework=prework)
    signer_before = deployment_signer_policy_snapshot(
        key_directory=key_directory, trusted_public_roots=roots)

    # Deliberately explicit: no dynamic field -> function lookup, plugin registry,
    # caller-supplied signing callback or fallback owner.
    fixed = {
        "repository_identity": lambda issuer, key: run_owner.issue_observed_captured_run_repository_identity(
            journal=journal, contract=contract, issuer_id=issuer, signing_key=key, **extra),
        "source_identity": lambda issuer, key: run_owner.issue_observed_captured_run_source(
            journal=journal, contract=contract, issuer_id=issuer, signing_key=key, **extra),
        "lock_identity": lambda issuer, key: lock_owner.issue_observed_captured_run_lock_identity(
            journal=journal, contract=contract, issuer_id=issuer, signing_key=key, **extra),
        "native_tool_identity": lambda issuer, key: tool_owner.issue_observed_captured_run_native_tool_identity(
            journal=journal, contract=contract, path_authority=path_authority,
            issuer_id=issuer, signing_key=key, **extra),
        "execution_environment_identity": lambda issuer, key: (
            environment_owner.issue_observed_captured_run_execution_environment_identity(
                journal=journal, contract=contract, issuer_id=issuer, signing_key=key,
                control_state_root=control_state_root, **extra)),
        "backend_capability_manifest_identity": lambda issuer, key: (
            capability_owner.issue_observed_captured_run_backend_capability_manifest_identity(
                journal=journal, contract=contract, issuer_id=issuer, signing_key=key,
                control_state_root=control_state_root, **extra)),
    }
    if set(fixed) != AUTHORITY_FIELDS:
        raise TrustProtocolError("fixed native issuer set is incomplete")
    evidence = tuple(issue_with_deployment_signer(
        key_directory=key_directory, trusted_public_roots=roots,
        field=field, contract=contract, native_issuer=fixed[field],
    ) for field in sorted(AUTHORITY_FIELDS))
    later, new_revision = run_owner._read_original_admission(journal, contract.fields["run_id"], prework=prework)
    signer_after = deployment_signer_policy_snapshot(
        key_directory=key_directory, trusted_public_roots=roots)
    if later != start or new_revision != revision or signer_after != signer_before:
        raise TrustProtocolError("durable Run or signer trust generation changed during issuance")
    if len(evidence) != len(AUTHORITY_FIELDS) or {x.field for x in evidence} != AUTHORITY_FIELDS:
        raise TrustProtocolError("native issuer coverage is incomplete")
    return evidence


def issue_fixed_native_evidence_with_signer_grant(
    *, journal: Journal, contract: AdmittedRunContract,
    path_authority: StrictHostPathAuthority, key_directory: Path,
    trusted_public_roots: Mapping[str, tuple[str, bytes]],
    signer_authority: "SignerTrustEpochAuthority",
    control_state_root: Path | None = None,
    prework: PreworkRunCapture | None = None,
) -> tuple["SignerTrustGrant", tuple[IssuerEvidence, ...]]:
    """Capture the durable signer generation BEFORE issuing all six claims.

    Keep the same owner-observed M164 fixed native issuer map. The returned
    immutable epoch must be transported unchanged into M165's pre-work admission
    fence; *never* substitute a new epoch after signature issuance.
    Non-live integration primitive, not an independent signer or Enforcer route.
    """
    from oh_goon.runtime.enforcer_lace_signer_trust_epoch import (
        SignerTrustEpochAuthority, identity_for_policy, identity_for_roots,
    )

    if not isinstance(signer_authority, SignerTrustEpochAuthority):
        raise TrustProtocolError("same-database durable signer authority required")
    roots = dict(TrustedIssuerVerifier(trusted_public_roots)._roots)
    snapshot = deployment_signer_policy_snapshot(
        key_directory=key_directory, trusted_public_roots=roots)
    grant = signer_authority.active()
    if (grant.policy_identity != identity_for_policy(snapshot)
            or grant.root_identity != identity_for_roots(roots)):
        raise TrustProtocolError("durable signer trust generation differs before issuance")
    evidence = issue_fixed_native_admission_evidence(
        journal=journal, contract=contract, path_authority=path_authority,
        key_directory=key_directory, trusted_public_roots=roots,
        control_state_root=control_state_root, prework=prework,
    )
    after = deployment_signer_policy_snapshot(
        key_directory=key_directory, trusted_public_roots=roots)
    if (signer_authority.active() != grant or after != snapshot):
        raise TrustProtocolError("durable signer trust generation changed during issuance")
    return grant, evidence


def issue_fixed_prework_native_evidence_with_signer_grant(
    *, prework: PreworkRunCapture, contract: AdmittedRunContract,
    path_authority: StrictHostPathAuthority, key_directory: Path,
    trusted_public_roots: Mapping[str, tuple[str, bytes]],
    signer_authority: "SignerTrustEpochAuthority",
    control_state_root: Path | None = None,
) -> tuple["SignerTrustGrant", tuple[IssuerEvidence, ...]]:
    """M167: six issuer observations of the *uncommitted* Journal candidate.

    No caller-supplied Run event or alternative issuer callbacks. Must be called
    inside the original admission transaction; never commits or starts effects.
    """
    if type(prework) is not PreworkRunCapture:
        raise TrustProtocolError("original transaction-bound pre-work capture required")
    prework.read(journal=prework.journal, run_id=contract.fields["run_id"])
    return issue_fixed_native_evidence_with_signer_grant(
        journal=prework.journal, contract=contract,
        path_authority=path_authority, key_directory=key_directory,
        trusted_public_roots=trusted_public_roots, signer_authority=signer_authority,
        control_state_root=control_state_root, prework=prework,
    )

"""M155: owner-observed source claim, issued outside the Enforcer process.

Only the canonical package-source authority may determine source_identity. This
is a *non-live* issuer composition primitive; it does not discover or load a key,
authorize a Run, attest dependency locks, or imply a stable pinned worktree.
Private signing keys belong solely to the separately deployed source issuer.
"""

from __future__ import annotations

from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from oh_goon.core.enforcer_lace_issuer_evidence import (
    ISSUER_PROTOCOL,
    SIGNATURE_DOMAIN,
    IssuerEvidence,
)
from oh_goon.core.enforcer_lace_trust import (
    AdmittedRunContract,
    TrustProtocolError,
    canonical_bytes,
)
from oh_goon.repository_tooling.release.package_slim import canonical_source_identity

SOURCE_FIELD = "source_identity"


def issue_observed_source_identity(
    *,
    root: Path,
    contract: AdmittedRunContract,
    issuer_id: str,
    signing_key: Ed25519PrivateKey,
) -> IssuerEvidence:
    """Attest the observed native source owner only, never a caller's identity.

    The first and second source-owner readings must agree. This does *not*
    replace immutable source pinning and pre-work staleness checks in LACE.
    Callers must deploy the issuer with an independent private key and an exact
    deployment-selected source root. Do not expose this method to Plan input.
    """
    contract.verify()
    if (
        not isinstance(root, Path)
        or not root.is_absolute()
        or root.is_symlink()
        or not root.is_dir()
        or not issuer_id
        or not isinstance(issuer_id, str)
        or not issuer_id.isascii()
        or any(c in issuer_id for c in "\x00\n/")
        or not isinstance(signing_key, Ed25519PrivateKey)
    ):
        raise TrustProtocolError("explicit source authority and private issuer key required")
    expected = contract.fields
    try:
        observed = canonical_source_identity(root)
    except (OSError, ValueError) as exc:
        raise TrustProtocolError("native source authority observation failed") from exc
    if observed != expected[SOURCE_FIELD]:
        raise TrustProtocolError("admitted source identity differs from native authority")
    payload = canonical_bytes(
        {
            "protocol": ISSUER_PROTOCOL,
            "field": SOURCE_FIELD,
            "run_id": expected["run_id"],
            "contract_identity": contract.identity,
            "identity": observed,
            "executor_generation": expected["executor_generation"],
        }
    )
    # This second native read catches observable mutation while preparing the
    # claim. Post-issuance replacement is handled by freshness/rebinding, not by
    # claiming a signed value permanently pins physical source bytes.
    try:
        observed_after = canonical_source_identity(root)
    except (OSError, ValueError) as exc:
        raise TrustProtocolError("native source authority no longer readable") from exc
    if observed_after != observed:
        raise TrustProtocolError("source authority changed during evidence issuance")
    message = SIGNATURE_DOMAIN + SOURCE_FIELD.encode("ascii") + b"\x00" + payload
    return IssuerEvidence(SOURCE_FIELD, issuer_id, payload, signing_key.sign(message).hex())

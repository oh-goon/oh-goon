"""M156: non-live issuer for an *admitted* single-repository source snapshot.

The Fleet admission source authority and Hashmarks-backed source_snapshot_identity
remain the owners. This module neither hashes repository bytes independently nor
replaces immutable binding, source pinning, or production signer key custody.
The multi-repository Run aggregation contract is intentionally not inferred here.
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
    digest,
)
from oh_goon.core.models.source_authority import RepositorySourceAuthority
from oh_goon.core.product_identity import (
    repository_subject_instance_id as repository_subject_instance_id,
)
from oh_goon.integrations.source_snapshot import (
    source_snapshot_identity as source_snapshot_identity,
)

SOURCE_FIELD = "source_identity"
SOURCE_IDENTITY_DOMAIN = "oh-goon/fleet-repository-source-authority/v1"


def admitted_repository_source_identity(authority: RepositorySourceAuthority) -> str:
    """One-repository contract projection from the surviving Fleet authority.

    A multi-repository Run needs its own *explicit* canonical aggregation and is
    not authorized by this single-repository projection.
    """
    if not isinstance(authority, RepositorySourceAuthority):
        raise TrustProtocolError("admitted repository source authority required")
    # Pydantic's frozen model validates embedded evidence and its canonical ID.
    verified = RepositorySourceAuthority.model_validate(authority.model_dump(mode="python"))
    if verified != authority:
        raise TrustProtocolError("repository source authority is not canonical")
    return digest(
        SOURCE_IDENTITY_DOMAIN,
        canonical_bytes(
            {
                "authority_id": authority.authority_id,
                "subject_instance_id": authority.subject_instance_id,
                "snapshot": authority.source_snapshot.model_dump(mode="json"),
            }
        ),
    )


def issue_observed_repository_source_identity(
    *,
    root: Path,
    authority: RepositorySourceAuthority,
    contract: AdmittedRunContract,
    issuer_id: str,
    signing_key: Ed25519PrivateKey,
) -> IssuerEvidence:
    """Sign only after *two* matching native snapshot and subject observations.

    Deployment selects root and independently holds the private signing key.
    Neither repository data nor a Fleet Plan may supply signer identity/key.
    The observed filtered identity is not a guarantee that source bytes are pinned
    after issuance: LACE must rebind/check physical source before any work.
    """
    contract.verify()
    if (
        not isinstance(root, Path)
        or not root.is_absolute()
        or root.is_symlink()
        or not root.is_dir()
        or root.resolve() != root
        or not isinstance(authority, RepositorySourceAuthority)
        or not isinstance(issuer_id, str)
        or not issuer_id
        or not issuer_id.isascii()
        or any(char in issuer_id for char in "\x00\n/")
        or not isinstance(signing_key, Ed25519PrivateKey)
    ):
        raise TrustProtocolError(
            "explicit native repository authority and private issuer key required"
        )

    expected = contract.fields
    projected = admitted_repository_source_identity(authority)
    if expected[SOURCE_FIELD] != projected:
        raise TrustProtocolError(
            "contract source identity differs from admitted repository authority"
        )

    def observe() -> tuple[str, str, int, int]:
        try:
            subject = repository_subject_instance_id(root)
            snapshot = source_snapshot_identity(root)
        except (OSError, ValueError, ImportError) as exc:
            raise TrustProtocolError("native repository snapshot observation failed") from exc
        return (subject, snapshot.sha256, snapshot.byte_count, snapshot.entry_count)

    expected_snapshot = authority.source_snapshot
    admitted = (
        authority.subject_instance_id,
        expected_snapshot.sha256,
        expected_snapshot.byte_count,
        expected_snapshot.entry_count,
    )
    first = observe()
    if first != admitted:
        raise TrustProtocolError(
            "native repository source snapshot differs from admitted authority"
        )

    payload = canonical_bytes(
        {
            "protocol": ISSUER_PROTOCOL,
            "field": SOURCE_FIELD,
            "run_id": expected["run_id"],
            "contract_identity": contract.identity,
            "identity": projected,
            "executor_generation": expected["executor_generation"],
        }
    )
    if observe() != first:
        raise TrustProtocolError("native repository source snapshot changed during issuance")
    signed = SIGNATURE_DOMAIN + SOURCE_FIELD.encode("ascii") + b"\x00" + payload
    return IssuerEvidence(SOURCE_FIELD, issuer_id, payload, signing_key.sign(signed).hex())

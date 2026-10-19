"""M157: non-live multi-repository Run source identity and native issuer.

The existing Fleet repository admissions own the selected source/subject facts;
Hashmarks-backed source_snapshot_identity and repository_subject_instance_id remain
the only native observers. The Run digest is a projection, never a re-hash.
"""

from __future__ import annotations

from pathlib import Path
from typing import Mapping

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
from oh_goon.core.models.run_admission import PlanRepositoryAdmissionEvidence
from oh_goon.native_issuer import enforcer_lace_repository_snapshot_issuer as native

RUN_SOURCE_DOMAIN = "oh-goon/fleet-run-repository-source-authorities/v1"
SOURCE_FIELD = "source_identity"


def _validated_entries(
    admissions: tuple[PlanRepositoryAdmissionEvidence, ...],
    selected_repository_ids: tuple[str, ...],
) -> tuple[dict[str, str], ...]:
    """Canonical exact set, same Plan and each surviving native authority."""
    if (
        type(admissions) is not tuple
        or len(admissions) < 2
        or type(selected_repository_ids) is not tuple
        or len(selected_repository_ids) != len(admissions)
    ):
        raise TrustProtocolError(
            "multi-repository admission requires exact captured repository scope"
        )
    ids = tuple(
        admission.repository_id
        for admission in admissions
        if isinstance(admission, PlanRepositoryAdmissionEvidence)
    )
    if (
        len(ids) != len(admissions)
        or ids != tuple(sorted(set(ids)))
        or selected_repository_ids != ids
    ):
        raise TrustProtocolError(
            "multi-repository admissions do not match canonical captured scope"
        )
    plan_ids = {admission.plan_id for admission in admissions}
    if len(plan_ids) != 1:
        raise TrustProtocolError("multi-repository admission must bind one admitted Plan")
    entries: list[dict[str, str]] = []
    for admission in admissions:
        try:
            verified = PlanRepositoryAdmissionEvidence.model_validate(
                admission.model_dump(mode="python")
            )
        except (ValueError, TypeError) as exc:
            raise TrustProtocolError("invalid repository admission authority") from exc
        if verified != admission:
            raise TrustProtocolError("noncanonical repository admission")
        entries.append(
            {
                "repository_id": admission.repository_id,
                "plan_id": admission.plan_id,
                "admission_id": admission.admission_id,
                "source_authority_identity": native.admitted_repository_source_identity(
                    admission.source_authority
                ),
            }
        )
    # Distinct repository IDs must not disguise duplicate physical subjects.
    subjects = [admission.source_authority.subject_instance_id for admission in admissions]
    if len(set(subjects)) != len(subjects):
        raise TrustProtocolError("different repositories cannot share one admitted subject")
    return tuple(entries)


def admitted_run_repository_source_identity(
    *,
    admissions: tuple[PlanRepositoryAdmissionEvidence, ...],
    selected_repository_ids: tuple[str, ...],
) -> str:
    """Canonical digest over the captured ordered per-repository admissions."""
    entries = _validated_entries(admissions, selected_repository_ids)
    return digest(RUN_SOURCE_DOMAIN, canonical_bytes({"repositories": list(entries)}))


def issue_observed_run_repository_source_identity(
    *,
    repositories: Mapping[str, Path],
    admissions: tuple[PlanRepositoryAdmissionEvidence, ...],
    selected_repository_ids: tuple[str, ...],
    contract: AdmittedRunContract,
    issuer_id: str,
    signing_key: Ed25519PrivateKey,
) -> IssuerEvidence:
    """Measure *all* selected repositories before and after the signing cut.

    The caller must transport the selected repository set from Fleet admission;
    neither arbitrary repository paths nor a claimed subset authorize the Run.
    There is no filesystem mutation or physical pinning here. LACE later binds.
    """
    contract.verify()
    entries = _validated_entries(admissions, selected_repository_ids)
    if (
        not isinstance(repositories, Mapping)
        or set(repositories) != set(selected_repository_ids)
        or not isinstance(issuer_id, str)
        or not issuer_id
        or not issuer_id.isascii()
        or any(c in issuer_id for c in "\x00\n/")
        or not isinstance(signing_key, Ed25519PrivateKey)
    ):
        raise TrustProtocolError("exact repository set and independent signing authority required")
    roots: list[Path] = []
    for repository_id in selected_repository_ids:
        root = repositories[repository_id]
        if (
            not isinstance(root, Path)
            or not root.is_absolute()
            or root.is_symlink()
            or not root.is_dir()
            or root.resolve() != root
        ):
            raise TrustProtocolError("unsafe or missing repository root")
        if any(root == other or root in other.parents or other in root.parents for other in roots):
            raise TrustProtocolError("repository roots must be disjoint physical subjects")
        roots.append(root)
    projected = digest(RUN_SOURCE_DOMAIN, canonical_bytes({"repositories": list(entries)}))
    fields = contract.fields
    if fields[SOURCE_FIELD] != projected:
        raise TrustProtocolError("contract source identity differs from captured Run repositories")

    def observe() -> tuple[tuple[str, str, int, int], ...]:
        observed = []
        for root in roots:
            try:
                subject = native.repository_subject_instance_id(root)
                snapshot = native.source_snapshot_identity(root)
            except (OSError, ValueError, ImportError) as exc:
                raise TrustProtocolError("native repository snapshot observation failed") from exc
            observed.append((subject, snapshot.sha256, snapshot.byte_count, snapshot.entry_count))
        return tuple(observed)

    expected = tuple(
        (
            admission.source_authority.subject_instance_id,
            admission.source_authority.source_snapshot.sha256,
            admission.source_authority.source_snapshot.byte_count,
            admission.source_authority.source_snapshot.entry_count,
        )
        for admission in admissions
    )
    first = observe()
    if first != expected:
        raise TrustProtocolError("native Run repositories differ from admitted source authority")
    payload = canonical_bytes(
        {
            "protocol": ISSUER_PROTOCOL,
            "field": SOURCE_FIELD,
            "run_id": fields["run_id"],
            "contract_identity": contract.identity,
            "identity": projected,
            "executor_generation": fields["executor_generation"],
        }
    )
    if observe() != first:
        raise TrustProtocolError("Run repository source changed during issuance")
    signed = SIGNATURE_DOMAIN + SOURCE_FIELD.encode("ascii") + b"\x00" + payload
    return IssuerEvidence(SOURCE_FIELD, issuer_id, payload, signing_key.sign(signed).hex())

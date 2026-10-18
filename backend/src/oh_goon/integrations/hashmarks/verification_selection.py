from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from oh_goon.integrations.hashmarks.verification_selection_primitives import (
    HashmarksVerificationMember,
    validate_native_selection,
)
from oh_goon.integrations.hashmarks.verification_selection_primitives import (
    HashmarksVerificationSelectionInputError as HashmarksVerificationSelectionInputError,
)


@dataclass(frozen=True, slots=True)
class VerifiedHashmarksVerificationSelection:
    producer_version: str
    producer_artifact_identity: str
    producer_implementation_identity: str
    repository_identity: str
    source_identity: str
    repository_provenance_identity: str
    membership_identity: str
    producer_contract_identity: str
    codemap_generation: int
    identity_generation: int | None
    evidence_hashes: tuple[tuple[str, str], ...]
    members: tuple[HashmarksVerificationMember, ...]

    @property
    def downstream_contract_identity(self) -> str:
        return self.producer_contract_identity

    @property
    def selected_test_nodes(self) -> tuple[str, ...]:
        return tuple(member.executable_selector for member in self.members)

    @property
    def unproven_owner_members(self) -> tuple[str, ...]:
        # Native Hashmarks carries selection-level owner evidence, not per-member
        # execution-authority proof. Oh-Goon must not infer stronger authority.
        return self.selected_test_nodes

    @property
    def revision_identity(self) -> str:
        return self.source_identity

    @property
    def envelope_identity(self) -> str:
        return self.repository_provenance_identity

    @property
    def hashmarks_evidence_identity(self) -> str:
        return self.repository_provenance_identity


def load_hashmarks_verification_selection(
    contract_path: Path,
    envelope_path: Path,
    *,
    admitted_repository_identity: str,
    admitted_source_identity: str,
    expected_producer_artifact_identity: str,
) -> VerifiedHashmarksVerificationSelection:
    """Freeze native Hashmarks membership; never reconstruct repository intelligence."""

    projected = validate_native_selection(
        contract_path,
        envelope_path,
        repository_identity=admitted_repository_identity,
        source_identity=admitted_source_identity,
        artifact_identity=expected_producer_artifact_identity,
    )
    return VerifiedHashmarksVerificationSelection(
        producer_version=projected.producer_version,
        producer_artifact_identity=expected_producer_artifact_identity,
        producer_implementation_identity=projected.producer_implementation_identity,
        repository_identity=projected.repository_identity,
        source_identity=projected.source_identity,
        repository_provenance_identity=projected.repository_provenance_identity,
        membership_identity=projected.membership_identity,
        producer_contract_identity=projected.producer_contract_identity,
        codemap_generation=projected.codemap_generation,
        identity_generation=projected.identity_generation,
        evidence_hashes=projected.evidence_hashes,
        members=projected.members,
    )
